# HuntMap - interactive Hunt Showdown maps (recreation)

A self-contained recreation of the interactive maps at `https://hunt.kamille.ovh/maps/`,
rebuilt from the captured HAR archive plus a live inspection of the page's DOM and CSS.

Version: **1.1.0**

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
- English and French, straight from the site's own translation file
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

## 4. Comparing against the game files

`tools/compare_game_pois.py` diffs the community POI data against POIs mined
straight out of the game by the sibling **Hunt-ify** repo
(`../Hunt-ify/structured/maps/*.json`).

```powershell
python tools/compare_game_pois.py                 # all 4 maps -> compare/
python tools/compare_game_pois.py --map 3 --tolerance 20
python tools/compare_game_pois.py --suggest       # layer discovery
```

**It never writes to `huntmap/data/`.** There is a hard guard that aborts if the
output directory would land inside it; the community data stays the source of
truth and the report is the only output.

### The two datasets are not on the same grid

Hunt-ify coordinates are pixels on the game's own **2048 x 2048** overview
render, with the axes **transposed** relative to the community image and the
playable square filling only the middle ~1024 px. So they are not a 1:1 scale
of the interactive map and the script solves the transform rather than assuming
one:

1. **Seed** - least-squares affine from compound and landmark centroids matched
   by fuzzy name. Good to roughly 150 px; the two sources define a compound's
   "centre" differently.
2. **Refine** - ICP against a dense anchor layer (cash registers by default),
   whose counts line up almost exactly and whose placements are per-object.

It converges to the same answer on all four maps - scale ~4.0, 90 degree
rotation, mirrored, i.e. `X = 4y - 2048`, `Y = 4x - 2048` - at **2.3 to 2.7 m
RMS** over 87 / 81 / 173 / 208 anchor points. Pin it with `--transform` to skip
the fit.

### What lines up

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

`--suggest` ranks every game layer against every POI type by **lift** (coverage
divided by what a random layer of the same size would score), which is how the
tower / beetle proxies were found. Its output is a hint, not ground truth, and
only pairings promoted into `CATEGORIES` are used by the diff.

Outputs land in `compare/`: `REPORT.md`, `SUGGEST.md`, per-map `report-N.json`,
and with `--overlay` a `overlay-N.json` carrying every matched pair, game-only
point and map-only POI id.

---

## 5. Layout

```
HuntMap/
├─ hunt.kamille.ovh_Archive [...].har   source capture
├─ har_extract/                         raw response bodies pulled out of the HAR
├─ huntmap/
│  ├─ index.html
│  ├─ css/styles.css                    HuntWiki-derived palette
│  ├─ js/theme.js                       marker palettes (warm / original)
│  ├─ js/icons.js                       POI glyphs (original artwork)
│  ├─ js/app.js                         everything else
│  ├─ data/                             from the HAR, unmodified except as noted
│  │  ├─ maps.json  poi-types.json  data-1..4.json  translations.json
│  └─ images/                           1-4.webp (map art) + 1-4.svg (boundaries)
├─ tools/compare_game_pois.py           diff vs Hunt-ify game data (read-only)
├─ compare/                             generated reports (regenerate any time)
└─ .claude/launch.json
```

`data/translations.json` keeps only the `maps` sub-tree of each language and has its
em/en dashes replaced with hyphens. All other data files are byte-identical to the
HAR responses.

---

## 6. Attribution

Map imagery, boundary overlays, POI data and translations belong to
`hunt.kamille.ovh` and its contributors; Hunt: Showdown is Crytek's.
This is a local study rebuild of the front end, not a redistribution.

The colour scheme comes from the sibling HuntWiki project, and the game-derived
POI data compared in section 4 comes from Hunt-ify, both local sibling repos.
