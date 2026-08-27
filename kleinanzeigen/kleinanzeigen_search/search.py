"""Search orchestration: the plain city search and the along-the-route search."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from . import deals, geo, parser
from .client import HttpClient, HttpError
from .filters import SearchFilters
from .locations import Location, LocationResolver, plz_table, radius_at_least
from .models import Listing
from .routes import OSRM_TABLE_URL, Route, added_trip_minutes

log = logging.getLogger(__name__)

ADS_PER_PAGE = 25
# The site stops paginating long before the reported hit count is exhausted;
# asking beyond this just burns requests on empty pages.
MAX_PAGE = 50


@dataclass
class AreaCoverage:
    """How much of one search area's inventory we actually looked at."""

    label: str
    fetched: int
    reported: int | None
    exhausted: bool = False

    @property
    def complete(self) -> bool:
        if self.exhausted:
            return True  # paged until the site ran out, whatever it claimed
        return self.reported is not None and self.fetched >= self.reported


@dataclass
class SearchResult:
    listings: list[Listing]
    filters: SearchFilters
    location: Location | None = None
    route: Route | None = None
    centres: list[Location] = field(default_factory=list)
    total_reported: int | None = None
    pages_fetched: int = 0
    requests: int = 0
    warnings: list[str] = field(default_factory=list)
    corridor_km: float | None = None
    coverage: list[AreaCoverage] = field(default_factory=list)

    def summary(self) -> dict:
        data = deals.summarise(self.listings)
        data.update(
            pages_fetched=self.pages_fetched,
            requests=self.requests,
            total_reported=self.total_reported,
            search_areas=len(self.centres) or (1 if self.location else 0),
            areas_truncated=sum(1 for area in self.coverage if not area.complete),
        )
        return data

    def coverage_warnings(self) -> list[str]:
        """Areas where the site had more ads than we paged through.

        This is the failure mode that matters most: results are ordered newest
        first, so a truncated area silently hides every older ad in it - which
        is exactly where the bargains sit.
        """
        short = [area for area in self.coverage if not area.complete]
        if not short:
            return []
        worst = sorted(short, key=lambda a: (a.reported or 0) - a.fetched, reverse=True)[:3]
        detail = ", ".join(
            f"{a.label} ({a.fetched} of {a.reported if a.reported is not None else 'unknown'})" for a in worst
        )
        return [
            f"only saw part of the inventory in {len(short)} of {len(self.coverage)} areas: {detail}"
            f" - raise --pages, narrow the search term, or shrink --corridor for smaller areas"
        ]


@dataclass
class RequestBudget:
    """A shared ceiling on result-page requests for one run."""

    total: int | None = None
    spent: int = 0

    @property
    def exhausted(self) -> bool:
        return self.total is not None and self.spent >= self.total

    @property
    def remaining(self) -> int | None:
        return None if self.total is None else max(0, self.total - self.spent)


@dataclass
class AreaSearch:
    """One search area, paged lazily so depth can be bought where it pays."""

    label: str
    filters: SearchFilters
    listings: dict[str, Listing] = field(default_factory=dict)
    reported: int | None = None
    pages: int = 0
    exhausted: bool = False

    @property
    def deficit(self) -> int:
        """Ads the site says exist here that we have not seen yet.

        Capped at what pagination can actually reach, so a query reporting
        40,000 hits does not look infinitely worth deepening.
        """
        if self.exhausted:
            return 0
        if self.reported is None:
            # The site did not say how many hits there are, but the pages we
            # got were full, so there is more. Rank it below any area with a
            # known, larger backlog.
            return ADS_PER_PAGE
        reachable = min(self.reported, MAX_PAGE * ADS_PER_PAGE)
        return max(0, reachable - len(self.listings))

    @property
    def coverage(self) -> AreaCoverage:
        return AreaCoverage(self.label, len(self.listings), self.reported, self.exhausted)


def fetch_next_page(client: HttpClient, area: AreaSearch, include_sponsored: bool = False) -> int:
    """Fetch one more page of an area. Returns how many new ads it added."""
    page = area.pages + 1
    url = area.filters.for_page(page).url()
    log.debug("GET %s", url)
    markup = client.get(url)
    area.pages = page

    if area.reported is None:
        area.reported = parser.parse_result_total(markup)

    listings = parser.parse_listings(markup)
    organic = [l for l in listings if not l.sponsored]
    added = 0
    for listing in listings:
        # Sponsored "TOP" ads ignore the location filter entirely, so they are
        # dropped unless explicitly asked for.
        if listing.sponsored and not include_sponsored:
            continue
        if listing.ad_id not in area.listings:
            area.listings[listing.ad_id] = listing
            added += 1

    if len(organic) < ADS_PER_PAGE or not organic:
        area.exhausted = True          # last page
    elif added == 0:
        area.exhausted = True          # the site is repeating itself
    elif page >= MAX_PAGE:
        area.exhausted = True
    elif area.reported is not None and page * ADS_PER_PAGE >= area.reported:
        area.exhausted = True
    return added


