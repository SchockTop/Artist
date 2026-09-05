"""Recurring watches: run the same searches on a schedule and report what changed.

The interesting signal on this market is not what appears - it is what moves.
Ads sit for months (median about a month, plenty over a year), so a listing
vanishing means little, while a price cut means the seller is ready to deal.
A watch therefore reports three things per run: ads it has never seen, ads
whose price fell, and ads that disappeared from a fully covered area.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
from dataclasses import asdict, dataclass, field

from .deals import similarity, tokenize
from .models import Listing


@dataclass
class Seen:
    """What a watch remembers about one ad between runs."""

    ad_id: str
    title: str
    url: str
    price_eur: int | None
    first_seen: str
    last_seen: str
    lowest_price: int | None = None
    plz: str | None = None
    ort: str | None = None

    @classmethod
    def from_listing(cls, listing: Listing, now: str) -> "Seen":
        return cls(listing.ad_id, listing.title, listing.url, listing.price_eur,
                   now, now, listing.price_eur, listing.plz, listing.ort)


@dataclass
class PriceDrop:
    listing: Listing
    was: int
    now: int
    first_seen: str

    @property
    def percent(self) -> float:
        return 100.0 * (self.was - self.now) / self.was if self.was else 0.0


@dataclass
class Repost:
    """The same guitar, deleted and listed again under a fresh ad id.

    Sellers do this to jump back to the top of the newest-first list, usually
    while trimming the price. Counting it as a sale plus a new arrival would
    be wrong twice over, and would hide the fact that the thing has actually
    been sitting unsold since ``first_seen``.
    """

    listing: Listing
    previous: Seen

    @property
    def was(self) -> int | None:
        return self.previous.price_eur

    @property
    def first_seen(self) -> str:
        return self.previous.first_seen


@dataclass
class Changes:
    key: str
    new: list[Listing] = field(default_factory=list)
    drops: list[PriceDrop] = field(default_factory=list)
    gone: list[Seen] = field(default_factory=list)
    reposts: list[Repost] = field(default_factory=list)
    unchanged: int = 0
    coverage_complete: bool = True

    @property
    def quiet(self) -> bool:
        return not (self.new or self.drops or self.gone or self.reposts)


class WatchStore:
    """A JSON file remembering every ad a watch has ever seen."""

    def __init__(self, path: str | pathlib.Path):
        self.path = pathlib.Path(path).expanduser()
        self.data: dict[str, dict[str, dict]] = {}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.data = {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")

    def known(self, key: str) -> dict[str, Seen]:
        return {ad_id: Seen(**row) for ad_id, row in self.data.get(key, {}).items()}

    def diff(
        self,
        key: str,
        listings: list[Listing],
        coverage_complete: bool = True,
        now: str | None = None,
    ) -> Changes:
        """Compare this run against what the watch already knows.

        ``coverage_complete`` guards the "gone" list: if the run only paged
        part of an area, an ad missing from the results was probably just not
        reached, and calling it sold would be a lie.
        """
        now = now or dt.date.today().isoformat()
        known = self.known(key)
        changes = Changes(key=key, coverage_complete=coverage_complete)
        seen_now = set()

        for listing in listings:
            seen_now.add(listing.ad_id)
            before = known.get(listing.ad_id)
            if before is None:
                changes.new.append(listing)
                known[listing.ad_id] = Seen.from_listing(listing, now)
                continue
            if (listing.price_eur is not None and before.price_eur is not None
                    and listing.price_eur < before.price_eur):
                changes.drops.append(PriceDrop(listing, before.price_eur, listing.price_eur, before.first_seen))
            else:
                changes.unchanged += 1
            before.price_eur = listing.price_eur
            before.last_seen = now
            before.title = listing.title
            if listing.price_eur is not None:
                before.lowest_price = min(before.lowest_price or listing.price_eur, listing.price_eur)

        if coverage_complete:
            for ad_id, before in known.items():
                if ad_id not in seen_now and before.last_seen != now:
                    changes.gone.append(before)
            _match_reposts(changes, known, now)

        self.data[key] = {ad_id: asdict(row) for ad_id, row in known.items()
                          if ad_id in seen_now or row.last_seen == now
                          or ad_id not in {g.ad_id for g in changes.gone}}
        return changes


def render_digest(all_changes: list[Changes], limit: int = 12) -> str:
    """A short, skimmable digest - the thing you actually read twice a day."""
    lines: list[str] = []
    total_new = sum(len(c.new) for c in all_changes)
    total_drops = sum(len(c.drops) for c in all_changes)
    total_gone = sum(len(c.gone) for c in all_changes)
    total_reposts = sum(len(c.reposts) for c in all_changes)
    headline = f"{total_new} new · {total_drops} price drop(s) · {total_gone} vanished"
    if total_reposts:
        headline += f" · {total_reposts} relisted"
    lines.append(headline)

    for changes in all_changes:
        if changes.quiet:
            continue
        lines.append("")
        lines.append(f"── {changes.key}")
        for drop in sorted(changes.drops, key=lambda d: -d.percent)[:limit]:
            detour = f" · +{drop.listing.detour_min:.0f} min" if drop.listing.detour_min is not None else ""
            lines.append(f"   ↓ {drop.was} → {drop.now} € ({drop.percent:.0f}% off, first seen {drop.first_seen})"
                         f"{detour}  {drop.listing.title[:52]}")
            lines.append(f"     {drop.listing.url}")
        for listing in sorted(changes.new, key=lambda l: l.detour_min if l.detour_min is not None else 999)[:limit]:
            detour = f" · +{listing.detour_min:.0f} min" if listing.detour_min is not None else ""
            score = f" · score {listing.deal_score:.0f}" if listing.deal_score is not None else ""
            lines.append(f"   + {listing.price_label:>10}{detour}{score}  {listing.title[:52]}")
            lines.append(f"     {listing.url}")
        for repost in changes.reposts[:limit]:
            price = ""
            if repost.was is not None and repost.listing.price_eur is not None:
                price = (f" {repost.was} → {repost.listing.price_eur} €"
                         if repost.was != repost.listing.price_eur else f" still {repost.was} €")
            lines.append(f"   ↻ relisted{price} · unsold since {repost.first_seen}"
                         f"  {repost.listing.title[:52]}")
            lines.append(f"     {repost.listing.url}")
        for row in changes.gone[:limit]:
            lines.append(f"   × gone: {row.title[:52]} (last {row.price_eur} €, first seen {row.first_seen})")
        if not changes.coverage_complete:
            lines.append("   (area not fully covered this run - 'gone' not checked)")
    return "\n".join(lines)


def _match_reposts(changes: Changes, known: dict[str, Seen], now: str, min_similarity: float = 0.8) -> None:
    """Pair vanished ads with new ones that are plainly the same guitar.

    Matched on the same postcode and a near-identical title. The new entry
    inherits the original ``first_seen``, so a relisted ad cannot disguise how
    long it has really been on the market.
    """
    if not (changes.gone and changes.new):
        return
    still_gone, still_new, reposts = [], list(changes.new), []

    for before in changes.gone:
        before_tokens = tokenize(before.title)
        match = None
        for listing in still_new:
            if before.plz and listing.plz and before.plz != listing.plz:
                continue
            if similarity(before_tokens, tokenize(listing.title)) >= min_similarity:
                match = listing
                break
        if match is None:
            still_gone.append(before)
            continue
        still_new.remove(match)
        reposts.append(Repost(match, before))
        if match.ad_id in known:                    # carry the true age across
            known[match.ad_id].first_seen = before.first_seen
        known.pop(before.ad_id, None)

    changes.gone, changes.new, changes.reposts = still_gone, still_new, reposts
