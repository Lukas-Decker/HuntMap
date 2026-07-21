#!/usr/bin/env python3
"""
compare_game_pois.py - diff the community POI data against game-derived POIs.

READ-ONLY with respect to huntmap/data/. This script never writes there; it
only emits a report into the output directory (default: compare/).

Why a transform is needed
-------------------------
The two datasets do not share a coordinate system:

  * HuntMap  - pixels on the community 4096 x 4096 map image.
  * Hunt-ify - pixels on the game's own 2048 x 2048 overview render
               (structured/maps/<slug>.json), whose axes are transposed
               relative to the community image and where the playable
               square only occupies the middle ~1024 px.

So the game points are NOT a 1:1 scale of the interactive map and must be
fitted, not assumed. The fit is solved per map in two stages:

  1. Seed - least-squares affine from named compound / landmark centroids
     (matched by fuzzy name). Good to roughly 150 px; compound "centres"
     are defined differently in the two sources.
  2. Refine - ICP (iterative closest point) against a dense anchor layer,
     by default cash registers, which are precise per-object placements
     and whose counts line up almost exactly. This converges to ~10 px
     (about 2.5 m) RMS.

The solved transform is reported so it can be sanity-checked, and can be
pinned with --transform to skip the fit entirely.

Usage
-----
    python tools/compare_game_pois.py
    python tools/compare_game_pois.py --map 3 --tolerance 20
    python tools/compare_game_pois.py --huntify ../Hunt-ify --overlay
"""

from __future__ import annotations

import argparse
import difflib
import json
import math
import os
import re
import sys
from pathlib import Path

# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------

MAP_PX = 4096          # community map image is 4096 x 4096
MAP_M = 1000.0         # a Hunt map is 1 km across
M_PER_PX = MAP_M / MAP_PX

# Hunt-ify internal level name -> HuntMap map id
SLUGS = {
    "cemetery": 1,     # Stillwater Bayou
    "civilwar": 2,     # Lawson Delta
    "creek": 3,        # DeSalle
    "colorado": 4,     # Mammon's Gulch
}

# HuntMap POI type -> the game layers that should back it.
#
#   layers  - Hunt-ify layer keys, merged into one point set
#   merge   - collapse game points closer than this many metres into one
#             (several anchors often sit on a single physical object)
#   tol     - override the global match radius, in metres
#   match   - "dist" (default) or "name" (compounds carry names in both sets)
#   note    - what the game layer really is, printed in the report
#   grade   - how literally the pairing should be read:
#             exact  - one game entity per map POI, counts line up
#             high   - same objects, minor bookkeeping differences
#             medium - same class of object, partial overlap expected
#             partial- game set is a subset or superset by design
#             proxy  - not the thing itself, a structural correlate found
#                      by --suggest; useful as a hint, not as ground truth
#             low    - large superset, only counts are meaningful
CATEGORIES = {
    "cash_register": dict(
        layers=["pt_spawnachor_LootCashRegister"],
        merge=0.0, grade="exact",
        note="one loot anchor per register; counts line up 1:1",
    ),
    "workbench": dict(
        layers=["pt_spawnachor_LootWorkbench_Weapon",
                "pt_spawnachor_LootWorkbench_Upgrade"],
        merge=6.0, grade="high",
        note="weapon + upgrade anchors usually share one physical bench",
    ),
    "melee_weapon": dict(
        layers=["pt_2mWorldBluntWW0009Shovel",
                "pt_2mWorldStabWW0010Pitchfork",
                "pt_2mWorldBluntWW0001SledgeHammer",
                "pt_sandbox_pickable_world_items/pickable_world_items/"
                "weapon_woodaxe_a_spawner"],
        merge=0.0, grade="medium",
        note="world-placed shovels / pitchforks / sledges / wood axes",
    ),
    "spawn": dict(
        layers=["pt_SpawnPointTeam"],
        merge=0.0, tol=40.0, grade="partial",
        note="per-team spawn points; the map shows grouped spawn areas",
    ),
    "compound": dict(
        layers=["__compounds__"], match="name",
        merge=0.0, grade="high",
        note="matched by name; the offset is centroid definition, not error",
    ),
    "tower": dict(
        layers=["pt_SimpleObjectSpawner"],
        merge=8.0, tol=25.0, grade="proxy",
        note="object spawners; towers are static geometry with no entity of "
             "their own, but nearly every one carries one of these",
    ),
    "big_tower": dict(
        layers=["pt_SimpleObjectSpawner"],
        merge=8.0, tol=25.0, grade="proxy",
        note="same spawner layer as hunting towers - it cannot tell the two "
             "tower sizes apart, so treat this as presence only",
    ),
    "beetle": dict(
        layers=["pt_Supply_Box"],
        merge=0.0, tol=25.0, grade="proxy",
        note="supply boxes; beetles perch on them often enough to be a hint",
    ),
    "extraction": dict(
        layers=["pt_spawnAnchor_ExtractionZone"],
        merge=0.0, tol=60.0, grade="low",
        note="zone anchors sit tens of metres off the visible extract and "
             "only cover the ones a mission actually uses - counts only",
    ),
    "wild_target": dict(
        layers=["pt_spawnAnchor_Target"],
        merge=0.0, grade="low",
        note="ALL possible target anchors, a large superset of one match",
    ),
}

