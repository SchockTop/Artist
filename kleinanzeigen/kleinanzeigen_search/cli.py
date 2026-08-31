"""Command line interface.

    python -m kleinanzeigen_search city  "E-Bike" --in Köln --radius 20 --max-price 800
    python -m kleinanzeigen_search route "E-Bike" --maps-url "https://maps.app.goo.gl/..." --corridor 15
    python -m kleinanzeigen_search where "Frankfurt"
    python -m kleinanzeigen_search categories --for "E-Bike"
"""
from __future__ import annotations

import argparse
import logging
import pathlib
import sys

from . import report
from .client import HttpClient, HttpError
from .filters import SearchFilters
from .locations import LocationResolver, normalise_radius
from .parser import parse_category_index, parse_suggested_categories
from .routes import OSRM_URL, build_route
from .search import search_city, search_route

DEFAULT_CACHE = pathlib.Path.home() / ".cache" / "kleinanzeigen_search"
CATEGORY_INDEX_URL = "https://www.kleinanzeigen.de/s-kategorien.html"


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--delay", type=float, default=2.0, help="seconds between requests (default: 2)")
    parser.add_argument("--no-cache", action="store_true", help="do not reuse cached pages")
    parser.add_argument("--cache-ttl", type=float, default=900.0, help="cache lifetime in seconds (default: 900)")
    parser.add_argument("-v", "--verbose", action="count", default=0)


def add_filter_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("query", nargs="?", help="what to look for, e.g. 'Bosch GSR 18V'")
    parser.add_argument("--category-id", type=int, help="restrict to a category (see the 'categories' command)")
    parser.add_argument("--min-price", type=int)
    parser.add_argument("--max-price", type=int)
    parser.add_argument("--seller", choices=["privat", "gewerblich"], help="private or commercial sellers only")
    parser.add_argument("--type", dest="ad_type", choices=["angebote", "gesuche"], default="angebote",
                        help="offers or wanted ads (default: angebote)")
    parser.add_argument("--sort", choices=["neueste", "preis", "entfernung"], default="neueste")
    parser.add_argument("--shipping", action="store_true", help="only ads that offer shipping")
    parser.add_argument("--filter", dest="extra", action="append", default=[], metavar="SEGMENT",
                        help="extra URL filter segment copied from the site, e.g. 'zustand:neu' (repeatable)")
    parser.add_argument("--pages", type=int, default=2,
                        help="result pages to fetch from every area up front (25 ads each)")
    parser.add_argument("--budget", type=int, metavar="N",
                        help="total result-page requests; anything left over is spent paging "
                             "deeper into the areas still hiding the most ads")
    parser.add_argument("--include-sponsored", action="store_true",
                        help="keep TOP ads (they ignore the location filter)")
    parser.add_argument("--no-score", action="store_true", help="skip deal scoring")
    parser.add_argument("--min-score", type=float, help="only show listings scoring at least this")
    parser.add_argument("--format", choices=["table", "details", "json", "csv", "html"], default="table")
    parser.add_argument("--limit", type=int, help="show at most this many listings")
    parser.add_argument("-o", "--output", type=pathlib.Path, help="write the report to a file")


def build_filters(args: argparse.Namespace) -> SearchFilters:
    filters = SearchFilters(
        query=args.query,
        category_id=args.category_id,
        min_price=args.min_price,
        max_price=args.max_price,
        seller=args.seller,
        ad_type=args.ad_type,
        sort=args.sort,
        shipping_only=args.shipping,
        extra=list(args.extra),
    )
    filters.validate()
    return filters


def make_client(args: argparse.Namespace) -> HttpClient:
    return HttpClient(
        delay=args.delay,
        cache_dir=None if args.no_cache else DEFAULT_CACHE,
        cache_ttl=args.cache_ttl,
    )


def emit(result, args: argparse.Namespace) -> None:
    if args.min_score is not None:
        result.listings = [l for l in result.listings if (l.deal_score or 0) >= args.min_score]
    text = report.render(result, args.format, args.limit)
    if args.output:
        args.output.write_text(text, encoding="utf-8")
        print(f"wrote {args.output}", file=sys.stderr)
    else:
        print(text)

    if args.format in ("table", "details"):
        summary = result.summary()
        truncated = summary.get("areas_truncated") or 0
        coverage = f" · {truncated} area(s) not fully seen" if truncated else " · full coverage"
        print(
            f"\n{summary['listings']} ads · median {summary['median_price'] or '-'} € "
            f"(p25 {summary['p25_price'] or '-'} / p75 {summary['p75_price'] or '-'}) · "
            f"{summary['search_areas']} search area(s) · {summary['pages_fetched']} pages · "
            f"{summary['requests']} requests{coverage}",
            file=sys.stderr,
        )
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)


# ------------------------------------------------------------------ commands
def cmd_city(args: argparse.Namespace) -> int:
    client = make_client(args)
    resolver = LocationResolver(client)
    filters = build_filters(args)

    location = None
    if args.location_id:
        from .locations import Location

        location = Location(args.location_id, f"location {args.location_id}")
    elif args.place:
        location = resolver.resolve(args.place)
        if location is None:
            print(f"could not find a location called {args.place!r} - try 'where {args.place}'", file=sys.stderr)
            return 2
        print(f"searching {location.label} within {normalise_radius(args.radius)} km", file=sys.stderr)

    if location:
        filters = filters.at_location(location.id, normalise_radius(args.radius))
    result = search_city(client, filters, location, args.pages, args.include_sponsored,
                         not args.no_score, budget=args.budget)
    emit(result, args)
    return 0


