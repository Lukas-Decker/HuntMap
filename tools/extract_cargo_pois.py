#!/usr/bin/env python3
"""
extract_cargo_pois.py - mine cargo / crate POIs out of the Hunt level files.

These do NOT come from Hunt-ify's structured/maps output. Its extractor reads
only `mission_mission0.xml` plus `bm_*extraction*`, so the ~345 `optional/`
layer files per map - which is where every crate lives - were never mined.

What is collected (entity classes, per map):

    spawnachor_BalloonCrates            cargo crate spawn anchors
    weapon_crate                        weapon crates
    WaterCrate                          water crates
    S_2mWorldSupportWW019BalloonCrate   balloon crate supports
    LootExtractionBalloon               the cargo balloon itself,
    LootExtractionBalloonGasCrank         plus its gas crank and
    LootExtractionBalloonPipeJunction     pipe junction

Coordinates
-----------
Level entities carry a raw 3D world position, e.g.

    Pos="1123.8049,966.71973,55.045574"     (x, y, z)

world -> community map px is solved here directly, NOT via Hunt-ify's stored
`fit.affine`. That affine does not even reproduce Hunt-ify's own image-space
output (median 242 px off on cemetery cash registers), so it is stale.

Instead the transform is fitted the same way the rest of this repo fits
sources: take a dense, reliable anchor class present in both the level file and
the community map - cash registers, which match 87/87 - try the eight axis
orientations, and ICP each. The winner on all four maps is the transposed form

    X ~ 4*world_y - 2048        Y ~ 4*world_x - 2048

which lands cash registers within ~2 m median. World units are metres.

Crates still fall outside the 4096 square (event props staged beyond the
playable boundary), so both the world coordinate and the derived map position
are written out and can be corrected by hand in the app's edit mode.

This is EXPERIMENTAL data. It is written to huntmap/experimental/ so the app
can fetch it, and is never merged into huntmap/data/.

    python tools/extract_cargo_pois.py
    python tools/extract_cargo_pois.py --map 1 --huntify ../Hunt-ify
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compare_game_pois as C          # noqa: E402  (transform + loaders)

# Hunt-ify entity class -> our experimental POI type
CLASS_TO_TYPE = {
    "spawnachor_BalloonCrates": "cargo_crate",
    "weapon_crate": "weapon_crate",
    "WaterCrate": "water_crate",
    "S_2mWorldSupportWW019BalloonCrate": "balloon_support",
    "LootExtractionBalloon": "cargo_balloon",
    "LootExtractionBalloonGasCrank": "cargo_balloon",
    "LootExtractionBalloonPipeJunction": "cargo_balloon",
}

# type -> display metadata, mirroring the shape of huntmap/data/poi-types.json
TYPE_META = {
    "cargo_crate": dict(
        label="Cargo Crates", radius=14,
        borderColor="#d8a13f", fillColor="#3b2c12",
        note="cargo crate spawn anchors from the Cargo Balloon event layers",
    ),
    "cargo_balloon": dict(
        label="Cargo Balloons", radius=26,
        borderColor="#e0b866", fillColor="#42331a",
        note="the balloon itself plus its gas crank and pipe junction",
    ),
    "weapon_crate": dict(
        label="Weapon Crates", radius=10,
        borderColor="#c98f8f", fillColor="#3a2222",
        note="placed across the rs_* variant layers; the game uses a subset",
    ),
    "water_crate": dict(
        label="Water Crates", radius=10,
        borderColor="#6fa8c4", fillColor="#22323c",
        note="firefighter-themed variant layers",
    ),
    "balloon_support": dict(
        label="Balloon Supports", radius=10,
        borderColor="#9aa79b", fillColor="#2b302b",
        note="crashed-balloon set dressing",
    ),
}

TYPE_ORDER = ["cargo_crate", "cargo_balloon", "weapon_crate",
              "water_crate", "balloon_support"]


def load_cryxmlb(huntify):
    """Hunt-ify ships the CryXmlB parser; reuse it rather than reimplement."""
    sys.path.insert(0, str(huntify / "tools"))
    try:
        import cryxmlb
    except ImportError as exc:
        raise SystemExit(f"cannot import Hunt-ify's cryxmlb: {exc}")
    return cryxmlb


def scan_level(cryxmlb, level_dir):
    """Every crate entity in one level, with its source layer file."""
    found = []
    files = sorted(glob.glob(os.path.join(level_dir, "optional", "*.xml")))
    main = os.path.join(level_dir, "mission_mission0.xml")
    if os.path.isfile(main):
        files.append(main)

    for path in files:
        try:
            tree = cryxmlb.parse(path)
        except Exception:
            continue
        for ent in tree.iter("Entity"):
            cls = ent.attrib.get("EntityClass", "")
            tkey = CLASS_TO_TYPE.get(cls)
            if not tkey:
                continue
            pos = ent.attrib.get("Pos", "").split(",")
            if len(pos) < 3:
                continue
            try:
                x, y, z = (float(pos[0]), float(pos[1]), float(pos[2]))
            except ValueError:
                continue
            found.append(dict(
                type=tkey,
                entity_class=cls,
                name=ent.attrib.get("Name", ""),
                layer=os.path.basename(path),
                world=[round(x, 3), round(y, 3), round(z, 3)],
            ))
    return found


ANCHOR_CLASS = "spawnachor_LootCashRegister"
ANCHOR_TYPE = "cash_register"


def _bbox(pts):
    return (min(p[0] for p in pts), max(p[0] for p in pts),
            min(p[1] for p in pts), max(p[1] for p in pts))


def _orient(pts, swap, sx, sy):
    return [((p[1] if swap else p[0]) * sx, (p[0] if swap else p[1]) * sy)
            for p in pts]


def solve_world_transform(world_anchors, map_anchors, rounds=30, gate=400.0):
    """Fit world -> community map px.

    The two frames may be transposed and/or mirrored, so all eight axis
    orientations are seeded from their bounding boxes and ICP-refined; the one
    with the lowest median residual wins.
    """
    if len(world_anchors) < 8 or len(map_anchors) < 8:
        raise SystemExit("not enough anchor points to solve the world fit")

    best = None
    bx0, bx1, by0, by1 = _bbox(map_anchors)
    for swap in (0, 1):
        for sx in (1, -1):
            for sy in (1, -1):
                o = _orient(world_anchors, swap, sx, sy)
                ax0, ax1, ay0, ay1 = _bbox(o)
                if ax1 == ax0 or ay1 == ay0:
                    continue
                s = min((bx1 - bx0) / (ax1 - ax0), (by1 - by0) / (ay1 - ay0))
                T = (s, 0.0, bx0 - s * ax0, 0.0, s, by0 - s * ay0)
                for _ in range(rounds):
                    keep_s, keep_d = [], []
                    for i, p in enumerate(C.apply_affine(T, o)):
                        j, d = C.nearest(p, map_anchors)
                        if d <= gate:
                            keep_s.append(o[i])
                            keep_d.append(map_anchors[j])
                    if len(keep_s) < 6:
                        break
                    try:
                        T = C.fit_affine(keep_s, keep_d)
                    except ValueError:
                        break
                res = sorted(C.nearest(p, map_anchors)[1]
                             for p in C.apply_affine(T, o))
                if not res:
                    continue
                med = res[len(res) // 2]
                if best is None or med < best[0]:
                    best = (med, (swap, sx, sy), T, res)

    if best is None:
        raise SystemExit("could not fit a world transform")

    med, orient, T, res = best
    inl = [r for r in res if r <= 40.0]
    quality = dict(
        anchor=ANCHOR_CLASS,
        orientation=dict(transposed=bool(orient[0]),
                         flip_x=orient[1] < 0, flip_y=orient[2] < 0),
        anchor_points=len(world_anchors),
        map_points=len(map_anchors),
        median_m=round(med * C.M_PER_PX, 2),
        inliers_40px=len(inl),
        rms_m=round(math.sqrt(sum(r * r for r in inl) / len(inl)) * C.M_PER_PX, 2)
        if inl else None,
    )
    return T, orient, quality


def world_to_map(cryxmlb, huntify, root, slug, map_id):
    """Solve world -> map px from cash-register anchors in this level."""
    level_dir = huntify / "extracted" / "paks" / "levels" / slug
    mission = level_dir / "mission_mission0.xml"
    if not mission.is_file():
        raise SystemExit(f"missing mission file: {mission}")

    tree = cryxmlb.parse(str(mission))
    world_anchors = []
    for ent in tree.iter("Entity"):
        if ent.attrib.get("EntityClass") != ANCHOR_CLASS:
            continue
        pos = ent.attrib.get("Pos", "").split(",")
        if len(pos) >= 2:
            world_anchors.append((float(pos[0]), float(pos[1])))

    hm = C.load_huntmap(root, map_id)
    map_anchors = [p["xy"] for p in hm["pois"].get(ANCHOR_TYPE, [])]

    T, orient, quality = solve_world_transform(world_anchors, map_anchors)

    def convert(wx, wy):
        o = _orient([(wx, wy)], *orient)
        return C.apply_affine(T, o)[0]

    return convert, T, orient, quality


def build_map(cryxmlb, huntify, root, slug, map_id):
    level_dir = huntify / "extracted" / "paks" / "levels" / slug
    if not level_dir.is_dir():
        raise SystemExit(f"missing extracted level: {level_dir}")

    raw = scan_level(cryxmlb, str(level_dir))
    convert, T, orient, quality = world_to_map(cryxmlb, huntify, root, slug, map_id)

    types = {k: [] for k in TYPE_ORDER}
    inside = 0
    for i, rec in enumerate(raw):
        mx, my = convert(rec["world"][0], rec["world"][1])
        ok = 0 <= mx <= C.MAP_PX and 0 <= my <= C.MAP_PX
        inside += ok
        types[rec["type"]].append(dict(
            id=f'x{map_id}_{rec["type"][:2]}{i:03d}',
            c=[round(mx, 1), round(my, 1)],
            world=rec["world"],
            layer=rec["layer"],
            entity=rec["entity_class"],
            name=rec["name"],
            in_bounds=ok,
        ))

    for k in types:
        types[k].sort(key=lambda r: (r["c"][1], r["c"][0]))

    return dict(
        map_id=map_id,
        game_slug=slug,
        experimental=True,
        source="Hunt-ify level files, optional/ layers",
        note="positions are derived from 3D world coordinates and are "
             "approximate; correct them in the app's experimental edit mode",
        world_transform=dict(
            orientation=dict(transposed=bool(orient[0]),
                             flip_x=orient[1] < 0, flip_y=orient[2] < 0),
            matrix=[round(v, 6) for v in T],
            formula="orient(world) then X = ax*u + bx*v + cx ; Y = ay*u + by*v + cy",
        ),
        fit_quality=quality,
        counts={k: len(v) for k, v in types.items()},
        total=len(raw),
        in_bounds=inside,
        type_meta={k: TYPE_META[k] for k in TYPE_ORDER},
        types=types,
    )


def main(argv=None):
    root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(
        description="Mine cargo/crate POIs from the Hunt level files "
                    "(experimental; never written into huntmap/data).")
    ap.add_argument("--huntify", default=str(root.parent / "Hunt-ify"),
                    help="path to the Hunt-ify repo (default: ../Hunt-ify)")
    ap.add_argument("--out", default=str(root / "huntmap" / "experimental"),
                    help="output directory (default: huntmap/experimental, "
                         "where the app can fetch it)")
    ap.add_argument("--map", default="all", help="map id 1-4, or 'all'")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    huntify = Path(args.huntify).resolve()
    if not (huntify / "extracted" / "paks" / "levels").is_dir():
        raise SystemExit(f"no extracted levels under {huntify}")

    out_dir = Path(args.out).resolve()
    data_dir = (root / "huntmap" / "data").resolve()
    if out_dir == data_dir or data_dir in out_dir.parents:
        raise SystemExit("refusing to write inside huntmap/data")
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.map == "all":
        wanted = sorted(C.SLUGS.items(), key=lambda kv: kv[1])
    else:
        mid = int(args.map)
        wanted = [(s, m) for s, m in C.SLUGS.items() if m == mid]
        if not wanted:
            raise SystemExit(f"unknown map id {args.map}")

    cryxmlb = load_cryxmlb(huntify)

    for slug, map_id in wanted:
        rec = build_map(cryxmlb, huntify, root, slug, map_id)
        (out_dir / f"{map_id}.json").write_text(
            json.dumps(rec, indent=1, ensure_ascii=False), encoding="utf-8")
        if not args.quiet:
            counts = "  ".join(f'{k}={v}' for k, v in rec["counts"].items())
            print(f'map {map_id} {slug:9} {rec["total"]:4} crates '
                  f'({rec["in_bounds"]} in bounds)   {counts}')

    if not args.quiet:
        print(f"\nwrote {len(wanted)} file(s) to {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
