# Kleinanzeigen Route Searcher

Search [kleinanzeigen.de](https://www.kleinanzeigen.de) (formerly eBay Kleinanzeigen) in two ways:

* **Mode A - city search:** a place, a radius and the site's standard filters (price, category,
  private/commercial, offers/wanted, shipping, sorting).
* **Mode B - route search:** paste a Google Maps route and get everything for sale within *N* km
  of the road you are driving anyway - sorted by where along the trip it sits.

Both modes rank the hits with a **deal score**: how each price compares to what similar ads in the
same result set are asking, plus the usual red flags (defective, wanted ad, reserved, too good to
be true).

Pure Python 3.10+, **no third-party packages, no API keys**.

```
cd kleinanzeigen
python3 -m kleinanzeigen_search city  "Bosch GSR 18V" --in Köln --radius 20 --max-price 150
python3 -m kleinanzeigen_search route "Rennrad" --maps-url "https://maps.app.goo.gl/…" --corridor 15
```

---

## Mode A - around one place

```bash
python3 -m kleinanzeigen_search city "E-Bike" \
    --in "Köln" --radius 20 \
    --min-price 300 --max-price 1200 \
    --seller privat --sort preis \
    --pages 3 --format details
```

```
#   score  price       ref    location                dist  age    title
--  -----  ----------  -----  ----------------------  ----  -----  ---------------------------------
1   100    10 €        35 €   50676 Köln Altstadt     1 km  18d    Bosch Akkuschrauber
2   83     25 € VB     35 €   51103 Kalk              4 km  31d    Bosch Akku Ladegerät
3   78     8 €         35 €   51065 Mülheim           4 km  8d     Bosch Akkuschrauber *Defekt*
```

`--in` accepts a city (`Köln`), a district (`Köln Ehrenfeld`) or a postcode (`50667`). Ambiguous
names are resolved against the site's own location index - use `where` to see the alternatives:

```bash
python3 -m kleinanzeigen_search where "Frankfurt"
    7950  Frankfurt (Oder) - Brandenburg
    4292  Frankfurt am Main - Hessen
```

## Mode B - along a driving route

```bash
python3 -m kleinanzeigen_search route "Werkbank" \
    --maps-url "https://www.google.com/maps/dir/Köln/Kassel/" \
    --corridor 20 --max-price 300 \
    --format html -o unterwegs.html
```

```
route: Köln → Kassel (247 km, ~2.6 h, via osrm)
#   score  price    ref   location              off-route  age    title
--  -----  -------  ----  --------------------  ---------  -----  ------------------------------
3   100    12 €     30 €  50259 Pulheim         13 km @0   3d     WERKBANK SPANNTISCH
4   79     1 € VB   30 €  50321 Brühl           12 km @1   today  Werkbank aus Europaletten
```

```
#   score  price    ref   location         detour   off-road  at km  age    title
--  -----  -------  ----  ---------------  -------  --------  -----  -----  --------------------------
3   100    12 €     30 €  50259 Pulheim    +9 min   13 km     0      3d     WERKBANK SPANNTISCH
4   79     1 € VB   30 €  50321 Brühl      +7 min   12 km     1      today  Werkbank aus Europaletten
```

`detour` is the number a navigation app shows next to a suggested stop: the extra driving time
for going *A → this ad → B* rather than straight from A to B. `off-road` is the straight-line
distance to the road, and `at km` is how far into the trip it sits - so the list doubles as a
pick-up plan for the drive. Cap the detour with `--max-detour-min 10`; that is usually what you
actually mean, since 10 km of Autobahn is not 10 km of Landstraße.

Four ways to hand over the route (pick one):

| Flag | Input |
| --- | --- |
| `--maps-url` | a Google Maps directions link, long or `maps.app.goo.gl` short form |
| `--waypoints` | `"Köln; Kassel; Berlin"` - stops separated by `;` |
| `--gpx` | a GPX track exported from any navigation app |
| `--polyline` | an encoded polyline (Google Directions API or OSRM) |

**Getting the link out of Google Maps:** plan the trip, then *Share → Copy link*. Both the short
link and the long `/maps/dir/A/B/@…` URL work; the stops are read from the link, geocoded with
OpenStreetMap and turned into a real driving route with the public OSRM router.

### How the route search works

1. The driving route comes back as a polyline (~250 km ≈ 60 points after smoothing).
2. The polyline is covered with overlapping search circles whose radius is the smallest the site
   offers that still covers `--corridor`, and each circle centre is mapped to the postcode
   Kleinanzeigen knows for that spot.
