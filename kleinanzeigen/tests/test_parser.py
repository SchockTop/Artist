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

    def test_result_total_with_a_category_filter(self):
        # Picking a category changes the noun: "633 Musikinstrumente".
        markup = ('<span class="breadcrump-summary">1 - 25 von 633 Musikinstrumente '
                  'f&uuml;r &#8222;gitarre&#8220; in N&uuml;rnberg</span>')
        self.assertEqual(parser.parse_result_total(markup), 633)

    def test_result_total_when_nothing_matched(self):
        markup = '<span class="breadcrump-summary">Es wurden keine Ergebnisse gefunden.</span>'
        self.assertEqual(parser.parse_result_total(markup), 0)

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


MODERN = """<html><body>
<div>1 - 25 von 62 Musikinstrumente f&uuml;r &#8222;gitarre&#8220; in 85276 Pfaffenhofen</div>
<ul id="srchrslt-adtable">
<li data-clickable="card" class="relative mb-xsmall">
  <article class="flex justify-between p-medium" data-adid="3500000001"
           data-href="/s-anzeige/testgitarre/3500000001-74-7191">
    <div><img class="size-full" src="https://img.invalid/a.jpg" alt="Testgitarre Bayern - Wolnzach Vorschau"></div>
    <div><div><div><span>85283 Wolnzach</span><span class="ml-xsmall">(ca. 12 km)</span></div>
    <div><span>Heute, 09:18</span></div></div>
    <div class="flex flex-col">
      <h3 class="text-base"><a class="x" href="/s-anzeige/testgitarre/3500000001-74-7191">Yamaha Testgitarre 4/4</a></h3>
      <p class="text-sm">Eine sehr sch&ouml;ne Gitarre mit Tasche und Zubeh&ouml;r zu verkaufen...</p>
      <div class="flex"><p class="text-base">250 &euro; VB</p></div>
    </div></div>
  </article>
</li>
</ul>
<script>props="{&quot;id&quot;:[0,3500000001],&quot;x&quot;:[0,1],&quot;topAd&quot;:[0,true]}"</script>
</body></html>"""


class ModernLayoutTest(unittest.TestCase):
    """The 2026 redesign: Tailwind classes, <article data-adid>, no .aditem."""

    def setUp(self):
        [self.listing] = parser.parse_listings(MODERN)

    def test_identity_and_link(self):
        self.assertEqual(self.listing.ad_id, "3500000001")
        self.assertEqual(self.listing.url,
                         "https://www.kleinanzeigen.de/s-anzeige/testgitarre/3500000001-74-7191")

    def test_title_and_description(self):
        self.assertEqual(self.listing.title, "Yamaha Testgitarre 4/4")
        self.assertIn("schöne Gitarre", self.listing.description)

    def test_price(self):
        self.assertEqual((self.listing.price_eur, self.listing.price_type), (250, "vb"))

    def test_location_and_date(self):
        self.assertEqual((self.listing.plz, self.listing.ort), ("85283", "Wolnzach"))
        self.assertEqual(self.listing.posted_raw, "Heute, 09:18")
        self.assertIsNotNone(self.listing.posted_at)

    def test_sponsored_flag_from_the_props_payload(self):
        self.assertTrue(self.listing.sponsored)

    def test_result_total_without_breadcrump(self):
        self.assertEqual(parser.parse_result_total(MODERN), 62)

    def test_old_layout_still_parsed(self):
        self.assertEqual(len(parser.parse_listings(MARKUP)), 4)
