"""Search filters and Kleinanzeigen URL construction.

The site encodes every filter as a path segment, e.g.::

    https://www.kleinanzeigen.de/s-anbieter:privat/preis:100:500/seite:2/e-bike/k0c217l3331r20
                                  \\_ filter segments ______________/ \\_query_/ \\_ k0 c<cat> l<loc> r<radius>

Every segment below was verified against the live site.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

BASE_URL = "https://www.kleinanzeigen.de"

UMLAUTS = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "ae", "Ö": "oe", "Ü": "ue"}

SORT_CHOICES = {
    "neueste": None,          # site default, no segment needed
    "preis": "sortierung:preis",
    "entfernung": "sortierung:entfernung",
}
SELLER_CHOICES = {"privat": "anbieter:privat", "gewerblich": "anbieter:gewerblich"}
AD_TYPE_CHOICES = {"angebote": "anzeige:angebote", "gesuche": "anzeige:gesuche"}


def slugify(text: str) -> str:
    """Query text -> URL slug, the way the site's own search box does it."""
    text = "".join(UMLAUTS.get(ch, ch) for ch in text)
    text = re.sub(r"[^A-Za-z0-9]+", "-", text.lower())
    return text.strip("-")


@dataclass
class SearchFilters:
    """Everything the standard Kleinanzeigen search form can express."""

    query: str | None = None
    location_id: int | None = None
    radius_km: int = 0
    category_id: int | None = None
    min_price: int | None = None
    max_price: int | None = None
    seller: str | None = None            # privat | gewerblich
    ad_type: str | None = "angebote"     # angebote | gesuche
    sort: str = "neueste"                # neueste | preis | entfernung
    shipping_only: bool = False
    page: int = 1
    # Escape hatch for category specific filters such as "zustand:neu" or
    # "autos.ez_i:2015,2020" - copy them straight out of a browser URL.
    extra: list[str] = field(default_factory=list)

    def validate(self) -> None:
        if self.sort not in SORT_CHOICES:
            raise ValueError(f"unknown sort {self.sort!r}, choose from {sorted(SORT_CHOICES)}")
        if self.seller is not None and self.seller not in SELLER_CHOICES:
            raise ValueError(f"unknown seller {self.seller!r}, choose from {sorted(SELLER_CHOICES)}")
        if self.ad_type is not None and self.ad_type not in AD_TYPE_CHOICES:
            raise ValueError(f"unknown ad type {self.ad_type!r}, choose from {sorted(AD_TYPE_CHOICES)}")
        if self.min_price is not None and self.max_price is not None and self.min_price > self.max_price:
            raise ValueError("min_price must not exceed max_price")
        if self.page < 1:
            raise ValueError("page starts at 1")
        if not self.query and not self.category_id:
            raise ValueError("give a search term, a category, or both")

    def segments(self) -> list[str]:
        """Filter path segments in the order the site itself emits them."""
        out: list[str] = []
        if self.seller:
            out.append(SELLER_CHOICES[self.seller])
        if self.ad_type:
            out.append(AD_TYPE_CHOICES[self.ad_type])
        if self.shipping_only:
            out.append("versand:ja")
        if self.min_price is not None or self.max_price is not None:
            lo = "" if self.min_price is None else str(int(self.min_price))
            hi = "" if self.max_price is None else str(int(self.max_price))
            out.append(f"preis:{lo}:{hi}")
        sort_segment = SORT_CHOICES[self.sort]
        if sort_segment:
            out.append(sort_segment)
        out.extend(self.extra)
        if self.page > 1:
            out.append(f"seite:{self.page}")
        return out

    def url(self) -> str:
        self.validate()
        parts = self.segments()
        query_slug = slugify(self.query) if self.query else ""
        if query_slug:
            parts.append(query_slug)
        suffix = "k0"
        if self.category_id:
            suffix += f"c{int(self.category_id)}"
        if self.location_id:
            suffix += f"l{int(self.location_id)}"
            if self.radius_km:
                suffix += f"r{int(self.radius_km)}"
        parts.append(suffix)
        return f"{BASE_URL}/s-" + "/".join(parts)

    def for_page(self, page: int) -> "SearchFilters":
        return replace(self, page=page)

    def at_location(self, location_id: int, radius_km: int) -> "SearchFilters":
        return replace(self, location_id=location_id, radius_km=radius_km)

    def describe(self) -> str:
        bits = [f"'{self.query}'" if self.query else "all ads"]
        if self.category_id:
            bits.append(f"category c{self.category_id}")
        if self.min_price is not None or self.max_price is not None:
            bits.append(f"{self.min_price or 0}-{self.max_price if self.max_price is not None else '∞'} EUR")
        if self.seller:
            bits.append(self.seller)
        if self.ad_type:
            bits.append(self.ad_type)
        if self.shipping_only:
            bits.append("shipping")
        return ", ".join(bits)