3. Every circle is searched with your filters. Each area is paged **adaptively** - see below.
4. Results are merged and de-duplicated by ad id.
5. Every ad's postcode is turned back into coordinates (bundled offline table) to compute its
   distance to the road; anything further than `--corridor` is dropped.
6. The survivors are grouped by postcode and priced through OSRM's duration matrix in one request,
   giving each ad its real added driving time.
7. What remains is scored and sorted by position along the trip.

A 250 km route at `--corridor 20 --pages 1` costs about 18 requests and 45 seconds.

### Depth, coverage and `--budget`

Results come back **newest first**, so an area holding more ads than you page through hides its
whole back catalogue - which is exactly where the bargains are. A single 20 km circle around a city
can report 600 hits; two pages is the newest 8 % of it.

So depth is bought, not fixed:

* `--pages N` fetches N pages from *every* area up front, for breadth.
* `--budget N` caps the total number of result-page requests. Whatever is left after the first pass
  is spent on the area still hiding the most ads, one page at a time, until every area is exhausted
  or the budget runs out. A dense town gets depth; an empty stretch of countryside does not.
* Either way the run reports what it actually saw, and says so when it fell short:

```
189 ads · median 100 € · 5 search area(s) · 10 pages · 17 requests · 3 area(s) not fully seen
warning: only saw part of the inventory in 3 of 5 areas: Nürnberg (50 of 633), Fürth (50 of 190)
  - raise --pages/--budget, narrow the search term, or shrink --corridor for smaller areas
```

Without `--budget` the areas are simply paged `--pages` deep, exactly as before - deepening is
opt-in, so a long route can never quietly turn into hundreds of requests.

It is worth the requests. The same route (Pfaffenhofen a.d. Ilm → Nürnberg Hbf, "Gitarre" in
Musikinstrumente, 20 km corridor) measured against the live site:

| run | pages | requests | ads found | coverage |
| --- | --- | --- | --- | --- |
| `--pages 2` (no deepening) | 10 | 17 | 189 | 3 of 5 areas truncated |
| every area paged to exhaustion | 61 | 62 | **996** | complete |

The extra 807 ads were not further away - they were simply older than the newest 50 in each area.

Filtering at the source is what makes full coverage cheap, because it shrinks each area's inventory
before paging starts. The same route with `--min-price 150 --max-price 600 --pages 1 --budget 80`
reached **complete coverage of all five areas in 23 requests**, returning 318 ads in that price
band - fewer requests than the unfiltered shallow run, and nothing left unseen.

## Watching a route on a schedule

Ads on this market do not vanish quickly - the median listing has been online about a
month and plenty sit for over a year - so "it disappeared" is a weak signal. What moves is
**price**. A watch runs the same searches twice a day and reports only what changed: ads never
seen before, ads whose price fell, and ads that vanished from an area that was **fully covered**
(a partially paged area never claims an ad is gone - it may simply not have been reached).

```bash
cp watches.example.json watches.json     # edit routes, keywords, price band
python3 -m kleinanzeigen_search watch --config watches.json --slot morning
```

The config lists a home anchor, a keyword slot per run, and the routes to sweep:

```json
{
  "home": "85276 Pfaffenhofen a.d. Ilm",
  "slots": { "morning": ["Konzertgitarre", "Westerngitarre"],
             "evening": ["Akustikgitarre", "Klassikgitarre"] },
  "routes": [ { "name": "wolnzach", "to": "Wolnzach, Bayern" } ]
}
```

Anchor on a town or postcode rather than a street address - house-level precision changes the
detour by seconds, and this file usually ends up in version control. With `--slot` omitted the
run picks `morning` before noon and `evening` after, so the same command works in both cron
entries. State lives in `state_file` (default `~/.local/state/kleinanzeigen_search/watches.json`);
`--dry-run` reports without recording, which is how you test a config change.

Twice a day, 8:00 and 18:00:

```cron
0 8  * * * cd /path/to/kleinanzeigen && python3 -m kleinanzeigen_search watch --config watches.json >> ~/guitar-watch.log 2>&1
0 18 * * * cd /path/to/kleinanzeigen && python3 -m kleinanzeigen_search watch --config watches.json >> ~/guitar-watch.log 2>&1
```

A quiet run prints one line, so an empty digest costs nothing to read.