def run_areas(
    client: HttpClient,
    areas: list[AreaSearch],
    initial_pages: int = 2,
    budget: RequestBudget | None = None,
    include_sponsored: bool = False,
    warnings: list[str] | None = None,
) -> RequestBudget:
    """Page every area, then spend what is left of the budget on depth.

    Results come back newest first, so an area with more ads than pages
    fetched hides its whole back catalogue - which is where the bargains sit.
    The second pass therefore keeps buying pages for whichever area is still
    hiding the most, so a dense town gets depth and an empty one does not.

    Deepening only happens when a budget is set: without one there is no
    stopping condition short of exhausting every area, which on a long route
    is hundreds of requests nobody asked for.
    """
    budget = budget or RequestBudget()
    warnings = warnings if warnings is not None else []

    def page_once(area: AreaSearch) -> bool:
        if area.exhausted or budget.exhausted:
            return False
        try:
            fetch_next_page(client, area, include_sponsored)
        except HttpError as exc:
            warnings.append(f"search in {area.label} stopped: {exc}")
            area.exhausted = True
        budget.spent += 1
        return True

    # Breadth first, round robin: every area gets its first page before any
    # gets a second, so a budget too small for the whole route still covers
    # all of it thinly rather than the first few areas deeply.
    for _ in range(max(1, initial_pages)):
        for area in areas:
            page_once(area)

    while budget.total is not None and not budget.exhausted:  # then depth where it pays
        hungry = [area for area in areas if area.deficit > 0]
        if not hungry:
            break
        page_once(max(hungry, key=lambda a: a.deficit))

    unopened = [area for area in areas if area.pages == 0]
    if unopened:
        warnings.append(
            f"--budget ran out before {len(unopened)} of {len(areas)} areas were searched at all "
            f"(first missed: {unopened[0].label})"
        )
    elif budget.exhausted and any(area.deficit > 0 for area in areas):
        warnings.append(
            f"stopped after {budget.spent} page requests (--budget); "
            f"{sum(a.deficit for a in areas)} ads in range were left unseen"
        )
    return budget


def fetch_pages(
    client: HttpClient,
    filters: SearchFilters,
    max_pages: int = 1,
    include_sponsored: bool = False,
) -> tuple[list[Listing], int | None, int]:
    """Fetch up to ``max_pages`` result pages, stopping when the site runs dry."""
    area = AreaSearch("", filters)
    for _ in range(max_pages):
        if area.exhausted:
            break
        fetch_next_page(client, area, include_sponsored)
    return list(area.listings.values()), area.reported, area.pages


def _annotate_coordinates(listings: list[Listing]) -> int:
    table = plz_table()
    unresolved = 0
    for listing in listings:
        entry = table.locate(listing.plz, listing.ort)
        if entry is None:
            unresolved += 1
            continue
        listing.lat, listing.lon = entry.lat, entry.lon
    return unresolved


def search_city(
    client: HttpClient,
    filters: SearchFilters,
    location: Location | None = None,
    max_pages: int = 2,
    include_sponsored: bool = False,
    score: bool = True,
    budget: int | None = None,
) -> SearchResult:
    """Mode A: one place, one radius, the standard filters."""
    before = client.request_count
    warnings: list[str] = []
    area = AreaSearch(location.label if location else "search", filters)
    run_areas(client, [area], max_pages, RequestBudget(budget), include_sponsored, warnings)
    listings = list(area.listings.values())
    total = area.reported
    pages = area.pages
    _annotate_coordinates(listings)

    centre = location.point if location else None
    if centre:
        for listing in listings:
            if listing.point:
                listing.distance_km = round(geo.haversine_km(centre, listing.point), 1)
        listings.sort(key=lambda l: (l.distance_km is None, l.distance_km or 0))

    if score:
        deals.evaluate(listings)
    result = SearchResult(
        listings=listings,
        filters=filters,
        location=location,
        total_reported=total,
        pages_fetched=pages,
        requests=client.request_count - before,
        warnings=warnings,
        coverage=[area.coverage],
    )
    result.warnings.extend(result.coverage_warnings())
    return result


