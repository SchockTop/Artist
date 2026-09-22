import unittest

from kleinanzeigen_search.locations import (
    Location, LocationResolver, normalise_radius, plz_table, radius_at_least,
)

BERLIN = (52.52, 13.405)


class PlzTableTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.table = plz_table()

    def test_table_is_populated(self):
        self.assertGreater(len(self.table.entries), 8000)

    def test_lookup_by_postcode(self):
        entry = self.table.get("51105")
        self.assertEqual(entry.ort, "Köln")
        self.assertAlmostEqual(entry.lat, 50.92, delta=0.1)

    def test_unknown_postcode(self):
        self.assertIsNone(self.table.get("00000"))

    def test_nearest_returns_local_postcode(self):
        entry = self.table.nearest(BERLIN)
        self.assertTrue(entry.plz.startswith("10"))
        self.assertEqual(entry.ort, "Berlin")

    def test_nearest_k_is_sorted_and_unique(self):
        entries = self.table.nearest_k(BERLIN, 5)
        self.assertEqual(len(entries), 5)
        self.assertEqual(len({e.plz for e in entries}), 5)

    def test_nearest_skips_large_customer_codes(self):
        self.assertFalse(self.table.nearest(BERLIN).is_big_customer)

    def test_locate_falls_back_to_place_name(self):
        entry = self.table.locate(None, "Pleinfeld")
        self.assertEqual(entry.plz, "91785")

    def test_locate_prefers_postcode(self):
        entry = self.table.locate("91785", "Irgendwo")
        self.assertEqual(entry.ort, "Pleinfeld")


class RadiusTest(unittest.TestCase):
    def test_snaps_to_supported_values(self):
        self.assertEqual(normalise_radius(17), 20)
        self.assertEqual(normalise_radius(3), 5)
        self.assertEqual(normalise_radius(120), 100)

    def test_never_zero(self):
        self.assertEqual(normalise_radius(0), 5)

    def test_at_least_rounds_up(self):
        self.assertEqual(radius_at_least(15), 20)
        self.assertEqual(radius_at_least(20), 20)
        self.assertEqual(radius_at_least(1), 5)

    def test_at_least_caps_at_the_largest_radius(self):
        self.assertEqual(radius_at_least(500), 200)


class PickTest(unittest.TestCase):
    """The suggestion picker decides which place a search actually runs in."""

    def suggestions(self, *labels):
        return [Location(index + 1, label, label[:5] if label[:5].isdigit() else None)
                for index, label in enumerate(labels)]

    def test_city_beats_district(self):
        picked = LocationResolver._pick("berlin", self.suggestions("Pankow - Berlin", "Berlin - Berlin"))
        self.assertEqual(picked.label, "Berlin - Berlin")

    def test_full_city_name(self):
        picked = LocationResolver._pick(
            "frankfurt am main", self.suggestions("Frankfurt (Oder) - Brandenburg", "Frankfurt am Main - Hessen")
        )
        self.assertEqual(picked.label, "Frankfurt am Main - Hessen")

    def test_postcode_requires_exact_match(self):
        # Kleinanzeigen answers unknown postcodes with fuzzy neighbours; those
        # must never be accepted silently.
        self.assertIsNone(LocationResolver._pick("39097", self.suggestions("39397 - Kroppenstedt")))

    def test_postcode_exact_match(self):
        picked = LocationResolver._pick("50667", self.suggestions("50667 Köln Altstadt"))
        self.assertEqual(picked.id, 1)

    def test_no_suggestions(self):
        self.assertIsNone(LocationResolver._pick("nirgendwo", []))


if __name__ == "__main__":
    unittest.main()


class GeocodeFallbackTest(unittest.TestCase):
    """A route may start abroad even though the ads are German-only."""

    class FakeGeoClient:
        """Answers only for queries without a country restriction."""

        def __init__(self, german_hits=()):
            self.german_hits = set(german_hits)
            self.queries = []

        def get_json(self, url, use_cache=True):
            import urllib.parse
            params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            self.queries.append(params)
            restricted = "countrycodes" in params
            place = params["q"][0]
            if restricted and place not in self.german_hits:
                return []
            return [{"lat": "48.5734", "lon": "7.7521"}]

    def test_german_place_resolves_on_the_first_try(self):
        from kleinanzeigen_search.locations import geocode
        client = self.FakeGeoClient(german_hits={"Freiburg"})
        self.assertIsNotNone(geocode(client, "Freiburg"))
        self.assertEqual(len(client.queries), 1)

    def test_foreign_place_falls_back_to_a_worldwide_lookup(self):
        from kleinanzeigen_search.locations import geocode
        client = self.FakeGeoClient(german_hits=set())
        point = geocode(client, "Strasbourg, France")
        self.assertIsNotNone(point)
        self.assertEqual(len(client.queries), 2)
        self.assertNotIn("countrycodes", client.queries[1])

    def test_unknown_place_still_returns_none(self):
        from kleinanzeigen_search.locations import geocode

        class Empty:
            def get_json(self, url, use_cache=True):
                return []

        self.assertIsNone(geocode(Empty(), "Nirgendwo XYZ"))
