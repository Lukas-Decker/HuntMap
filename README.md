# HuntMap - interactive Hunt Showdown maps (recreation)

A self-contained recreation of the interactive maps at `https://hunt.kamille.ovh/maps/`,
rebuilt from the captured HAR archive plus a live inspection of the page's DOM and CSS.

Version: **1.3.0**

---

## 1. What the HAR revealed

`hunt.kamille.ovh_Archive [26-07-21 17-59-46].har` contains 12 requests, all JSON.
No HTML or JS was captured, so the runtime behaviour was reconstructed by inspecting
the live page (DOM tree, computed styles, Leaflet panes) rather than reading its source.

| Endpoint | Purpose |
| --- | --- |
| `sounds/translations.json` | Full site i18n (`en`, `fr`). The `maps` sub-tree holds every string of the map UI. |
| `sounds/patches.json` | Weapon-patch manifest for another part of the site. Not used by the maps. |
| `maps/cache/poi-types.json` | 13 POI types: label, category key, border/fill colour, marker radius. |
| `api/maps` | Map list: id, slug, name, image path, overlay path, per-type POI counts. |
| `maps/cache/data-1..4.json` | All POIs for each of the 4 maps. |
| `api/me` | Returned **401** - account, contribution and moderation features need a login. |

### POI record shape

```jsonc
{
  "c":  [3423, 2521],              // position in image pixels (4096 x 4096)
  "d":  "Inside the big house.",   // optional description
  "u":  ["https://i.imgur.com/…"], // optional screenshots (absolute or site-relative)
  "x":  [3479, 2593],              // optional secondary coordinate
  "n":  "Port Reeker",             // compounds only: name
  "z":  true,                      // compounds only: landmark, NOT a boss lair
  "id": "m4dnPFMIE9Vi"
}
```

`z` was originally read here as "boss lair" - that is backwards. Cross-checking
against Hunt-ify settled it: on all four maps the three `z:true` entries are
exactly its **landmark** list, and the other 13 compounds are the boss lairs.

`x` appears on ~50 POIs. The live site renders exactly `250` markers for Stillwater Bayou
(= 250 POIs + 16 compounds), so `x` is **not** drawn as its own marker. It is treated here
as the vantage point the screenshot was taken from and is shown as a dashed camera pin only
while its POI is selected.

### Rendering facts measured on the live page

- Leaflet **1.9.4**, `L.CRS.Simple`, a 4096 x 4096 `.webp` image overlay
- a second `.svg` overlay per map paints the out-of-bounds area in `#680106`
- custom panes: boundary (410), names (450), poi (600)
- markers are `divIcon`s, not canvas: a coloured chip with an inline SVG glyph
- marker box size follows `round(14.4 + 0.7 x radius)` from `poi-types.json`
  (radius 8 -> 20 px, 18 -> 27, 20 -> 28, 30 -> 35, 35 -> 39)
- the scale bar read `100 m = 51 px` at 1/8 image scale, so **1 map = 1000 m**
  and **4.096 px per metre** at native zoom

---

## 2. What this recreation does

Everything that works without a backend:

**Map**
- 4 maps with instant switching, thumbnails and POI totals
- pan / zoom (wheel, buttons, `+` `-`), fit-to-view, max-bounds clamping
- dynamic scale bar (10 m to 1 km) and compass
- boundary overlay, optional edge vignette

**POIs**
- all 13 types with the site's own colours and radii, custom SVG glyphs
- per-type filters with live counts, enable / disable all
- screenshot filter: all / with photo / without photo, plus a "needs a photo" tally
- compound name labels and compound markers as separate toggles, boss lairs highlighted
- multi-screenshot badge on markers that have more than one image

**Interaction**
- hover preview with the first screenshot (S / M / L)
- side panel: description, screenshot grid, coordinates, id, copy-link
- full-screen image viewer with prev / next and arrow keys
- personal highlights (right-click a marker), stored per map on the device
- search over compound names, descriptions and type names
- deep links: `#m=<mapId>&p=<poiId>`

**Tools**
- ruler: two-point distance in metres, unlimited measurements, right-click to delete
- route: multi-point path with running total, `Enter` or Finish to close
- spotlight: dims everything but a circle around the cursor
- fullscreen (map only), help modal, settings modal
- shortcuts: `1`-`6` tools, `F` fullscreen, `/` search, `Esc` cancel, `+` / `-` zoom

**Other**
- 14 languages from the game's own language paks, language stored in a cookie
- filters, language, map, options and highlights persist in `localStorage`

### Deliberately not recreated

