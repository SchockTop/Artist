"""Offline stand-ins for the HTTP layer, so tests never touch the network."""
from __future__ import annotations

import pathlib
import re
import urllib.parse

from kleinanzeigen_search.locations import plz_table

FIXTURE = (pathlib.Path(__file__).with_name("fixtures") / "search_page.html").read_text(encoding="utf-8")


class FakeClient:
    """Serves the fixture page for searches and the real postcode table for lookups."""

    def __init__(self, markup: str = FIXTURE):
        self.markup = markup
        self.urls: list[str] = []
        self.request_count = 0

    def get(self, url: str, accept: str = "text/html", referer=None, use_cache: bool = True) -> str:
        self.urls.append(url)
        self.request_count += 1
        return self.markup

    def get_json(self, url: str, use_cache: bool = True):
        self.urls.append(url)
        self.request_count += 1
        query = urllib.parse.unquote(urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("query", [""])[0])
        payload = {"_0": "Deutschland"}
        if re.fullmatch(r"\d{5}", query):
            entry = plz_table().get(query)
            if entry:
                payload[f"_{int(query)}"] = f"{entry.plz} {entry.ort}"
        else:
            for entry in plz_table().entries:
                if entry.ort.lower() == query.lower():
                    payload[f"_{int(entry.plz)}"] = f"{entry.ort} - Testland"
                    break
        return payload

    def resolve_redirect(self, url: str) -> str:
        return url


class FakeOsrmClient(FakeClient):
    """FakeClient that also answers OSRM duration-matrix requests.

    Durations are synthesised from the coordinates so the maths stays
    deterministic: one second per 0.001 degree of separation.
    """

    def __init__(self, markup: str = FIXTURE, table_limit: int = 100):
        super().__init__(markup)
        self.table_limit = table_limit
        self.matrix_calls: list[int] = []

    def get_json(self, url: str, use_cache: bool = True):
        if "/table/v1/" not in url:
            return super().get_json(url, use_cache)
        self.urls.append(url)
        self.request_count += 1
        raw = url.split("/table/v1/driving/")[1].split("?")[0]
        points = [tuple(float(v) for v in pair.split(",")) for pair in raw.split(";")]
        self.matrix_calls.append(len(points))
        if len(points) > self.table_limit:
            return {"code": "TooBig", "message": "too many coordinates"}
        durations = [[abs(a[0] - b[0]) * 1000 + abs(a[1] - b[1]) * 1000 for b in points] for a in points]
        return {"code": "Ok", "durations": durations}


AD_TEMPLATE = """
<li class="ad-listitem {sponsored_class}">
  <article class="aditem" data-adid="{ad_id}" data-href="/s-anzeige/ad/{ad_id}-74-1">
    <div class="aditem-image"><img src="https://img.invalid/{ad_id}.jpg" /></div>
    <div class="aditem-main">
      <div class="aditem-main--top">
        <div class="aditem-main--top--left">{plz} {ort}</div>
        <div class="aditem-main--top--right">Heute, 10:00</div>
      </div>
      <div class="aditem-main--middle">
        <h2 class="text-module-begin">
          <a class="ellipsis" href="/s-anzeige/ad/{ad_id}-74-1">{title}</a>
        </h2>
        <p class="aditem-main--middle--description">Beschreibung {ad_id}</p>
        <div class="aditem-main--middle--price-shipping">
          <p class="aditem-main--middle--price-shipping--price">{price} &euro;</p>
        </div>
      </div>
    </div>
  </article>
</li>
"""


def make_page(ad_ids, total, plz="51105", ort="Kalk", sponsored=()):
    """Render a result page in the shape the real site emits."""
    items = "".join(
        AD_TEMPLATE.format(
            ad_id=ad_id, plz=plz, ort=ort, title=f"Gitarre {ad_id}", price=100 + (int(ad_id) % 50),
            sponsored_class="is-topad" if ad_id in sponsored else "",
        )
        for ad_id in ad_ids
    )
    return (
        '<html><body><span class="breadcrump-summary">'
        f"1 - 25 von {total} Ergebnissen</span>"
        f'<ul class="itemlist ad-list it3">{items}</ul></body></html>'
    )


class PagedFakeClient(FakeClient):
    """Serves a synthetic multi-page corpus per search area.

    ``inventory`` maps a location id to how many ads that area holds; pages are
    generated on demand with 25 ads each, exactly like the real site.
    """

    def __init__(self, inventory: dict[int, int], per_page: int = 25, reported=None):
        super().__init__()
        self.inventory = inventory
        self.per_page = per_page
        self.reported = reported or {}
        self.page_requests: list[tuple[int, int]] = []

    def get(self, url: str, accept: str = "text/html", referer=None, use_cache: bool = True) -> str:
        self.urls.append(url)
        self.request_count += 1
        location = int(re.search(r"l(\d+)r", url).group(1)) if re.search(r"l(\d+)r", url) else 0
        page = int(re.search(r"seite:(\d+)", url).group(1)) if "seite:" in url else 1
        self.page_requests.append((location, page))
        held = self.inventory.get(location, 0)
        start = (page - 1) * self.per_page
        ad_ids = [f"{location}{index:05d}" for index in range(start, min(start + self.per_page, held))]
        return make_page(ad_ids, self.reported.get(location, held))
