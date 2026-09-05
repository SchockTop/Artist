import datetime as dt
import unittest

from kleinanzeigen_search import deals
from kleinanzeigen_search.models import Listing


def make(ad_id: str, title: str, price: int | None, **kwargs) -> Listing:
    return Listing(ad_id=ad_id, title=title, url=f"https://example.invalid/{ad_id}", price_eur=price,
                   price_type=kwargs.pop("price_type", "fixed"), **kwargs)


class TokenTest(unittest.TestCase):
    def test_umlauts_and_stopwords(self):
        self.assertEqual(deals.tokenize("Küchenmöbel mit der Tür"), {"kuechenmoebel", "tuer"})

    def test_similarity(self):
        self.assertEqual(deals.similarity({"a", "b"}, {"a", "b"}), 1.0)
        self.assertEqual(deals.similarity({"a"}, set()), 0.0)
        self.assertAlmostEqual(deals.similarity({"a", "b"}, {"b", "c", "d"}), 0.5)


class StatsTest(unittest.TestCase):
    def test_median_and_quartiles(self):
        listings = [make(str(i), "x", price) for i, price in enumerate([100, 200, 300, 400])]
        stats = deals.price_stats(listings)
        self.assertEqual(stats.count, 4)
        self.assertEqual(stats.median, 250)

    def test_extremes_are_trimmed(self):
        prices = [1] + [100] * 10 + [999999]
        listings = [make(str(i), "x", price) for i, price in enumerate(prices)]
        stats = deals.price_stats(listings)
        self.assertEqual(stats.median, 100)
        self.assertLess(stats.p75, 1000)

    def test_no_prices(self):
        self.assertIsNone(deals.price_stats([make("1", "x", None, price_type="none")]))


class EvaluateTest(unittest.TestCase):
    def pool(self, target_price: int, target_title: str = "Bosch GSR 18V Akkuschrauber", **kwargs):
        listings = [make(str(index), "Bosch GSR 18V Akkuschrauber", 200) for index in range(10)]
        listings.append(make("target", target_title, target_price, **kwargs))
        return deals.evaluate(listings)

    def target(self, listings):
        return next(l for l in listings if l.ad_id == "target")

    def test_average_price_scores_neutral(self):
        target = self.target(self.pool(200))
        self.assertAlmostEqual(target.deal_score, 50, delta=2)
        self.assertEqual(target.reference_price, 200)

    def test_cheap_scores_high(self):
        target = self.target(self.pool(120))
        self.assertGreater(target.deal_score, 65)

    def test_expensive_scores_low(self):
        target = self.target(self.pool(320))
        self.assertLess(target.deal_score, 35)

    def test_defective_is_penalised(self):
        cheap = self.target(self.pool(120)).deal_score
        broken = self.target(self.pool(120, "Bosch GSR 18V Akkuschrauber defekt")).deal_score
        self.assertLess(broken, cheap)

    def test_wanted_ad_is_penalised(self):
        target = self.target(self.pool(120, "Suche Bosch GSR 18V Akkuschrauber"))
        self.assertIn("wanted ad, not an offer", target.deal_reasons)

    def test_giveaway(self):
        target = self.target(self.pool(0, price_type="giveaway"))
        self.assertEqual(target.deal_score, 95.0)

    def test_missing_price_has_no_score(self):
        target = self.target(self.pool(None, price_type="none"))
        self.assertIsNone(target.deal_score)
        self.assertIn("no price stated - ask the seller", target.deal_reasons)

    def test_unrelated_item_is_flagged_and_damped(self):
        target = self.target(self.pool(20, "Gartenzwerg aus Keramik"))
        self.assertIn("no closely comparable ad - compared against the whole result set", target.deal_reasons)
        self.assertLess(target.deal_score, 90)

    def test_suspiciously_cheap_is_called_out(self):
        target = self.target(self.pool(10))
        self.assertTrue(any("scam" in reason for reason in target.deal_reasons))

    def test_fresh_ad_bonus(self):
        stale = self.target(self.pool(150, posted_at=dt.datetime.now() - dt.timedelta(days=60))).deal_score
        fresh = self.target(self.pool(150, posted_at=dt.datetime.now())).deal_score
        self.assertGreater(fresh, stale)

    def test_score_stays_in_range(self):
        for price in (1, 50, 200, 5000):
            target = self.target(self.pool(price))
            self.assertTrue(0 <= target.deal_score <= 100, target.deal_score)


class SummaryTest(unittest.TestCase):
    def test_summary_fields(self):
        listings = deals.evaluate([make(str(i), "Fahrrad", 100 + i * 10) for i in range(6)])
        summary = deals.summarise(listings)
        self.assertEqual(summary["listings"], 6)
        self.assertEqual(summary["with_price"], 6)
        self.assertIsNotNone(summary["median_price"])


if __name__ == "__main__":
    unittest.main()
