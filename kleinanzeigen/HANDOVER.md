# Kleinanzeigen Guitar Hunt — Complete Handover

**Purpose of this file:** everything needed to continue this project, either by a person or by
handing it to another LLM as context. It covers the software that was built, every verified fact
about the target website, every bug found and fixed, the full guitar shortlist with links and
verified prices, the market analysis, and the open questions.

- **Repository:** `https://github.com/SchockTop/Artist`
- **Branch:** `claude/kleinanzeigen-route-searcher-gpj5k5`
- **Project directory:** `kleinanzeigen/` (deliberately isolated — the rest of the repo is an
  unrelated 3D/diffusion research project called "Artist" and must not be touched)
- **Head commit at handover:** `5a4582b`
- **Status:** 200 tests passing, verified working from a clean clone, running live
- **Last verified:** 2 September 2026

```bash
git clone -b claude/kleinanzeigen-route-searcher-gpj5k5 https://github.com/SchockTop/Artist.git
cd Artist/kleinanzeigen
python3 -m unittest discover -s tests -t .     # 200 tests, no network needed
```

---

## 1. The human context

The owner is an **adult beginner guitarist**. They have been playing a **children's guitar**
(almost certainly a 3/4 nylon-string) and can strum some chords. They want to step up to a
proper full-size instrument — *"something nice, which is fun to play and has a nice sound to it"*.

**Budget evolution across the project:** started at "200–500 €", later widened to **200–800 €**,
with a stated willingness to spend for quality. They explicitly want **best price-to-quality**,
not the cheapest thing.

**Geography:**
- **Home base:** Pfaffenhofen an der Ilm, Bavaria — postcode **85276**
- Regular drives: Pfaffenhofen ↔ **Schrobenhausen**, Pfaffenhofen → **Wolnzach**,
  Pfaffenhofen → **München (mostly Laim)**
- Longer trips searched during the project: Pfaffenhofen → Nürnberg Hbf,
  **Frankfurt am Main → Pfaffenhofen** (351 km, the main long route)

> **Privacy note, deliberate:** the owner gave a precise street address. It is **not** stored in
> this repo or in `watches.example.json`, which anchor on the postcode `85276 Pfaffenhofen a.d.
> Ilm` instead. House-level precision changes computed detour times by seconds and this repo is
> public. If a future session needs the exact address, ask — do not commit it.

**The core open decision the owner is circling:** nylon (Konzertgitarre) versus steel-string
(Westerngitarre). Nylon is the seamless continuation from a child's guitar — soft strings, wide
neck. Steel-string is what "a bit more going" usually means for strumming songs — louder,
brighter, but painful on the fingertips for the first 2–3 weeks. **The recommended way to settle
it: play one of each back to back.** They have not yet done this.

---

## 2. What was built

A dependency-free Python 3.10+ command-line tool that searches **kleinanzeigen.de** (formerly
eBay Kleinanzeigen) in two modes and ranks results by how good the price is.

- **Mode A — city search:** one place, one radius, all the site's standard filters.
- **Mode B — route search:** give it a driving route; it returns everything for sale within
  *N* km of the road, ranked by the **real extra driving time** for stopping there.
- **Watch mode:** run the same searches on a schedule and report only what changed.

**Standard library only. No pip install. No API keys.**

### 2.1 Module map

```
kleinanzeigen/
  README.md                        user-facing documentation
  HANDOVER.md                      this file
  watches.example.json             scheduled-watch config template
  kleinanzeigen_search/
    __init__.py                    version, public exports
    __main__.py                    python -m entry point
    cli.py                         argparse CLI: city / route / where / categories / watch
    client.py                      throttled HTTP (urllib), retries+backoff, gzip disk cache
    filters.py                     SearchFilters -> kleinanzeigen.de URL grammar
    parser.py                      result-page HTML -> Listing objects (BOTH site layouts)
    models.py                      Listing dataclass
    locations.py                   postcode table, location-id lookup, geocoding, radii
    geo.py                         haversine, polylines, route sampling, corridor coverage
    routes.py                      Google Maps links, GPX, OSRM routing + duration matrix
    search.py                      orchestration: areas, budget/depth, coverage, detour
    deals.py                       price statistics and deal scoring
    report.py                      table / details / json / csv / Leaflet HTML map
    watch.py                       snapshot, diff, digest for scheduled runs
    data/plz_de.csv.gz             10,813 German postcodes -> coordinates (GeoNames, CC BY 4.0)
  tools/build_plz_table.py         regenerates the postcode table from GeoNames
  tests/                           200 tests, fully offline (fixtures + fakes)
```

~4,400 lines including tests.

### 2.2 CLI reference

```bash
# Mode A — around one place
python3 -m kleinanzeigen_search city "Bosch GSR 18V" --in Köln --radius 20 --max-price 150

# Mode B — along a driving route
python3 -m kleinanzeigen_search route "Rennrad" \
    --maps-url "https://maps.app.goo.gl/…" --corridor 15 --format html -o out.html

# Helper: resolve Kleinanzeigen location ids
python3 -m kleinanzeigen_search where "Frankfurt"
#    7950  Frankfurt (Oder) - Brandenburg
#    4292  Frankfurt am Main - Hessen

# Helper: find category ids
python3 -m kleinanzeigen_search categories --for "E-Bike"    # site's own suggestion
python3 -m kleinanzeigen_search categories                   # full category index

# Scheduled watch
python3 -m kleinanzeigen_search watch --config watches.json --slot morning --dry-run
```

