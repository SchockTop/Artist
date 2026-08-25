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
