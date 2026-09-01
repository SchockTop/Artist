import datetime as dt
import json
import pathlib
import tempfile
import unittest

from kleinanzeigen_search.models import Listing
from kleinanzeigen_search.watch import Changes, WatchStore, render_digest


def ad(ad_id: str, price: int | None, title: str = "Konzertgitarre", **kw) -> Listing:
    return Listing(ad_id=ad_id, title=title, url=f"https://example.invalid/{ad_id}",
                   price_eur=price, price_type="fixed", **kw)


class DiffTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.tmp.name) / "state.json"
        self.store = WatchStore(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_first_run_is_all_new(self):
        changes = self.store.diff("r", [ad("1", 200), ad("2", 300)], now="2026-09-01")
        self.assertEqual(len(changes.new), 2)
        self.assertEqual(changes.drops, [])
        self.assertEqual(changes.gone, [])

    def test_second_run_reports_nothing_when_stable(self):
        self.store.diff("r", [ad("1", 200)], now="2026-09-01")
        changes = self.store.diff("r", [ad("1", 200)], now="2026-09-02")
        self.assertTrue(changes.quiet)
        self.assertEqual(changes.unchanged, 1)

    def test_price_drop_is_reported_with_percentage(self):
        self.store.diff("r", [ad("1", 200)], now="2026-09-01")
        changes = self.store.diff("r", [ad("1", 150)], now="2026-09-02")
        [drop] = changes.drops
        self.assertEqual((drop.was, drop.now), (200, 150))
        self.assertAlmostEqual(drop.percent, 25.0)
        self.assertEqual(drop.first_seen, "2026-09-01")

    def test_price_rise_is_not_a_drop(self):
        self.store.diff("r", [ad("1", 200)], now="2026-09-01")
        self.assertEqual(self.store.diff("r", [ad("1", 260)], now="2026-09-02").drops, [])

    def test_lowest_price_is_remembered(self):
        self.store.diff("r", [ad("1", 200)], now="2026-09-01")
        self.store.diff("r", [ad("1", 150)], now="2026-09-02")
        self.store.diff("r", [ad("1", 170)], now="2026-09-03")
        self.assertEqual(self.store.known("r")["1"].lowest_price, 150)

    def test_vanished_ad_is_reported(self):
        self.store.diff("r", [ad("1", 200), ad("2", 300)], now="2026-09-01")
        changes = self.store.diff("r", [ad("1", 200)], now="2026-09-02")
        self.assertEqual([g.ad_id for g in changes.gone], ["2"])

    def test_partial_coverage_never_claims_an_ad_vanished(self):
        # A missing ad may simply not have been paged to; calling it sold lies.
        self.store.diff("r", [ad("1", 200), ad("2", 300)], now="2026-09-01")
        changes = self.store.diff("r", [ad("1", 200)], coverage_complete=False, now="2026-09-02")
        self.assertEqual(changes.gone, [])
        self.assertFalse(changes.coverage_complete)

    def test_watches_are_kept_apart(self):
        self.store.diff("route-a", [ad("1", 200)], now="2026-09-01")
        changes = self.store.diff("route-b", [ad("1", 200)], now="2026-09-01")
        self.assertEqual(len(changes.new), 1)

    def test_state_survives_a_reload(self):
        self.store.diff("r", [ad("1", 200)], now="2026-09-01")
        self.store.save()
        again = WatchStore(self.path)
        self.assertTrue(again.diff("r", [ad("1", 200)], now="2026-09-02").quiet)

    def test_corrupt_state_file_does_not_crash(self):
        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(len(WatchStore(self.path).diff("r", [ad("1", 200)]).new), 1)

    def test_missing_prices_are_not_treated_as_drops(self):
        self.store.diff("r", [ad("1", None)], now="2026-09-01")
        self.assertEqual(self.store.diff("r", [ad("1", 200)], now="2026-09-02").drops, [])


class DigestTest(unittest.TestCase):
    def test_digest_leads_with_the_counts(self):
        changes = Changes(key="wolnzach · Konzertgitarre", new=[ad("1", 200, detour_min=6.0)])
        text = render_digest([changes])
        self.assertIn("1 new", text)
        self.assertIn("wolnzach · Konzertgitarre", text)
        self.assertIn("+6 min", text)

    def test_quiet_watches_are_omitted(self):
        text = render_digest([Changes(key="quiet", unchanged=12)])
        self.assertNotIn("quiet", text.split("\n", 1)[1] if "\n" in text else "")

    def test_partial_coverage_is_flagged(self):
        changes = Changes(key="r", new=[ad("1", 200)], coverage_complete=False)
        self.assertIn("not fully covered", render_digest([changes]))
