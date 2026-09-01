"""HTML parsing for Kleinanzeigen result pages (standard library only)."""
from __future__ import annotations

import datetime as dt
import html
import re
from html.parser import HTMLParser

from .models import Listing

BASE_URL = "https://www.kleinanzeigen.de"
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


class Node:
    __slots__ = ("tag", "attrs", "classes", "children", "parent", "_text")

    def __init__(self, tag: str, attrs: dict[str, str], parent: "Node | None" = None):
        self.tag = tag
        self.attrs = attrs
        self.classes = set(attrs.get("class", "").split())
        self.children: list["Node | str"] = []
        self.parent = parent

    def walk(self):
        for child in self.children:
            if isinstance(child, Node):
                yield child
                yield from child.walk()

    def find_all(self, tag: str | None = None, cls: str | None = None, limit: int | None = None) -> list["Node"]:
        out = []
        for node in self.walk():
            if tag and node.tag != tag:
                continue
            if cls and cls not in node.classes:
                continue
            out.append(node)
            if limit and len(out) >= limit:
                break
        return out

    def find(self, tag: str | None = None, cls: str | None = None) -> "Node | None":
        found = self.find_all(tag, cls, limit=1)
        return found[0] if found else None

    def text(self) -> str:
        parts: list[str] = []
        for child in self.children:
            parts.append(child if isinstance(child, str) else child.text())
        return re.sub(r"\s+", " ", "".join(parts)).strip()

    def direct_text(self) -> str:
        """Text of this element only, ignoring nested elements."""
        return re.sub(r"\s+", " ", "".join(c for c in self.children if isinstance(c, str))).strip()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{self.tag} class={sorted(self.classes)}>"


class _DomBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {})
        self._stack = [self.root]
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip_depth += 1
            return
        node = Node(tag, {k: (v or "") for k, v in attrs}, self._stack[-1])
        self._stack[-1].children.append(node)
        if tag not in VOID_TAGS:
            self._stack.append(node)

    def handle_startendtag(self, tag, attrs):
        node = Node(tag, {k: (v or "") for k, v in attrs}, self._stack[-1])
        self._stack[-1].children.append(node)

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        for i in range(len(self._stack) - 1, 0, -1):
            if self._stack[i].tag == tag:
                del self._stack[i:]
                break

    def handle_data(self, data):
        if self._skip_depth or not data.strip():
            return
        self._stack[-1].children.append(data)


def parse_dom(markup: str) -> Node:
    builder = _DomBuilder()
    builder.feed(markup)
    builder.close()
    return builder.root


# --------------------------------------------------------------------- fields
PRICE_RE = re.compile(r"([\d.]+)\s*€")


def parse_price(raw: str) -> tuple[int | None, str]:
    """'1.890 € VB' -> (1890, 'vb'); 'Zu verschenken' -> (0, 'giveaway')."""
    text = html.unescape(raw or "").strip()
    if not text:
        return None, "none"
    low = text.lower()
    if "verschenk" in low:
        return 0, "giveaway"
    match = PRICE_RE.search(text)
    if not match:
        return (None, "vb") if "vb" in low else (None, "unknown")
    value = int(match.group(1).replace(".", ""))
    return value, "vb" if "vb" in low else "fixed"


MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mrz", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"], start=1)}