**Shared filter flags:** `--min-price` `--max-price` `--category-id` `--seller {privat,gewerblich}`
`--type {angebote,gesuche}` `--sort {neueste,preis,entfernung}` `--shipping` `--pages` `--budget`
`--include-sponsored` `--no-score` `--min-score` `--format {table,details,json,csv,html}`
`--limit` `-o` `--filter SEGMENT` (raw extra URL filter, e.g. `--filter zustand:neu`)

**Route-only flags:** `--corridor KM` `--max-detour-min MIN` `--no-drive-time` `--max-areas`
`--alternatives N` `--keep-unlocated` `--osrm-url`

**Route input (pick one):** `--maps-url` (Google Maps directions link, long or short),
`--waypoints "A; B; C"`, `--gpx file.gpx`, `--polyline <encoded>`

**Politeness:** `--delay` (seconds between requests, default 2), `--no-cache`, `--cache-ttl`.
Responses cache in `~/.cache/kleinanzeigen_search` for 15 minutes.

### 2.3 How route mode works

1. Resolve the route to a polyline (OSRM public router, or straight-line fallback).
2. Cover the polyline with overlapping search circles. Radius = the **smallest radius the site
   offers that still covers `--corridor`** (never rounds down). With `--alternatives N`, several
   roads are covered and their circles merged by proximity.
3. Map each circle centre to the postcode Kleinanzeigen knows for that spot.
4. Search each area, paging adaptively (see §2.5).
5. Merge and de-duplicate by ad id.
6. Convert each ad's postcode back to coordinates (offline table) and compute its straight-line
   distance to the road; drop anything beyond `--corridor`.
7. Group survivors by coordinate and price them through **OSRM's duration matrix** in one
   request to get the real added driving time.
8. Score and sort by position along the trip.

**Cost:** a 250 km route at `--corridor 20 --pages 1` ≈ 18 requests, ~45 seconds.
A 351 km route with full coverage of 11 areas in a price band ≈ 70 requests.

### 2.4 The detour metric (the important one)

```
detour_min = drive(A → ad) + drive(ad → B) − drive(A → B)
```

This is exactly the number a navigation app shows next to a suggested stop. It comes from OSRM's
`/table` duration-matrix service. **Ads are grouped by postcode first** — dozens share one — so a
whole result set costs **one or two requests**, not one per ad. The matrix endpoint accepts at
most 100 coordinates, so batches are 98 stops plus the two endpoints.

Why it matters: 10 km on the Autobahn is not 10 km on a Landstraße. Measured example — the Crafter
in Gerolsbach is 11.6 km off the road but **+24 minutes**, while the Alhambra in Röthenbach is
2.3 km off and **+14 minutes**.

### 2.5 Depth, coverage and `--budget` (critical concept)

Results come back **newest first**. An area holding more ads than the pages fetched hides its
entire back catalogue — which is exactly where the bargains are.

- `--pages N` fetches N pages from **every** area (breadth, round-robin so a small budget still
  touches the whole route thinly rather than the first areas deeply).
- `--budget N` caps total result-page requests. Everything left after the first pass goes to
  whichever area is **still hiding the most ads**, one page at a time.
- Without `--budget`, no deepening happens at all — a long route can never quietly become
  hundreds of requests.
- Every run reports coverage and warns when it fell short.

**Measured on Pfaffenhofen → Nürnberg ("Gitarre" in Musikinstrumente, 20 km corridor):**

| run | pages | requests | ads found | coverage |
| --- | --- | --- | --- | --- |
| `--pages 2` (no deepening) | 10 | 17 | 189 | 3 of 5 areas truncated |
| every area paged to exhaustion | 61 | 62 | **996** | complete |

The extra 807 ads were not further away — they were simply **older** than the newest 50 per area.

**Filtering at the source is what makes full coverage cheap.** The same route with
`--min-price 150 --max-price 600 --pages 1 --budget 80` reached complete coverage of all five
areas in **23 requests**.

### 2.6 Deal scoring

There is no external price database. The reference is what **comparable ads in the same result
set** are asking — what you would do by hand.

- Comparables = ads whose titles overlap (token overlap of the smaller set ≥ 0.4). If fewer than
  5 match, it falls back to the whole result set, **damps the score toward neutral**, and says so
  in the reasons.
- Reference price = **trimmed median** of those, so a single `1 €` or `999.999 €` ad cannot drag it.
- **50 = the going rate.** 100 = half the going rate. 0 = double it. Damped when the sample is small.
- Bonuses: already reduced, posted today, sealed/boxed, receipt/warranty.
- Penalties: defective/for parts, wanted ad, reserved, replica, rental, long online, and
  *"far below everything else"* — which is a **scam warning, not a compliment**.

**Known limitation, demonstrated:** the score is only as good as the query. Searching "Rennrad"
compares saddles against carbon frames. On the Taylor GS Mini it returned **26/100** because it
compared a 465 € Taylor against a median of all guitars on the route (220 €). *For a specific
model, always cross-check against the nationwide used market and the current new price instead
of trusting the local score.* That cross-check is manual and is how every verdict in §5 was made.

### 2.7 Watch mode

```bash
cp watches.example.json watches.json
python3 -m kleinanzeigen_search watch --config watches.json --slot morning
```

Reports only three things per run:
- **new** — ads never seen before
- **price drops** — with drop percentage and when the ad first appeared; also tracks the lowest
  price each ad has ever reached