Anything behind `api/me`: login, POI drafts, submissions, moderation, verification,
history, achievements, leaderboards. Those need the original server.

### Theme

The palette is adopted from the sibling **HuntWiki** project
(`../HuntWiki/site/css/style.css`): smoked browns for the chrome, tallow gold
as the primary accent, blood red as the secondary, moss green for "all done",
Georgia for headings.

Marker colours have two modes, switchable under Settings -> Marker palette:

- **Weathered** (default) - the 13 POI types re-cast into the warm range so they
  sit on the brown chrome, defined in [js/theme.js](huntmap/js/theme.js).
- **Original** - the exact colours from `data/poi-types.json`.

`poi-types.json` itself is never modified; the warm set is an overlay table.

### Languages

14 languages, switchable from the top bar. The **selection is stored in a
cookie** (`hm_lang`, 10 years, `SameSite=Lax`), so it survives independently of
the per-device map settings in `localStorage`.

Nothing is machine-translated. `tools/build_i18n.py` pulls each string out of
Hunt's own language paks through a curated key map, and anything without a
pairing falls back to English:

```powershell
python tools/build_i18n.py            # -> huntmap/i18n/ui.json + REVIEW.md
python tools/build_i18n.py --suggest  # propose new keymap pairings
```

- `sources/i18n/keymap.json` - map UI key -> game `ui_*` key. Hand-curated,
  because matching purely on English text is unreliable: it pairs "Brutes" with
  the game's word for bears and "Beetles" with the consumable item.
- `sources/i18n/overrides.json` - **your manual layer.** Anything you put here
  wins over the game string and over the English fallback.
- `sources/i18n/REVIEW.md` - generated. Every mapped key with its English text
  and what each of the 13 languages resolved to, side by side, plus a list of
  every key that is still English. This is the page to read when hunting for
  wrong or missing conversions.

Of 205 UI keys, 14 currently have a genuine game equivalent (POI type names and
a few commands like Filters / Settings / Cancel). The rest are map-tool wording
the game has no string for. Add pairings to `keymap.json` as you find them, or
translate directly in `overrides.json`.

Reading the paks is done here rather than via Hunt-ify's `Localizer`: each row
is `key | English source | translation`, and that loader returns column 3, which
silently yields English for every non-English pak.

---

## 3. Running it

Any static file server works; `fetch` needs http(s), not `file://`.

```powershell
python -m http.server 8777 --directory huntmap
# then open http://localhost:8777
```

A ready-made config is in `.claude/launch.json` (name: `huntmap`).

Leaflet is loaded from unpkg, so the first load needs internet. POI screenshots are
also remote (imgur, or `hunt.kamille.ovh/maps/uploads/...` for site-hosted ones);
relative paths are resolved against `https://hunt.kamille.ovh/` at runtime.
Map images and boundary overlays are local, so the map itself renders offline.

---

## 4. Experimental mode

Off by default; enable it under Settings. It adds a separate **Experimental**
section to the filter rail holding POIs mined straight from the Hunt level
files, drawn with a dashed outline and a corner tick so they are never confused
with the community data.

| type | Stillwater | Lawson | DeSalle | Mammon's |
|---|--:|--:|--:|--:|
| Cargo Crates | 28 | 33 | 24 | 50 |
| Cargo Balloons | 10 | 10 | 10 | 10 |
| Weapon Crates | 73 | 44 | 63 | 60 |
| Water Crates | 9 | 8 | 9 | 8 |
| Balloon Supports | 6 | 6 | 6 | 5 |

```powershell
python tools/extract_cargo_pois.py     # -> huntmap/experimental/<map>.json
```

None of this comes from Hunt-ify's `structured/maps`: its extractor reads only
`mission_mission0.xml` plus `bm_*extraction*`, so the ~345 `optional/` layer
files per map - where every crate lives - were never mined. The `rs_*` layers
are variant sets the game picks a subset from, so the source layer is recorded
on each POI.

### From 3D world coordinates to the square map

Level entities carry a raw world position (`Pos="1123.8,966.7,55.0"`). Hunt-ify's
stored `fit.affine` is **not** used - it does not even reproduce Hunt-ify's own
image-space output (242 px off). Instead the transform is solved the same way
every other source in this repo is: take cash registers, which appear in both
the level file and the community map and match 87/87, try the eight axis
orientations and ICP each. The winner on all four maps is

    X ~ 4 * world_y - 2048        Y ~ 4 * world_x - 2048

i.e. world units are metres, transposed. Validated on a **hold-out class the
fit never saw**: workbenches land at 1.9-3.4 m median (31/32, 36/37, 31/31,
37/38 within 10 m). All 126/101/112/133 crates land inside the square.