## Output formats

| `--format` | What you get |
| --- | --- |
| `table` (default) | aligned terminal table |
| `details` | one block per ad with the score reasons and the link |
| `json` | full data incl. coordinates, route and summary |
| `csv` | spreadsheet-friendly |
| `html` | **interactive Leaflet map**: the route as a line, ads as colour-coded pins |

`-o FILE` writes to a file, `--limit N` shortens the list, `--min-score N` keeps only the good ones.

## Deal scoring

There is no external price database - the reference is what **comparable ads in the same result
set** are asking, which is what you would do by hand:

* comparables = ads whose titles overlap yours (fallback: the whole result set, with the score
  damped towards neutral and the reason spelled out);
* the reference price is the trimmed median of those, so a single `1 €` or `999.999 €` ad cannot
  drag it around;
* **50 = the going rate**, 100 = half the going rate, 0 = double it, damped when the sample is small;
* modifiers: already reduced, posted today, negotiable, long online, sealed/boxed, receipt/warranty;
* penalties: defective / for parts, wanted ad, reserved, replica, rental - and *"far below
  everything else"*, which is a scam warning rather than a compliment.

The score is only as good as the query: `"Rennrad"` compares saddles with carbon frames, while
`"Canyon Ultimate CF SL 7 Größe M"` gives you a real verdict. Narrow the query, then trust it.

## All options

```
python3 -m kleinanzeigen_search {city,route,where,categories} --help
```

Shared filters: `--min-price` `--max-price` `--category-id` `--seller {privat,gewerblich}`
`--type {angebote,gesuche}` `--sort {neueste,preis,entfernung}` `--shipping` `--pages` `--budget`
`--include-sponsored` `--no-score` `--min-score` `--format` `--limit` `-o`
`--filter SEGMENT` (any extra filter copied from a browser URL, e.g. `--filter zustand:neu`).

Route only: `--corridor` `--max-detour-min` `--no-drive-time` `--max-areas` `--keep-unlocated`
`--osrm-url`.

Category ids:

```bash
python3 -m kleinanzeigen_search categories --for "E-Bike"   # what the site suggests for a term
python3 -m kleinanzeigen_search categories                  # the whole category index
```

Politeness knobs: `--delay` (seconds between requests, default 2), `--no-cache`, `--cache-ttl`.
Responses are cached under `~/.cache/kleinanzeigen_search` for 15 minutes so repeated runs are free.

## Notes and limits

* **Sponsored ads are dropped** by default: "TOP" ads ignore the location filter, so keeping them
  would put Bavarian bikes into a Cologne route. `--include-sponsored` keeps them.
* **Location precision is the postcode**, not the street - listings only expose a postcode. A hit
  can therefore be a few km off the reported detour.
* There is no public API. The tool reads the normal search pages, throttles every request and backs
  off when the site answers `403`. Keep `--delay` at 2 s or more, and keep it to personal use -
  hammering the site violates its terms of service and will get you blocked.
* Site markup changes break scrapers. If results ever come back empty, run with `-v` and check
  `tests/fixtures/search_page.html` against a fresh page.

## Data sources

* Postcode coordinates: [GeoNames](https://www.geonames.org/) postal codes, CC BY 4.0
  (bundled as `kleinanzeigen_search/data/plz_de.csv.gz`, rebuild with `tools/build_plz_table.py`).
* Geocoding: [OpenStreetMap Nominatim](https://nominatim.openstreetmap.org/) - ODbL.
* Driving routes and detour times: [OSRM demo server](https://router.project-osrm.org/) - light use
  only; point `--osrm-url` at your own instance for heavy use.

## Tests

```bash
cd kleinanzeigen
python3 -m unittest discover -s tests -t .   # 198 tests, no network access
```

The HTML fixture is a trimmed real result page, so parsing regressions surface immediately.

## Layout

```
kleinanzeigen_search/
  cli.py         command line interface
  filters.py     search filters -> kleinanzeigen.de URLs
  client.py      throttled HTTP client with retries and disk cache
  parser.py      result page -> Listing objects
  locations.py   postcode table, location ids, geocoding
  routes.py      Google Maps links, GPX, polylines, OSRM
  geo.py         distances, polylines, route sampling and coverage
  search.py      city search and route search
  deals.py       price statistics and deal scoring
  report.py      table / details / json / csv / Leaflet map
  watch.py       scheduled watches: snapshot, diff, digest
```
