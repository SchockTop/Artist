"""Output formats: terminal table, JSON, CSV and an interactive HTML map."""
from __future__ import annotations

import csv
import html
import io
import json

from .models import Listing
from .search import SearchResult


def _truncate(text: str, width: int) -> str:
    text = (text or "").replace("\n", " ")
    return text if len(text) <= width else text[: width - 1] + "…"


def render_table(result: SearchResult, limit: int | None = None, width: int = 46) -> str:
    listings = result.listings[:limit] if limit else result.listings
    route_mode = result.route is not None
    headers = ["#", "score", "price", "ref", "location"]
    headers += ["detour", "off-road", "at km"] if route_mode else ["dist"]
    headers += ["age", "title"]
    rows: list[list[str]] = []
    for index, listing in enumerate(listings, start=1):
        if route_mode:
            columns = [
                f"+{listing.detour_min:.0f} min" if listing.detour_min is not None else "?",
                f"{listing.detour_km:.0f} km" if listing.detour_km is not None else "?",
                f"{listing.along_route_km:.0f}" if listing.along_route_km is not None else "?",
            ]
        else:
            columns = [f"{listing.distance_km:.0f} km" if listing.distance_km is not None else "?"]
        age = "?" if listing.age_days is None else (
            "today" if listing.age_days < 1 else f"{listing.age_days:.0f}d"
        )
        rows.append([
            str(index),
            "-" if listing.deal_score is None else f"{listing.deal_score:.0f}",
            listing.price_label,
            f"{listing.reference_price} €" if listing.reference_price else "-",
            _truncate(listing.location_label, 22),
            *columns,
            age,
            _truncate(listing.title, width),
        ])

    widths = [max(len(headers[i]), *(len(r[i]) for r in rows)) if rows else len(headers[i]) for i in range(len(headers))]
    lines = ["  ".join(h.ljust(w) for h, w in zip(headers, widths)).rstrip()]
    lines.append("  ".join("-" * w for w in widths))
    for row in rows:
        lines.append("  ".join(cell.ljust(w) for cell, w in zip(row, widths)).rstrip())
    return "\n".join(lines)


def render_details(result: SearchResult, limit: int = 10) -> str:
    out: list[str] = []
    for index, listing in enumerate(result.listings[:limit], start=1):
        head = f"{index}. [{'-' if listing.deal_score is None else f'{listing.deal_score:.0f}'}] {listing.title}"
        out.append(head)
        line = f"   {listing.price_label} · {listing.location_label}"
        if listing.detour_min is not None:
            line += f" · +{listing.detour_min:.0f} min detour"
        if listing.detour_km is not None:
            line += f" · {listing.detour_km:.0f} km off route (km {listing.along_route_km:.0f} of the trip)"
        elif listing.distance_km is not None:
            line += f" · {listing.distance_km:.0f} km away"
        if listing.posted_raw:
            line += f" · {listing.posted_raw}"
        out.append(line)
        if listing.deal_reasons:
            out.append("   " + " · ".join(listing.deal_reasons))
        out.append(f"   {listing.url}")
        out.append("")
    return "\n".join(out)


def render_json(result: SearchResult) -> str:
    payload = {
        "query": result.filters.describe(),
        "url_example": result.filters.url(),
        "summary": result.summary(),
        "warnings": result.warnings,
        "listings": [listing.to_dict() for listing in result.listings],
    }
    if result.location:
        payload["location"] = {"id": result.location.id, "label": result.location.label}
    if result.route:
        payload["route"] = {
            "waypoints": result.route.waypoints,
            "length_km": round(result.route.length_km, 1),
            "duration_min": round(result.route.duration_min) if result.route.duration_min else None,
            "corridor_km": result.corridor_km,
            "search_areas": [c.label for c in result.centres],
        }
    return json.dumps(payload, ensure_ascii=False, indent=2)


CSV_FIELDS = [
    "ad_id", "title", "price_eur", "price_type", "reference_price", "deal_score",
    "plz", "ort", "detour_min", "detour_km", "along_route_km", "distance_km",
    "posted_raw", "url", "deal_reasons",
]


def render_csv(result: SearchResult) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for listing in result.listings:
        row = listing.to_dict()
        row["deal_reasons"] = "; ".join(listing.deal_reasons)
        writer.writerow(row)
    return buffer.getvalue()