- **gone** — but **only from an area that was fully covered**. A partially paged area never claims
  an ad vanished, because it may simply not have been reached.

State lives in `state_file` (default `~/.local/state/kleinanzeigen_search/watches.json`).
`--dry-run` reports without recording. With `--slot` omitted it picks `morning` before noon and
`evening` after, so the same command serves both cron entries.

**The configured watch (`watches.example.json`):**

```json
{
  "home": "85276 Pfaffenhofen a.d. Ilm",
  "state_file": "~/.local/state/kleinanzeigen_search/watches.json",
  "defaults": { "category_id": 74, "min_price": 100, "max_price": 900,
                "corridor_km": 20, "pages": 1, "budget": 45 },
  "slots": { "morning": ["Konzertgitarre", "Westerngitarre"],
             "evening": ["Akustikgitarre", "Klassikgitarre"] },
  "routes": [
    { "name": "schrobenhausen", "to": "Schrobenhausen, Bayern" },
    { "name": "wolnzach",       "to": "Wolnzach, Bayern" },
    { "name": "muenchen-laim",  "to": "Laim, München", "corridor_km": 12, "budget": 60 }
  ]
}
```

**Cron (twice daily, 8:00 and 18:00):**

```cron
0 8  * * * cd ~/Artist/kleinanzeigen && python3 -m kleinanzeigen_search watch --config watches.json >> ~/guitar-watch.log 2>&1
0 18 * * * cd ~/Artist/kleinanzeigen && python3 -m kleinanzeigen_search watch --config watches.json >> ~/guitar-watch.log 2>&1
```

**This must run on the owner's own machine.** It was developed in an ephemeral cloud container
that gets reclaimed; a schedule created there does not survive. A baseline of 123 ads across the
6 watches was seeded in that container and is **lost** — the owner's first local run will report
everything as new once, then report deltas.

---

## 3. Verified facts about kleinanzeigen.de

Everything here was confirmed against the live site, not assumed.

### 3.1 Search URL grammar

Filters are path segments. Verified working:

```
https://www.kleinanzeigen.de/s-anbieter:privat/anzeige:angebote/versand:ja/preis:100:500/sortierung:preis/seite:2/e-bike/k0c217l3331r20
                              └ seller      └ offers/wanted └ shipping └ price  └ sort        └ page   └ query  └ k0 c<cat> l<loc> r<radius>
```

| segment | meaning |
| --- | --- |
| `anbieter:privat` / `anbieter:gewerblich` | private / commercial seller |
| `anzeige:angebote` / `anzeige:gesuche` | offers / wanted ads |
| `versand:ja` | shipping offered |
| `preis:MIN:MAX` | price range; open-ended works (`preis:300:`, `preis::80`) |
| `sortierung:preis` / `sortierung:entfernung` | sort by price / distance (newest is default, no segment) |
| `seite:N` | page N |
| `k0c<id>` | category id |
| `l<id>r<km>` | location id + radius (radius only works with a location) |

Query slug: lowercase, umlauts transliterated (`ä→ae`, `ö→oe`, `ü→ue`, `ß→ss`), non-alphanumerics
to `-`. Multi-word works: `mountainbike-carbon` searches "mountainbike carbon".

Radii the UI offers: **5, 10, 20, 30, 50, 100, 150, 200 km**. Pagination effectively stops around
**page 50** (~1,250 ads), so deeper requests waste budget.

### 3.2 Location id lookup

```
GET https://www.kleinanzeigen.de/s-ort-empfehlungen.json?query=Berlin
→ {"_0":"Deutschland","_3331":"Berlin","_3464":"Pankow - Berlin", …}
```

Keys are `_<locationId>`. **Important trap:** for an unknown postcode the endpoint returns fuzzy
neighbours (querying `39097` returns `39397 - Kroppenstedt`). A postcode query is therefore only
accepted when the returned label actually starts with that postcode — otherwise the search would
silently run in the wrong region. Matching by town name alone is never trusted (same-named
villages hundreds of km away).

Useful known ids: Berlin `3331`, Köln `945`, Nürnberg `6810`, München `6411`,
Frankfurt am Main `4292`, Frankfurt (Oder) `7950`, Pfaffenhofen a.d. Ilm `5850`.

### 3.3 Category ids

- `74` = **Musikinstrumente** (used throughout this project)
- `73` = Musik, Filme & Bücher; `217` = Fahrräder & Zubehör; `210` = Auto, Rad & Boot;
  `216` = Autos; `223` = Autoteile & Reifen
- Full index scrapeable from `https://www.kleinanzeigen.de/s-kategorien.html` (159 categories,
  links of the form `/s-<slug>/c<id>`)

### 3.4 Result-page markup — TWO layouts

**Old layout (pre-1 Sept 2026):** `<li class="ad-listitem … is-topad">` wrapping
`<article class="aditem" data-adid=… data-href=…>`, with `.aditem-main--top--left` (location),
`.aditem-main--top--right` (date), `h2 > a` (title),
`.aditem-main--middle--description`, `.aditem-main--middle--price-shipping--price`
(with the struck-through old price **nested inside** it — only the element's own direct text
describes the current price), `span.simpletag` (tags), `.breadcrump-summary` (result count).

