import io
import unittest
from contextlib import redirect_stdout

from kleinanzeigen_search.cli import build_filters, build_parser, emit
from kleinanzeigen_search.models import Listing
from kleinanzeigen_search.search import SearchResult
from kleinanzeigen_search.filters import SearchFilters


class ArgumentTest(unittest.TestCase):
    def parse(self, argv):
        return build_parser().parse_args(argv)

    def test_city_defaults(self):
        args = self.parse(["city", "Fahrrad", "--in", "Köln"])
        self.assertEqual((args.place, args.radius, args.pages, args.ad_type), ("Köln", 20, 2, "angebote"))

    def test_filters_are_mapped_to_the_url(self):
        args = self.parse([
            "city", "E-Bike", "--in", "Köln", "--min-price", "100", "--max-price", "900",
            "--seller", "privat", "--sort", "preis", "--shipping", "--filter", "zustand:neu",
        ])
        url = build_filters(args).url()
        self.assertIn("anbieter:privat", url)
        self.assertIn("preis:100:900", url)
        self.assertIn("versand:ja", url)
        self.assertIn("sortierung:preis", url)
        self.assertIn("zustand:neu", url)

    def test_route_requires_a_source(self):
        with self.assertRaises(SystemExit):
            self.parse(["route", "Fahrrad"])

    def test_route_sources_are_exclusive(self):
        with self.assertRaises(SystemExit):
            self.parse(["route", "Fahrrad", "--waypoints", "A;B", "--gpx", "t.gpx"])

    def test_route_defaults(self):
        args = self.parse(["route", "Fahrrad", "--waypoints", "Köln; Bonn"])
        self.assertEqual((args.corridor, args.max_areas, args.pages), (15.0, 40, 2))

    def test_command_is_required(self):
        with self.assertRaises(SystemExit):
            self.parse([])


class EmitTest(unittest.TestCase):
    def result(self):
        listings = [
            Listing(ad_id="1", title="cheap", url="u1", price_eur=10, deal_score=90.0),
            Listing(ad_id="2", title="pricey", url="u2", price_eur=99, deal_score=20.0),
        ]
        return SearchResult(listings=listings, filters=SearchFilters(query="x", ad_type=None))

    def test_min_score_filters(self):
        args = build_parser().parse_args(["city", "x", "--min-score", "50", "--format", "table"])
        result = self.result()
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            emit(result, args)
        self.assertIn("cheap", buffer.getvalue())
        self.assertNotIn("pricey", buffer.getvalue())

    def test_output_file(self):
        import tempfile, pathlib

        with tempfile.TemporaryDirectory() as tmp:
            target = pathlib.Path(tmp) / "out.csv"
            args = build_parser().parse_args(["city", "x", "--format", "csv", "-o", str(target)])
            emit(self.result(), args)
            self.assertIn("cheap", target.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
