import pathlib
import tempfile
import unittest

from kleinanzeigen_search import geo
from kleinanzeigen_search.routes import build_route, load_gpx, parse_google_maps_url

GPX = """<?xml version="1.0"?>
<gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><trkseg>
    <trkpt lat="52.5200" lon="13.4050"/>
    <trkpt lat="51.3397" lon="12.3731"/>
    <trkpt lat="48.1372" lon="11.5756"/>
  </trkseg></trk>
</gpx>
"""


class GoogleMapsUrlTest(unittest.TestCase):
    def test_dir_path(self):
        stops = parse_google_maps_url(
            "https://www.google.com/maps/dir/Berlin/Leipzig,+Deutschland/M%C3%BCnchen/@50.9,12.1,7z/data=!3m1!4b1"
        )
        self.assertEqual(stops, ["Berlin", "Leipzig, Deutschland", "München"])

    def test_api_form(self):
        stops = parse_google_maps_url(
            "https://www.google.com/maps/dir/?api=1&origin=Berlin&destination=M%C3%BCnchen&waypoints=Leipzig%7CN%C3%BCrnberg"
        )
        self.assertEqual(stops, ["Berlin", "Leipzig", "Nürnberg", "München"])

    def test_coordinates_are_kept_verbatim(self):
        stops = parse_google_maps_url("https://www.google.com/maps/dir/52.5200,13.4050/48.1372,11.5756/")
        self.assertEqual(stops, ["52.5200,13.4050", "48.1372,11.5756"])

    def test_rejects_non_directions_link(self):
        with self.assertRaises(ValueError):
            parse_google_maps_url("https://www.google.com/maps/place/Berlin")


class GpxTest(unittest.TestCase):
    def test_reads_track_points(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "trip.gpx"
            path.write_text(GPX, encoding="utf-8")
            points = load_gpx(str(path))
        self.assertEqual(len(points), 3)
        self.assertAlmostEqual(points[0][0], 52.52)


class BuildRouteTest(unittest.TestCase):
    """No network: polyline and GPX inputs are handled entirely offline."""

    def test_from_polyline(self):
        encoded = "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
        route = build_route(client=None, polyline=encoded)
        self.assertEqual(route.source, "polyline")
        self.assertGreaterEqual(len(route.points), 2)

    def test_from_gpx(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "trip.gpx"
            path.write_text(GPX, encoding="utf-8")
            route = build_route(client=None, gpx_path=str(path))
        self.assertEqual(route.source, "gpx")
        self.assertAlmostEqual(route.length_km, geo.route_length_km(route.points), places=3)

    def test_needs_two_waypoints(self):
        with self.assertRaises(ValueError):
            build_route(client=None, waypoints=["Berlin"])

    def test_rejects_degenerate_polyline(self):
        with self.assertRaises(ValueError):
            build_route(client=None, polyline="_p~iF~ps|U")


if __name__ == "__main__":
    unittest.main()