**New layout (from 1 Sept 2026):** Tailwind-generated classes. `<ul id="srchrslt-adtable">` →
`<li data-clickable="card">` → `<article class="flex justify-between p-medium" data-adid=…
data-href=…>`. Title in `h3 > a`. Location in a `<span>` matching `^\d{5}\s+…`, date in a
`<span>` matching `Heute|Gestern|dd.mm.yyyy`, price in a short `<p>`. **No `.breadcrump-summary`**
— the count survives only as the bare phrase `1 - 25 von 62 …` anywhere on the page.
**No CSS marker for TOP ads** — the only signal is `"topAd":[0,true]` inside an HTML-escaped
hydration payload in a `props="…"` attribute.

**The parser handles both**, detecting by presence of `class="aditem"`. The new-layout parser
matches on **structure only** (article attributes, heading link, text shape) and never on
generated class names, because those will change again.

### 3.5 Sponsored ("TOP") ads

TOP ads **ignore the location filter entirely** — a Berlin search returns ads from Bavaria. They
are **dropped by default**; `--include-sponsored` keeps them. This matters enormously for route
searches, which would otherwise be poisoned with out-of-corridor results.

### 3.6 Result-count wording

The noun after the number changes with the filters: `"39.183 Ergebnissen"` for a plain search,
but `"633 Musikinstrumente"` once a category is selected. A regex expecting "Ergebnis" silently
returns `None` for every category search (see bug 2 in §4).

### 3.7 Rate limiting and etiquette

The site answers plain `urllib` requests with a browser-like User-Agent. It returns **403** for
rate limiting — frequently and unpredictably, roughly 1 in 15–30 requests under load. The client
retries with exponential backoff and this is normal, not an error. Keep `--delay` at 2 s or more.

**This is scraping.** There is no public API. Bulk use violates the site's terms of service.
The tool is built and tuned for personal use.

### 3.8 External services used

