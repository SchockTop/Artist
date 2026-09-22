"""Geometry helpers: distances, polylines and route sampling.

All coordinates are ``(lat, lon)`` tuples in degrees.  Distances are in
kilometres.  Nothing in here touches the network.
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence

Point = tuple[float, float]

EARTH_RADIUS_KM = 6371.0088


def haversine_km(a: Point, b: Point) -> float:
    """Great-circle distance between two points."""
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(h)))


def _to_local_xy(p: Point, origin: Point) -> tuple[float, float]:
    """Equirectangular projection around ``origin`` (good enough over a few 100 km)."""
    lat0 = math.radians(origin[0])
    x = math.radians(p[1] - origin[1]) * math.cos(lat0) * EARTH_RADIUS_KM
    y = math.radians(p[0] - origin[0]) * EARTH_RADIUS_KM
    return x, y


def point_to_segment_km(p: Point, a: Point, b: Point) -> tuple[float, float]:
    """Distance from ``p`` to segment ``a``-``b``.

    Returns ``(distance_km, t)`` where ``t`` in [0, 1] is how far along the
    segment the closest point sits.
    """
    ax, ay = _to_local_xy(a, a)
    bx, by = _to_local_xy(b, a)
    px, py = _to_local_xy(p, a)
    dx, dy = bx - ax, by - ay
    seg_sq = dx * dx + dy * dy
    if seg_sq == 0.0:
        return haversine_km(p, a), 0.0
    t = ((px - ax) * dx + (py - ay) * dy) / seg_sq
    t = max(0.0, min(1.0, t))
    closest = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
    return haversine_km(p, closest), t


def cumulative_km(route: Sequence[Point]) -> list[float]:
    """Distance from the route start to each of its vertices."""
    out = [0.0]
    for a, b in zip(route, route[1:]):
        out.append(out[-1] + haversine_km(a, b))
    return out


def route_length_km(route: Sequence[Point]) -> float:
    return cumulative_km(route)[-1] if len(route) > 1 else 0.0


def distance_to_route_km(p: Point, route: Sequence[Point]) -> tuple[float, float]:
    """Shortest distance from ``p`` to a polyline.

    Returns ``(detour_km, along_km)`` - how far the point sits off the route and
    how many kilometres into the trip its closest point lies.  ``along_km``
    is what makes "first stop on the way" ordering possible.
    """
    if not route:
        raise ValueError("empty route")
    if len(route) == 1:
        return haversine_km(p, route[0]), 0.0

    cum = cumulative_km(route)
    best = (float("inf"), 0.0)
    for i, (a, b) in enumerate(zip(route, route[1:])):
        dist, t = point_to_segment_km(p, a, b)
        if dist < best[0]:
            best = (dist, cum[i] + (cum[i + 1] - cum[i]) * t)
    return best


def interpolate(a: Point, b: Point, t: float) -> Point:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def sample_route(route: Sequence[Point], step_km: float) -> list[Point]:
    """Points every ``step_km`` along the route (start and end always included)."""
    if step_km <= 0:
        raise ValueError("step_km must be positive")
    if len(route) < 2:
        return list(route)

    out = [route[0]]
    carry = 0.0
    for a, b in zip(route, route[1:]):
        seg = haversine_km(a, b)
        if seg == 0.0:
            continue
        pos = step_km - carry
        while pos <= seg:
            out.append(interpolate(a, b, pos / seg))
            pos += step_km
        carry = (carry + seg) % step_km
    if haversine_km(out[-1], route[-1]) > 1e-6:
        out.append(route[-1])
    return out


def simplify(route: Sequence[Point], tolerance_km: float = 0.5) -> list[Point]:
    """Ramer-Douglas-Peucker, to keep long OSRM geometries manageable."""
    if len(route) < 3:
        return list(route)
    first, last = route[0], route[-1]
    idx, worst = 0, 0.0
    for i in range(1, len(route) - 1):
        d, _ = point_to_segment_km(route[i], first, last)
        if d > worst:
            idx, worst = i, d
    if worst <= tolerance_km:
        return [first, last]
    left = simplify(route[: idx + 1], tolerance_km)
    right = simplify(route[idx:], tolerance_km)
    return left[:-1] + right


def decode_polyline(encoded: str, precision: int = 5) -> list[Point]:
    """Decode a Google/OSRM encoded polyline into ``(lat, lon)`` points."""
    coords: list[Point] = []
    index = lat = lon = 0
    factor = float(10 ** precision)
    while index < len(encoded):
        for axis in range(2):
            shift = result = 0
            while True:
                if index >= len(encoded):
                    return coords
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else (result >> 1)
            if axis == 0:
                lat += delta
            else:
                lon += delta
        coords.append((lat / factor, lon / factor))
    return coords


def bounding_box(points: Iterable[Point]) -> tuple[float, float, float, float]:
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    if not lats:
        raise ValueError("no points")
    return min(lats), min(lons), max(lats), max(lons)


def cover_route(route: Sequence[Point], radius_km: float, overlap: float = 0.85) -> list[Point]:
    """Circle centres whose ``radius_km`` discs cover the whole route.

    Spacing is ``2 * radius * overlap``; with the default the discs overlap
    enough that the covered corridor never pinches below ~0.9 * radius, while
    keeping the number of searches (and therefore requests) low.
    """
    if radius_km <= 0:
        raise ValueError("radius_km must be positive")
    step = max(0.5, 2 * radius_km * overlap)
    centres = sample_route(route, step)
    # sample_route always appends the end point, which can sit right next to the
    # previous centre; drop it when it adds nothing.
    if len(centres) > 1 and haversine_km(centres[-1], centres[-2]) < step * 0.25:
        centres.pop()
    return centres