def parse_posted(raw: str, now: dt.datetime | None = None) -> dt.datetime | None:
    """'Heute, 11:31' / 'Gestern, 09:12' / '24.08.2025' -> datetime."""
    text = (raw or "").strip()
    if not text:
        return None
    now = now or dt.datetime.now()
    time_match = re.search(r"(\d{1,2}):(\d{2})", text)
    hour, minute = (int(time_match.group(1)), int(time_match.group(2))) if time_match else (0, 0)
    low = text.lower()
    if low.startswith("heute"):
        return now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if low.startswith("gestern"):
        return (now - dt.timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    date_match = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", text)
    if date_match:
        day, month, year = (int(g) for g in date_match.groups())
        return dt.datetime(year, month, day, hour, minute)
    return None


LOCATION_RE = re.compile(r"^(\d{5})\s+(.*)$")


def parse_location(raw: str) -> tuple[str | None, str | None]:
    text = re.sub(r"\s+", " ", (raw or "")).strip()
    if not text:
        return None, None
    match = LOCATION_RE.match(text)
    if match:
        return match.group(1), match.group(2).strip() or None
    return None, text


def _absolute(href: str) -> str:
    if not href:
        return ""
    return href if href.startswith("http") else BASE_URL + href


# ------------------------------------------------------------------- listings
PLZ_SPAN_RE = re.compile(r"^(\d{5})\s+(.+)$")
DATE_TEXT_RE = re.compile(r"^(Heute|Gestern)[,.]|^\d{1,2}\.\d{1,2}\.\d{4}")
PRICE_TEXT_RE = re.compile(r"(\d[\d.]*\s*€|Zu verschenken|VB\b)", re.IGNORECASE)
TOP_AD_RE = re.compile(r'"id":\[0,(\d+)\].{0,400}?"topAd":\[0,(true|false)\]', re.S)


def _top_ad_flags(markup: str) -> dict[str, bool]:
    """Sponsored flags from the redesign's embedded props JSON.

    The new cards carry no CSS marker for a TOP ad; the only signal left is the
    hydration payload, so read it there and default to "not sponsored". The
    payload lives inside an HTML attribute, so its quotes arrive escaped -
    matching the raw markup would silently find nothing.
    """
    return {m.group(1): m.group(2) == "true" for m in TOP_AD_RE.finditer(html.unescape(markup))}


def parse_listings_modern(markup: str) -> list[Listing]:
    """Parse the 2026 result-page layout (Tailwind classes, <article data-adid>).

    Class names in that design are generated and change without warning, so
    every field is found by structure - the article's own attributes, the
    heading link, and the shape of the text - never by class name.
    """
    listings: list[Listing] = []
    sponsored_flags = _top_ad_flags(markup)

    for chunk in markup.split("<article ")[1:]:
        block = chunk.split("</article>", 1)[0]
        ad_id = re.search(r'data-adid="(\d+)"', block)
        if not ad_id:
            continue
        ad_id = ad_id.group(1)

        href = re.search(r'data-href="([^"]+)"', block)
        title_match = re.search(r"<h3[^>]*>.*?<a[^>]*>(.*?)</a>", block, re.S)
        title = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", title_match.group(1))).strip()) if title_match else ""

        spans = [
            html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", raw)).strip())
            for raw in re.findall(r"<span[^>]*>(.*?)</span>", block, re.S)
        ]
        plz = ort = posted_raw = None
        for text in spans:
            place = PLZ_SPAN_RE.match(text)
            if place and plz is None:
                plz, ort = place.group(1), place.group(2)
            elif DATE_TEXT_RE.match(text) and posted_raw is None:
                posted_raw = text

        paragraphs = [
            html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", raw)).strip())
            for raw in re.findall(r"<p[^>]*>(.*?)</p>", block, re.S)
        ]
        price_text = next((t for t in paragraphs if t and PRICE_TEXT_RE.search(t) and len(t) < 30), "")
        description = next((t for t in paragraphs if t and t != price_text and len(t) > 20), "")
        price_eur, price_type = parse_price(price_text)

        image = re.search(r'<img[^>]+src="([^"]+)"', block)
        if plz is None and ort is None:
            location_alt = re.search(r'alt="[^"]*?([A-ZÄÖÜ][\wäöüß.\- ]+) Vorschau"', block)
            ort = location_alt.group(1).strip() if location_alt else None

        listings.append(
            Listing(
                ad_id=ad_id,
                title=title,
                url=_absolute(href.group(1) if href else ""),
                price_eur=price_eur,
                price_type=price_type,
                description=description,
                plz=plz,
                ort=ort,
                posted_raw=posted_raw,
                posted_at=parse_posted(posted_raw or ""),
                image_url=image.group(1) if image else None,
                sponsored=sponsored_flags.get(ad_id, False),
            )
        )
    return listings


def parse_listings(markup: str) -> list[Listing]:
    """Parse a result page, whichever layout the site is currently serving."""
    if 'class="aditem"' not in markup:
        return parse_listings_modern(markup)
    root = parse_dom(markup)
    listings: list[Listing] = []
    for article in root.find_all("article", "aditem"):
        ad_id = article.attrs.get("data-adid", "").strip()
        if not ad_id:
            continue
        title_link = article.find("h2")
        anchor = title_link.find("a") if title_link else None
        title = anchor.text() if anchor else ""
        href = (anchor.attrs.get("href") if anchor else "") or article.attrs.get("data-href", "")

        price_node = article.find(cls="aditem-main--middle--price-shipping--price")
        # The struck-through previous price is nested inside the price element,
        # so only this element's own text describes the current price.
        price_eur, price_type = parse_price(price_node.direct_text() if price_node else "")
        old_node = article.find(cls="aditem-main--middle--price-shipping--old-price")
        old_price, _ = parse_price(old_node.text() if old_node else "")

        location_node = article.find(cls="aditem-main--top--left")
        plz, ort = parse_location(location_node.text() if location_node else "")

        date_node = article.find(cls="aditem-main--top--right")
        posted_raw = date_node.text() if date_node else ""

        description_node = article.find(cls="aditem-main--middle--description")
        image = article.find("img")

        # The sponsored flag lives on the wrapping <li>; those ads ignore the
        # location filter, which matters a lot for route searches.
        wrapper, sponsored = article.parent, False
        while wrapper is not None:
            if "ad-listitem" in wrapper.classes:
                sponsored = "is-topad" in wrapper.classes or "badge-topad" in wrapper.classes
                break
            wrapper = wrapper.parent

        listings.append(
            Listing(
                ad_id=ad_id,
                title=html.unescape(title),
                url=_absolute(href),
                price_eur=price_eur,
                price_type=price_type,
                old_price_eur=old_price,
                description=html.unescape(description_node.text()) if description_node else "",
                plz=plz,
                ort=ort,
                posted_raw=posted_raw or None,
                posted_at=parse_posted(posted_raw),
                tags=[tag.text() for tag in article.find_all("span", "simpletag") if tag.text()],
                image_url=(image.attrs.get("src") if image else None) or None,
                sponsored=sponsored,
            )
        )
    return listings


# The noun after the number changes with the filters: "39.183 Ergebnissen" for
# a plain search, "633 Musikinstrumente" once a category is picked.
SUMMARY_RE = re.compile(r"von\s+([\d.]+)\s+\S")
NO_RESULTS_RE = re.compile(r"keine\s+Ergebnisse", re.IGNORECASE)


# The 2026 redesign dropped the breadcrump-summary element but kept the
# wording, so fall back to the bare "1 - 25 von 62 ..." phrase anywhere.
RANGE_TOTAL_RE = re.compile(r"\d+\s*-\s*\d+\s+von\s+([\d.]+)\s")


def parse_result_total(markup: str) -> int | None:
    """Total hits reported by the site ('1 - 25 von 39.183 Ergebnissen')."""
    match = re.search(r'class="breadcrump-summary"[^>]*>(.*?)</span>', markup, re.S)
    if match:
        text = re.sub(r"<[^>]+>", "", match.group(1))
        if NO_RESULTS_RE.search(text):
            return 0
        hit = SUMMARY_RE.search(text)
        if hit:
            return int(hit.group(1).replace(".", ""))
    text = html.unescape(re.sub(r"<[^>]+>", " ", markup))
    if NO_RESULTS_RE.search(text):
        return 0
    hit = RANGE_TOTAL_RE.search(text)
    return int(hit.group(1).replace(".", "")) if hit else None


def parse_suggested_categories(markup: str) -> list[tuple[int, str]]:
    """Categories the site itself proposes for a query ('/s-fahrraeder/fahrrad/k0c217')."""
    out: dict[int, str] = {}
    for match in re.finditer(r'<a[^>]+href="(/s-[^"]*?/k0c(\d+))"[^>]*>(.*?)</a>', markup, re.S):
        label = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", match.group(3))).strip()
        if label:
            out.setdefault(int(match.group(2)), html.unescape(label))
    return sorted(out.items())


def parse_category_index(markup: str) -> list[tuple[int, str]]:
    """All categories from https://www.kleinanzeigen.de/s-kategorien.html."""
    out: dict[int, str] = {}
    for match in re.finditer(r'<a[^>]+href="/s-([a-z0-9\-]+)/c(\d+)"[^>]*>(.*?)</a>', markup, re.S):
        label = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", match.group(3))).strip()
        out.setdefault(int(match.group(2)), html.unescape(label) or match.group(1))
    return sorted(out.items())
