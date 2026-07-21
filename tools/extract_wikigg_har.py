#!/usr/bin/env python3
"""
extract_wikigg_har.py - pull the huntshowdown.wiki.gg map markers out of HARs.

The wiki runs the MediaWiki DataMaps extension; each map page answers

    /api.php?action=queryDataMap&pageid=<id>&revid=<rev>

with { query: { title, revisionId, markers: { <Category>: [ [x, y, meta?] ] } } }.

Markers live on a ~1000 x 1000 canvas, same axis order as the community map
(no transpose, no flip), so roughly  map_px = 4.096 * wiki_px.  The exact fit
is solved per map by compare_game_pois.py, not assumed here.

This writes one normalised file per map into sources/wikigg/ and touches
nothing else:

    python tools/extract_wikigg_har.py
    python tools/extract_wikigg_har.py --har-dir . --out sources/wikigg
"""

from __future__ import annotations

import argparse
import glob
import html
import json
import re
import sys
from pathlib import Path

# map page title -> HuntMap map id
TITLE_TO_ID = {
    "stillwater bayou": 1,
    "lawson delta": 2,
    "desalle": 3,
    "mammon's gulch": 4,
}


def strip_html(text):
    """Wiki descriptions are HTML fragments; keep the words, drop the markup."""
    if not text:
        return ""
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def map_id_for(title):
    key = re.sub(r"^Map:", "", title or "").strip().lower()
    if key in TITLE_TO_ID:
        return TITLE_TO_ID[key]
    for name, mid in TITLE_TO_ID.items():          # tolerate punctuation drift
        a = re.sub(r"[^a-z]", "", key)
        b = re.sub(r"[^a-z]", "", name)
        if a and a == b:
            return mid
    return None


def parse_har(path: Path):
    """Return every queryDataMap payload found in one HAR."""
    with path.open(encoding="utf-8") as fh:
        har = json.load(fh)
    out = []
    for entry in har.get("log", {}).get("entries", []):
        url = entry.get("request", {}).get("url", "")
        if "queryDataMap" not in url:
            continue
        text = entry.get("response", {}).get("content", {}).get("text")
        if not text:
            continue
        try:
            payload = json.loads(text)
        except ValueError:
            continue
        query = payload.get("query")
        if query and "markers" in query:
            out.append((query, url))
    return out


def normalise(query, source_url, har_name):
    title = query.get("title", "")
    map_id = map_id_for(title)
    cats = {}
    for cat, items in (query.get("markers") or {}).items():
        rows = []
        for it in items:
            if not isinstance(it, list) or len(it) < 2:
                continue
            meta = it[2] if len(it) > 2 and isinstance(it[2], dict) else {}
            rows.append({
                "xy": [float(it[0]), float(it[1])],
                "label": strip_html(meta.get("label", "")),
                "desc": strip_html(meta.get("desc", "")),
                "article": meta.get("article", ""),
            })
        cats[cat] = rows
    return dict(
        source="huntshowdown.wiki.gg",
        map_id=map_id,
        title=title,
        revision=query.get("revisionId"),
        coord_space=1000,
        from_har=har_name,
        url=source_url,
        counts={k: len(v) for k, v in sorted(cats.items())},
        categories=cats,
    )


def main(argv=None):
    root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(
        description="Extract huntshowdown.wiki.gg DataMaps markers from HAR captures.")
    ap.add_argument("--har-dir", default=str(root),
                    help="directory to scan for *.har (default: repo root)")
    ap.add_argument("--out", default=str(root / "sources" / "wikigg"),
                    help="output directory (default: sources/wikigg)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    har_dir = Path(args.har_dir).resolve()
    out_dir = Path(args.out).resolve()

    # never let extraction land on top of the community POI data
    data_dir = (root / "huntmap" / "data").resolve()
    if out_dir == data_dir or data_dir in out_dir.parents:
        raise SystemExit("refusing to write inside huntmap/data")

    hars = sorted(Path(p) for p in glob.glob(str(har_dir / "*.har")))
    if not hars:
        raise SystemExit(f"no .har files in {har_dir}")

    out_dir.mkdir(parents=True, exist_ok=True)
    written, skipped = [], []

    for har in hars:
        found = parse_har(har)
        if not found:
            skipped.append(har.name)
            continue
        for query, url in found:
            rec = normalise(query, url, har.name)
            if rec["map_id"] is None:
                print(f"  ? unknown map title {rec['title']!r} in {har.name}",
                      file=sys.stderr)
                continue
            dest = out_dir / f"{rec['map_id']}.json"
            dest.write_text(json.dumps(rec, indent=1, ensure_ascii=False),
                            encoding="utf-8")
            written.append((rec, dest))

    if not args.quiet:
        for rec, dest in sorted(written, key=lambda r: r[0]["map_id"]):
            total = sum(rec["counts"].values())
            print(f'map {rec["map_id"]}  {rec["title"]:<24} rev {rec["revision"]}  '
                  f'{len(rec["counts"])} categories, {total} markers  -> {dest.name}')
        if skipped:
            print(f'\nno queryDataMap payload in: {", ".join(skipped)}')
        print(f"\nwrote {len(written)} file(s) to {out_dir}")
    return 0 if written else 1


if __name__ == "__main__":
    sys.exit(main())