### Reposition editor

Inside Experimental mode, **Edit positions** makes the experimental markers
draggable. The community POIs stay fixed - `huntmap/data/` is read-only here.

- moves are stored per map in `localStorage`
- **Export** / **Import** as JSON, **Reset** per map, or revert one POI from its
  side panel, which also reports how far it was moved
- a moved marker is tinted and its imported coordinate is kept, so the original
  is never lost

---

## 5. Comparing against other POI sources

`tools/compare_game_pois.py` diffs the community POI data against two
independent sources:

| key | source | good for |
|---|---|---|
| `game` | **Hunt-ify**, mined from the game files (`../Hunt-ify/structured/maps/*.json`) | exact placements: workbenches, cash registers, melee, spawns |
| `wiki` | **huntshowdown.wiki.gg** interactive map, via its DataMaps API | the types the game files cannot tag: armories, towers, brutes, easter eggs |

They are complementary, not redundant: each covers almost exactly what the
other misses.

```powershell
python tools/extract_wikigg_har.py                # HARs -> sources/wikigg/
python tools/compare_game_pois.py                 # both sources, 4 maps
python tools/compare_game_pois.py --source wiki --map 3
python tools/compare_game_pois.py --map 3 --tolerance 20
python tools/compare_game_pois.py --suggest       # layer discovery (game only)
```

The wiki data arrives as HAR captures of
`api.php?action=queryDataMap&pageid=…`; `extract_wikigg_har.py` normalises
each into `sources/wikigg/<map id>.json` (strips the HTML out of labels and
descriptions, records the page revision).

**It never writes to `huntmap/data/`.** There is a hard guard that aborts if the
output directory would land inside it; the community data stays the source of
truth and the report is the only output.

### None of them are on the same grid

- **Hunt-ify** coordinates are pixels on the game's own **2048 x 2048** overview
  render, with the axes **transposed** relative to the community image and the
  playable square filling only the middle ~1024 px.
- **wiki.gg** markers sit on a **~1000 x 1000** canvas, same axis order as the
  community image but its own scale and origin.

So neither is a 1:1 scale of the interactive map, and the script solves each
transform rather than assuming one:

1. **Seed** - least-squares affine from compound centroids matched by fuzzy
   name. Good to roughly 150 px; the sources define a compound's "centre"
   differently.
2. **Refine** - ICP. `game` uses one dense anchor layer (cash registers,
   per-object placements whose counts line up almost exactly). `wiki` has no
   such layer, so it runs a **typed** ICP: nearest-neighbour is resolved inside
   each 1:1 category, so a tower can only ever snap to a tower.

| source | solved transform | RMS |
|---|---|---|
| `game` | scale ~4.0, 90 deg, mirrored: `X = 4y - 2048`, `Y = 4x - 2048` | **2.3 - 2.7 m** |
| `wiki` | scale ~4.08, no rotation, no mirror: `X ~ 4.1x`, `Y ~ 4.1y` | **6.5 - 8.1 m** |

The wiki's larger residual is that source's own precision, not a fit failure:
its markers are hand-placed by editors on a 1000 px canvas, so ~1 m of
quantisation before human error. Read sub-10 m offsets there as agreement.
Pin either with `--transform` (requires a single `--source`).

### What lines up

**game** - the placement-accurate source:

| Type | grade | result across the 4 maps |
|---|---|---|
| `cash_register` | exact | 87/87, 81/81, 168/170, 208/209 matched, ~2 m median offset |
| `compound` | high | 16/16 on every map (matched by name, not distance) |
| `workbench` | high | 30/36, 32/35, 30/33, 37/41 |
| `melee_weapon` | medium | world shovels / pitchforks / sledges / axes |
| `spawn` | partial | per-team spawn points vs grouped spawn areas |
| `tower`, `big_tower`, `beetle` | proxy | correlated layers found by `--suggest`, not the object itself |
| `extraction`, `wild_target` | low | game set is offset or a large superset; counts only |
| `armory`, `brute`, `easter_egg` | - | no game layer at all |

**wiki** - covers the gaps, and corroborates them cleanly:

| Type | grade | result across the 4 maps |
|---|---|---|
| `compound` | exact | 16/16 on every map |
| `tower` | exact | 14/14, 17/19, 12/13, 3/3 - a real tag, no proxy needed |
| `big_tower` | exact | 3/3, 2/2, 4/4, 2/2 |
| `armory` | exact | 1/1, 1/1, 1/2, 4/8 |
| `brute` | high | 5/5, 4/5, 5/6, 5/6 |
| `extraction` | exact | 13/16, 19/20, 13/17, 18/20 |
| `easter_egg` | high | 15/22, 19/25, 21/27 (Lawson is sparse, see below) |
| `beetle` | high | the wiki lists 4-5 where the map has 7, on every map |
| `workbench`, `cash_register`, `melee_weapon`, `wild_target` | - | not tracked by the wiki |

