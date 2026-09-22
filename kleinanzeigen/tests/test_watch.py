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


    def test_gone_is_reported_on_a_second_run_the_same_day(self):
        """The evening slot has to catch an ad that sold during the day."""
        self.store.diff("r", [ad("1", 200), ad("2", 300)], now="2026-09-06")
        changes = self.store.diff("r", [ad("1", 200)], now="2026-09-06")
        self.assertEqual([g.ad_id for g in changes.gone], ["2"])

    def test_a_vanished_ad_is_reported_once(self):
        self.store.diff("r", [ad("1", 200), ad("2", 300)], now="2026-09-06")
        self.store.diff("r", [ad("1", 200)], now="2026-09-06")
        again = self.store.diff("r", [ad("1", 200)], now="2026-09-07")
        self.assertEqual(again.gone, [])


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


    def test_partly_covered_areas_are_named_even_when_otherwise_quiet(self):
        """A skipped area must not read as a clean bill of health."""
        quiet = Changes(key="muc · Konzertgitarre", coverage_complete=False)
        digest = render_digest([quiet])
        self.assertIn("1 area(s) not fully covered", digest)
        self.assertIn("muc · Konzertgitarre", digest)

    def test_fully_covered_quiet_run_says_nothing_extra(self):
        digest = render_digest([Changes(key="muc · Konzertgitarre")])
        self.assertNotIn("not fully covered", digest)


