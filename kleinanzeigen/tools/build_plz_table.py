#!/usr/bin/env python3
"""Rebuild the bundled German postcode -> coordinate table.

Source: GeoNames postal code dump (https://download.geonames.org/export/zip/DE.zip),
licensed CC BY 4.0.  The bundled file keeps one averaged coordinate per postcode
plus a human readable municipality name, which is all this project needs.

Usage:
    python tools/build_plz_table.py [DE.txt] [-o kleinanzeigen_search/data/plz_de.csv.gz]
"""
from __future__ import annotations

import argparse
import collections
import csv
import gzip
import io
import pathlib
import re
import urllib.request
import zipfile

GEONAMES_URL = "https://download.geonames.org/export/zip/DE.zip"
COMPANY_RE = re.compile(
    r"\b(GmbH|AG|KG|mbH|SE|Co\.|e\.V\.|Universit|Hochschule|Fakult|Klinik|Postfach|Bank|"
    r"Versicherung|Verlag|Zeitung|Behörde|Bundesamt|Finanzamt|Deutsche|Sparkasse|Vertrieb|Service)\b|&",
    re.IGNORECASE,
)
DEFAULT_OUT = pathlib.Path(__file__).resolve().parents[1] / "kleinanzeigen_search" / "data" / "plz_de.csv.gz"


def download() -> str:
    with urllib.request.urlopen(GEONAMES_URL, timeout=120) as resp:
        blob = resp.read()
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        return zf.read("DE.txt").decode("utf-8")


def build(text: str) -> list[tuple[str, float, float, str, str]]:
    lat_sum: dict[str, float] = collections.defaultdict(float)
    lon_sum: dict[str, float] = collections.defaultdict(float)
    count: dict[str, int] = collections.defaultdict(int)
    names: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    big_customer: dict[str, int] = collections.defaultdict(int)

    for row in csv.reader(io.StringIO(text), delimiter="\t"):
        if len(row) < 11:
            continue
        plz, place, admin3, lat, lon = row[1], row[2], row[7], row[9], row[10]
        if not (plz and lat and lon):
            continue
        lat_sum[plz] += float(lat)
        lon_sum[plz] += float(lon)
        count[plz] += 1
        # The place column holds the town, except for "Grosskunden" postcodes
        # (large-volume mail customers) where GeoNames stores a company name.
        # Those postcodes have no area on the map and Kleinanzeigen does not
        # know them, so they are flagged and skipped when anchoring searches.
        if place and not COMPANY_RE.search(place):
            names[plz][place] += 1
        else:
            names[plz][admin3 or place] += 1
            big_customer[plz] += 1

    out = []
    for plz in sorted(count):
        n = count[plz]
        name = names[plz].most_common(1)[0][0]
        kind = "g" if big_customer[plz] * 2 >= n else "p"
        out.append((plz, round(lat_sum[plz] / n, 4), round(lon_sum[plz] / n, 4), name, kind))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", nargs="?", help="local DE.txt; downloaded from GeoNames when omitted")
    ap.add_argument("-o", "--output", type=pathlib.Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    text = pathlib.Path(args.source).read_text(encoding="utf-8") if args.source else download()
    rows = build(text)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(args.output, "wt", encoding="utf-8", newline="", compresslevel=9) as fh:
        writer = csv.writer(fh)
        writer.writerow(["plz", "lat", "lon", "ort", "typ"])
        writer.writerows(rows)
    print(f"wrote {len(rows)} postcodes to {args.output}")


if __name__ == "__main__":
    main()