# HuntMap types the game data has no counterpart for
NO_GAME_LAYER = {
    "armory": "too few per map (1-8) for any candidate layer to beat chance",
    "brute": "spawned by the AI director at runtime, not placed in the level",
    "easter_egg": "community observation; nothing tags these in the level",
}

ANCHOR_DEFAULT = "pt_spawnachor_LootCashRegister"


# --------------------------------------------------------------------------
# tiny linear algebra (no numpy dependency)
# --------------------------------------------------------------------------

def _solve3(m, v):
    """Solve a 3x3 system by Gaussian elimination with partial pivoting."""
    a = [row[:] + [v[i]] for i, row in enumerate(m)]
    for col in range(3):
        piv = max(range(col, 3), key=lambda r: abs(a[r][col]))
        if abs(a[piv][col]) < 1e-12:
            raise ValueError("singular system - not enough distinct points")
        a[col], a[piv] = a[piv], a[col]
        for r in range(3):
            if r == col:
                continue
            f = a[r][col] / a[col][col]
            for c in range(col, 4):
                a[r][c] -= f * a[col][c]
    return [a[i][3] / a[i][i] for i in range(3)]


def fit_affine(src, dst):
    """Least-squares affine  dst = A * src + t  from paired point lists.

    Returns (ax, bx, cx, ay, by, cy) such that
        X = ax*x + bx*y + cx
        Y = ay*x + by*y + cy
    """
    n = len(src)
    if n < 3:
        raise ValueError("need at least 3 point pairs to fit an affine")
    sxx = sxy = sx = syy = sy = 0.0
    for x, y in src:
        sxx += x * x
        sxy += x * y
        sx += x
        syy += y * y
        sy += y
    normal = [[sxx, sxy, sx],
              [sxy, syy, sy],
              [sx, sy, float(n)]]

    out = []
    for axis in (0, 1):
        rx = ry = r1 = 0.0
        for (x, y), d in zip(src, dst):
            t = d[axis]
            rx += x * t
            ry += y * t
            r1 += t
        out.extend(_solve3(normal, [rx, ry, r1]))
    return tuple(out)


def apply_affine(T, pts):
    ax, bx, cx, ay, by, cy = T
    return [(ax * x + bx * y + cx, ay * x + by * y + cy) for x, y in pts]


def decompose(T):
    """Human-readable read of the transform: scale, rotation, whether it flips."""
    ax, bx, cx, ay, by, cy = T
    sx = math.hypot(ax, ay)
    sy = math.hypot(bx, by)
    det = ax * by - bx * ay
    rot = math.degrees(math.atan2(ay, ax))
    return dict(scale_x=round(sx, 5), scale_y=round(sy, 5),
                rotation_deg=round(rot, 3), mirrored=det < 0,
                offset=[round(cx, 2), round(cy, 2)])


