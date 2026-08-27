import unittest

from kleinanzeigen_search import geo
from kleinanzeigen_search.filters import SearchFilters
from kleinanzeigen_search.locations import LocationResolver
from kleinanzeigen_search.models import Listing
from kleinanzeigen_search.routes import Route, added_trip_minutes, duration_matrix
from kleinanzeigen_search.search import AreaCoverage, SearchResult, annotate_drive_time, search_route
from tests.fakes import FakeOsrmClient

KOELN = (50.938, 6.959)
BONN = (50.735, 7.100)


class MatrixTest(unittest.TestCase):
    def test_matrix_shape(self):
        client = FakeOsrmClient()
        matrix = duration_matrix(client, [KOELN, BONN, (50.8, 7.0)])
        self.assertEqual(len(matrix), 3)
        self.assertEqual(len(matrix[0]), 3)
        self.assertEqual(matrix[0][0], 0)

    def test_refuses_oversized_request(self):
        with self.assertRaises(ValueError):
            duration_matrix(FakeOsrmClient(), [KOELN] * 101)


class AddedTripTimeTest(unittest.TestCase):
    def test_detour_is_zero_on_the_direct_line(self):
        client = FakeOsrmClient()
        # A point on the straight line A-B adds nothing under the fake metric.
        midpoint = geo.interpolate(KOELN, BONN, 0.5)
        [extra] = added_trip_minutes(client, KOELN, BONN, [midpoint])
        self.assertAlmostEqual(extra, 0.0, places=1)

    def test_detour_grows_with_distance(self):
        client = FakeOsrmClient()
        near, far = (50.85, 7.05), (51.5, 7.6)
        close_extra, far_extra = added_trip_minutes(client, KOELN, BONN, [near, far])
        self.assertGreater(far_extra, close_extra)

    def test_empty_input(self):
        self.assertEqual(added_trip_minutes(FakeOsrmClient(), KOELN, BONN, []), [])

    def test_batches_stay_within_the_server_limit(self):
        client = FakeOsrmClient(table_limit=100)
        stops = [(50.7 + i * 0.01, 7.0 + i * 0.01) for i in range(250)]
        minutes = added_trip_minutes(client, KOELN, BONN, stops)
        self.assertEqual(len(minutes), 250)
        self.assertTrue(all(size <= 100 for size in client.matrix_calls), client.matrix_calls)
        self.assertEqual(len(client.matrix_calls), 3)  # 98 + 98 + 54 stops


class AnnotateTest(unittest.TestCase):
    def listings(self):
        return [
            Listing(ad_id="1", title="a", url="u", lat=50.9, lon=7.0),
            Listing(ad_id="2", title="b", url="u", lat=50.9, lon=7.0),  # same postcode
            Listing(ad_id="3", title="c", url="u", lat=51.4, lon=7.5),
            Listing(ad_id="4", title="d", url="u"),                     # no coordinates
        ]

    def test_one_request_per_batch_not_per_ad(self):
        client = FakeOsrmClient()
        annotate_drive_time(client, self.listings(), Route([KOELN, BONN]))
        self.assertEqual(len(client.matrix_calls), 1)

    def test_ads_sharing_a_postcode_share_the_time(self):
        listings = self.listings()
        annotate_drive_time(FakeOsrmClient(), listings, Route([KOELN, BONN]))
        self.assertEqual(listings[0].detour_min, listings[1].detour_min)
        self.assertGreater(listings[2].detour_min, listings[0].detour_min)
        self.assertIsNone(listings[3].detour_min)


class RouteSearchDriveTimeTest(unittest.TestCase):
    def setUp(self):
        self.client = FakeOsrmClient()
        self.resolver = LocationResolver(self.client)
        self.route = Route(geo.sample_route([KOELN, BONN], 5.0), ["Köln", "Bonn"])
        self.filters = SearchFilters(query="Fahrrad", ad_type=None)

    def test_listings_get_drive_times(self):
        result = search_route(self.client, self.resolver, self.filters, self.route,
                              corridor_km=20, max_pages=1)
        self.assertTrue(result.listings)
        self.assertTrue(all(l.detour_min is not None for l in result.listings))

    def test_max_detour_minutes_filters(self):
        kwargs = dict(corridor_km=20, max_pages=1)
        generous = search_route(self.client, self.resolver, self.filters, self.route,
                                max_detour_min=999.0, **kwargs)
        self.assertTrue(generous.listings)
        strict = search_route(self.client, self.resolver, self.filters, self.route,
                              max_detour_min=-1.0, **kwargs)
        self.assertEqual(strict.listings, [])

    def test_detour_minutes_are_never_negative(self):
        result = search_route(self.client, self.resolver, self.filters, self.route, **dict(corridor_km=20, max_pages=1))
        self.assertTrue(all(l.detour_min >= 0 for l in result.listings))

    def test_drive_time_can_be_switched_off(self):
        result = search_route(self.client, self.resolver, self.filters, self.route,
                              corridor_km=20, max_pages=1, drive_time=False)
        self.assertTrue(all(l.detour_min is None for l in result.listings))
        self.assertEqual(self.client.matrix_calls, [])

    def test_routing_failure_degrades_to_distance(self):
        client = FakeOsrmClient(table_limit=1)  # every matrix request is rejected
        result = search_route(client, LocationResolver(client), self.filters, self.route,
                              corridor_km=20, max_pages=1)
        self.assertTrue(result.listings)
        self.assertTrue(any("driving times unavailable" in w for w in result.warnings))
        self.assertTrue(all(l.detour_km is not None for l in result.listings))


class CoverageTest(unittest.TestCase):
    def test_truncated_areas_are_reported(self):
        result = SearchResult(listings=[], filters=SearchFilters(query="x", ad_type=None),
                              coverage=[AreaCoverage("Köln", 50, 800), AreaCoverage("Bonn", 12, 12)])
        [warning] = result.coverage_warnings()
        self.assertIn("1 of 2 areas", warning)
        self.assertIn("Köln (50 of 800)", warning)
        self.assertEqual(result.summary()["areas_truncated"], 1)

    def test_complete_coverage_is_silent(self):
        result = SearchResult(listings=[], filters=SearchFilters(query="x", ad_type=None),
                              coverage=[AreaCoverage("Bonn", 12, 12),
                                        AreaCoverage("Köln", 5, None, exhausted=True)])
        self.assertEqual(result.coverage_warnings(), [])

    def test_route_search_reports_coverage_per_area(self):
        client = FakeOsrmClient()
        result = search_route(client, LocationResolver(client),
                              SearchFilters(query="Fahrrad", ad_type=None),
                              Route(geo.sample_route([KOELN, BONN], 5.0)), corridor_km=20, max_pages=1)
        self.assertTrue(result.coverage)
        # The fixture page is short, so every area really is exhausted - even
        # though its summary line claims 39,183 hits.
        self.assertTrue(all(area.complete for area in result.coverage))
        self.assertEqual(result.coverage_warnings(), [])


if __name__ == "__main__":
    unittest.main()