MAP_TEMPLATE = """<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  html, body {{ margin: 0; height: 100%; font: 14px/1.45 system-ui, sans-serif; }}
  #map {{ height: 100%; }}
  .legend {{ background: #fff; padding: 8px 10px; border-radius: 6px; box-shadow: 0 1px 4px rgba(0,0,0,.3); }}
  .legend b {{ display: block; margin-bottom: 4px; }}
  .dot {{ display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; }}
  .popup h4 {{ margin: 0 0 4px; font-size: 14px; }}
  .popup p {{ margin: 2px 0; }}
</style>
</head>
<body>
<div id="map"></div>
<script>
const route = {route};
const listings = {listings};
const map = L.map('map');
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
  maxZoom: 18, attribution: '&copy; OpenStreetMap contributors'
}}).addTo(map);

let bounds = null;
if (route.length) {{
  const line = L.polyline(route, {{color: '#2563eb', weight: 4, opacity: .7}}).addTo(map);
  bounds = line.getBounds();
}}
function colour(score) {{
  if (score === null) return '#94a3b8';
  if (score >= 80) return '#15803d';
  if (score >= 65) return '#65a30d';
  if (score >= 50) return '#ca8a04';
  return '#b91c1c';
}}
const markers = [];
listings.forEach(item => {{
  if (item.lat === null || item.lon === null) return;
  const marker = L.circleMarker([item.lat, item.lon], {{
    radius: 7, color: '#1f2937', weight: 1, fillColor: colour(item.score), fillOpacity: .9
  }}).addTo(map);
  marker.bindPopup(
    '<div class="popup"><h4><a href="' + item.url + '" target="_blank" rel="noopener">' + item.title + '</a></h4>' +
    '<p><b>' + item.price + '</b>' + (item.reference ? ' · typical ' + item.reference + ' €' : '') + '</p>' +
    '<p>' + item.location +
      (item.minutes !== null && item.minutes !== undefined ? ' · +' + item.minutes + ' min detour' : '') +
      (item.detour !== null ? ' · ' + item.detour + ' km off route' : '') + '</p>' +
    '<p>' + (item.score === null ? 'no score' : 'score ' + item.score) + '</p>' +
    '<p style="color:#475569">' + item.reasons + '</p></div>'
  );
  markers.push(marker);
}});
if (markers.length) {{
  const group = L.featureGroup(markers);
  bounds = bounds ? bounds.extend(group.getBounds()) : group.getBounds();
}}
map.fitBounds(bounds || L.latLngBounds([[47.3, 5.9], [55.1, 15.0]]), {{padding: [30, 30]}});

const legend = L.control({{position: 'bottomright'}});
legend.onAdd = () => {{
  const div = L.DomUtil.create('div', 'legend');
  div.innerHTML = '<b>{title}</b>' +
    '<div><span class="dot" style="background:#15803d"></span>80+ strong deal</div>' +
    '<div><span class="dot" style="background:#65a30d"></span>65+ good</div>' +
    '<div><span class="dot" style="background:#ca8a04"></span>50+ average</div>' +
    '<div><span class="dot" style="background:#b91c1c"></span>below average</div>' +
    '<div><span class="dot" style="background:#94a3b8"></span>no price</div>';
  return div;
}};
legend.addTo(map);
</script>
</body>
</html>
"""


def render_map(result: SearchResult, title: str | None = None) -> str:
    route_points = [[round(lat, 5), round(lon, 5)] for lat, lon in (result.route.points if result.route else [])]
    items = []
    for listing in result.listings:
        if listing.point is None:
            continue
        items.append({
            "lat": listing.lat,
            "lon": listing.lon,
            "title": html.escape(listing.title),
            "price": html.escape(listing.price_label),
            "reference": listing.reference_price,
            "location": html.escape(listing.location_label),
            "detour": listing.detour_km,
            "minutes": listing.detour_min,
            "score": listing.deal_score,
            "reasons": html.escape(" · ".join(listing.deal_reasons)),
            "url": listing.url,
        })
    heading = title or result.filters.describe()
    return MAP_TEMPLATE.format(
        title=html.escape(heading),
        route=json.dumps(route_points),
        listings=json.dumps(items, ensure_ascii=False),
    )


def render(result: SearchResult, fmt: str, limit: int | None = None) -> str:
    if fmt == "table":
        return render_table(result, limit)
    if fmt == "details":
        return render_details(result, limit or 10)
    if fmt == "json":
        return render_json(result)
    if fmt == "csv":
        return render_csv(result)
    if fmt == "html":
        return render_map(result)
    raise ValueError(f"unknown format {fmt!r}")
