"""Data model for a parsed Kleinanzeigen ad."""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field

from . import geo


@dataclass
class Listing:
    ad_id: str
    title: str
    url: str
    price_eur: int | None = None
    price_type: str = "unknown"      # fixed | vb (negotiable) | giveaway | none | unknown
    old_price_eur: int | None = None
    description: str = ""
    plz: str | None = None
    ort: str | None = None
    posted_raw: str | None = None
    posted_at: dt.datetime | None = None
    tags: list[str] = field(default_factory=list)
    image_url: str | None = None
    sponsored: bool = False          # "TOP" ad - shown regardless of the location filter
    # Filled in later by the search layer
    lat: float | None = None
    lon: float | None = None
    distance_km: float | None = None       # to the search centre (city mode)
    detour_km: float | None = None         # straight line to the route (route mode)
    detour_min: float | None = None        # extra driving time for stopping here
    along_route_km: float | None = None    # how far into the trip it sits
    found_near: str | None = None          # label of the search circle that found it
    deal_score: float | None = None
    deal_reasons: list[str] = field(default_factory=list)
    reference_price: int | None = None

    @property
    def point(self) -> geo.Point | None:
        if self.lat is None or self.lon is None:
            return None
        return (self.lat, self.lon)

    @property
    def location_label(self) -> str:
        return " ".join(part for part in (self.plz, self.ort) if part) or "?"

    @property
    def price_label(self) -> str:
        if self.price_type == "giveaway":
            return "zu verschenken"
        if self.price_eur is None:
            return "VB" if self.price_type == "vb" else "-"
        suffix = " VB" if self.price_type == "vb" else ""
        return f"{self.price_eur:,} €".replace(",", ".") + suffix

    @property
    def age_days(self) -> float | None:
        if self.posted_at is None:
            return None
        return (dt.datetime.now() - self.posted_at).total_seconds() / 86400

    def to_dict(self) -> dict:
        data = asdict(self)
        data["posted_at"] = self.posted_at.isoformat() if self.posted_at else None
        data["price_label"] = self.price_label
        return data
