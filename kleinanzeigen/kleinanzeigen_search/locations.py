"""Location handling: bundled postcode table, Kleinanzeigen location ids, geocoding."""
from __future__ import annotations

import csv
import functools
import gzip
import math
import logging
import pathlib
import re
import urllib.parse
from dataclasses import dataclass

from . import geo
from .client import HttpClient

log = logging.getLogger(__name__)

DATA_FILE = pathlib.Path(__file__).with_name("data") / "plz_de.csv.gz"
AUTOCOMPLETE_URL = "https://www.kleinanzeigen.de/s-ort-empfehlungen.json?query={query}"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search?{params}"

# Radii the site's own UI offers.  Arbitrary values work too, but sticking to
# these keeps the requests indistinguishable from normal browsing.
SUPPORTED_RADII = (0, 5, 10, 20, 30, 50, 100, 150, 200)


@dataclass(frozen=True)
class PlzEntry:
    plz: str
    lat: float
    lon: float
    ort: str
    # "g" marks a Deutsche Post large-customer postcode: it has no area on the
    # map and Kleinanzeigen does not know it, so it is a poor search anchor.
    typ: str = "p"

    @property
    def is_big_customer(self) -> bool:
        return self.typ == "g"

    @property
    def point(self) -> geo.Point:
        return (self.lat, self.lon)


@dataclass(frozen=True)
class Location:
    """A Kleinanzeigen search location."""

    id: int
    label: str
    plz: str | None = None
    point: geo.Point | None = None

    def __str__(self) -> str:
        return f"{self.label} (l{self.id})"


class PlzTable:
    """Offline German postcode -> coordinate lookup (GeoNames, CC BY 4.0)."""

    def __init__(self, path: pathlib.Path = DATA_FILE):
        self.entries: list[PlzEntry] = []
        self.by_plz: dict[str, PlzEntry] = {}
        self._grid: dict[tuple[int, int], list[PlzEntry]] = {}
        with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                entry = PlzEntry(
                    row["plz"], float(row["lat"]), float(row["lon"]), row["ort"], row.get("typ", "p")
                )
                self.entries.append(entry)
                self.by_plz[entry.plz] = entry
                self._grid.setdefault(self._cell(entry.lat, entry.lon), []).append(entry)

    @staticmethod
    def _cell(lat: float, lon: float) -> tuple[int, int]:
        return (int(lat * 2), int(lon * 2))  # ~55 x 35 km buckets

    def get(self, plz: str) -> PlzEntry | None:
        return self.by_plz.get(plz.strip())

    def nearest_k(self, point: geo.Point, k: int = 1, skip_big_customer: bool = True) -> list[PlzEntry]:
        """The ``k`` closest postcode centroids, nearest first."""
        lat, lon = point
        base = self._cell(lat, lon)
        # Narrowest cell dimension in km, so we know when a ring is exhaustive.
        cell_km = min(0.5 * 111.32 * math.cos(math.radians(lat)), 0.5 * 111.32)
        seen: list[tuple[float, PlzEntry]] = []
        for ring in range(0, 20):
            for dx in range(-ring, ring + 1):
                for dy in range(-ring, ring + 1):
                    if ring and max(abs(dx), abs(dy)) < ring:
                        continue  # already scanned by a smaller ring
                    for entry in self._grid.get((base[0] + dx, base[1] + dy), ()):
                        if skip_big_customer and entry.is_big_customer:
                            continue
                        seen.append((geo.haversine_km(point, entry.point), entry))
            seen.sort(key=lambda t: t[0])
            if len(seen) >= k and seen[k - 1][0] <= ring * cell_km:
                break
        if not seen and skip_big_customer:
            return self.nearest_k(point, k, skip_big_customer=False)
        return [entry for _, entry in seen[:k]]

    def nearest(self, point: geo.Point, skip_big_customer: bool = True) -> PlzEntry:
        found = self.nearest_k(point, 1, skip_big_customer)
        if not found:  # pragma: no cover - only for points far outside Germany
            return min(self.entries, key=lambda e: geo.haversine_km(point, e.point))
        return found[0]

    def locate(self, plz: str | None, ort: str | None = None) -> PlzEntry | None:
        """Resolve a listing's location string to coordinates."""
        if plz:
            entry = self.get(plz)
            if entry:
                return entry
        if ort:
            needle = ort.strip().lower()
            for entry in self.entries:
                if entry.ort.lower() == needle:
                    return entry
        return None


@functools.lru_cache(maxsize=1)
def plz_table() -> PlzTable:
    return PlzTable()


def normalise_radius(radius_km: float) -> int:
    """Snap to the nearest radius the site's UI uses (never downgrading to 0)."""
    candidates = [r for r in SUPPORTED_RADII if r > 0]
    return min(candidates, key=lambda r: (abs(r - radius_km), r))