class RepostTest(unittest.TestCase):
    """A deleted-and-relisted ad is neither a sale nor a new arrival."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = WatchStore(pathlib.Path(self.tmp.name) / "s.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_relist_is_not_counted_as_gone_and_new(self):
        self.store.diff("r", [ad("1", 159, "Pro Arte GC-210 M Konzertgitarre", plz="80687")], now="2026-09-01")
        changes = self.store.diff("r", [ad("2", 149, "Pro Arte GC-210 M Konzertgitarre", plz="80687")], now="2026-09-05")
        self.assertEqual(changes.gone, [])
        self.assertEqual(changes.new, [])
        [repost] = changes.reposts
        self.assertEqual((repost.was, repost.listing.price_eur), (159, 149))
        self.assertEqual(repost.first_seen, "2026-09-01")

    def test_true_age_survives_the_relist(self):
        self.store.diff("r", [ad("1", 159, "Yamaha CG101MS Konzertgitarre", plz="85049")], now="2026-08-01")
        self.store.diff("r", [ad("2", 149, "Yamaha CG101MS Konzertgitarre", plz="85049")], now="2026-09-05")
        self.assertEqual(self.store.known("r")["2"].first_seen, "2026-08-01")

    def test_a_different_guitar_is_still_new(self):
        self.store.diff("r", [ad("1", 159, "Pro Arte GC-210 M", plz="80687")], now="2026-09-01")
        changes = self.store.diff("r", [ad("2", 149, "Seagull S6 Westerngitarre", plz="80687")], now="2026-09-05")
        self.assertEqual(len(changes.new), 1)
        self.assertEqual(len(changes.gone), 1)
        self.assertEqual(changes.reposts, [])

    def test_same_title_in_another_town_is_not_a_relist(self):
        self.store.diff("r", [ad("1", 159, "Ortega R121 Konzertgitarre", plz="80687")], now="2026-09-01")
        changes = self.store.diff("r", [ad("2", 159, "Ortega R121 Konzertgitarre", plz="90402")], now="2026-09-05")
        self.assertEqual(changes.reposts, [])
        self.assertEqual(len(changes.new), 1)

    def test_digest_shows_relists_separately(self):
        self.store.diff("r", [ad("1", 159, "Pro Arte GC-210 M", plz="80687")], now="2026-09-01")
        changes = self.store.diff("r", [ad("2", 149, "Pro Arte GC-210 M", plz="80687")], now="2026-09-05")
        text = render_digest([changes])
        self.assertIn("1 relisted", text)
        self.assertIn("159 → 149 €", text)
        self.assertIn("unsold since 2026-09-01", text)


class ShortlistTest(unittest.TestCase):
    """The reserved/gone distinction that kept getting this wrong."""

    def setUp(self):
        from kleinanzeigen_search import shortlist
        self.shortlist = shortlist
        self.candidate = shortlist.Candidate(
            label="Crafter", url="https://example.invalid/1", asking_eur=380, verdict="BUY")

    def _page(self, body: str) -> str:
        return ('<h1 id="viewad-title">Gitarre</h1>'
                '<h2 id="viewad-price">380 &euro; VB</h2>' + body)

    def check(self, page=None, error=None):
        class FakeClient:
            def get(self, url, use_cache=True):
                if error is not None:
                    raise error
                return page
        return self.shortlist.check(FakeClient(), self.candidate)

    def test_reserviert_in_the_description_is_not_a_sale(self):
        row = self.check(self._page(
            "<p>Für den Versand ist eine gepolsterte Tasche reserviert.</p>"))
        self.assertEqual(row.state, "live")
        self.assertEqual(row.price_eur, 380)

    def test_the_reserved_badge_is_reported(self):
        row = self.check(self._page("<span>Reserviert</span>"))
        self.assertEqual(row.state, "RESERVED")

    def test_deleted_ad(self):
        row = self.check("<p>Diese Anzeige ist nicht mehr verfügbar</p>")
        self.assertEqual(row.state, "GONE")

    def test_throttling_is_not_a_sale(self):
        from kleinanzeigen_search.client import HttpError
        row = self.check(error=HttpError("https://example.invalid/1", 403, "Forbidden"))
        self.assertEqual(row.state, "UNKNOWN(403)")

    def test_missing_ad_is_gone(self):
        from kleinanzeigen_search.client import HttpError
        row = self.check(error=HttpError("https://example.invalid/1", 404, "Not Found"))
        self.assertEqual(row.state, "GONE")

    def test_price_cut_is_reported_against_the_recorded_price(self):
        page = ('<h1 id="viewad-title">Gitarre</h1>'
                '<h2 id="viewad-price">300 &euro;</h2>')
        row = self.check(page)
        self.assertEqual(row.moved, -80)

    def test_percent_of_new_needs_both_prices(self):
        from kleinanzeigen_search import shortlist
        priced = shortlist.Candidate(label="a", url="u", asking_eur=199, new_price_eur=335)
        self.assertEqual(priced.percent_of_new, 59)
        self.assertIsNone(shortlist.Candidate(label="b", url="u", asking_eur=199).percent_of_new)
        self.assertIsNone(shortlist.Candidate(label="c", url="u", new_price_eur=335).percent_of_new)

    def test_percent_of_new_follows_the_latest_price_cut(self):
        """A cut since first-seen must move the ratio, not the stale asking price."""
        from kleinanzeigen_search import shortlist
        candidate = shortlist.Candidate(
            label="a", url="u", asking_eur=149, last_price_eur=129, new_price_eur=299)
        self.assertEqual(candidate.percent_of_new, 43)


class HeadlineCountTest(unittest.TestCase):
    def test_one_ad_in_two_overlapping_areas_counts_once(self):
        """Corridors overlap, so the same ad shows up under several keys."""
        store_a = Changes(key="a")
        store_b = Changes(key="b")
        listing = ad("1", 117)
        seen = __import__("kleinanzeigen_search.watch", fromlist=["Seen"]).Seen(
            ad_id="1", title="LaMancha", url="https://example.invalid/1",
            price_eur=117, first_seen="2026-09-01", last_seen="2026-09-08")
        store_a.new.append(listing)
        store_b.new.append(listing)
        store_a.gone.append(seen)
        store_b.gone.append(seen)
        digest = render_digest([store_a, store_b])
        self.assertIn("1 new", digest)
        self.assertIn("1 vanished", digest)
