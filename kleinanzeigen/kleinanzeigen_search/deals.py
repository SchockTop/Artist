"""Deal evaluation: what is a listing worth compared to its peers?

There is no price database behind this - the reference price comes from the
other ads in the same result set, which is exactly what you would do by hand:
look at what comparable items are asking, then judge the outlier.
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass

from .models import Listing

STOPWORDS = {
    "der", "die", "das", "und", "oder", "mit", "ohne", "für", "fur", "von", "vom", "zu", "zum", "zur",
    "ein", "eine", "einen", "einem", "eines", "im", "in", "am", "an", "auf", "aus", "neu", "neuwertig",
    "sehr", "gut", "guter", "gute", "top", "wie", "inkl", "inklusive", "gebraucht", "verkaufe", "biete",
    "original", "stück", "stueck", "cm", "mm", "kg", "the", "and", "for", "with", "new", "used",
}

# Wording that says "this is not the thing you are looking for".
NEGATIVE_PATTERNS = [
    (re.compile(r"\b(defekt|kaputt|bastler|bastel|ersatzteil|ersatzteile|teilespender|schrott)\b", re.I),
     -22, "defective / for parts"),
    (re.compile(r"\b(nur\s+teile|zum\s+ausschlachten|funktioniert\s+nicht)\b", re.I), -22, "not working"),
    (re.compile(r"\b(suche|gesucht|ankauf|kaufe)\b", re.I), -30, "wanted ad, not an offer"),
    (re.compile(r"\b(reserviert|verkauft)\b", re.I), -25, "already reserved/sold"),
    (re.compile(r"\b(nachbau|replika|fake|nachbildung)\b", re.I), -12, "replica"),
    (re.compile(r"\b(miete|mieten|leihen|verleih)\b", re.I), -20, "rental, not a sale"),
]
POSITIVE_PATTERNS = [
    (re.compile(r"\b(ovp|originalverpackt|versiegelt|ungeöffnet|ungeoeffnet)\b", re.I), 5, "sealed / boxed"),
    (re.compile(r"\b(rechnung|garantie|gewährleistung|gewaehrleistung)\b", re.I), 4, "receipt / warranty"),
]


@dataclass
class PriceStats:
    count: int
    median: float
    p25: float
    p75: float

    @property
    def spread(self) -> float:
        return self.p75 - self.p25


def normalise(text: str) -> str:
    text = text.lower()
    for src, dst in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(src, dst)
    return text


def tokenize(text: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9]+", normalise(text))
    return {t for t in tokens if len(t) >= 3 and t not in STOPWORDS}


def similarity(a: set[str], b: set[str]) -> float:
    """Overlap of the smaller token set - robust against very long titles."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def priced(listings: list[Listing]) -> list[Listing]:
    return [l for l in listings if l.price_eur is not None and l.price_eur > 0]


