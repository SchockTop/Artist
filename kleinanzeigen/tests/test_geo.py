import math
import unittest

from kleinanzeigen_search import geo

BERLIN = (52.5200, 13.4050)
MUNICH = (48.1372, 11.5756)
LEIPZIG = (51.3397, 12.3731)


class HaversineTest(unittest.TestCase):
    def test_known_distance(self):
        self.assertAlmostEqual(geo.haversine_km(BERLIN, MUNICH), 504.3, delta=2.0)

    def test_zero(self):
        self.assertEqual(geo.haversine_km(BERLIN, BERLIN), 0.0)


class PolylineTest(unittest.TestCase):
    def test_reference_vector(self):
        points = geo.decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@")
        self.assertEqual(
            [(round(a, 5), round(b, 5)) for a, b in points],
            [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)],
        )

    def test_empty(self):
        self.assertEqual(geo.decode_polyline(""), [])


class RouteMathTest(unittest.TestCase):
    def setUp(self):
        self.route = [BERLIN, LEIPZIG, MUNICH]

    def test_distance_to_route_on_vertex(self):
        detour, along = geo.distance_to_route_km(LEIPZIG, self.route)
        self.assertLess(detour, 0.01)
        self.assertAlmostEqual(along, geo.haversine_km(BERLIN, LEIPZIG), delta=0.5)

    def test_distance_to_route_off_route(self):
        # Hamburg is nowhere near a Berlin-Munich trip.
        detour, _ = geo.distance_to_route_km((53.5511, 9.9937), self.route)
        self.assertGreater(detour, 200)

    def test_along_route_is_monotonic(self):
        _, first = geo.distance_to_route_km((52.0, 13.0), self.route)
        _, second = geo.distance_to_route_km((49.0, 11.8), self.route)
        self.assertLess(first, second)

    def test_sample_route_spacing(self):
        samples = geo.sample_route([BERLIN, MUNICH], 25.0)
        gaps = [geo.haversine_km(a, b) for a, b in zip(samples, samples[1:])]
        self.assertTrue(all(gap <= 25.5 for gap in gaps), gaps)
        self.assertLess(geo.haversine_km(samples[-1], MUNICH), 0.01)

    def test_sample_route_short_input(self):
        self.assertEqual(geo.sample_route([BERLIN], 10), [BERLIN])

    def test_simplify_keeps_ends(self):
        dense = geo.sample_route([BERLIN, LEIPZIG, MUNICH], 5.0)
        thin = geo.simplify(dense, 2.0)
        self.assertLess(len(thin), len(dense))
        self.assertEqual(thin[0], dense[0])
        self.assertEqual(thin[-1], dense[-1])

    def test_cover_route_covers_every_point(self):
        radius = 20.0
        centres = geo.cover_route(self.route, radius)
        for point in geo.sample_route(self.route, 2.0):
            nearest = min(geo.haversine_km(point, centre) for centre in centres)
            self.assertLessEqual(nearest, radius, f"gap at {point}: {nearest:.1f} km")

    def test_cover_route_rejects_zero_radius(self):
        with self.assertRaises(ValueError):
            geo.cover_route(self.route, 0)


class ProjectionTest(unittest.TestCase):
    def test_point_to_segment_projects_inside(self):
        distance, t = geo.point_to_segment_km((52.0, 13.0), BERLIN, LEIPZIG)
        self.assertTrue(0.0 < t < 1.0)
        self.assertLess(distance, 30)

    def test_point_to_segment_clamps(self):
        _, t = geo.point_to_segment_km(MUNICH, BERLIN, LEIPZIG)
        self.assertEqual(t, 1.0)

    def test_degenerate_segment(self):
        distance, t = geo.point_to_segment_km(LEIPZIG, BERLIN, BERLIN)
        self.assertEqual(t, 0.0)
        self.assertAlmostEqual(distance, geo.haversine_km(LEIPZIG, BERLIN), places=6)


if __name__ == "__main__":
    unittest.main()