def radius_at_least(radius_km: float) -> int:
    """Smallest supported radius that still covers ``radius_km``.

    Route searches must never round the radius down: the corridor filter would
    then promise a width the searches never looked at.
    """
    candidates = [r for r in SUPPORTED_RADII if r > 0]
    covering = [r for r in candidates if r >= radius_km]
    return min(covering) if covering else max(candidates)


class LocationResolver:
    """Turns human input ("Köln", "50667", coordinates) into Kleinanzeigen ids."""

    def __init__(self, client: HttpClient):
        self.client = client
        self._cache: dict[str, Location | None] = {}
        self.table = plz_table()

    def suggest(self, query: str) -> list[Location]:
        url = AUTOCOMPLETE_URL.format(query=urllib.parse.quote(query.strip()))
        # Deliberately not swallowing errors here: a failed lookup that silently
        # returns "no suggestions" would make the caller search the wrong place.
        payload = self.client.get_json(url)
        out = []
        for key, label in payload.items():
            try:
                loc_id = int(key.lstrip("_"))
            except ValueError:
                continue
            if loc_id == 0:  # "Deutschland" - the whole country, never what we want
                continue
            plz_match = re.match(r"^(\d{5})\b", label)
            out.append(Location(loc_id, label, plz_match.group(1) if plz_match else None))
        return out

    def resolve(self, query: str) -> Location | None:
        """Best matching location for a city name or postcode."""
        key = query.strip().lower()
        if key in self._cache:
            return self._cache[key]

        suggestions = self.suggest(query)
        result = self._pick(key, suggestions)

        if result is not None and result.point is None:
            entry = self.table.get(result.plz) if result.plz else None
            if entry is None:
                entry = self.table.locate(None, re.sub(r"\s*-\s*.*$", "", result.label))
            if entry is not None:
                result = Location(result.id, result.label, entry.plz, entry.point)

        self._cache[key] = result
        return result

    @staticmethod
    def _pick(key: str, suggestions: list[Location]) -> Location | None:
        """Choose the suggestion that best matches what the user typed.

        Labels come in two shapes: ``"Köln - Nordrhein-Westfalen"`` (a place)
        and ``"50667 Köln Altstadt"`` (a postcode).  A postcode query is only
        accepted when the label really starts with it - Kleinanzeigen answers
        unknown postcodes with fuzzy neighbours, which would silently search
        the wrong region.
        """
        if not suggestions:
            return None
        if re.fullmatch(r"\d{5}", key):
            hits = [s for s in suggestions if s.plz == key]
            return hits[0] if hits else None

        def head(label: str) -> str:
            return label.split(" - ", 1)[0].strip().lower()

        for test in (
            lambda s: head(s.label) == key,
            lambda s: s.label.strip().lower() == key,
            lambda s: head(s.label).startswith(key),
        ):
            hits = [s for s in suggestions if test(s)]
            if hits:
                return hits[0]
        return suggestions[0]

    def resolve_point(self, point: geo.Point, attempts: int = 5) -> Location | None:
        """Location id for the postcode area a coordinate falls into.

        Walks outwards through the nearest postcodes until Kleinanzeigen
        confirms one, so gaps in the postcode table never abort a route.
        """
        for entry in self.table.nearest_k(point, attempts):
            location = self.resolve(entry.plz)
            # Only an exact postcode match is trusted - matching by town name
            # picks up same-named villages hundreds of kilometres away.
            if location is None or location.plz != entry.plz:
                continue
            return Location(location.id, location.label, entry.plz, entry.point)
        return None


def geocode(client: HttpClient, query: str, country: str | None = "de") -> geo.Point | None:
    """Free-form place -> coordinates via OpenStreetMap Nominatim.

    Biased to ``country`` first so that bare town names resolve to the German
    one, then retried worldwide. A trip can perfectly well start in Strasbourg
    or Salzburg even though the ads themselves are only ever German.
    """
    attempts: list[dict] = []
    if country:
        attempts.append({"countrycodes": country})
    attempts.append({})

    for extra in attempts:
        params = urllib.parse.urlencode(
            {"q": query, "format": "jsonv2", "limit": 1, "addressdetails": 0, **extra}
        )
        try:
            payload = client.get_json(NOMINATIM_URL.format(params=params))
        except Exception as exc:  # noqa: BLE001
            log.warning("geocoding failed for %r: %s", query, exc)
            return None
        if payload:
            if not extra:
                log.info("geocoded %r outside %s", query, country)
            return (float(payload[0]["lat"]), float(payload[0]["lon"]))
    return None
