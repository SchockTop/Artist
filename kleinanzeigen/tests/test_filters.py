import unittest

from kleinanzeigen_search.filters import SearchFilters, slugify


class SlugTest(unittest.TestCase):
    def test_umlauts_and_spaces(self):
        self.assertEqual(slugify("Küchenmöbel weiß"), "kuechenmoebel-weiss")

    def test_punctuation(self):
        self.assertEqual(slugify("Bosch GSR 18V-60 (neu!)"), "bosch-gsr-18v-60-neu")


class UrlTest(unittest.TestCase):
    def test_minimal(self):
        url = SearchFilters(query="Fahrrad", ad_type=None).url()
        self.assertEqual(url, "https://www.kleinanzeigen.de/s-fahrrad/k0")

    def test_location_and_radius(self):
        url = SearchFilters(query="Fahrrad", location_id=3331, radius_km=20, ad_type=None).url()
        self.assertEqual(url, "https://www.kleinanzeigen.de/s-fahrrad/k0l3331r20")

    def test_all_filters_in_site_order(self):
        url = SearchFilters(
            query="E Bike", location_id=3331, radius_km=20, category_id=217,
            min_price=100, max_price=500, seller="privat", ad_type="angebote",
            sort="preis", shipping_only=True, page=2,
        ).url()
        self.assertEqual(
            url,
            "https://www.kleinanzeigen.de/s-anbieter:privat/anzeige:angebote/versand:ja/"
            "preis:100:500/sortierung:preis/seite:2/e-bike/k0c217l3331r20",
        )

    def test_open_ended_prices(self):
        self.assertIn("preis:300:", SearchFilters(query="x", min_price=300, ad_type=None).url())
        self.assertIn("preis::80", SearchFilters(query="x", max_price=80, ad_type=None).url())

    def test_radius_needs_location(self):
        self.assertNotIn("r20", SearchFilters(query="x", radius_km=20, ad_type=None).url())

    def test_extra_segments(self):
        url = SearchFilters(query="x", ad_type=None, extra=["zustand:neu"]).url()
        self.assertIn("/s-zustand:neu/x/k0", url)

    def test_category_only(self):
        self.assertEqual(SearchFilters(category_id=217, ad_type=None).url(),
                         "https://www.kleinanzeigen.de/s-k0c217")

    def test_helpers_do_not_mutate(self):
        base = SearchFilters(query="x", ad_type=None)
        page2 = base.for_page(2)
        located = base.at_location(3331, 10)
        self.assertEqual(base.page, 1)
        self.assertIsNone(base.location_id)
        self.assertEqual(page2.page, 2)
        self.assertEqual((located.location_id, located.radius_km), (3331, 10))


class ValidationTest(unittest.TestCase):
    def test_requires_query_or_category(self):
        with self.assertRaises(ValueError):
            SearchFilters().url()

    def test_price_order(self):
        with self.assertRaises(ValueError):
            SearchFilters(query="x", min_price=500, max_price=100).url()

    def test_unknown_sort(self):
        with self.assertRaises(ValueError):
            SearchFilters(query="x", sort="billigste").url()

    def test_unknown_seller(self):
        with self.assertRaises(ValueError):
            SearchFilters(query="x", seller="haendler").url()


if __name__ == "__main__":
    unittest.main()
