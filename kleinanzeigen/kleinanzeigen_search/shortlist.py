"""Re-check a hand-kept shortlist of ads: still there, and at what price?

The watch answers "what changed in the whole area". This answers the other
half - "is the one I picked still available, and has the seller moved" - for
a list you curate by hand and keep between sessions.
"""
from __future__ import annotations

import dataclasses
import html
import json
import pathlib
import re

from .client import HttpClient, HttpError

# Only these two page-level phrases mean the ad is over.  Note what is *not*
# here: "reserviert". It shows up in ordinary prose - one seller reserves a
# gig bag for shipping - and matching it anywhere on the page reported a
# guitar as sold for a week while it sat there for sale.
DEAD_RE = re.compile(
    r"nicht mehr verf[üu]gbar|wurde gel[öo]scht|Anzeige nicht gefunden"
    r"|Diese Anzeige ist nicht mehr online",
    re.IGNORECASE,
)
# The reserved badge is an element of its own; prose never renders as >Reserviert<.
RESERVED_RE = re.compile(r">\s*Reserviert\s*<")
TITLE_RE = re.compile(r'<h1[^>]*id="viewad-title"[^>]*>')
PRICE_RE = re.compile(r'id="viewad-price"[^>]*>(.*?)</h2>', re.S)


@dataclasses.dataclass
class Candidate:
    label: str
    url: str
    asking_eur: int | None = None
    verdict: str = ""
    first_seen: str = ""
    note: str = ""
    # The price seen on the last run. asking_eur stays the first-sighting
    # anchor, so a cut made a week ago does not keep reappearing as news.
    last_price_eur: int | None = None
    # What this model costs new, checked against a retailer - never guessed.
    # A range or a foreign currency belongs in new_price_note instead of
    # here; new_price_eur only holds a figure fit to divide by.
    new_price_eur: int | None = None
    new_price_note: str = ""

    @property
    def current_eur(self) -> int | None:
        """The most recently observed price, falling back to first-seen."""
        return self.last_price_eur if self.last_price_eur is not None else self.asking_eur

    @property
    def percent_of_new(self) -> int | None:
        """Current price as a percentage of new_price_eur, for ranking finds."""
        if not self.current_eur or not self.new_price_eur:
            return None
        return round(100 * self.current_eur / self.new_price_eur)


@dataclasses.dataclass
class Check:
    candidate: Candidate
    state: str          # live | RESERVED | GONE | UNKNOWN(<status>)
    price_label: str = ""
    price_eur: int | None = None

    @property
    def before(self) -> int | None:
        """The price this ad had at the previous check."""
        candidate = self.candidate
        return candidate.last_price_eur if candidate.last_price_eur is not None else candidate.asking_eur

    @property
    def moved(self) -> int | None:
        """Change since the previous check, negative for a cut."""
        if self.price_eur is None or self.before is None:
            return None
        return (self.price_eur - self.before) or None

    @property
    def total_moved(self) -> int | None:
        """Change since the ad was first written down."""
        if self.price_eur is None or self.candidate.asking_eur is None:
            return None
        return (self.price_eur - self.candidate.asking_eur) or None


def load(path: str | pathlib.Path) -> list[Candidate]:
    raw = json.loads(pathlib.Path(path).expanduser().read_text(encoding="utf-8"))
    rows = raw["candidates"] if isinstance(raw, dict) else raw
    return [Candidate(**row) for row in rows]


def _price(markup: str) -> str:
    match = PRICE_RE.search(markup)
    if not match:
        return ""
    text = re.sub(r"<[^>]+>", "", match.group(1))
    return html.unescape(re.sub(r"\s+", " ", text)).strip()


def check(client: HttpClient, candidate: Candidate) -> Check:
    try:
        page = client.get(candidate.url, use_cache=False)
    except HttpError as exc:
        # A 404/410 is a deletion.  A 403 is the site throttling us, and
        # reading that as a sale invents a disappearance that never happened.
        state = "GONE" if exc.status in (404, 410) else f"UNKNOWN({exc.status})"
        return Check(candidate, state)
    if DEAD_RE.search(page) or not TITLE_RE.search(page):
        return Check(candidate, "GONE")
    label = _price(page)
    digits = re.search(r"([\d.]+)\s*€", label)
    return Check(
        candidate,
        "RESERVED" if RESERVED_RE.search(page) else "live",
        label,
        int(digits.group(1).replace(".", "")) if digits else None,
    )


def save(path: str | pathlib.Path, checks: list[Check]) -> None:
    """Write the observed prices back so the next run reports the new delta."""
    file = pathlib.Path(path).expanduser()
    raw = json.loads(file.read_text(encoding="utf-8"))
    rows = raw["candidates"] if isinstance(raw, dict) else raw
    by_url = {row.candidate.url: row for row in checks}
    for row in rows:
        seen = by_url.get(row["url"])
        if seen is not None and seen.price_eur is not None:
            row["last_price_eur"] = seen.price_eur
    file.write_text(json.dumps(raw, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def render(checks: list[Check]) -> str:
    lines = []
    for row in checks:
        moved = row.moved
        move = f"  ({row.before} → {row.price_eur} €)" if moved else ""
        pct = row.candidate.percent_of_new
        pct_label = f"  [{pct}% v. Neupreis {row.candidate.new_price_eur} €]" if pct is not None else ""
        lines.append(
            f"{row.candidate.verdict:<7} {row.candidate.label:<26}"
            f" {row.state:<11} {row.price_label:<12}{move}{pct_label}"
        )
    gone = [r for r in checks if r.state == "GONE"]
    unknown = [r for r in checks if r.state.startswith("UNKNOWN")]
    cuts = [r for r in checks if (r.moved or 0) < 0]
    lines.append("")
    lines.append(f"{len(checks)} tracked · {len(gone)} gone · {len(cuts)} price cut(s) since last check"
                 + (f" · {len(unknown)} unchecked" if unknown else ""))
    for row in gone:
        lines.append(f"   × {row.candidate.label} ({row.before} €)")
    for row in cuts:
        total = row.total_moved
        since = (f"  ({row.candidate.asking_eur} € when first seen)"
                 if total is not None and total != row.moved else "")
        lines.append(f"   ↓ {row.candidate.label}: {row.before} → {row.price_eur} €{since}")
    for row in unknown:
        lines.append(f"   ? {row.candidate.label} - {row.state}, not checked")
    return "\n".join(lines)
