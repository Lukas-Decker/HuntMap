"""
compare_game_pois.py - diff the community POI data against other POI sources.

READ-ONLY with respect to huntmap/data/. This script never writes there; it
only emits a report into the output directory (default: compare/).

Sources
-------
  game  Hunt-ify, mined straight out of the game files
        (../Hunt-ify/structured/maps/<slug>.json)
  wiki  huntshowdown.wiki.gg interactive map, via the DataMaps API
        (sources/wikigg/<map id>.json, produced by extract_wikigg_har.py)

Why a transform is needed
-------------------------
None of the three share a coordinate system:

  * HuntMap  - pixels on the community 4096 x 4096 map image.
  * Hunt-ify - pixels on the game's own 2048 x 2048 overview render, whose
               axes are transposed relative to the community image and where
               the playable square only occupies the middle ~1024 px.
  * wiki.gg  - a ~1000 x 1000 canvas, same axis order as the community image
               but its own scale and origin.

So neither source is a 1:1 scale of the interactive map and both must be
fitted, not assumed. Each is solved per map in two stages:

  1. Seed - least-squares affine from named compound / landmark centroids
     (matched by fuzzy name). Good to roughly 150 px; the sources define a
     compound's "centre" differently.
  2. Refine - ICP (iterative closest point). The game source uses one dense
     anchor layer (cash registers, precise per-object placements whose counts
     line up almost exactly) and converges to ~2.5 m RMS. The wiki source has
     no such layer, so it runs a typed ICP over every category that pairs 1:1
     and converges to ~5 m - which is that source's own precision, its markers
     being hand-placed by editors on a 1000 px canvas.

The solved transform is reported so it can be sanity-checked, and can be
pinned with --transform to skip the fit entirely.

Usage
-----
    python tools/compare_game_pois.py
    python tools/compare_game_pois.py --source wiki --map 3
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
GAME_CATEGORIES = {
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
# huntshowdown.wiki.gg
#
# The wiki covers almost exactly the types the game files cannot back
# (armories, both tower sizes, brutes, easter eggs) and misses the ones the
# game files nail (workbenches, cash registers, melee, wild targets), so the
# two sources are complementary rather than redundant.
#
# Its markers are hand-placed by editors on a 1000 px canvas, i.e. roughly
# 4 map pixels (~1 m) of quantisation before human error, so anything inside
# ~10 m counts as agreement.
# --------------------------------------------------------------------------

WIKI_CATEGORIES = {
    "compound": dict(
        layers=["Compounds"], match="name", merge=0.0, grade="exact",
        note="matched by name; both sources place the label by eye",
    ),
    "extraction": dict(
        layers=["Extraction_Points"], merge=0.0, tol=25.0, grade="exact",
        note="the wiki tracks the visible extracts, same as the community map",
    ),
    "tower": dict(
        layers=["Hunting_Tower"], merge=0.0, tol=25.0, grade="exact",
        note="the wiki tags hunting towers directly - no proxy needed",
    ),
    "big_tower": dict(
        layers=["Watch_Tower"], merge=0.0, tol=25.0, grade="exact",
        note="the wiki tags watch towers directly and separately",
    ),
    "armory": dict(
        layers=["Arsenals"], merge=0.0, tol=25.0, grade="exact",
        note="the wiki calls them arsenals",
    ),
    "brute": dict(
        layers=["Brute_Spawns"], merge=0.0, tol=30.0, grade="high",
        note="fixed brute spawn points, which the game files do not place",
    ),
    "beetle": dict(
        layers=["Beetle_Spawns"], merge=0.0, tol=30.0, grade="high",
        note="the wiki lists fewer than the community map on every map",
    ),
    "spawn": dict(
        layers=["Spawn_Points"], merge=0.0, tol=40.0, grade="high",
        note="hunter spawn points",
    ),
    "easter_egg": dict(
        layers=["Easter_Eggs"], merge=0.0, tol=25.0, grade="high",
        note="both sides are community observation, so disagreement is normal",
    ),
}

# wiki categories with no HuntMap type, reported for completeness
WIKI_EXTRA_CATEGORIES = {
    "Boss_Lairs": "individual lair positions inside compounds",
    "Rotjaw_Spawns": "Rotjaw spawn locations",
    "Hellborn_Spawns": "Hellborn spawn locations",
    "Event_Spawns": "seasonal event caches and circus spawns",
}

# HuntMap types the wiki does not cover at all
NO_WIKI_CATEGORY = {
    "workbench": "not tracked by the wiki map",
    "cash_register": "not tracked by the wiki map",
    "melee_weapon": "not tracked by the wiki map",
    "wild_target": "not tracked by the wiki map",
}

# a source category holding far fewer markers than the community map is
# treated as incomplete rather than as evidence the map is wrong
SPARSE_RATIO = 0.4
SPARSE_MIN = 5


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


def load_wikigg(wiki_dir, map_id):
    path = wiki_dir / f"{map_id}.json"
    if not path.is_file():
        raise SystemExit(
            f"missing wiki.gg data: {path}\n"
            "run tools/extract_wikigg_har.py first")
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


def wiki_points(wdata, cat_keys):
    """Same contract as game_points, over wiki.gg categories."""
    pts, labels, missing = [], [], []
    for key in cat_keys:
        rows = wdata.get("categories", {}).get(key)
        if rows is None:
            missing.append(key)
            continue
        for r in rows:
            pts.append((float(r["xy"][0]), float(r["xy"][1])))
            labels.append(r.get("label", ""))
    return pts, labels, missing


def collect_points(src, keys):
    """Dispatch to the right extractor for the source kind."""
    if src["kind"] == "game":
        return game_points(src["raw"], keys)
    return wiki_points(src["raw"], keys)


def named_points(src):
    """[(name, (x, y))] for whatever the source uses to name compounds."""
    out = []
    if src["kind"] == "game":
        raw = src["raw"]
        for c in raw.get("compounds", []) + raw.get("landmarks", []):
            out.append((c["name"], (float(c["cx"]), float(c["cy"]))))
    else:
        for r in src["raw"].get("categories", {}).get("Compounds", []):
            if r.get("label"):
                out.append((r["label"], (float(r["xy"][0]), float(r["xy"][1]))))
    return out


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

def seed_transform(hm, src_obj):
    """Affine seeded from compound names common to the map and the source."""
    hm_by_name = {norm_name(p["name"]): p["xy"]
                  for p in hm["pois"].get("compound", []) if p["name"]}
    named = {}
    for name, xy in named_points(src_obj):
        named[norm_name(name)] = xy

    src, dst, used, unmatched = [], [], [], []
    keys = list(hm_by_name)
    for gname, gxy in named.items():
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


def typed_icp(T, hm, src_obj, catmap, rounds=14, gate_px=800.0):
    """ICP for sources with no single dense anchor layer.

    Nearest-neighbour is resolved *within* each category that pairs 1:1, so a
    tower can only ever snap to a tower. The union of those pairs drives the
    refit, which gives enough constraints even when every category is small.
    """
    usable = [(t, s) for t, s in catmap.items()
              if s["grade"] in ("exact", "high") and hm["pois"].get(t)]
    if not usable:
        return T, None

    gate = gate_px
    pairs_n = 0
    for _ in range(rounds):
        keep_s, keep_d = [], []
        for tkey, spec in usable:
            pts, _lab, _miss = collect_points(src_obj, spec["layers"])
            dst = [p["xy"] for p in hm["pois"][tkey]]
            if not pts or not dst:
                continue
            for i, p in enumerate(apply_affine(T, pts)):
                j, d = nearest(p, dst)
                if d <= gate:
                    keep_s.append(pts[i])
                    keep_d.append(dst[j])
        if len(keep_s) < 6:
            break
        pairs_n = len(keep_s)
        try:
            new_T = fit_affine(keep_s, keep_d)
        except ValueError:
            break
        done = max(abs(a - b) for a, b in zip(new_T, T)) < 1e-9
        T = new_T
        if done:
            break
        gate = max(60.0, gate * 0.72)

    res = []
    for tkey, spec in usable:
        pts, _lab, _miss = collect_points(src_obj, spec["layers"])
        dst = [p["xy"] for p in hm["pois"][tkey]]
        if not pts or not dst:
            continue
        res += [nearest(p, dst)[1] for p in apply_affine(T, pts)]
    inl = [d for d in res if d <= 60.0]
    quality = dict(
        anchor="typed ICP over " + ", ".join(t for t, _ in usable),
        anchor_points=pairs_n,
        map_points=sum(len(hm["pois"][t]) for t, _ in usable),
        inliers_40px=len(inl),
        rms_px=round(math.sqrt(sum(d * d for d in inl) / len(inl)), 2) if inl else None,
        rms_m=round(math.sqrt(sum(d * d for d in inl) / len(inl)) * M_PER_PX, 2) if inl else None,
        median_px=round(sorted(res)[len(res) // 2], 2) if res else None,
    )
    return T, quality


def refine_icp(T, gdata, hm, anchor_key, rounds=12, gate_px=250.0):
    """Tighten the transform with ICP against a dense, reliable layer."""
    layer = gdata["layers"].get(anchor_key)
    if layer is None:
        return T, None
    src = [(float(i[0]), float(i[1])) for i in layer["items"]]
    dst_type = next((k for k, v in GAME_CATEGORIES.items()
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

def compare_source(hm, src_obj, catmap, map_id, tol_m, anchor,
                   want_overlay, pinned=None):
    """Align one source to the community map and diff every mapped category."""
    if pinned is not None:
        T, matched_names, unmatched_names = tuple(pinned), [], []
        quality = dict(anchor="pinned via --transform", anchor_points=None,
                       map_points=None, inliers_40px=None, rms_px=None,
                       rms_m=None, median_px=None)
    else:
        T, matched_names, unmatched_names = seed_transform(hm, src_obj)
        if src_obj["kind"] == "game":
            # one dense, per-object layer beats a typed pass here
            T, quality = refine_icp(T, src_obj["raw"], hm, anchor)
        else:
            T, quality = typed_icp(T, hm, src_obj, catmap)

    out = dict(
        source=src_obj["key"],
        source_label=src_obj["label"],
        origin=src_obj.get("origin", ""),
        transform=dict(
            matrix=[round(v, 6) for v in T],
            formula="X = ax*x + bx*y + cx ; Y = ay*x + by*y + cy",
            **decompose(T),
        ),
        seed=dict(compounds_matched=len(matched_names),
                  compounds_unmatched=unmatched_names),
        fit_quality=quality,
        categories={},
        unbacked_types=dict(src_obj.get("unbacked", {})),
        extra_categories={},
    )

    overlay = dict(map_id=map_id, source=src_obj["key"],
                   transform=[round(v, 6) for v in T],
                   categories={}) if want_overlay else None

    for tkey, spec in catmap.items():
        map_pois = hm["pois"].get(tkey, [])
        map_pts = [p["xy"] for p in map_pois]

        raw_pts, labels, missing_layers = collect_points(src_obj, spec["layers"])
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
            source_only = [i for i in range(len(merged)) if i not in seen_g]
            map_only = [j for j in range(len(map_pts)) if j not in seen_m]
        else:
            pairs, source_only, map_only = greedy_match(merged, map_pts, cat_tol_px)

        offsets_m = [d * M_PER_PX for _, _, d in pairs]

        # a source category far thinner than the map is incomplete, not proof
        # that the map invented POIs
        sparse = (len(map_pts) >= SPARSE_MIN
                  and len(merged) < SPARSE_RATIO * len(map_pts))

        entry = dict(
            grade=spec["grade"],
            matched_by=spec.get("match", "distance"),
            tolerance_m=None if spec.get("match") == "name" else cat_tol_m,
            note=spec["note"],
            source_layers=spec["layers"],
            missing_layers=missing_layers,
            sparse=sparse,
            source_raw=len(raw_pts),
            source_merged=len(merged),
            map_count=len(map_pts),
            matched=len(pairs),
            source_only=len(source_only),
            map_only=len(map_only),
            offset_m=stats(offsets_m),
            source_only_points=[[round(merged[i][0], 1), round(merged[i][1], 1)]
                                for i in source_only],
            map_only_pois=[dict(id=map_pois[j]["id"],
                                xy=[int(map_pts[j][0]), int(map_pts[j][1])],
                                name=map_pois[j]["name"],
                                desc=map_pois[j]["desc"][:70])
                           for j in map_only],
        )
        out["categories"][tkey] = entry

        if overlay is not None:
            overlay["categories"][tkey] = dict(
                source_only=entry["source_only_points"],
                map_only=[p["id"] for p in entry["map_only_pois"]],
                matched=[[map_pois[j]["id"],
                          [round(merged[i][0], 1), round(merged[i][1], 1)],
                          round(d * M_PER_PX, 2)]
                         for i, j, d in pairs],
            )

    # categories the source carries that the community map has no type for
    if src_obj["kind"] == "wiki":
        used = {k for s in catmap.values() for k in s["layers"]}
        for cat, rows in src_obj["raw"].get("categories", {}).items():
            if cat not in used:
                out["extra_categories"][cat] = dict(
                    count=len(rows),
                    note=WIKI_EXTRA_CATEGORIES.get(cat, ""))

    return out, overlay


def compare_map(root, huntify, wiki_dir, slug, map_id, tol_m, anchor,
                want_overlay, source_keys, pinned=None):
    """Run every requested source against one map."""
    hm = load_huntmap(root, map_id)

    result = dict(
        map_id=map_id,
        map_name=hm["name"],
        game_slug=slug,
        tolerance_m=tol_m,
        sources={},
    )
    overlays = {}

    for key in source_keys:
        if key == "game":
            raw = load_huntify(huntify, slug)
            src_obj = dict(kind="game", key="game",
                           label="Hunt-ify (game files)",
                           origin=f"structured/maps/{slug}.json",
                           raw=raw, unbacked=NO_GAME_LAYER)
            catmap = GAME_CATEGORIES
            result["game_display"] = raw.get("displayName")
        else:
            raw = load_wikigg(wiki_dir, map_id)
            src_obj = dict(kind="wiki", key="wiki",
                           label="huntshowdown.wiki.gg",
                           origin=f"{raw.get('title', '')} rev {raw.get('revision')}",
                           raw=raw, unbacked=NO_WIKI_CATEGORY)
            catmap = WIKI_CATEGORIES

        res, ov = compare_source(hm, src_obj, catmap, map_id, tol_m, anchor,
                                 want_overlay, pinned)
        result["sources"][key] = res
        if ov is not None:
            overlays[key] = ov

    return result, overlays


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
    src_obj = dict(kind="game", key="game", label="Hunt-ify (game files)",
                   raw=gdata, unbacked=NO_GAME_LAYER)
    T, _, _ = seed_transform(hm, src_obj)
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
         "pairings promoted into the category tables are used by the diff.\n"]
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

GRADE_NOTE = {
    "exact": "one source entity per map POI; counts should line up",
    "high": "same objects, minor bookkeeping differences expected",
    "medium": "same class of object, partial overlap expected",
    "partial": "source set is a subset or superset by design",
    "proxy": "a structural correlate, not the object itself",
    "low": "large superset or systematic offset; counts only",
}


def markdown(results, tol_m, source_keys):
    L = []
    add = L.append
    add("# Community POI data vs other POI sources\n")
    add("Generated by `tools/compare_game_pois.py`. The community data in "
        "`huntmap/data/` is **read-only** here - nothing in this report has "
        "been written back.\n")
    add(f"Default match tolerance: **{tol_m:g} m** (some categories set their "
        f"own). 1 map pixel = {M_PER_PX:.4f} m.\n")

    # ---- sources -------------------------------------------------------
    add("## Sources\n")
    add("| key | source | what it is good for |")
    add("|---|---|---|")
    if "game" in source_keys:
        add("| `game` | Hunt-ify, mined from the game files | exact placements: "
            "workbenches, cash registers, melee, spawns |")
    if "wiki" in source_keys:
        add("| `wiki` | huntshowdown.wiki.gg interactive map | the types the "
            "game files cannot tag: armories, towers, brutes, easter eggs |")
    add("")

    # ---- alignment -----------------------------------------------------
    add("## Alignment\n")
    add("Each source is fitted to the community map, not assumed to share its "
        "grid. Residual RMS is the *disagreement* between the two, so it also "
        "measures how precise the source is.\n")
    add("| Map | source | scale x | scale y | rotation | mirrored | inliers | RMS |")
    add("|---|---|--:|--:|--:|:--:|--:|--:|")
    for r in results:
        for key in source_keys:
            s = r["sources"].get(key)
            if not s:
                continue
            q = s["fit_quality"] or {}
            t = s["transform"]
            rms = (f'{q.get("rms_px")} px / {q.get("rms_m")} m'
                   if q.get("rms_px") else "n/a")
            inl = (f'{q.get("inliers_40px")} / {q.get("anchor_points")}'
                   if q.get("anchor_points") else "n/a")
            add(f'| {r["map_name"]} | `{key}` | {t["scale_x"]} | {t["scale_y"]} | '
                f'{t["rotation_deg"]}deg | {"yes" if t["mirrored"] else "no"} | '
                f'{inl} | {rms} |')
    add("")

    # ---- per map -------------------------------------------------------
    for r in results:
        add(f'## {r["map_name"]}\n')

        # combined view: every type, every source, side by side
        types = []
        for key in source_keys:
            s = r["sources"].get(key)
            if s:
                types += [t for t in s["categories"] if t not in types]
        if types:
            add("### Coverage by type\n")
            head = "| Type | map |"
            sep = "|---|--:|"
            for key in source_keys:
                head += f" {key} | matched | diff |"
                sep += "--:|--:|--:|"
            add(head)
            add(sep)
            for tkey in types:
                map_n = None
                cells = ""
                for key in source_keys:
                    s = r["sources"].get(key)
                    c = s["categories"].get(tkey) if s else None
                    if not c:
                        cells += " - | - | - |"
                        continue
                    map_n = c["map_count"]
                    flag = " !" if c["sparse"] else ""
                    diff = c["source_only"] + c["map_only"]
                    cells += (f' {c["source_merged"]}{flag} | {c["matched"]} |'
                              f' {diff} |')
                add(f'| {tkey} | {map_n if map_n is not None else "-"} |' + cells)
            add("")
            add("`!` marks a source category holding under "
                f"{int(SPARSE_RATIO * 100)}% of what the map has - read that as "
                "the source being incomplete, not the map being wrong.\n")

        for key in source_keys:
            s = r["sources"].get(key)
            if not s:
                continue
            add(f'### Source `{key}` - {s["source_label"]}\n')
            if s.get("origin"):
                add(f'<sub>{s["origin"]}</sub>\n')
            q = s["fit_quality"]
            if q and q.get("rms_m") is not None and q["rms_m"] > 8:
                add(f'> Alignment residual is {q["rms_m"]} m. For a hand-placed '
                    "source that is its own precision, not a fit failure - but "
                    "treat sub-10 m offsets below as agreement.\n")
            if s["seed"]["compounds_unmatched"]:
                add("> Compounds with no name match: "
                    + ", ".join(s["seed"]["compounds_unmatched"]) + "\n")

            add("| Type | grade | source | map | matched | source only | "
                "map only | median off |")
            add("|---|:--:|--:|--:|--:|--:|--:|--:|")
            for tkey, c in s["categories"].items():
                g = (f'{c["source_merged"]}'
                     + (f' ({c["source_raw"]} raw)'
                        if c["source_merged"] != c["source_raw"] else ""))
                off = f'{c["offset_m"]["median"]} m' if c["offset_m"] else "-"
                add(f'| {tkey} | {c["grade"]} | {g} | {c["map_count"]} | '
                    f'{c["matched"]} | {c["source_only"]} | {c["map_only"]} | '
                    f'{off} |')
            add("")

            sparse = [t for t, c in s["categories"].items() if c["sparse"]]
            if sparse:
                add(f'> **Incomplete in this source:** {", ".join(sparse)}. '
                    "Their differences are listed but should not be read as "
                    "gaps in the community map.\n")

            for tkey, c in s["categories"].items():
                if c["grade"] == "low" or c["sparse"]:
                    continue
                if not c["source_only"] and not c["map_only"]:
                    continue
                add(f'#### {tkey} <sub>({key})</sub>\n')
                add(f'_{c["note"]}_\n')
                if c["source_only"]:
                    add(f'**{c["source_only"]} in the source with no POI on the '
                        "map** (map-image coordinates):\n")
                    pts = ", ".join(f'({x:.0f}, {y:.0f})'
                                    for x, y in c["source_only_points"][:40])
                    add(pts + ("  ..." if c["source_only"] > 40 else "") + "\n")
                if c["map_only"]:
                    add(f'**{c["map_only"]} on the map with nothing in the '
                        "source:**\n")
                    add("| id | x / y | note |")
                    add("|---|---|---|")
                    for p in c["map_only_pois"][:40]:
                        label = p["name"] or p["desc"] or ""
                        add(f'| `{p["id"]}` | {p["xy"][0]} / {p["xy"][1]} | {label} |')
                    if c["map_only"] > 40:
                        add(f'| ... | | {c["map_only"] - 40} more |')
                    add("")

            if s["extra_categories"]:
                add(f'#### Source categories with no map type <sub>({key})</sub>\n')
                add("| category | count | what it is |")
                add("|---|--:|---|")
                for cat, info in sorted(s["extra_categories"].items()):
                    add(f'| `{cat}` | {info["count"]} | {info["note"]} |')
                add("")

            if s["unbacked_types"]:
                add(f'#### Map types this source cannot back <sub>({key})</sub>\n')
                for k, why in s["unbacked_types"].items():
                    add(f'- **{k}** - {why}')
                add("")

    add("---\n")
    add("### How to read the grades\n")
    for g, why in GRADE_NOTE.items():
        add(f'- **{g}** - {why}')
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
    ap.add_argument("--wikigg", default=str(root / "sources" / "wikigg"),
                    help="directory of extracted wiki.gg data "
                         "(default: sources/wikigg)")
    ap.add_argument("--source", default="all", choices=["all", "game", "wiki"],
                    help="which source(s) to diff against (default: all)")
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

    source_keys = ["game", "wiki"] if args.source == "all" else [args.source]

    huntify = Path(args.huntify).resolve()
    if "game" in source_keys and not (huntify / "structured" / "maps").is_dir():
        raise SystemExit(f"not a Hunt-ify checkout: {huntify}")

    wiki_dir = Path(args.wikigg).resolve()
    if "wiki" in source_keys and not wiki_dir.is_dir():
        if args.source == "all":
            print(f"note: no wiki.gg data at {wiki_dir}, comparing game only "
                  "(run tools/extract_wikigg_har.py to add it)", file=sys.stderr)
            source_keys = ["game"]
        else:
            raise SystemExit(f"no wiki.gg data at {wiki_dir}\n"
                             "run tools/extract_wikigg_har.py first")

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
        if len(source_keys) != 1:
            raise SystemExit("--transform pins one source's fit, so pass "
                             "--source game or --source wiki with it")
        parts = [p for p in re.split(r"[,\s]+", args.transform.strip()) if p]
        if len(parts) != 6:
            raise SystemExit("--transform needs exactly 6 numbers: ax,bx,cx,ay,by,cy")
        pinned = [float(p) for p in parts]

    results = []
    for slug, map_id in sorted(wanted, key=lambda kv: kv[1]):
        res, overlays = compare_map(root, huntify, wiki_dir, slug, map_id,
                                    args.tolerance, args.anchor, args.overlay,
                                    source_keys, pinned)
        results.append(res)
        (out / f"report-{map_id}.json").write_text(
            json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
        for key, ov in overlays.items():
            (out / f"overlay-{key}-{map_id}.json").write_text(
                json.dumps(ov, ensure_ascii=False), encoding="utf-8")

    (out / "REPORT.md").write_text(
        markdown(results, args.tolerance, source_keys), encoding="utf-8")

    if not args.quiet:
        for r in results:
            print(f'{r["map_name"]}')
            for key in source_keys:
                s = r["sources"].get(key)
                if not s:
                    continue
                q = s["fit_quality"] or {}
                print(f'  [{key}] fit {q.get("rms_m", "?")} m RMS '
                      f'({q.get("inliers_40px")}/{q.get("anchor_points")} pairs)')
                for tkey, c in s["categories"].items():
                    flag = " SPARSE" if c["sparse"] else ""
                    print(f'     {tkey:<15} src {c["source_merged"]:>4}  '
                          f'map {c["map_count"]:>4}  matched {c["matched"]:>4}  '
                          f'src-only {c["source_only"]:>4}  '
                          f'map-only {c["map_only"]:>4}{flag}')
        print(f'\nreport -> {out}')
    return 0


if __name__ == "__main__":
    sys.exit(main())