# --------------------------------------------------------------------------
# point-set helpers
# --------------------------------------------------------------------------

def dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def nearest(pt, pool):
    """(index, distance) of the closest point in pool."""
    best_i, best_d = -1, float("inf")
    for i, q in enumerate(pool):
        d = dist(pt, q)
        if d < best_d:
            best_i, best_d = i, d
    return best_i, best_d


def merge_close(pts, radius_px):
    """Collapse points within radius_px into their centroid."""
    if radius_px <= 0 or not pts:
        return list(pts), []
    used = [False] * len(pts)
    out, groups = [], []
    for i, p in enumerate(pts):
        if used[i]:
            continue
        grp = [i]
        used[i] = True
        for j in range(i + 1, len(pts)):
            if not used[j] and dist(p, pts[j]) <= radius_px:
                used[j] = True
                grp.append(j)
        cx = sum(pts[k][0] for k in grp) / len(grp)
        cy = sum(pts[k][1] for k in grp) / len(grp)
        out.append((cx, cy))
        groups.append(grp)
    return out, groups


def greedy_match(a_pts, b_pts, tol_px):
    """Mutually-exclusive nearest-neighbour matching within tol_px.

    Returns (pairs, a_only, b_only) where pairs is [(ai, bi, distance)].
    """
    cand = []
    for i, p in enumerate(a_pts):
        for j, q in enumerate(b_pts):
            d = dist(p, q)
            if d <= tol_px:
                cand.append((d, i, j))
    cand.sort()
    ta, tb, pairs = set(), set(), []
    for d, i, j in cand:
        if i in ta or j in tb:
            continue
        ta.add(i)
        tb.add(j)
        pairs.append((i, j, d))
    return (pairs,
            [i for i in range(len(a_pts)) if i not in ta],
            [j for j in range(len(b_pts)) if j not in tb])