def plan_circles(
    resolver: LocationResolver,
    route: Route,
    radius_km: int,
    max_circles: int = 40,
) -> tuple[list[Location], list[str]]:
    """Kleinanzeigen locations whose search radii blanket the route."""
    warnings: list[str] = []
    centres = geo.cover_route(route.points, radius_km)
    if len(centres) > max_circles:
        warnings.append(
            f"route needs {len(centres)} search areas at r={radius_km} km; "
            f"limited to {max_circles} (raise --max-areas or the corridor width to cover it fully)"
        )
        step = len(centres) / max_circles
        centres = [centres[int(i * step)] for i in range(max_circles)]

    locations: list[Location] = []
    seen: set[int] = set()
    for point in centres:
        try:
            location = resolver.resolve_point(point)
        except HttpError as exc:
            warnings.append(f"location lookup failed near {point[0]:.3f},{point[1]:.3f}: {exc}")
            continue
        if location is None:
            warnings.append(f"no Kleinanzeigen location found near {point[0]:.3f},{point[1]:.3f}")
            continue
        if location.id in seen:
            continue  # neighbouring samples land in the same town
        seen.add(location.id)
        locations.append(location)
    return locations, warnings


def search_route(
    client: HttpClient,
    resolver: LocationResolver,
    filters: SearchFilters,
    route: Route,
    corridor_km: float = 15.0,
    max_pages: int = 1,
    max_circles: int = 40,
    include_sponsored: bool = False,
    keep_unlocated: bool = False,
    score: bool = True,
    drive_time: bool = True,
    max_detour_min: float | None = None,
    table_url: str = OSRM_TABLE_URL,
    budget: int | None = None,
) -> SearchResult:
    """Mode B: everything within ``corridor_km`` of the driving route."""
    before = client.request_count
    # Never smaller than the corridor - otherwise ads at the edge of the
    # corridor are filtered for but never searched for.
    radius = radius_at_least(corridor_km)
    centres, warnings = plan_circles(resolver, route, radius, max_circles)
    if not centres:
        raise RuntimeError("could not map a single point of the route to a Kleinanzeigen location")

    areas = [AreaSearch(loc.label, filters.at_location(loc.id, radius)) for loc in centres]
    run_areas(client, areas, max_pages, RequestBudget(budget), include_sponsored, warnings)

    collected: dict[str, Listing] = {}
    for area in areas:
        for listing in area.listings.values():
            if listing.ad_id not in collected:
                listing.found_near = area.label
                collected[listing.ad_id] = listing

    coverage = [area.coverage for area in areas]
    pages = sum(area.pages for area in areas)
    listings = list(collected.values())
    unresolved = _annotate_coordinates(listings)
    if unresolved:
        warnings.append(f"{unresolved} ads had no usable postcode")

    kept: list[Listing] = []
    for listing in listings:
        if listing.point is None:
            if keep_unlocated:
                kept.append(listing)
            continue
        detour, along = geo.distance_to_route_km(listing.point, route.points)
        listing.detour_km = round(detour, 1)
        listing.along_route_km = round(along, 1)
        if detour <= corridor_km:
            kept.append(listing)

    if drive_time and kept:
        try:
            kept = annotate_drive_time(client, kept, route, table_url)
        except (HttpError, RuntimeError, ValueError) as exc:
            warnings.append(f"driving times unavailable ({exc}) - falling back to straight-line distance")
        else:
            if max_detour_min is not None:
                kept = [l for l in kept if l.detour_min is None or l.detour_min <= max_detour_min]

    kept.sort(key=lambda l: (l.along_route_km is None, l.along_route_km or 0))
    if score:
        deals.evaluate(kept)
    result = SearchResult(
        listings=kept,
        filters=filters,
        route=route,
        centres=centres,
        pages_fetched=pages,
        requests=client.request_count - before,
        warnings=warnings,
        corridor_km=corridor_km,
        coverage=coverage,
    )
    result.warnings.extend(result.coverage_warnings())
    return result


def annotate_drive_time(
    client: HttpClient,
    listings: list[Listing],
    route: Route,
    table_url: str = OSRM_TABLE_URL,
) -> list[Listing]:
    """Fill in ``detour_min``: the extra driving time for stopping at each ad.

    Ads are grouped by coordinate first - a postcode is the finest location the
    site exposes, so dozens of ads usually share a handful of points and the
    whole result set costs one or two matrix requests.
    """
    groups: dict[geo.Point, list[Listing]] = {}
    for listing in listings:
        if listing.point is not None:
            groups.setdefault(listing.point, []).append(listing)
    if not groups:
        return listings

    stops = list(groups)
    minutes = added_trip_minutes(client, route.points[0], route.points[-1], stops, table_url)
    for point, extra in zip(stops, minutes):
        for listing in groups[point]:
            listing.detour_min = extra
    return listings