def cmd_route(args: argparse.Namespace) -> int:
    client = make_client(args)
    resolver = LocationResolver(client)
    filters = build_filters(args)

    waypoints = [w.strip() for w in (args.waypoints or "").split(";") if w.strip()]
    routes = build_route(
        client,
        google_maps_url=args.maps_url,
        waypoints=waypoints,
        polyline=args.polyline,
        gpx_path=args.gpx,
        osrm_url=args.osrm_url,
        alternatives=args.alternatives,
        want_all=True,
    )
    for index, one in enumerate(routes):
        print(f"route{'' if index == 0 else f' (alt {index})'}: {one.describe()}", file=sys.stderr)
    if args.alternatives and len(routes) == 1:
        print("note: the router offered no alternative road for this trip", file=sys.stderr)

    result = search_route(
        client,
        resolver,
        filters,
        routes,
        corridor_km=args.corridor,
        max_pages=args.pages,
        max_circles=args.max_areas,
        include_sponsored=args.include_sponsored,
        keep_unlocated=args.keep_unlocated,
        score=not args.no_score,
        drive_time=not args.no_drive_time,
        max_detour_min=args.max_detour_min,
        budget=args.budget,
    )
    emit(result, args)
    return 0


def cmd_where(args: argparse.Namespace) -> int:
    client = make_client(args)
    resolver = LocationResolver(client)
    suggestions = resolver.suggest(args.place)
    if not suggestions:
        print("no matches", file=sys.stderr)
        return 1
    for suggestion in suggestions:
        print(f"{suggestion.id:>8}  {suggestion.label}")
    return 0


def cmd_categories(args: argparse.Namespace) -> int:
    client = make_client(args)
    if args.for_query:
        filters = SearchFilters(query=args.for_query, ad_type=None)
        found = parse_suggested_categories(client.get(filters.url()))
        if not found:
            print("the site suggested no category for this term", file=sys.stderr)
            return 1
        print(f"categories Kleinanzeigen suggests for {args.for_query!r}:")
    else:
        found = parse_category_index(client.get(CATEGORY_INDEX_URL))
    for category_id, label in found:
        print(f"{category_id:>6}  {label}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kleinanzeigen_search",
        description="Search kleinanzeigen.de around a city or along a driving route, and rank the deals.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    city = subparsers.add_parser("city", help="search around one place (mode A)")
    add_filter_args(city)
    city.add_argument("--in", dest="place", help="city or postcode, e.g. 'Köln' or '50667'")
    city.add_argument("--location-id", type=int, help="Kleinanzeigen location id, skips the lookup")
    city.add_argument("--radius", type=float, default=20, help="search radius in km (default: 20)")
    add_common(city)
    city.set_defaults(func=cmd_city)

    route = subparsers.add_parser("route", help="search along a driving route (mode B)")
    add_filter_args(route)
    source = route.add_mutually_exclusive_group(required=True)
    source.add_argument("--maps-url", help="Google Maps directions link (short links work too)")
    source.add_argument("--waypoints", help="stops separated by ';', e.g. 'Köln; Kassel; Berlin'")
    source.add_argument("--polyline", help="encoded polyline from the Google Directions API or OSRM")
    source.add_argument("--gpx", help="GPX file with the track")
    route.add_argument("--corridor", type=float, default=15.0,
                       help="how far off the route you are willing to drive, in km (default: 15)")
    route.add_argument("--max-areas", type=int, default=40, help="cap on search areas along the route")
    route.add_argument("--alternatives", type=int, default=0, metavar="N",
                       help="also search along up to N alternative roads between the same two places")
    route.add_argument("--max-detour-min", type=float, metavar="MIN",
                       help="drop ads that add more than this many minutes to the drive")
    route.add_argument("--no-drive-time", action="store_true",
                       help="skip the driving-time lookup and rank by straight-line distance only")
    route.add_argument("--osrm-url", default=OSRM_URL,
                       help="routing service URL template; point it at your own OSRM instance for heavy use")
    route.add_argument("--keep-unlocated", action="store_true",
                       help="keep ads whose postcode could not be placed on the map")
    add_common(route)
    route.set_defaults(func=cmd_route)

    where = subparsers.add_parser("where", help="look up Kleinanzeigen location ids")
    where.add_argument("place")
    add_common(where)
    where.set_defaults(func=cmd_where)

    categories = subparsers.add_parser("categories", help="list category ids")
    categories.add_argument("--for", dest="for_query", help="show the categories the site suggests for a search term")
    add_common(categories)
    categories.set_defaults(func=cmd_categories)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=[logging.WARNING, logging.INFO, logging.DEBUG][min(args.verbose, 2)],
        format="%(levelname)s %(message)s",
    )
    try:
        return args.func(args)
    except (ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except HttpError as exc:
        print(f"request failed: {exc}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
