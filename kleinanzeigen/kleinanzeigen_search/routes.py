"""Getting a driving route into the searcher.

Supported inputs:

* a Google Maps directions link (long or ``maps.app.goo.gl`` short link)
* an encoded polyline (as returned by the Google Directions API or OSRM)
* a GPX track exported from any navigation app
* a plain list of waypoints ("Berlin; Leipzig; Nürnberg; München")

Waypoints are geocoded with OpenStreetMap Nominatim and connected with the
public OSRM routing service, so no API key is needed anywhere.
"""
from __future__ import annotations

import logging
import re
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from . import geo
from .client import HttpClient
from .locations import geocode

log = logging.getLogger(__name__)

OSRM_URL = "https://router.project-osrm.org/route/v1/driving/{coords}?overview=full&geometries=polyline"
OSRM_ALT_URL = OSRM_URL + "&alternatives={alternatives}"
OSRM_TABLE_URL = "https://router.project-osrm.org/table/v1/driving/{coords}?annotations=duration"
# The public OSRM server refuses matrices with more than 100 coordinates.
OSRM_TABLE_LIMIT = 100
COORD_RE = re.compile(r"^\s*(-?\d{1,2}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)\s*$")


@dataclass
class Route:
    points: list[geo.Point]
    waypoints: list[str] = field(default_factory=list)
    distance_km: float | None = None
    duration_min: float | None = None
    source: str = "unknown"

    @property
    def length_km(self) -> float:
        return self.distance_km if self.distance_km is not None else geo.route_length_km(self.points)

    def describe(self) -> str:
        label = " → ".join(self.waypoints) if self.waypoints else f"{len(self.points)} points"
        duration = f", ~{self.duration_min / 60:.1f} h" if self.duration_min else ""
        return f"{label} ({self.length_km:.0f} km{duration}, via {self.source})"


# ------------------------------------------------------------- Google Maps
def expand_short_link(client: HttpClient, url: str) -> str:
    if re.search(r"(maps\.app\.goo\.gl|goo\.gl/maps)", url):
        expanded = client.resolve_redirect(url)
        log.info("expanded short link to %s", expanded)
        return expanded
    return url


def parse_google_maps_url(url: str) -> list[str]:
    """Extract the waypoints of a Google Maps directions link, in order.

    Handles ``/maps/dir/A/B/C/@...`` paths as well as the
    ``?api=1&origin=...&destination=...&waypoints=A|B`` form.
    """
    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsed.query)

    if "origin" in params or "destination" in params:
        stops = list(params.get("origin", []))
        for raw in params.get("waypoints", []):
            stops.extend(part for part in raw.split("|") if part.strip())
        stops.extend(params.get("destination", []))
        return [s.strip() for s in stops if s.strip()]

    path = urllib.parse.unquote(parsed.path)
    if "/dir/" not in path:
        raise ValueError("not a Google Maps directions link (no '/dir/' and no origin/destination)")
    tail = path.split("/dir/", 1)[1]
    stops = []
    for segment in tail.split("/"):
        segment = segment.strip()
        # '@52.5,13.4,7z' is the map viewport and 'data=...' the encoded state;
        # neither is a stop on the trip.
        if not segment or segment.startswith("@") or segment.startswith("data=") or segment == "dir":
            continue
        stops.append(segment.replace("+", " "))
    if not stops:
        raise ValueError("no waypoints found in the Google Maps link")
    return stops


# --------------------------------------------------------------- resolving
def waypoint_to_point(client: HttpClient, waypoint: str) -> geo.Point | None:
    match = COORD_RE.match(waypoint)
    if match:
        return (float(match.group(1)), float(match.group(2)))
    return geocode(client, waypoint)


def route_via_osrm(
    client: HttpClient,
    points: list[geo.Point],
    osrm_url: str = OSRM_URL,
    alternatives: int = 0,
) -> list[tuple[list[geo.Point], float, float]]:
    """Driving route(s) through ``points``, fastest first.

    With ``alternatives`` the router is asked for that many other roads between
    the same endpoints - the "or you could go via ..." options a navigation app
    offers. It may return fewer, or none at all on a route with no sensible
    alternative.
    """
    coords = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in points)
    url = osrm_url.format(coords=coords)
    if alternatives:
        url += f"&alternatives={int(alternatives)}"
    payload = client.get_json(url, use_cache=True)
    if payload.get("code") != "Ok" or not payload.get("routes"):
        raise RuntimeError(f"routing failed: {payload.get('message') or payload.get('code')}")
    return [
        (geo.decode_polyline(r["geometry"]), r["distance"] / 1000.0, r["duration"] / 60.0)
        for r in payload["routes"]
    ]