def stats(values):
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    return dict(
        n=n,
        min=round(s[0], 2),
        median=round(s[n // 2], 2),
        mean=round(sum(s) / n, 2),
        p95=round(s[min(n - 1, int(n * 0.95))], 2),
        max=round(s[-1], 2),
    )


# --------------------------------------------------------------------------
# data loading
# --------------------------------------------------------------------------

def norm_name(s):
    s = s.lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def load_huntmap(root, map_id):
    path = root / "huntmap" / "data" / f"data-{map_id}.json"
    with path.open(encoding="utf-8") as fh:
        raw = json.load(fh)
    types_path = root / "huntmap" / "data" / "poi-types.json"
    with types_path.open(encoding="utf-8") as fh:
        types = json.load(fh)

    pois = {}
    for tkey, tdef in types.items():
        cat = tdef["categories"]
        pois[tkey] = [
            dict(id=p["id"], xy=(float(p["c"][0]), float(p["c"][1])),
                 desc=p.get("d", ""), name=p.get("n", ""),
                 shots=len(p.get("u", [])))
            for p in raw.get(cat, [])
        ]
    return dict(name=raw.get("n", f"map {map_id}"), pois=pois, types=types)


def load_huntify(huntify, slug):
    path = huntify / "structured" / "maps" / f"{slug}.json"
    if not path.is_file():
        raise SystemExit(f"missing Hunt-ify map data: {path}")
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def game_points(gdata, layer_keys):
    """Collect points from one or more Hunt-ify layers, in game px.

    Returns (points, labels, missing_layer_keys). labels[i] is a name when
    the source carries one (compounds / landmarks), else "".
    """
    pts, labels, missing = [], [], []
    for key in layer_keys:
        if key == "__compounds__":
            for c in gdata.get("compounds", []):
                pts.append((float(c["cx"]), float(c["cy"])))
                labels.append(c["name"])
            for c in gdata.get("landmarks", []):
                pts.append((float(c["cx"]), float(c["cy"])))
                labels.append(c["name"])
            continue
        layer = gdata["layers"].get(key)
        if layer is None:
            missing.append(key)
            continue
        for it in layer["items"]:
            pts.append((float(it[0]), float(it[1])))
            labels.append("")
    return pts, labels, missing


def name_match(game_labels, map_pois):
    """Pair game entries to map POIs by fuzzy name. Returns [(gi, mi)]."""
    keys = [norm_name(p["name"]) for p in map_pois]
    lookup = {}
    for i, k in enumerate(keys):
        lookup.setdefault(k, i)
    taken, pairs = set(), []
    for gi, raw in enumerate(game_labels):
        hit = difflib.get_close_matches(norm_name(raw), list(lookup), 1, 0.70)
        if hit and lookup[hit[0]] not in taken:
            mi = lookup[hit[0]]
            taken.add(mi)
            pairs.append((gi, mi))
    return pairs


# --------------------------------------------------------------------------
# the fit
# --------------------------------------------------------------------------

def seed_transform(hm, gdata):
    """Affine seeded from compound / landmark names common to both sources."""
    hm_by_name = {norm_name(p["name"]): p["xy"]
                  for p in hm["pois"].get("compound", []) if p["name"]}
    game = {}
    for c in gdata.get("compounds", []) + gdata.get("landmarks", []):
        game[norm_name(c["name"])] = (float(c["cx"]), float(c["cy"]))

    src, dst, used, unmatched = [], [], [], []
    keys = list(hm_by_name)
    for gname, gxy in game.items():
        hit = difflib.get_close_matches(gname, keys, 1, 0.70)
        if hit:
            src.append(gxy)
            dst.append(hm_by_name[hit[0]])
            used.append((gname, hit[0]))
        else:
            unmatched.append(gname)
    if len(src) < 3:
        raise SystemExit("could not match enough compounds to seed the fit")
    return fit_affine(src, dst), used, unmatched


def refine_icp(T, gdata, hm, anchor_key, rounds=12, gate_px=250.0):
    """Tighten the transform with ICP against a dense, reliable layer."""
    layer = gdata["layers"].get(anchor_key)
    if layer is None:
        return T, None
    src = [(float(i[0]), float(i[1])) for i in layer["items"]]
    dst_type = next((k for k, v in CATEGORIES.items()
                     if anchor_key in v["layers"]), None)
    dst = [p["xy"] for p in hm["pois"].get(dst_type, [])] if dst_type else []
    if len(src) < 8 or len(dst) < 8:
        return T, None

    gate = gate_px
    for _ in range(rounds):
        moved = apply_affine(T, src)
        keep_s, keep_d = [], []
        for i, p in enumerate(moved):
            j, d = nearest(p, dst)
            if d <= gate:
                keep_s.append(src[i])
                keep_d.append(dst[j])
        if len(keep_s) < 6:
            break
        try:
            new_T = fit_affine(keep_s, keep_d)
        except ValueError:
            break
        if max(abs(a - b) for a, b in zip(new_T, T)) < 1e-9:
            T = new_T
            break
        T = new_T
        gate = max(30.0, gate * 0.7)

    moved = apply_affine(T, src)
    res = [nearest(p, dst)[1] for p in moved]
    inl = [d for d in res if d <= 40.0]
    quality = dict(
        anchor=anchor_key,
        anchor_points=len(src),
        map_points=len(dst),
        inliers_40px=len(inl),
        rms_px=round(math.sqrt(sum(d * d for d in inl) / len(inl)), 2) if inl else None,
        rms_m=round(math.sqrt(sum(d * d for d in inl) / len(inl)) * M_PER_PX, 2) if inl else None,
        median_px=round(sorted(res)[len(res) // 2], 2) if res else None,
    )
    return T, quality


# --------------------------------------------------------------------------
# comparison
# --------------------------------------------------------------------------

def compare_map(root, huntify, slug, map_id, tol_m, anchor, want_overlay,
                pinned=None):
    hm = load_huntmap(root, map_id)
    gdata = load_huntify(huntify, slug)

    if pinned is not None:
        T, matched_names, unmatched_names = tuple(pinned), [], []
        quality = dict(anchor="pinned via --transform", anchor_points=None,
                       map_points=None, inliers_40px=None, rms_px=None,
                       rms_m=None, median_px=None)
    else:
        T, matched_names, unmatched_names = seed_transform(hm, gdata)
        T, quality = refine_icp(T, gdata, hm, anchor)

    result = dict(
        map_id=map_id,
        map_name=hm["name"],
        game_slug=slug,
        game_display=gdata.get("displayName"),
        tolerance_m=tol_m,
        transform=dict(
            matrix=[round(v, 6) for v in T],
            formula="X = ax*x + bx*y + cx ; Y = ay*x + by*y + cy",
            **decompose(T),
        ),
        seed=dict(compounds_matched=len(matched_names),
                  compounds_unmatched=unmatched_names),
        fit_quality=quality,
        categories={},
        unbacked_types={k: v for k, v in NO_GAME_LAYER.items()},
    )

    overlay = dict(map_id=map_id, transform=[round(v, 6) for v in T],
                   categories={}) if want_overlay else None

    for tkey, spec in CATEGORIES.items():
        map_pois = hm["pois"].get(tkey, [])
        map_pts = [p["xy"] for p in map_pois]

        raw_pts, labels, missing_layers = game_points(gdata, spec["layers"])
        moved = apply_affine(T, raw_pts)
        merged, groups = merge_close(moved, spec["merge"] / M_PER_PX)
        if groups:                                  # keep labels aligned
            labels = [labels[g[0]] for g in groups]

        cat_tol_m = spec.get("tol", tol_m)
        cat_tol_px = cat_tol_m / M_PER_PX

        if spec.get("match") == "name":
            np_pairs = name_match(labels, map_pois)
            pairs = [(gi, mi, dist(merged[gi], map_pts[mi])) for gi, mi in np_pairs]
            seen_g = {gi for gi, _, _ in pairs}
            seen_m = {mi for _, mi, _ in pairs}
            game_only = [i for i in range(len(merged)) if i not in seen_g]
            map_only = [j for j in range(len(map_pts)) if j not in seen_m]
        else:
            pairs, game_only, map_only = greedy_match(merged, map_pts, cat_tol_px)

        offsets_m = [d * M_PER_PX for _, _, d in pairs]

        entry = dict(
            grade=spec["grade"],
            matched_by=spec.get("match", "distance"),
            tolerance_m=None if spec.get("match") == "name" else cat_tol_m,
            note=spec["note"],
            game_layers=spec["layers"],
            missing_layers=missing_layers,
            game_raw=len(raw_pts),
            game_merged=len(merged),
            map_count=len(map_pts),
            matched=len(pairs),
            game_only=len(game_only),
            map_only=len(map_only),
            offset_m=stats(offsets_m),
            game_only_points=[[round(merged[i][0], 1), round(merged[i][1], 1)]
                              for i in game_only],
            map_only_pois=[dict(id=map_pois[j]["id"],
                                xy=[int(map_pts[j][0]), int(map_pts[j][1])],
                                name=map_pois[j]["name"],
                                desc=map_pois[j]["desc"][:70])
                           for j in map_only],
        )
        result["categories"][tkey] = entry

        if overlay is not None:
            overlay["categories"][tkey] = dict(
                game_only=entry["game_only_points"],
                map_only=[p["id"] for p in entry["map_only_pois"]],
                matched=[[map_pois[j]["id"],
                          [round(merged[i][0], 1), round(merged[i][1], 1)],
                          round(d * M_PER_PX, 2)]
                         for i, j, d in pairs],
            )

    return result, overlay


# --------------------------------------------------------------------------
# layer discovery
# --------------------------------------------------------------------------

def suggest_layers(root, huntify, slug, map_id, radius_m, top):
    """For every HuntMap POI type, rank game layers by how well they cover it.

    coverage = share of map POIs with a game point of that layer within
    `radius_m`. `lift` divides that by the coverage a same-sized random
    layer would get, so a small precise layer outranks a huge dense one.
    Anything with a low lift is just density, not a real correspondence.
    """
    hm = load_huntmap(root, map_id)
    gdata = load_huntify(huntify, slug)
    T, _, _ = seed_transform(hm, gdata)
    T, quality = refine_icp(T, gdata, hm, ANCHOR_DEFAULT)
    rad_px = radius_m / M_PER_PX

    # a layer of n points sprinkled at random covers roughly this share
    area = float(MAP_PX * MAP_PX)
    def baseline(n):
        return 1.0 - math.exp(-n * math.pi * rad_px * rad_px / area)

    out = dict(map_id=map_id, map_name=hm["name"], radius_m=radius_m,
               fit_quality=quality, types={})
    for tkey, pois in hm["pois"].items():
        map_pts = [p["xy"] for p in pois]
        if not map_pts:
            continue
        ranked = []
        for key, layer in gdata["layers"].items():
            if layer.get("type") != "point" or not layer["items"]:
                continue
            pts = [(float(i[0]), float(i[1])) for i in layer["items"]]
            moved = apply_affine(T, pts)
            hits = sum(1 for p in map_pts if nearest(p, moved)[1] <= rad_px)
            if not hits:
                continue
            cov = hits / len(map_pts)
            base = baseline(len(pts))
            ranked.append(dict(layer=key, label=layer.get("label", key),
                               group=layer.get("group", ""),
                               layer_points=len(pts), hits=hits,
                               coverage=round(cov, 3),
                               lift=round(cov / base, 2) if base > 0 else None))
        # coverage first - a tiny layer can post a huge lift off two hits.
        # lift below 2 is just density, so drop it entirely.
        ranked = [r for r in ranked if (r["lift"] or 0) >= 2.0]
        ranked.sort(key=lambda r: (-r["coverage"], -(r["lift"] or 0)))
        out["types"][tkey] = dict(map_count=len(map_pts), candidates=ranked[:top])
    return out


def suggest_markdown(reports, radius_m):
    L = ["# Candidate game layers per POI type\n",
         "Produced by `--suggest`. For each HuntMap POI type this ranks the "
         "game layers by **lift** - coverage divided by what a random layer "
         "of the same size would score. Lift near 1 means the layer is simply "
         "dense enough to be near everything; high lift with few points is a "
         "real correspondence.\n",
         f"Hit radius: **{radius_m:g} m**.\n",
         "> These are statistical hints, not authoritative mappings. Only the "
         "pairings promoted into `CATEGORIES` are used by the diff.\n"]
    for rep in reports:
        L.append(f'## {rep["map_name"]}\n')
        for tkey, t in rep["types"].items():
            L.append(f'**{tkey}** ({t["map_count"]} on the map)\n')
            if not t["candidates"]:
                L.append("- nothing within radius\n")
                continue
            L.append("| lift | coverage | layer points | layer |")
            L.append("|--:|--:|--:|---|")
            for c in t["candidates"]:
                L.append(f'| {c["lift"]} | {c["hits"]}/{t["map_count"]} '
                         f'({c["coverage"]:.0%}) | {c["layer_points"]} | '
                         f'`{c["layer"]}` - {c["label"]} |')
            L.append("")
    return "\n".join(L)


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------

def markdown(results, tol_m):
    L = []
    add = L.append
    add("# Community POI data vs game-derived POIs\n")
    add("Generated by `tools/compare_game_pois.py`. The community data in "
        "`huntmap/data/` is **read-only** here - nothing in this report has "
        "been written back.\n")
    add(f"Match tolerance: **{tol_m:g} m**. "
        f"1 map pixel = {M_PER_PX:.4f} m.\n")

    add("## Alignment\n")
    add("| Map | scale x | scale y | rotation | mirrored | anchor inliers | "
        "RMS |")
    add("|---|--:|--:|--:|:--:|--:|--:|")
    for r in results:
        q = r["fit_quality"] or {}
        t = r["transform"]
        rms = f'{q.get("rms_px")} px / {q.get("rms_m")} m' if q.get("rms_px") else "n/a"
        inl = f'{q.get("inliers_40px")} / {q.get("anchor_points")}' if q else "n/a"
        add(f'| {r["map_name"]} | {t["scale_x"]} | {t["scale_y"]} | '
            f'{t["rotation_deg"]}deg | {"yes" if t["mirrored"] else "no"} | '
            f'{inl} | {rms} |')
    add("")

    for r in results:
        add(f'## {r["map_name"]}  <sub>(game level `{r["game_slug"]}`)</sub>\n')
        q = r["fit_quality"]
        if q and q.get("rms_m") is not None and q["rms_m"] > 8:
            add(f'> **Warning** - alignment is loose ({q["rms_m"]} m RMS). '
                "Treat the per-category numbers below as indicative only.\n")
        if r["seed"]["compounds_unmatched"]:
            add("> Compounds with no name match: "
                + ", ".join(r["seed"]["compounds_unmatched"]) + "\n")

        add("| Type | grade | game | map | matched | game only | map only | "
            "median off |")
        add("|---|:--:|--:|--:|--:|--:|--:|--:|")
        for tkey, c in r["categories"].items():
            g = (f'{c["game_merged"]}'
                 + (f' ({c["game_raw"]} raw)' if c["game_merged"] != c["game_raw"] else ""))
            off = f'{c["offset_m"]["median"]} m' if c["offset_m"] else "-"
            add(f'| {tkey} | {c["grade"]} | {g} | {c["map_count"]} | '
                f'{c["matched"]} | {c["game_only"]} | {c["map_only"]} | {off} |')
        add("")

        for tkey, c in r["categories"].items():
            if c["grade"] in ("low",):
                continue
            if not c["game_only"] and not c["map_only"]:
                continue
            add(f'### {tkey}\n')
            add(f'_{c["note"]}_\n')
            if c["game_only"]:
                add(f'**{c["game_only"]} in the game data with no POI on the map** '
                    "(map-image coordinates):\n")
                pts = ", ".join(f'({x:.0f}, {y:.0f})'
                                for x, y in c["game_only_points"][:40])
                add(pts + ("  ..." if c["game_only"] > 40 else "") + "\n")
            if c["map_only"]:
                add(f'**{c["map_only"]} on the map with nothing in the game data:**\n')
                add("| id | x / y | note |")
                add("|---|---|---|")
                for p in c["map_only_pois"][:40]:
                    label = p["name"] or p["desc"] or ""
                    add(f'| `{p["id"]}` | {p["xy"][0]} / {p["xy"][1]} | {label} |')
                if c["map_only"] > 40:
                    add(f'| ... | | {c["map_only"] - 40} more |')
                add("")

        add("### Types with no game counterpart\n")
        for k, why in r["unbacked_types"].items():
            add(f'- **{k}** - {why}')
        add("")

    return "\n".join(L)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main(argv=None):
    root = Path(__file__).resolve().parent.parent

    ap = argparse.ArgumentParser(
        description="Compare huntmap/data POIs against Hunt-ify game-derived POIs "
                    "(read-only, solves the coordinate transform).")
    ap.add_argument("--huntify", default=str(root.parent / "Hunt-ify"),
                    help="path to the Hunt-ify repo (default: ../Hunt-ify)")
    ap.add_argument("--map", default="all",
                    help="map id 1-4, or 'all' (default: all)")
    ap.add_argument("--out", default=str(root / "compare"),
                    help="output directory (default: compare/)")
    ap.add_argument("--tolerance", type=float, default=15.0,
                    help="match radius in metres (default: 15)")
    ap.add_argument("--anchor", default=ANCHOR_DEFAULT,
                    help="Hunt-ify layer key used to refine the fit")
    ap.add_argument("--transform", metavar="ax,bx,cx,ay,by,cy",
                    help="pin the game->map affine instead of solving it, e.g. "
                         "'0,4,-2048,4,0,-2048'")
    ap.add_argument("--overlay", action="store_true",
                    help="also emit overlay-<id>.json with per-POI diffs")
    ap.add_argument("--suggest", action="store_true",
                    help="instead of diffing, rank which game layers best "
                         "explain each POI type (layer discovery)")
    ap.add_argument("--suggest-radius", type=float, default=12.0,
                    help="hit radius in metres for --suggest (default: 12)")
    ap.add_argument("--suggest-top", type=int, default=6,
                    help="candidates listed per type for --suggest")
    ap.add_argument("--quiet", action="store_true", help="no stdout summary")
    args = ap.parse_args(argv)

    huntify = Path(args.huntify).resolve()
    if not (huntify / "structured" / "maps").is_dir():
        raise SystemExit(f"not a Hunt-ify checkout: {huntify}")

    out = Path(args.out).resolve()
    data_dir = (root / "huntmap" / "data").resolve()
    # hard guard: never let the report land on top of the POI data
    if out == data_dir or data_dir in out.parents:
        raise SystemExit("refusing to write inside huntmap/data - "
                         "the community POI data is read-only here")
    out.mkdir(parents=True, exist_ok=True)

    if args.map == "all":
        wanted = list(SLUGS.items())
    else:
        mid = int(args.map)
        wanted = [(s, m) for s, m in SLUGS.items() if m == mid]
        if not wanted:
            raise SystemExit(f"unknown map id {args.map}")

    if args.suggest:
        reports = [suggest_layers(root, huntify, slug, map_id,
                                  args.suggest_radius, args.suggest_top)
                   for slug, map_id in sorted(wanted, key=lambda kv: kv[1])]
        for rep in reports:
            (out / f'suggest-{rep["map_id"]}.json').write_text(
                json.dumps(rep, indent=1, ensure_ascii=False), encoding="utf-8")
        (out / "SUGGEST.md").write_text(
            suggest_markdown(reports, args.suggest_radius), encoding="utf-8")
        if not args.quiet:
            for rep in reports:
                print(f'{rep["map_name"]}:')
                for tkey, t in rep["types"].items():
                    top = t["candidates"][0] if t["candidates"] else None
                    if top:
                        print(f'   {tkey:<15} lift {top["lift"]:>6}  '
                              f'{top["hits"]}/{t["map_count"]}  {top["layer"]}')
            print(f'\nsuggestions -> {out}')
        return 0

    pinned = None
    if args.transform:
        parts = [p for p in re.split(r"[,\s]+", args.transform.strip()) if p]
        if len(parts) != 6:
            raise SystemExit("--transform needs exactly 6 numbers: ax,bx,cx,ay,by,cy")
        pinned = [float(p) for p in parts]

    results = []
    for slug, map_id in sorted(wanted, key=lambda kv: kv[1]):
        res, overlay = compare_map(root, huntify, slug, map_id,
                                   args.tolerance, args.anchor, args.overlay,
                                   pinned)
        results.append(res)
        (out / f"report-{map_id}.json").write_text(
            json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
        if overlay is not None:
            (out / f"overlay-{map_id}.json").write_text(
                json.dumps(overlay, ensure_ascii=False), encoding="utf-8")

    (out / "REPORT.md").write_text(markdown(results, args.tolerance),
                                   encoding="utf-8")

    if not args.quiet:
        for r in results:
            q = r["fit_quality"] or {}
            print(f'{r["map_name"]:<18} fit {q.get("rms_m", "?")} m RMS '
                  f'({q.get("inliers_40px")}/{q.get("anchor_points")} anchors)')
            for tkey, c in r["categories"].items():
                print(f'   {tkey:<15} game {c["game_merged"]:>4}  '
                      f'map {c["map_count"]:>4}  matched {c["matched"]:>4}  '
                      f'game-only {c["game_only"]:>4}  map-only {c["map_only"]:>4}')
        print(f'\nreport -> {out}')
    return 0


if __name__ == "__main__":
    sys.exit(main())
