import unittest

from kleinanzeigen_search import geo
from kleinanzeigen_search.filters import SearchFilters
from kleinanzeigen_search.locations import Location, LocationResolver
from kleinanzeigen_search.routes import Route
from kleinanzeigen_search.search import (
    MAX_PAGE, AreaSearch, RequestBudget, fetch_next_page, run_areas, search_city, search_route,
)
from tests.fakes import PagedFakeClient

KOELN = (50.938, 6.959)
BONN = (50.735, 7.100)


def area(location_id: int, label: str = "area") -> AreaSearch:
    return AreaSearch(label, SearchFilters(query="Gitarre", location_id=location_id, radius_km=20, ad_type=None))


class BudgetTest(unittest.TestCase):
    def test_unbounded_budget_never_exhausts(self):
        budget = RequestBudget()
        budget.spent = 10_000
        self.assertFalse(budget.exhausted)
        self.assertIsNone(budget.remaining)

    def test_bounded_budget(self):
        budget = RequestBudget(3)
        budget.spent = 3
        self.assertTrue(budget.exhausted)
        self.assertEqual(budget.remaining, 0)


class AreaPagingTest(unittest.TestCase):
    def test_pages_accumulate_without_duplicates(self):
        client = PagedFakeClient({7: 60})
        a = area(7)
        for _ in range(3):
            fetch_next_page(client, a)
        self.assertEqual(len(a.listings), 60)
        self.assertEqual(a.pages, 3)
        self.assertTrue(a.exhausted)

    def test_short_page_ends_the_area(self):
        client = PagedFakeClient({7: 10})
        a = area(7)
        fetch_next_page(client, a)
        self.assertTrue(a.exhausted)
        self.assertEqual(a.deficit, 0)

    def test_deficit_tracks_unseen_ads(self):
        client = PagedFakeClient({7: 500})
        a = area(7)
        fetch_next_page(client, a)
        self.assertEqual(a.reported, 500)
        self.assertEqual(a.deficit, 475)

    def test_deficit_capped_at_reachable_pages(self):
        client = PagedFakeClient({7: 30_000}, reported={7: 30_000})
        a = area(7)
        fetch_next_page(client, a)
        self.assertEqual(a.deficit, MAX_PAGE * 25 - 25)

    def test_repeated_page_stops_the_area(self):
        client = PagedFakeClient({7: 200})
        a = area(7)
        fetch_next_page(client, a)
        a.pages = 0                      # force the same page to be served again
        fetch_next_page(client, a)
        self.assertTrue(a.exhausted)