def build_route(
    client: HttpClient,
    google_maps_url: str | None = None,
    waypoints: list[str] | None = None,
    polyline: str | None = None,
    gpx_path: str | None = None,
    osrm_url: str = OSRM_URL,
    simplify_km: float = 0.35,
    alternatives: int = 0,
    want_all: bool = False,
) -> "Route | list[Route]":
    """Turn whatever the user supplied into a polyline.

    Returns one Route, or - with ``want_all`` - every route the router offered,
    fastest first.
    """
    def one(route: Route) -> "Route | list[Route]":
        return [route] if want_all else route

    if polyline:
        points = geo.decode_polyline(polyline)
        if len(points) < 2:
            raise ValueError("polyline decoded to fewer than two points")
        return one(Route(geo.simplify(points, simplify_km), source="polyline"))

    if gpx_path:
        points = load_gpx(gpx_path)
        if len(points) < 2:
            raise ValueError(f"{gpx_path} contains fewer than two track points")
        return one(Route(geo.simplify(points, simplify_km), source="gpx"))

    stops = list(waypoints or [])
    if google_maps_url:
        stops = parse_google_maps_url(expand_short_link(client, google_maps_url)) + stops
    if len(stops) < 2:
        raise ValueError("a route needs at least two waypoints")

    resolved: list[geo.Point] = []
    labels: list[str] = []
    for stop in stops:
        point = waypoint_to_point(client, stop)
        if point is None:
            raise ValueError(f"could not locate waypoint {stop!r}")
        resolved.append(point)
        labels.append(stop)

    try:
        found = route_via_osrm(client, resolved, osrm_url, alternatives)
        routes = [
            Route(geo.simplify(points, simplify_km), labels, distance_km, duration_min, "osrm")
            for points, distance_km, duration_min in found
        ]
        return routes if want_all else routes[0]
    except Exception as exc:  # noqa: BLE001 - fall back to a usable approximation
        log.warning("routing service unavailable (%s) - falling back to straight lines", exc)
        points = []
        for a, b in zip(resolved, resolved[1:]):
            points.extend(geo.sample_route([a, b], max(5.0, geo.haversine_km(a, b) / 20))[:-1])
        points.append(resolved[-1])
        return one(Route(points, labels, source="straight-line (routing unavailable)"))


def load_gpx(path: str) -> list[geo.Point]:
    tree = ET.parse(path)
    points: list[geo.Point] = []
    for element in tree.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag in ("trkpt", "rtept", "wpt"):
            lat, lon = element.get("lat"), element.get("lon")
            if lat and lon:
                points.append((float(lat), float(lon)))
    return points


# --------------------------------------------------------- real driving detour
def duration_matrix(client: HttpClient, points: list[geo.Point], table_url: str = OSRM_TABLE_URL) -> list[list[float | None]]:
    """Pairwise driving durations in seconds for up to ``OSRM_TABLE_LIMIT`` points."""
    if len(points) > OSRM_TABLE_LIMIT:
        raise ValueError(f"at most {OSRM_TABLE_LIMIT} points per matrix request")
    coords = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in points)
    payload = client.get_json(table_url.format(coords=coords))
    if payload.get("code") != "Ok":
        raise RuntimeError(f"duration matrix failed: {payload.get('message') or payload.get('code')}")
    return payload["durations"]


def added_trip_minutes(
    client: HttpClient,
    origin: geo.Point,
    destination: geo.Point,
    stops: list[geo.Point],
    table_url: str = OSRM_TABLE_URL,
) -> list[float | None]:
    """Extra driving time for visiting each stop on the way from A to B.

    This is the number a navigation app shows next to a suggested stop:
    ``drive(A→stop) + drive(stop→B) − drive(A→B)``, in minutes.  One request
    covers up to 98 stops, so a whole result set usually costs one or two.
    """
    if not stops:
        return []
    out: list[float | None] = []
    batch_size = OSRM_TABLE_LIMIT - 2
    for start in range(0, len(stops), batch_size):
        batch = stops[start : start + batch_size]
        matrix = duration_matrix(client, [origin, destination] + batch, table_url)
        baseline = matrix[0][1]
        for index in range(len(batch)):
            there, back = matrix[0][2 + index], matrix[2 + index][1]
            if there is None or back is None or baseline is None:
                out.append(None)
            else:
                out.append(round((there + back - baseline) / 60.0, 1))
    return out