Categories the wiki has and the community map does not: `Boss_Lairs`,
`Rotjaw_Spawns`, `Hellborn_Spawns`, `Event_Spawns`. They are counted in the
report but not diffed.

### Sparse-source guard

A source category holding under 40% of what the community map has is flagged
`!` / `SPARSE` and its differences are excluded from the "missing from the map"
narrative - the source is incomplete, not the map wrong. This fires on the
wiki's Lawson Delta spawn points (2 vs 26) and easter eggs (1 vs 11), and on
Mammon's Gulch spawn points (4 vs 37). Those wiki pages are genuinely thin
there; the captures are complete (no pagination, JSON intact).

`--suggest` ranks every game layer against every POI type by **lift** (coverage
divided by what a random layer of the same size would score), which is how the
tower / beetle proxies were found. Its output is a hint, not ground truth, and
only pairings promoted into the category tables are used by the diff.

Outputs land in `compare/`: `REPORT.md`, `SUGGEST.md`, per-map `report-N.json`,
and with `--overlay` an `overlay-<source>-N.json` per source, carrying every
matched pair, source-only point and map-only POI id. `REPORT.md` opens with a
per-map **coverage by type** table putting the map, `game` and `wiki` counts
side by side.

### Reading the reports in a browser

`tools/serve_compare.py` renders the Markdown as styled HTML in the same
weathered palette, with a document switcher, a sticky table of contents that
follows the scroll, and sticky table headers.

```powershell
python tools/serve_compare.py            # http://127.0.0.1:8778
python tools/serve_compare.py --open     # and launch a browser
python tools/serve_compare.py --dir compare --port 9000
```

The files are re-read on every request, so after re-running the compare tool a
refresh is enough - no restart. It binds to `127.0.0.1` only, serves the `.md`
files in that one directory and nothing else, and has no dependencies (the
small Markdown renderer is built in).

---

## 6. Layout

```
HuntMap/
├─ hunt.kamille.ovh_Archive [...].har   source capture (the community map)
├─ *-huntshowdown.wiki.gg_*.har        source captures (the wiki map, 4)
├─ har_extract/                         raw response bodies pulled out of the HAR
├─ huntmap/
│  ├─ index.html
│  ├─ css/styles.css                    HuntWiki-derived palette
│  ├─ js/theme.js                       marker palettes (warm / original)
│  ├─ js/experimental.js                game-mined POIs + reposition editor
│  ├─ i18n/ui.json                      generated, 14 languages
│  ├─ experimental/                      generated, crate POIs per map
│  ├─ js/icons.js                       POI glyphs (original artwork)
│  ├─ js/app.js                         everything else
│  ├─ data/                             from the HAR, unmodified except as noted
│  │  ├─ maps.json  poi-types.json  data-1..4.json  translations.json
│  └─ images/                           1-4.webp (map art) + 1-4.svg (boundaries)
├─ sources/
│  ├─ wikigg/                           wiki.gg markers extracted from the HARs
│  └─ i18n/                             keymap + your overrides + REVIEW.md
├─ tools/
│  ├─ extract_wikigg_har.py             wiki.gg HARs -> sources/wikigg/
│  ├─ extract_cargo_pois.py             level files -> huntmap/experimental/
│  ├─ build_i18n.py                     game language paks -> huntmap/i18n/
│  ├─ compare_game_pois.py              diff vs game + wiki data (read-only)
│  └─ serve_compare.py                  browse the reports at 127.0.0.1:8778
├─ compare/                             generated reports (regenerate any time)
└─ .claude/launch.json
```

`data/translations.json` keeps only the `maps` sub-tree of each language and has its
em/en dashes replaced with hyphens. All other data files are byte-identical to the
HAR responses.

---

## 7. Attribution

Map imagery, boundary overlays, POI data and translations belong to
`hunt.kamille.ovh` and its contributors; Hunt: Showdown is Crytek's.
This is a local study rebuild of the front end, not a redistribution.

The colour scheme comes from the sibling HuntWiki project, and the game-derived
POI data compared in section 4 comes from Hunt-ify, both local sibling repos.
The wiki POI data in section 4 belongs to huntshowdown.wiki.gg and its editors;
it is used here only to cross-check the community map, never merged into it.