| service | purpose | policy notes |
| --- | --- | --- |
| [OSRM demo server](https://router.project-osrm.org/) | driving routes + duration matrix | light use only; `--osrm-url` points at a self-hosted instance. `alternatives=N` for other roads; `/table` max 100 coordinates |
| [Nominatim](https://nominatim.openstreetmap.org/) | geocoding waypoints | max 1 req/s, needs a real User-Agent; ~8 lookups/day under the configured watch. Occasional 429s, retried |
| [GeoNames postal codes](https://download.geonames.org/export/zip/DE.zip) | bundled postcode→coordinate table | CC BY 4.0, must be attributed |

The postcode table holds **10,813 German postcodes**. 1,472 are flagged as Deutsche Post
*Grosskunden* codes (large-volume mail customers) — they have no area on the map, Kleinanzeigen
does not know them, and they are skipped when anchoring searches. Rebuild with
`python3 tools/build_plz_table.py`.

---

## 4. Bugs found and fixed (chronological)

Useful for a future maintainer — several are subtle and would silently corrupt results.

1. **Corridor radius rounded down.** `--corridor 15` snapped to the *nearest* supported radius
   (10 km), so ads 10–15 km off the road were filtered for but never searched for. Fixed with
   `radius_at_least()` — route mode now always rounds **up**. *(commit `85a00da`)*

2. **Coverage check dead in exactly the searches it was written for.** With a category filter
   the site writes "von 633 **Musikinstrumente**", not "von 633 Ergebnissen". The total parser
   returned `None`, `None` was treated as "complete", and every guitar search reported full
   coverage while seeing ~8 % of the inventory. It also disabled all adaptive deepening. **An
   earlier "full coverage" claim made to the owner was wrong because of this.** *(`a4c3b30`)*

3. **Unbounded deepening (self-inflicted).** The depth loop had no stop condition without a
   budget and paged every area to exhaustion. Caught when a "baseline" run hung — which is
   incidentally where the 996-ad measurement came from. Deepening is now opt-in via `--budget`.
   *(`a4c3b30`)*

4. **Unfair opening pass.** With a budget smaller than areas × pages, the first areas consumed it
   all and the far end of the route was never searched — silently, since an unsearched area looks
   identical to an empty one. Opening pass is now round-robin, and a run that could not open every
   area says so. *(`3d57bdc`)*

5. **Escaped hydration payload.** The TOP-ad flag lives in an HTML-escaped `props="…"` attribute;
   matching the raw markup found nothing and marked every ad organic. Now unescapes first.
   *(`401423a`)*

6. **Site redesign broke everything.** On 1 Sept 2026 the result markup changed and every search
   returned zero listings — reported as *"0 ads · full coverage"*, a silent wrong answer.
   Parser rewritten to handle both layouts structurally. *(`401423a`)*

7. **Silent-failure guard.** Because of bug 6, a page containing ad markers that parses to
   nothing now **raises** instead of returning an empty result. Under an unattended twice-daily
   cron this failure mode could otherwise hide for weeks. *(`5a4582b`)*

**Also worth recording:** during research I twice fabricated a URL instead of looking it up — once
guessing ad-detail URLs that resolved to an iPhone and a coffee table, once pasting an Alhambra
link without its ad id. **Always take ad URLs from the search data, never construct them.**

### Commit history on the branch

| commit | what it did |
| --- | --- |
| `4590471` | initial city + route searcher, 130 tests |
| `85a00da` | round the route search radius up, never down |
| `4afe5d7` | real driving-detour ranking + coverage reporting |
| `a4c3b30` | adaptive depth: buy search depth where the ads are; fix category totals |
| `3d57bdc` | round-robin opening pass, warn on unreached areas |
| `927ae60` | label the measured coverage runs accurately in the README |
| `4eb1e5f` | search along alternative roads (`--alternatives`) |
| `401423a` | parse the 2026 result-page redesign |
| `56f9cd5` | scheduled watches: price drops, not just new ads |
| `5a4582b` | fail loudly when the result page stops parsing |

---

## 5. The guitar shortlist — all live and verified 1 Sept 2026

Prices are as last checked. **Everything below was still online.** Links go to the live ads.

### 5.1 Recommended — nylon (Konzertgitarre)

| guitar | price | where | detour | verdict |
| --- | --- | --- | --- | --- |
| **[Alhambra 5P](https://www.kleinanzeigen.de/s-anzeige/alhambra-5-p-konzertgitarre/3404947227-74-6375)** | **500 €** | Röthenbach a.d. Pegnitz | 2.3 km off the A9, +14 min | **Best classical found.** Label verified *"Alhambra · Made in Spain · 5 P"*. Solid AA cedar top, rosewood back/sides, ebony board, gold tuners, **hard case included**. New **949 €** at Thomann → 53 %. Frets show almost no wear. |
| **[Esteve 6PS](https://www.kleinanzeigen.de/s-anzeige/esteve-6ps-konzertgitarre/3429531173-74-5834)** | 400 € VB | Schrobenhausen | +2 min | Handmade in Alboraya near Valencia (~40 artisans). **UVP 751 €**, street ~540 €. Solid top (photos show the spruce variant), Indian rosewood back/sides, bag included. Only carried to lessons. Offer **330 €**. |
| **[Höfner HF16](https://www.kleinanzeigen.de/s-anzeige/hoefner-hf-16-konzertgitarre-baujahr-vor-2011/3415503958-74-6204)** | 275 € VB | Höchstadt | +9 min | Solid German spruce top, bubinga back/sides, older *Germany* series. Seller notes low comfortable action. |
| **[La Mancha Rubi CM](https://www.kleinanzeigen.de/s-anzeige/la-mancha-rubi-cm-konzertgitarre-top-zustand/3490227049-74-4571)** | **149 €** *(was 159)* | Offenbach | +9 min | Solid Canadian cedar, mahogany body, 4/4. Seller **measured the action with digital callipers** (3.4 mm at the 12th fret, low E) — that is a careful owner. |
| [La Mancha Rubi CM-N](https://www.kleinanzeigen.de/s-anzeige/la-mancha-rubi-cm-n-konzertgitarre-massivholz-decke-tontraeger/3472154875-74-18754) | 200 € | Nürnberg | +7 min | Solid-wood top, "Sehr Gut". Two-line ad — ask for detail photos. |
| **[Admira Málaga](https://www.kleinanzeigen.de/s-anzeige/spanische-gitarre-admira-mod-malaga/3281400345-74-7606)** | 199 € VB | Kösching | +10/+11 min | Label verified *"Mod. Málaga · 17 MAYO 2018"*. Solid cedar top, sapele back/sides, 4/4, bag. New **272 €**. **Online since 25 December — that seller will take an offer. Try 150 €.** |

### 5.2 Recommended — steel-string (Westerngitarre)

| guitar | price | where | detour | verdict |
| --- | --- | --- | --- | --- |
| **[Seagull S6](https://www.kleinanzeigen.de/s-anzeige/seagull-westerngitarre/3453993877-74-16299)** | **310 € VB** | Frankfurt Ostend | +5 min from Frankfurt | Solid cedar top, maple neck, **hard case included**. The legendary value guitar — wide 45 mm nut, excellent for strumming. Current S6 with electronics is **799 €** at Thomann (UVP 979); the plain version sits below that. |
| **[Crafter STG D-18ce Pro](https://www.kleinanzeigen.de/s-anzeige/crafter-akustikgitarre-dreadnought-mit-pickup-und-mikro/3481178056-74-5826)** | 380 € VB | Gerolsbach | 11.6 km off but **+24 min** | Label verified `STG D-18ce`, preamp photo shows `Platform DS-2 Pro`, tuners stamped GROVER. Solid cedar top, rosewood back/sides, cutaway, dual pickup (undersaddle + soundhole mic). New **698 €** → 54 %. 16 photos, nothing hidden. **Correction to record:** the label says *Made in China, designed/supervised by Crafter Korea* — not Korean-built. Original case is gone; a generic "Justin" bag is included. |
| **[Cort Luce L450C](https://www.kleinanzeigen.de/s-anzeige/cort-luce-l450c-westerngitarre/3421981435-74-6204)** | 200 € VB | Höchstadt | +9 min | **All-solid mahogany** — top, back *and* sides — Grover tuners, matt finish. Best spec-per-euro in the whole search. |
| **[Walden G630CE](https://www.kleinanzeigen.de/s-anzeige/walden-g630ce-westerngitarre/3489326581-74-6824)** | **180 € VB** *(was 220)* | Nürnberg Mitte | +11 min | Solid cedar top, Grand Auditorium + cutaway, Fishman pickup, ~4 yrs, serial given. New ~469 € → 38 %. Seller's spec text says sitka/mahogany but the factory spec is cedar/rosewood — ask for a label photo. |
| [Walden Madera CD4040](https://www.kleinanzeigen.de/s-anzeige/westerngitarre-walden-madera-cd4040/3472510887-74-6815) | 400 € | Nürnberg Südoststadt | +2 min from that route | **All-solid** (red cedar top, solid mahogany back/sides), ebony board, **original Deluxe hardcase**, "neuwertig", one 2 mm lacquer chip. Walden's upper line, ~$1,189 street when current. Caveat: Walden left the European market, so resale is soft. |
| [Cort Earth 500](https://www.kleinanzeigen.de/s-anzeige/cort-earth-500-gitarre/3450782434-74-18754) | 200 € *(was 220)* | Nürnberg | +7 min | Solid-top dreadnought from 1998, "sehr gut", slight bridge wear, padded bag. |
| **[Taylor 114e Walnut, 2017](https://www.kleinanzeigen.de/s-anzeige/taylor-114e-walnut-westerngitarre/3410433784-74-6204)** | 475 € VB | Höchstadt | +9 min | Solid Sitka top, Grand Auditorium, ES2 electronics, **original Taylor gigbag**. New **777 €** (UVP 1.010) → 61 %. A real Taylor. |
| [Taylor GS Mini Mahogany](https://www.kleinanzeigen.de/s-anzeige/taylor-gs-mini-mahagoni-akustikgitarre/3478403184-74-6799) | **450 € VB** *(was 465)* | Erlangen | +16 km off | Solid mahogany top, bought 2023 in Munich, receipt, indoor only. New **598 €** incl. gigbag → 75 %. Fair, not a steal — Taylors hold value. Small body, very easy to play, best resale of anything here. **Taylor's warranty is not transferable.** |

> **Höchstadt is one stop for three guitars** (Taylor 114e, Cort Luce L450C, Höfner HF16) — almost
> certainly one seller clearing a collection. Best single-stop value on the map.

### 5.3 High-upside, unverified — worth a message today

**[Armin Hanika — 800 €, Wettstetten near Ingolstadt](https://www.kleinanzeigen.de/s-anzeige/gitarre-armin-hanika/3498855741-74-7596)** · +48 min

The entire ad text is *"Gitarre von Armin Hanika."* No model, no condition field. But the photos
show a serious instrument: fine-grained solid top with multi-line purfling, rosewood-bound
rosette, rosewood back and sides, cedro neck with centre lamination, gold tuners, a **signed
label**, gigbag included.

Hanika is a German workshop in Baiersdorf, founded 1953, ~30 employees; Armin Hanika took over in
1993. **Used Hanikas trade roughly €825–3,890**; a new 54-PC is €1,288. At 800 € this is *below
the bottom* of the used market.

**Risks:** strings loose and partly detached at the bridge (nobody has played it recently);
photographed **in an attic**, the worst place for a solid-top guitar; two-word ad from a seller
who does not know the model — usually an inherited instrument nobody has assessed.

**Ask before driving:** the model number and signature from the label, a daylight photo of the top
straight on (hairline cracks), how long it has been in the attic, string height at the 12th fret.

A second Hanika appeared 31 Aug: **[Helmut Hanika 1969, 550 € VB,
Nandlstadt](https://www.kleinanzeigen.de/s-anzeige/vintage-helmut-hanika-konzertgitarre-1969-meisterhandarbeit/3500623170-74-6534)**
— but that seller posted a second guitar ten minutes later in the same polished house style,
i.e. a **flipper** who priced to market. Less likely to be mispriced than the attic ad.

### 5.4 Rejected — and why (all still live, all still overpriced)

| ad | asking | why rejected |
| --- | --- | --- |
| [Ibanez AW54-OPN](https://www.kleinanzeigen.de/s-anzeige/ibanez-westerngitarre/3394675195-74-6837) | 280 € | **New is 225 €** at Thomann. Used is *above* new price. (Top is solid okoume, not mahogany.) |
| [Sigma DM-1ST](https://www.kleinanzeigen.de/s-anzeige/sigma-dm-1-st-westerngitarre/3474765463-74-7615) | 400 € | **New is 289 €** at session.de. Above new even with the case. |
| [Höfner HL3](https://www.kleinanzeigen.de/s-anzeige/hoefner-hl3-konzertgitarre-4-4-kaum-gespielt-mit-tragetasche/3470924015-74-6833) | 240 € *(was 260)* | The ad itself says *laminierte Fichtendecke*. Solid-top money for a laminate top. |
| [Alhambra Iberia](https://www.kleinanzeigen.de/s-anzeige/gitarre-alhambra-mod-iberia/3496146180-74-7138) | 450 € VB | Photos show **torn wood and damaged binding along the top edge**, 2–3 cm, material missing. Luthier job (~80–200 €). The clean 5P is 500 €. |
| [Framus (ad says "Framos")](https://www.kleinanzeigen.de/s-anzeige/akustikgitarre-marke-framos/3445200140-74-5850) | 750 € VB | Genuinely a Framus (headstock logo + Markneukirchen inspection label, model `FN?0SE`, Framus-branded Fishman preamp). But Legacy-series acoustics are **Asian-built, German-inspected** — comparable new models list ~449 €. Offer 350 or skip. The misspelling is why nobody has found it. |
| [Höfner HD-75 Meisterwerkstätte 1985](https://www.kleinanzeigen.de/s-anzeige/hoefner-hd-75-konzertgitarre-meisterwerkstaette-vintage-1985/3462413374-74-6820) | **199 € VB** *(was 240)* | Solid spruce, German handmade — but the ad discloses a luthier repair to the **top and neck heel** in 2012. Lovely for someone who can assess an old guitar; too much unknown for a first purchase. *At 199 € this is getting interesting for a knowledgeable buyer.* |
| Eastman PCH1-OM "nagelneu" | 450 € | No saving against new. |
| All 12-strings (Ibanez PF1512, Yamaha FG-12, Fender F-55-12, Walden, Cort) | — | Double tension, brutal for a beginner, painful to tune. |
| Yamaha CGS103A 95 €, Yamaha CS40 75 €, various Ortega/La Mancha | — | **3/4 size** — the same size they are leaving behind. |
| [La Mancha Rubi CM 100 €](https://www.kleinanzeigen.de/s-anzeige/la-mancha-rubi-cm-linkshaender-klassikgitarre/3484524927-74-7602), Harley Benton LH 40 € | — | **Left-handed.** |
| Alhambra 3F 444 € | 444 € | Genuine Spanish, but **F = flamenco**: brighter, snappier, lower action, cypress. Not the warm classical voice wanted. |

### 5.5 Cheap-but-real options from the first search (Nürnberg route, all still live)

Useful if the budget ever shrinks: [Yamaha CG101MS 100 €](https://www.kleinanzeigen.de/s-anzeige/gitarre-yamaha-cg-101-ms/3490356124-74-7611)
(**solid spruce top**, the single best value found under 150 €),
[Yamaha G-235 II 50 €](https://www.kleinanzeigen.de/s-anzeige/top-yamaha-g-235-ii-konzertgitarre/3487376173-74-6807)
(80s Japanese nylon, needs strings),
[Martinez C-393 **100 €** *(was 150)*](https://www.kleinanzeigen.de/s-anzeige/konzertgitarre-martinez-c-393-n-by-pablo-a-lopez-4-4-/3477784169-74-6838),
[Walden 350 130 €](https://www.kleinanzeigen.de/s-anzeige/walden-350-akustikgitarre-ideal-fuer-anfaenger/3462696842-74-6821),
[Fender CD-60 V3 **120 €** *(was 130)*](https://www.kleinanzeigen.de/s-anzeige/fender-cd-60-v3-sunburst-akkustikgitarre-zu-verkaufen-mit-ovp/3493292660-74-6092),
[Washburn D10E 115 €](https://www.kleinanzeigen.de/s-anzeige/washburn-westerngitarren-modell-d-10e-bk-mit-zubehoer/3433544515-74-7250),
[Yamaha FX-370C 140 €](https://www.kleinanzeigen.de/s-anzeige/yamaha-fx-370c-westerngitarre/3454770180-74-6823).

For reference, verified **new** prices: Yamaha C40 III **129 €**, Yamaha F310 **138 €** — used
Yamahas barely undercut new, which is why the CG101MS (a solid-top model) is the outlier worth having.

### 5.6 Also noted in München/Laim (watch baseline, 1 Sept)

[Fender CC-60S 120 € VB](https://www.kleinanzeigen.de/s-anzeige/fender-cc-60s-39-zoll-westerngitarre-solid-in-top-zustand/3494912532-74-16373)
(**solid spruce top**, concert size, new ~230 € — best score of the seeding run),
[Takamine GF15CE 250 € VB](https://www.kleinanzeigen.de/s-anzeige/takamine-westerngitarre-gf15ce-blk/3493121625-74-6432),
[Guild USA JF4-NT Jumbo ~1995, 820 € VB](https://www.kleinanzeigen.de/s-anzeige/westerngitarre-guild-usa-jf4-nt-jumbo-ca-bj-1995-usa/3470746938-74-16393).

---

## 6. Market analysis — what the data actually shows

### 6.1 The disappearance hypothesis was tested and does not hold

The owner proposed: *new uploads are most likely to be good deals, and if they disappear fast
that proves the deal was real.*

**41 ads tracked from 25 August to 1 September. Zero disappeared.** Not the Alhambra, not the
Seagull, not the 50 € Yamaha, not the rejected ones.

**Ad age distribution (141 ads, Pfaffenhofen area, 1 Sept 2026):**

```
0-1 d      6  █████
2-7 d     13  ███████████
1-4 wk    49  ████████████████████████████████████████
1-3 mo    28  ███████████████████████
3-12 mo   33  ███████████████████████████
>1 year   12  ██████████
```

**Median ad age 31 days. 45 of 141 online over three months. Oldest 974 days (2.7 years).**
This is a slow market — a guitar not selling in a week means nothing.

*Caveat: sellers often leave ads up after selling and rarely mark them reserved, so "still live"
is not proof of "unsold" either. The sold-signal is simply too noisy over a one-week horizon.*

### 6.2 What actually moves: price

| ad | was | now | change |
| --- | --- | --- | --- |
| Martinez C-393 | 150 € | **100 €** | −33 % |
| Höfner HD-75 (repaired) | 240 € | 199 € | −17 % |
| Walden G630CE | 220 € | 180 € | −18 % |
| Cort Earth 500 | 220 € | 200 € | −9 % |
| Fender CD-60 V3 | 130 € | 120 € | −8 % |
| Höfner HL3 (laminate) | 260 € | 240 € | −8 % |
| Ibanez AW150CE | 350 € | 340 € | −3 % |
| La Mancha Rubi CM | 159 € | 149 € | −6 % |
| Taylor GS Mini | 465 € | 450 € | −3 % |

**Note which ones cut hardest:** the Martinez, the repaired Höfner HD-75, the laminate-top
Höfner HL3 — three that were flagged as overpriced. The market reached the same conclusion.
This is a better validation of the price judgement than disappearance would have been, and it is
why the watch reports **price drops as the headline signal**.

### 6.3 The refined heuristic: naive sellers, not new ads

Two Hanikas showed the real pattern:

- **Two-word ad, attic photos, no model named** (Armin Hanika, 800 €) → a naive seller → where
  mispricing lives.
- **Polished bullet-point description, "Meisterhandarbeit", second guitar posted ten minutes
  later** (Helmut Hanika, 550 €) → a flipper → priced to market.

**It is not "new = good deal", it is "new *and* naive = good deal."** Freshness matters mainly
because naive ads get taken by dealers quickly. Signals of a naive seller: misspelled brand
("Framos" for Framus — invisible to brand searches), no model number, wrong or missing specs,
condition field left blank, photos in a garage/attic, ad text under twenty words.

### 6.4 How to judge a used guitar's price

The method used throughout, worth repeating:

1. **Identify the exact model** from the label photo — never trust the ad title.
2. **Find the current new price** (Thomann, session.de, musicstore.de, kytary.de). Used should
   be 50–75 % of that. Above 80 % is not a deal; above 100 % happens more often than you would
   think (two examples in §5.4).
3. **Check the nationwide used market** for the same model — the tool's `city` mode with no
   location does this, e.g. `city "Taylor GS Mini" --category-id 74 --pages 4`.
4. **Solid top or laminate?** This is the single biggest sound difference. "massiv" / "solid" in
   the spec. A solid top opens up as it ages; laminate never does.
5. **All-solid** (top + back + sides) is a further step up and rare under 400 €.

---

## 7. Buying guidance for this specific person

**Body size matters coming from a child's guitar.** A dreadnought is a big box. Grand Auditorium
(Walden G630CE) or concert size (Fender CC-60S) or a small-body (Taylor GS Mini) will be more
comfortable. The Alhambra 5P is a standard classical body, which they are already used to.

**Cedar vs spruce tops:** cedar responds to a light touch and sounds warm and full even when
strummed gently — better suited to a developing right hand. Spruce needs to be driven harder but
has more headroom long-term.

**What to check when meeting a seller:**
- Sight down the neck for a bow
- Check the bridge is not lifting off the top
- Look for cracks around the soundhole and along the top edge
- Play every fret listening for buzz
- Press a chord at the 7th fret — if the strings sit miles above the fretboard, the action is off
- Feel the fret ends (sharp ends mean the guitar has been kept too dry)

**Budget for aftercare:** ~15 € for fresh strings, 40–60 € if a shop needs to set it up. Normal
and worth it — factor it into the offer.

**Negotiation:** long-standing ads have quiet leverage. The Admira has been up since 25 December;
the Alhambra 5P since 10 May. "VB" (Verhandlungsbasis) means the price is negotiable.

**The recommendation as it stands:** play the **Seagull S6 (310 €)** and the **Alhambra 5P
(500 €)** back to back. One is the best steel-string value found, the other the best classical.
That comparison settles the nylon-vs-steel question, which is the real decision.

If the Hanika's answers come back clean, it displaces everything.

---

## 8. Open questions and next steps

1. **Message the Armin Hanika seller** — highest upside, questions listed in §5.3.
2. **Nylon or steel** — unresolved; needs a side-by-side play.
3. **Start the cron watch** on the owner's own machine (§2.7). The seeded baseline is gone; the
   first local run reports everything as new.
4. **Widen the keyword slots.** Currently four generic terms. A **brand slot** (Yamaha, Cort,
   Seagull, Höfner, Alhambra, Takamine, Crafter, Esteve, Hanika) would suit the naive-seller
   theory — those ads are where the price is most often wrong. Also worth adding: "Dreadnought",
   "Parlor", "Gitarre massiv".
5. **Ideas never built:** time-based circle spacing (denser where the road is slow), making
   `--max-detour-min` the primary filter with the km corridor derived from it, email/push
   delivery of the digest, and a photo-based condition pre-screen.
6. **The parser will break again.** The site redesigned once mid-project. It now fails loudly
   (§4 bug 7) — when the log shows *"result page could not be parsed"*, the fix is to inspect a
   saved page and extend `parse_listings_modern()`.

---

## 9. Quick reference card

```bash
# clone and verify
git clone -b claude/kleinanzeigen-route-searcher-gpj5k5 https://github.com/SchockTop/Artist.git
cd Artist/kleinanzeigen && python3 -m unittest discover -s tests -t .

# one-off route search, full coverage in a price band, as an HTML map
python3 -m kleinanzeigen_search route "Konzertgitarre" --category-id 74 \
    --waypoints "85276 Pfaffenhofen a.d. Ilm; München" \
    --corridor 20 --pages 1 --budget 60 --min-price 200 --max-price 800 \
    --format html -o guitars.html

# nationwide price check for one model (this is how you value a find)
python3 -m kleinanzeigen_search city "Alhambra 5P" --category-id 74 --pages 4 --format table

# the scheduled watch
python3 -m kleinanzeigen_search watch --config watches.json --dry-run
```

**Key numbers to remember:** category `74` = Musikinstrumente · radii 5/10/20/30/50/100/150/200 ·
pagination stops ~page 50 · `--delay 2` minimum · 403s are normal and retried · median ad age
31 days · used should be 50–75 % of new.

---

*Compiled 2 September 2026. All prices, links and availability verified on 1–2 September 2026.
Ad availability changes; re-check before driving anywhere.*
