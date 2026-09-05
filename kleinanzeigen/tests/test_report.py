import csv
import io
import json
import unittest

from kleinanzeigen_search import report
from kleinanzeigen_search.filters import SearchFilters
from kleinanzeigen_search.locations import Location
from kleinanzeigen_search.models import Listing
from kleinanzeigen_search.routes import Route
from kleinanzeigen_search.search import SearchResult

KOELN = (50.938, 6.959)
BONN = (50.735, 7.100)


def sample_result(route: bool = False) -> SearchResult:
    listings = [
        Listing(ad_id="1", title="Fahrrad <gut & günstig>", url="https://example.invalid/1", price_eur=120,
                price_type="vb", plz="51105", ort="Kalk", lat=50.92, lon=7.00, deal_score=81.0,
                reference_price=200, deal_reasons=["40% below the median"], detour_km=3.0,
                along_route_km=5.0, detour_min=9.0),
        Listing(ad_id="2", title="Rennrad", url="https://example.invalid/2", price_eur=None, price_type="none",
                plz="53111", ort="Bonn", lat=50.73, lon=7.10, deal_reasons=["no price stated - ask the seller"],
                detour_km=1.0, along_route_km=28.0, detour_min=2.0),
    ]
    filters = SearchFilters(query="Fahrrad", ad_type=None)
    if route:
        return SearchResult(listings=listings, filters=filters, route=Route([KOELN, BONN], ["Köln", "Bonn"], 30.0, 32.0),
                            centres=[Location(1, "Köln"), Location(2, "Bonn")], corridor_km=15.0)
    return SearchResult(listings=listings, filters=filters, location=Location(945, "Köln", "50667", KOELN))


class TableTest(unittest.TestCase):
    def test_columns_line_up(self):
        text = report.render_table(sample_result())
        lines = text.splitlines()
        self.assertIn("score", lines[0])
        self.assertEqual(len(lines), 4)  # header, rule, two ads

    def test_route_mode_shows_detour(self):
        text = report.render_table(sample_result(route=True))
        self.assertIn("detour", text)
        self.assertIn("+9 min", text)
        self.assertIn("3 km", text)

    def test_city_mode_has_no_detour_columns(self):
        text = report.render_table(sample_result())
        self.assertIn("dist", text)
        self.assertNotIn("off-road", text)

    def test_limit(self):
        self.assertEqual(len(report.render_table(sample_result(), limit=1).splitlines()), 3)

    def test_empty_result(self):
        empty = SearchResult(listings=[], filters=SearchFilters(query="x", ad_type=None))
        self.assertIn("score", report.render_table(empty))


class DetailsTest(unittest.TestCase):
    def test_contains_reasons_and_links(self):
        text = report.render_details(sample_result(route=True))
        self.assertIn("40% below the median", text)
        self.assertIn("https://example.invalid/1", text)
        self.assertIn("km off route", text)
        self.assertIn("+9 min detour", text)


class JsonTest(unittest.TestCase):
    def test_route_payload(self):
        payload = json.loads(report.render_json(sample_result(route=True)))
        self.assertEqual(payload["route"]["waypoints"], ["Köln", "Bonn"])
        self.assertEqual(payload["route"]["corridor_km"], 15.0)
        self.assertEqual(len(payload["listings"]), 2)
        self.assertEqual(payload["listings"][0]["price_label"], "120 € VB")

    def test_city_payload(self):
        payload = json.loads(report.render_json(sample_result()))
        self.assertEqual(payload["location"]["id"], 945)
        self.assertIn("summary", payload)


class CsvTest(unittest.TestCase):
    def test_header_and_rows(self):
        rows = list(csv.DictReader(io.StringIO(report.render_csv(sample_result(route=True)))))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["ad_id"], "1")
        self.assertEqual(rows[0]["deal_reasons"], "40% below the median")
        self.assertEqual(rows[0]["detour_min"], "9.0")


class MapTest(unittest.TestCase):
    def test_map_embeds_route_and_markers(self):
        html = report.render_map(sample_result(route=True))
        self.assertIn("leaflet", html)
        self.assertIn('"lat": 50.92', html)
        self.assertIn("[50.938, 6.959]", html)

    def test_map_carries_drive_time(self):
        self.assertIn('"minutes": 9.0', report.render_map(sample_result(route=True)))

    def test_titles_are_escaped(self):
        html = report.render_map(sample_result())
        self.assertNotIn("<gut &", html)
        self.assertIn("&lt;gut", html)

    def test_listings_without_coordinates_are_skipped(self):
        result = sample_result()
        result.listings[0].lat = None
        html = report.render_map(result)
        self.assertEqual(html.count('"url"'), 1)


class DispatchTest(unittest.TestCase):
    def test_every_format(self):
        result = sample_result(route=True)
        for fmt in ("table", "details", "json", "csv", "html"):
            self.assertTrue(report.render(result, fmt))

    def test_unknown_format(self):
        with self.assertRaises(ValueError):
            report.render(sample_result(), "pdf")


if __name__ == "__main__":
    unittest.main()
