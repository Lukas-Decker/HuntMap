#!/usr/bin/env python3
"""
build_i18n.py - build the map UI translations from Hunt's own language paks.

Nothing here is invented. Every non-English string is a real string shipped by
the game, resolved through a hand-curated key map; anything without a pairing
falls back to English.

Pipeline
--------
    huntmap/data/translations.json     en + fr, straight from the site HAR
              +
    sources/i18n/keymap.json           map UI key -> game ui_* key
              x
    <lang>_xml paks in Hunt-ify        the game's own translations
              +
    sources/i18n/overrides.json        your manual corrections, applied last
              =
    huntmap/i18n/ui.json               what the app loads
    sources/i18n/REVIEW.md             every key x language, and where it came from

Reading the paks
----------------
Hunt ships localization as Excel SpreadsheetML. Each row is

    cell 2 = ui_* key
    cell 3 = English source text
    cell 4 = the translation          <- only present in translated paks

Hunt-ify's own Localizer reads cell 3, which is right for english_xml and
silently returns English for every other language, so this module parses the
tables itself and keeps both columns. Holding the English source lets the
build verify that a pairing still points at the string it was chosen for: if
the game's English text drifts, the review flags it instead of quietly
shipping a translation of something else.

    python tools/build_i18n.py
    python tools/build_i18n.py --suggest      # propose keymap entries
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# display code -> (native label, pak folder). Same set HuntWiki ships.
LANGS = {
    "en": ("English", None),
    "de": ("Deutsch", "german_xml"),
    "fr": ("Français", "french_xml"),
    "es": ("Español", "spanish_xml"),
    "it": ("Italiano", "italian_xml"),
    "pl": ("Polski", "polish_xml"),
    "ru": ("Русский", "russian_xml"),
    "uk": ("Українська", "ukrainian_xml"),
    "tr": ("Türkçe", "turkish_xml"),
    "pt-BR": ("Português (BR)", "brazilian_xml"),
    "ja": ("日本語", "japanese_xml"),
    "ko": ("한국어", "korean_xml"),
    "zh-Hans": ("简体中文", "chineses_xml"),
    "zh-Hant": ("繁體中文", "chineset_xml"),
}


# --------------------------------------------------------------------------
# SpreadsheetML
# --------------------------------------------------------------------------

def _local(tag):
    return tag.rsplit("}", 1)[-1]


def read_pak(directory):
    """{ui_key: {"en": source, "tr": translation}} for one language folder."""
    table = {}
    for path in sorted(glob.glob(os.path.join(directory, "text_ui_*.xml"))):
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            continue
        for row in root.iter():
            if _local(row.tag) != "Row":
                continue
            cells = []
            for cell in row:
                if _local(cell.tag) != "Cell":
                    continue
                data = [d for d in cell if _local(d.tag) == "Data"]
                cells.append(data[0].text if data and data[0].text else "")
            if len(cells) >= 2 and cells[0].startswith("ui_"):
                table[cells[0]] = {"en": cells[1], "tr": cells[-1]}
    return table


def load_all_paks(huntify):
    out, missing = {}, []
    for code, (_label, pak) in LANGS.items():
        if pak is None:
            pak = "english_xml"
        directory = huntify / "extracted" / "paks" / pak / pak
        if not directory.is_dir():
            missing.append(code)
            continue
        out[code] = read_pak(str(directory))
    return out, missing


# --------------------------------------------------------------------------
# the map UI tree
# --------------------------------------------------------------------------

def flatten(tree, prefix=""):
    flat = {}
    for key, val in tree.items():
        path = prefix + key
        if isinstance(val, dict):
            flat.update(flatten(val, path + "."))
        elif isinstance(val, str):
            flat[path] = val
    return flat


def unflatten(flat):
    out = {}
    for path, val in flat.items():
        node = out
        parts = path.split(".")
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = val
    return out


def load_keymap(path):
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as fh:
        raw = json.load(fh)
    return {k: v for k, v in raw.items()
            if not k.startswith("_") and isinstance(v, str)}


def load_overrides(path):
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as fh:
        raw = json.load(fh)
    return {k: v for k, v in raw.items()
            if not k.startswith("_") and isinstance(v, dict)}


# --------------------------------------------------------------------------
# build
# --------------------------------------------------------------------------

def build(root, huntify):
    base_path = root / "huntmap" / "data" / "translations.json"
    with base_path.open(encoding="utf-8") as fh:
        base = json.load(fh)

    en_flat = flatten(base.get("en", {}).get("maps", {}))
    fr_flat = flatten(base.get("fr", {}).get("maps", {}))

    keymap = load_keymap(root / "sources" / "i18n" / "keymap.json")
    overrides = load_overrides(root / "sources" / "i18n" / "overrides.json")
    paks, missing_paks = load_all_paks(huntify)

    langs, provenance, drift = {}, {}, []

    for code in LANGS:
        flat = dict(en_flat)                       # English is the floor
        src = {k: "english" for k in en_flat}

        if code == "fr" and fr_flat:               # the HAR shipped real French
            for k, v in fr_flat.items():
                flat[k] = v
                src[k] = "site"

        table = paks.get(code, {})
        if code != "en" and table:
            for ui_key, game_key in keymap.items():
                entry = table.get(game_key)
                if not entry:
                    continue
                text = (entry.get("tr") or "").strip()
                if not text:
                    continue
                # guard: does this game key still say what it said when the
                # pairing was made? compare against the English pak, not ours,
                # since our UI wording may legitimately differ
                en_entry = paks.get("en", {}).get(game_key, {})
                if en_entry and entry.get("en") and \
                        en_entry.get("en", "").strip() != entry["en"].strip():
                    drift.append((code, ui_key, game_key))
                if ui_key in flat or ui_key.startswith("x_"):
                    flat[ui_key] = text
                    src[ui_key] = "game:" + game_key

        for k, v in (overrides.get(code) or {}).items():
            if isinstance(v, str) and v.strip():
                flat[k] = v
                src[k] = "override"

        langs[code] = flat
        provenance[code] = src

    return dict(base=base, en_flat=en_flat, langs=langs, provenance=provenance,
                keymap=keymap, paks=paks, missing_paks=missing_paks, drift=drift)


def write_ui(root, result):
    out = {}
    for code, flat in result["langs"].items():
        out[code] = dict(label=LANGS[code][0], maps=unflatten(flat))
    payload = dict(
        _generated_by="tools/build_i18n.py",
        _note="non-English strings are the game's own, resolved via "
              "sources/i18n/keymap.json; unmapped keys fall back to English",
        languages={c: LANGS[c][0] for c in LANGS},
        translations=out,
    )
    dest = root / "huntmap" / "i18n"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "ui.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return dest / "ui.json"


def write_review(root, result):
    langs = result["langs"]
    prov = result["provenance"]
    en = result["en_flat"]
    keymap = result["keymap"]

    codes = [c for c in LANGS if c != "en"]
    L = ["# Translation review\n",
         "Generated by `tools/build_i18n.py`. Every non-English string below is "
         "a real string shipped by the game, pulled through "
         "`sources/i18n/keymap.json`.\n",
         "**To correct anything on this page**, put the right text in "
         "`sources/i18n/overrides.json` under the language code and the key "
         "path, then re-run the build. Overrides are applied last and always "
         "win. To fix a *wrong pairing* rather than wrong wording, change the "
         "game key in `keymap.json` instead.\n"]

    if result["missing_paks"]:
        L.append("> Language paks not found in Hunt-ify: "
                 + ", ".join(result["missing_paks"]) + "\n")
    if result["drift"]:
        L.append("> **Pairing drift** - the game's English text for these keys "
                 "no longer matches across paks, so the pairing may be stale:\n")
        for code, ui_key, game_key in result["drift"][:20]:
            L.append(f"> - `{ui_key}` -> `{game_key}` ({code})")
        L.append("")

    # ---- coverage -------------------------------------------------------
    L.append("## Coverage\n")
    L.append("| language | from game | from site | override | English fallback |")
    L.append("|---|--:|--:|--:|--:|")
    for code in LANGS:
        src = prov[code]
        g = sum(1 for v in src.values() if v.startswith("game:"))
        s = sum(1 for v in src.values() if v == "site")
        o = sum(1 for v in src.values() if v == "override")
        e = sum(1 for v in src.values() if v == "english")
        L.append(f"| `{code}` {LANGS[code][0]} | {g} | {s} | {o} | {e} |")
    L.append("")
    L.append(f"Total UI keys: **{len(en)}**. Pairings in the key map: "
             f"**{len(keymap)}**.\n")

    # ---- the mapped keys, side by side ----------------------------------
    L.append("## Mapped keys\n")
    L.append("These are the keys the key map covers. Scan across a row: if a "
             "cell reads wrong for that language, override it.\n")
    mapped = [k for k in keymap if k in en or k.startswith("x_")]
    for ui_key in sorted(mapped):
        L.append(f"### `{ui_key}`\n")
        L.append(f"English: **{en.get(ui_key, '(experimental, not in base UI)')}**  ")
        L.append(f"game key: `{keymap[ui_key]}`\n")
        L.append("| language | text | source |")
        L.append("|---|---|---|")
        for code in codes:
            text = langs[code].get(ui_key, "")
            src = prov[code].get(ui_key, "english")
            tag = ("game" if src.startswith("game:") else src)
            L.append(f"| `{code}` | {text} | {tag} |")
        L.append("")

    # ---- what is still English -----------------------------------------
    unmapped = sorted(k for k in en if k not in keymap)
    L.append("## Keys with no game pairing\n")
    L.append(f"{len(unmapped)} of {len(en)} keys have no game equivalent and "
             "show English in every language. Most are map-tool wording the "
             "game has no string for. Add a pairing in `keymap.json` if you "
             "find one, or translate directly in `overrides.json`.\n")
    L.append("| key | English |")
    L.append("|---|---|")
    for k in unmapped:
        val = en[k].replace("|", "\\|")
        L.append(f"| `{k}` | {val[:80]} |")
    L.append("")

    dest = root / "sources" / "i18n" / "REVIEW.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(L), encoding="utf-8")
    return dest


def write_override_stub(root, result):
    """Seed overrides.json with every mapped key, so it can be edited in place."""
    path = root / "sources" / "i18n" / "overrides.json"
    if path.is_file():
        return path, False
    stub = {
        "_comment": [
            "Manual corrections, applied after the game strings and always",
            "winning. Shape: { \"<lang>\": { \"<ui.key.path>\": \"text\" } }.",
            "Delete or blank an entry to fall back to the generated value.",
            "See REVIEW.md for every key, its English text and what each",
            "language currently resolves to."
        ],
        "_example": {"de": {"filters": "Filter"}},
    }
    for code in LANGS:
        if code != "en":
            stub.setdefault(code, {})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(stub, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    return path, True


# --------------------------------------------------------------------------
# suggest
# --------------------------------------------------------------------------

def suggest(root, huntify, limit):
    base_path = root / "huntmap" / "data" / "translations.json"
    with base_path.open(encoding="utf-8") as fh:
        base = json.load(fh)
    en_flat = flatten(base.get("en", {}).get("maps", {}))
    keymap = load_keymap(root / "sources" / "i18n" / "keymap.json")

    directory = huntify / "extracted" / "paks" / "english_xml" / "english_xml"
    table = read_pak(str(directory))

    index = {}
    for key, entry in table.items():
        text = (entry.get("en") or "").strip()
        if text and len(text) < 60 and "%" not in text and "[[" not in text:
            index.setdefault(text.lower(), []).append(key)

    rows = []
    for ui_key, text in sorted(en_flat.items()):
        if ui_key in keymap:
            continue
        hits = index.get(text.strip().lower(), [])
        if hits:
            rows.append((ui_key, text, hits[:limit]))
    return rows


# --------------------------------------------------------------------------

def main(argv=None):
    # native language labels are Cyrillic / CJK; a cp1252 console would die
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

    root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(
        description="Build map UI translations from Hunt's language paks.")
    ap.add_argument("--huntify", default=str(root.parent / "Hunt-ify"),
                    help="path to the Hunt-ify repo (default: ../Hunt-ify)")
    ap.add_argument("--suggest", action="store_true",
                    help="propose keymap entries by exact English-text match "
                         "instead of building")
    ap.add_argument("--suggest-limit", type=int, default=3)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    huntify = Path(args.huntify).resolve()
    if not (huntify / "extracted" / "paks").is_dir():
        raise SystemExit(f"no extracted paks under {huntify}")

    if args.suggest:
        rows = suggest(root, huntify, args.suggest_limit)
        print(f"{len(rows)} unmapped key(s) have an exact English match in the "
              "game strings.\nThese are candidates only - many will be false "
              "friends, so check the meaning before adding them to keymap.json:\n")
        for ui_key, text, hits in rows:
            print(f'  "{ui_key}": "{hits[0]}",')
            print(f'       {text!r}  alternatives: {hits[1:] or "-"}')
        return 0

    result = build(root, huntify)
    ui_path = write_ui(root, result)
    stub_path, created = write_override_stub(root, result)
    review_path = write_review(root, result)

    if not args.quiet:
        prov = result["provenance"]
        print(f'{len(result["en_flat"])} UI keys, '
              f'{len(result["keymap"])} pairings, '
              f'{len(LANGS)} languages')
        for code in LANGS:
            src = prov[code]
            g = sum(1 for v in src.values() if v.startswith("game:"))
            o = sum(1 for v in src.values() if v == "override")
            s = sum(1 for v in src.values() if v == "site")
            print(f'   {code:8} {LANGS[code][0]:16} game {g:3}  site {s:3}  '
                  f'override {o:3}')
        if result["missing_paks"]:
            print("missing paks:", ", ".join(result["missing_paks"]))
        print(f'\n  {ui_path}')
        print(f'  {review_path}')
        print(f'  {stub_path}' + ("  (created)" if created else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