def price_stats(listings: list[Listing]) -> PriceStats | None:
    prices = sorted(l.price_eur for l in priced(listings))
    if not prices:
        return None
    if len(prices) >= 8:
        # Trim the extremes: single mispriced ads ("1 €", "999999 €") otherwise
        # drag the reference price around.
        cut = max(1, len(prices) // 20)
        prices = prices[cut:-cut] or prices
    quantiles = statistics.quantiles(prices, n=4) if len(prices) >= 4 else [prices[0], statistics.median(prices), prices[-1]]
    return PriceStats(len(prices), statistics.median(prices), quantiles[0], quantiles[2])


def comparables(
    target: Listing, pool: list[Listing], min_similarity: float = 0.4, min_count: int = 5
) -> tuple[list[Listing], bool]:
    """Listings similar enough to the target to serve as a price reference.

    Returns the peers and whether they are real title matches; when they are
    not, the caller is comparing an item against the whole result set, which
    deserves far less confidence (a saddle among road bikes, say).
    """
    target_tokens = tokenize(target.title)
    scored = []
    for other in priced(pool):
        if other.ad_id == target.ad_id:
            continue
        score = similarity(target_tokens, tokenize(other.title))
        if score >= min_similarity:
            scored.append((score, other))
    if len(scored) < min_count:
        # Not enough close matches: fall back to the whole result set, which is
        # already narrowed by the user's search term.
        return [l for l in priced(pool) if l.ad_id != target.ad_id], False
    scored.sort(key=lambda t: -t[0])
    return [listing for _, listing in scored], True


def evaluate(listings: list[Listing], min_similarity: float = 0.4, min_comparables: int = 5) -> list[Listing]:
    """Annotate every listing with a 0-100 deal score and human readable reasons.

    50 means "asking what everything else asks".  Above 65 is worth a look,
    above 80 is a genuine outlier - and outliers deserve suspicion, which the
    reasons spell out.
    """
    pool = priced(listings)
    for listing in listings:
        reasons: list[str] = []
        if listing.price_type == "giveaway":
            listing.deal_score = 95.0
            listing.deal_reasons = ["free to collect"]
            continue
        if listing.price_eur is None or listing.price_eur <= 0:
            listing.deal_score = None
            listing.deal_reasons = ["no price stated - ask the seller"]
            continue

        peers, matched = comparables(listing, pool, min_similarity, min_comparables)
        stats = price_stats(peers)
        if stats is None or stats.median <= 0:
            listing.deal_score = None
            listing.deal_reasons = ["not enough comparable ads to judge the price"]
            continue

        listing.reference_price = int(round(stats.median))
        discount = 1.0 - (listing.price_eur / stats.median)
        # 50% below the going rate scores 100, the going rate scores 50.
        score = 50.0 + 100.0 * max(-1.0, min(1.0, discount / 0.5)) / 2
        confidence = min(1.0, stats.count / 8.0)
        if not matched:
            # Nothing with a comparable title: the "reference" is really just
            # the whole result set, so keep the verdict close to neutral.
            confidence *= 0.55
        score = 50.0 + (score - 50.0) * confidence
        reasons.append(
            f"{abs(discount) * 100:.0f}% {'below' if discount >= 0 else 'above'} the median of "
            f"{stats.count} comparable ads ({stats.median:.0f} €)"
        )
        if not matched:
            reasons.append("no closely comparable ad - compared against the whole result set")
        if stats.count < min_comparables:
            reasons.append(f"thin reference: only {stats.count} comparable ads")

        if discount > 0.8:
            score -= 25
            reasons.append("far below everything else - check for scam, damage or missing parts")
        if listing.old_price_eur and listing.old_price_eur > listing.price_eur:
            score += 4
            reasons.append(f"seller already reduced from {listing.old_price_eur} €")
        if listing.age_days is not None and listing.age_days < 1:
            score += 4
            reasons.append("posted today")
        elif listing.age_days is not None and listing.age_days > 45:
            score -= 4
            reasons.append(f"online for {listing.age_days:.0f} days")
        if listing.price_type == "vb":
            reasons.append("price negotiable (VB)")

        haystack = f"{listing.title} {listing.description}"
        for pattern, delta, why in NEGATIVE_PATTERNS:
            if pattern.search(haystack):
                score += delta
                reasons.append(why)
        for pattern, delta, why in POSITIVE_PATTERNS:
            if pattern.search(haystack):
                score += delta
                reasons.append(why)

        listing.deal_score = max(0.0, min(100.0, round(score, 1)))
        listing.deal_reasons = reasons
    return listings


def summarise(listings: list[Listing]) -> dict:
    stats = price_stats(listings)
    scored = [l.deal_score for l in listings if l.deal_score is not None]
    return {
        "listings": len(listings),
        "with_price": len(priced(listings)),
        "median_price": round(stats.median) if stats else None,
        "p25_price": round(stats.p25) if stats else None,
        "p75_price": round(stats.p75) if stats else None,
        "best_score": round(max(scored), 1) if scored else None,
    }