class RunAreasTest(unittest.TestCase):
    def test_shallow_areas_are_exhausted_completely(self):
        client = PagedFakeClient({1: 10, 2: 20})
        areas = [area(1, "a"), area(2, "b")]
        run_areas(client, areas, initial_pages=1)
        self.assertTrue(all(a.exhausted for a in areas))
        self.assertEqual([len(a.listings) for a in areas], [10, 20])

    def test_without_a_budget_only_the_opening_pages_are_fetched(self):
        # No budget means no deepening: a huge area must not silently turn into
        # hundreds of requests.
        client = PagedFakeClient({1: 5000, 2: 5000})
        run_areas(client, [area(1, "a"), area(2, "b")], initial_pages=2)
        self.assertEqual(len(client.page_requests), 4)

    def test_budget_goes_to_the_area_hiding_the_most(self):
        # 'thin' is done after one page; every extra request must go to 'dense'.
        client = PagedFakeClient({1: 30, 2: 2000})
        areas = [area(1, "thin"), area(2, "dense")]
        run_areas(client, areas, initial_pages=1, budget=RequestBudget(8))
        pages = {loc: sum(1 for l, _ in client.page_requests if l == loc) for loc in (1, 2)}
        # One opening page each, then all six remaining requests go to 'dense'.
        self.assertEqual(pages, {1: 1, 2: 7})
        self.assertGreater(len(areas[1].listings), len(areas[0].listings))

    def test_budget_is_shared_and_respected(self):
        client = PagedFakeClient({1: 5000, 2: 5000})
        budget = run_areas(client, [area(1, "a"), area(2, "b")], initial_pages=1, budget=RequestBudget(6))
        self.assertEqual(budget.spent, 6)
        self.assertEqual(len(client.page_requests), 6)

    def test_depth_alternates_between_equally_dense_areas(self):
        client = PagedFakeClient({1: 5000, 2: 5000})
        run_areas(client, [area(1, "a"), area(2, "b")], initial_pages=1, budget=RequestBudget(6))
        per_area = {loc: sum(1 for l, _ in client.page_requests if l == loc) for loc in (1, 2)}
        self.assertEqual(per_area[1], per_area[2])

    def test_a_small_budget_still_touches_every_area(self):
        client = PagedFakeClient({1: 900, 2: 900, 3: 900})
        areas = [area(1, "a"), area(2, "b"), area(3, "c")]
        run_areas(client, areas, initial_pages=3, budget=RequestBudget(3))
        self.assertEqual(sorted(loc for loc, _ in client.page_requests), [1, 2, 3])
        self.assertTrue(all(a.pages == 1 for a in areas))

    def test_areas_never_reached_are_reported(self):
        client = PagedFakeClient({1: 900, 2: 900, 3: 900})
        warnings: list[str] = []
        run_areas(client, [area(1, "a"), area(2, "b"), area(3, "c")],
                  initial_pages=1, budget=RequestBudget(2), warnings=warnings)
        self.assertTrue(any("were searched at all" in w for w in warnings), warnings)

    def test_budget_shortfall_is_reported(self):
        client = PagedFakeClient({1: 5000})
        warnings: list[str] = []
        run_areas(client, [area(1, "a")], initial_pages=1, budget=RequestBudget(2), warnings=warnings)
        self.assertTrue(any("--budget" in w for w in warnings))

    def test_no_warning_when_everything_was_seen(self):
        client = PagedFakeClient({1: 12})
        warnings: list[str] = []
        run_areas(client, [area(1, "a")], initial_pages=1, budget=RequestBudget(20), warnings=warnings)
        self.assertEqual(warnings, [])


class SearchIntegrationTest(unittest.TestCase):
    def test_city_search_pages_until_exhausted(self):
        client = PagedFakeClient({945: 80})
        result = search_city(client, SearchFilters(query="Gitarre", location_id=945, radius_km=20, ad_type=None),
                             Location(945, "Köln", "50667", KOELN), max_pages=1, budget=20)
        self.assertEqual(len(result.listings), 80)
        self.assertEqual(result.summary()["areas_truncated"], 0)
        self.assertEqual(result.coverage_warnings(), [])

    def test_city_search_respects_the_budget_and_says_so(self):
        client = PagedFakeClient({945: 4000})
        result = search_city(client, SearchFilters(query="Gitarre", location_id=945, radius_km=20, ad_type=None),
                             Location(945, "Köln", "50667", KOELN), max_pages=1, budget=3)
        self.assertEqual(len(result.listings), 75)
        self.assertTrue(any("--budget" in w for w in result.warnings))
        self.assertEqual(result.summary()["areas_truncated"], 1)

    def test_route_search_spends_its_budget_across_areas(self):
        client = PagedFakeClient({}, reported={})
        resolver = LocationResolver(client)
        route = Route(geo.sample_route([KOELN, BONN], 5.0), ["Köln", "Bonn"])
        result = search_route(client, resolver, SearchFilters(query="Gitarre", ad_type=None), route,
                              corridor_km=20, max_pages=1, budget=5, drive_time=False)
        page_requests = [r for r in client.page_requests]
        self.assertLessEqual(len(page_requests), 5)
        self.assertTrue(result.coverage)


if __name__ == "__main__":
    unittest.main()
