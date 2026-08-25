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

`off-route` reads *"12 km off the road, at kilometre 1 of the trip"*, and the list is ordered by
that second number - so it doubles as a pick-up plan for the drive.

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
2. The polyline is covered with overlapping search circles of `--corridor` radius, and each circle
   centre is mapped to the postcode Kleinanzeigen knows for that spot.
3. Every circle is searched with your filters, results are merged and de-duplicated by ad id.
4. Every ad's postcode is turned back into coordinates (bundled offline table) to compute its real
   distance to the road; anything further than `--corridor` is dropped.
5. What remains is scored and sorted by position along the trip.

A 250 km route at `--corridor 20 --pages 1` costs about 17 requests and 45 seconds.

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
`--type {angebote,gesuche}` `--sort {neueste,preis,entfernung}` `--shipping` `--pages`
`--include-sponsored` `--no-score` `--min-score` `--format` `--limit` `-o`
`--filter SEGMENT` (any extra filter copied from a browser URL, e.g. `--filter zustand:neu`).

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
* Driving routes: [OSRM demo server](https://router.project-osrm.org/) - light use only; point
  `--osrm-url` at your own instance for heavy use (see `routes.OSRM_URL`).

## Tests

```bash
cd kleinanzeigen
python3 -m unittest discover -s tests -t .   # 130 tests, no network access
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
```
