import datetime as dt
import pathlib
import unittest

from kleinanzeigen_search import parser

FIXTURE = pathlib.Path(__file__).with_name("fixtures") / "search_page.html"
MARKUP = FIXTURE.read_text(encoding="utf-8")


class PriceTest(unittest.TestCase):
    def test_negotiable(self):
        self.assertEqual(parser.parse_price("1.890 € VB"), (1890, "vb"))

    def test_fixed(self):
        self.assertEqual(parser.parse_price("450 €"), (450, "fixed"))

    def test_giveaway(self):
        self.assertEqual(parser.parse_price("Zu verschenken"), (0, "giveaway"))

    def test_missing(self):
        self.assertEqual(parser.parse_price(""), (None, "none"))

    def test_vb_without_number(self):
        self.assertEqual(parser.parse_price("VB"), (None, "vb"))


class PostedTest(unittest.TestCase):
    now = dt.datetime(2025, 8, 25, 18, 0)

    def test_today(self):
        self.assertEqual(parser.parse_posted("Heute, 11:31", self.now), dt.datetime(2025, 8, 25, 11, 31))

    def test_yesterday(self):
        self.assertEqual(parser.parse_posted("Gestern, 09:05", self.now), dt.datetime(2025, 8, 24, 9, 5))

    def test_absolute_date(self):
        self.assertEqual(parser.parse_posted("24.07.2025", self.now), dt.datetime(2025, 7, 24, 0, 0))

    def test_unparseable(self):
        self.assertIsNone(parser.parse_posted("irgendwann", self.now))


class LocationTest(unittest.TestCase):
    def test_postcode_and_place(self):
        self.assertEqual(parser.parse_location(" 51105 Kalk "), ("51105", "Kalk"))

    def test_place_only(self):
        self.assertEqual(parser.parse_location("Berlin - Mitte"), (None, "Berlin - Mitte"))

    def test_empty(self):
        self.assertEqual(parser.parse_location(""), (None, None))


class ListingsTest(unittest.TestCase):
    def setUp(self):
        self.listings = parser.parse_listings(MARKUP)
        self.by_id = {l.ad_id: l for l in self.listings}

    def test_finds_every_ad(self):
        self.assertEqual(len(self.listings), 4)

    def test_sponsored_flag(self):
        sponsored = [l for l in self.listings if l.sponsored]
        self.assertEqual([l.ad_id for l in sponsored], ["3488841163"])

    def test_full_field_set(self):
        listing = self.by_id["3494282034"]
        self.assertEqual(listing.title, "Gravelbike Rose BACKROAD FF Rival eTap AXS 1x12 - S")
        self.assertEqual((listing.price_eur, listing.price_type), (2800, "vb"))
        self.assertEqual((listing.plz, listing.ort), ("51105", "Kalk"))
        self.assertEqual(listing.tags, ["Versand möglich"])
        self.assertTrue(listing.url.startswith("https://www.kleinanzeigen.de/s-anzeige/"))
        self.assertTrue(listing.image_url.startswith("https://"))
        self.assertTrue(listing.description)

    def test_old_price_is_not_mistaken_for_the_price(self):
        listing = self.by_id["3488841163"]
        self.assertEqual(listing.price_eur, 1890)
        self.assertEqual(listing.old_price_eur, 2190)

    def test_giveaway(self):
        listing = self.by_id["3494281897"]
        self.assertEqual(listing.price_type, "giveaway")
        self.assertEqual(listing.price_label, "zu verschenken")

    def test_price_label_formatting(self):
        self.assertEqual(self.by_id["3494282034"].price_label, "2.800 € VB")

    def test_html_entities_are_decoded(self):
        self.assertNotIn("&amp;", " ".join(l.title for l in self.listings))


class PageMetaTest(unittest.TestCase):
    def test_result_total(self):
        self.assertEqual(parser.parse_result_total(MARKUP), 39183)

    def test_result_total_missing(self):
        self.assertIsNone(parser.parse_result_total("<html></html>"))

    def test_suggested_categories(self):
        self.assertEqual(parser.parse_suggested_categories(MARKUP), [(217, "Fahrräder & Zubehör")])

    def test_category_index(self):
        markup = '<a href="/s-fahrraeder/c217">Fahrr&auml;der</a><a href="/s-autos/c216">Autos</a>'
        self.assertEqual(parser.parse_category_index(markup), [(216, "Autos"), (217, "Fahrräder")])


class DomTest(unittest.TestCase):
    def test_script_content_is_ignored(self):
        root = parser.parse_dom("<div><script>var x = 'boom';</script>text</div>")
        self.assertEqual(root.text(), "text")

    def test_direct_text_excludes_children(self):
        root = parser.parse_dom("<p>outer<span>inner</span></p>")
        paragraph = root.find("p")
        self.assertEqual(paragraph.direct_text(), "outer")
        self.assertEqual(paragraph.text(), "outerinner")

    def test_unclosed_tags_do_not_break_nesting(self):
        root = parser.parse_dom('<ul><li class="a">one<li class="b">two</ul>')
        self.assertEqual(len(root.find_all("li")), 2)


if __name__ == "__main__":
    unittest.main()
