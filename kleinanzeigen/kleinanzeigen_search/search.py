"""Search orchestration: the plain city search and the along-the-route search."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from . import deals, geo, parser
from .client import HttpClient, HttpError
from .filters import SearchFilters
from .locations import Location, LocationResolver, plz_table, radius_at_least
from .models import Listing
from .routes import Route

log = logging.getLogger(__name__)

ADS_PER_PAGE = 25


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

    def summary(self) -> dict:
        data = deals.summarise(self.listings)
        data.update(
            pages_fetched=self.pages_fetched,
            requests=self.requests,
            total_reported=self.total_reported,
            search_areas=len(self.centres) or (1 if self.location else 0),
        )
        return data


def fetch_pages(
    client: HttpClient,
    filters: SearchFilters,
    max_pages: int = 1,
    include_sponsored: bool = False,
) -> tuple[list[Listing], int | None, int]:
    """Fetch up to ``max_pages`` result pages, stopping when the site runs dry."""
    collected: dict[str, Listing] = {}
    total: int | None = None
    pages = 0
    for page in range(1, max_pages + 1):
        url = filters.for_page(page).url()
        log.debug("GET %s", url)
        markup = client.get(url)
        pages += 1
        if total is None:
            total = parser.parse_result_total(markup)
        listings = parser.parse_listings(markup)
        organic = [l for l in listings if not l.sponsored]
        for listing in listings:
            # Sponsored "TOP" ads ignore the location filter entirely, so they
            # are dropped unless explicitly asked for.
            if listing.sponsored and not include_sponsored:
                continue
            collected.setdefault(listing.ad_id, listing)
        if len(organic) < ADS_PER_PAGE:
            break  # last page
        if total is not None and page * ADS_PER_PAGE >= total:
            break
    return list(collected.values()), total, pages


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
) -> SearchResult:
    """Mode A: one place, one radius, the standard filters."""
    before = client.request_count
    listings, total, pages = fetch_pages(client, filters, max_pages, include_sponsored)
    _annotate_coordinates(listings)

    centre = location.point if location else None
    if centre:
        for listing in listings:
            if listing.point:
                listing.distance_km = round(geo.haversine_km(centre, listing.point), 1)
        listings.sort(key=lambda l: (l.distance_km is None, l.distance_km or 0))

    if score:
        deals.evaluate(listings)
    return SearchResult(
        listings=listings,
        filters=filters,
        location=location,
        total_reported=total,
        pages_fetched=pages,
        requests=client.request_count - before,
    )


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
) -> SearchResult:
    """Mode B: everything within ``corridor_km`` of the driving route."""
    before = client.request_count
    # Never smaller than the corridor - otherwise ads at the edge of the
    # corridor are filtered for but never searched for.
    radius = radius_at_least(corridor_km)
    centres, warnings = plan_circles(resolver, route, radius, max_circles)
    if not centres:
        raise RuntimeError("could not map a single point of the route to a Kleinanzeigen location")

    collected: dict[str, Listing] = {}
    pages = 0
    for location in centres:
        area_filters = filters.at_location(location.id, radius)
        try:
            listings, _, fetched = fetch_pages(client, area_filters, max_pages, include_sponsored)
        except HttpError as exc:
            warnings.append(f"search near {location.label} failed: {exc}")
            continue
        pages += fetched
        for listing in listings:
            if listing.ad_id not in collected:
                listing.found_near = location.label
                collected[listing.ad_id] = listing

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

    kept.sort(key=lambda l: (l.along_route_km is None, l.along_route_km or 0))
    if score:
        deals.evaluate(kept)
    return SearchResult(
        listings=kept,
        filters=filters,
        route=route,
        centres=centres,
        pages_fetched=pages,
        requests=client.request_count - before,
        warnings=warnings,
        corridor_km=corridor_km,
    )
