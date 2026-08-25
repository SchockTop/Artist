import unittest

from kleinanzeigen_search import geo
from kleinanzeigen_search.filters import SearchFilters
from kleinanzeigen_search.locations import Location, LocationResolver
from kleinanzeigen_search.routes import Route
from kleinanzeigen_search.search import fetch_pages, plan_circles, search_city, search_route
from tests.fakes import FakeClient

KOELN = (50.938, 6.959)
BONN = (50.735, 7.100)


class FetchPagesTest(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()
        self.filters = SearchFilters(query="Fahrrad", ad_type=None)

    def test_drops_sponsored_ads_by_default(self):
        listings, total, pages = fetch_pages(self.client, self.filters, max_pages=1)
        self.assertEqual(len(listings), 3)
        self.assertFalse(any(l.sponsored for l in listings))
        self.assertEqual(total, 39183)
        self.assertEqual(pages, 1)

    def test_keeps_sponsored_when_asked(self):
        listings, _, _ = fetch_pages(self.client, self.filters, max_pages=1, include_sponsored=True)
        self.assertEqual(len(listings), 4)

    def test_stops_when_a_page_is_not_full(self):
        # The fixture holds fewer than 25 organic ads, so page 2 is never requested.
        fetch_pages(self.client, self.filters, max_pages=5)
        self.assertEqual(len(self.client.urls), 1)

    def test_pagination_url(self):
        client = FakeClient(markup="<html></html>")
        fetch_pages(client, self.filters, max_pages=1)
        self.assertNotIn("seite:", client.urls[0])


class CitySearchTest(unittest.TestCase):
    def test_annotates_distance_and_sorts_by_it(self):
        client = FakeClient()
        filters = SearchFilters(query="Fahrrad", location_id=945, radius_km=20, ad_type=None)
        result = search_city(client, filters, Location(945, "Köln", "50667", KOELN), max_pages=1)
        distances = [l.distance_km for l in result.listings]
        self.assertEqual(distances, sorted(distances))
        self.assertLess(min(distances), 15)  # the Cologne ad
        self.assertTrue(all(l.deal_score is not None or l.price_eur is None for l in result.listings))

    def test_works_without_a_location(self):
        result = search_city(FakeClient(), SearchFilters(query="Fahrrad", ad_type=None), None, max_pages=1)
        self.assertEqual(len(result.listings), 3)
        self.assertTrue(all(l.distance_km is None for l in result.listings))


class PlanCirclesTest(unittest.TestCase):
    def test_circles_cover_the_route(self):
        resolver = LocationResolver(FakeClient())
        route = [KOELN, BONN]
        centres, warnings = plan_circles(resolver, Route(route), radius_km=10)
        self.assertEqual(warnings, [])
        self.assertTrue(centres)
        for point in geo.sample_route(route, 2.0):
            nearest = min(geo.haversine_km(point, c.point) for c in centres)
            self.assertLess(nearest, 12)

    def test_deduplicates_areas(self):
        resolver = LocationResolver(FakeClient())
        centres, _ = plan_circles(resolver, Route([KOELN, KOELN]), radius_km=10)
        self.assertEqual(len({c.id for c in centres}), len(centres))

    def test_area_cap_warns(self):
        resolver = LocationResolver(FakeClient())
        centres, warnings = plan_circles(resolver, Route([KOELN, (52.52, 13.405)]), radius_km=5, max_circles=3)
        self.assertLessEqual(len(centres), 3)
        self.assertTrue(any("limited to 3" in w for w in warnings))


class RouteSearchTest(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()
        self.resolver = LocationResolver(self.client)
        self.route = Route(geo.sample_route([KOELN, BONN], 5.0), ["Köln", "Bonn"])
        self.filters = SearchFilters(query="Fahrrad", ad_type=None)

    def test_keeps_only_ads_near_the_route(self):
        result = search_route(self.client, self.resolver, self.filters, self.route,
                              corridor_km=20, max_pages=1)
        # The fixture holds ads in Cologne, Balingen and Warendorf; only the
        # Cologne one is anywhere near a Cologne-Bonn drive.
        self.assertEqual([l.plz for l in result.listings], ["51105"])
        listing = result.listings[0]
        self.assertIsNotNone(listing.detour_km)
        self.assertIsNotNone(listing.along_route_km)
        self.assertTrue(listing.found_near)

    def test_deduplicates_across_search_areas(self):
        result = search_route(self.client, self.resolver, self.filters, self.route,
                              corridor_km=20, max_pages=1)
        ad_ids = [l.ad_id for l in result.listings]
        self.assertEqual(len(ad_ids), len(set(ad_ids)))
        self.assertGreater(len(result.centres), 1)  # several areas were searched

    def test_results_are_ordered_along_the_trip(self):
        route = Route(geo.sample_route([KOELN, (51.96, 7.63)], 10.0))  # Cologne -> Münster
        result = search_route(self.client, self.resolver, self.filters, route,
                              corridor_km=50, max_pages=1)
        positions = [l.along_route_km for l in result.listings]
        self.assertEqual(positions, sorted(positions))
        self.assertGreaterEqual(len(positions), 2)  # Cologne and Warendorf

    def test_unlocatable_ads_can_be_kept(self):
        markup = FakeClient().markup.replace("51105 Kalk", "Irgendwo")
        client = FakeClient(markup=markup)
        result = search_route(client, LocationResolver(client), self.filters, self.route,
                              corridor_km=20, max_pages=1, keep_unlocated=True)
        self.assertTrue(any(l.detour_km is None for l in result.listings))


if __name__ == "__main__":
    unittest.main()
