# HuntMap - interactive Hunt Showdown maps (recreation)

A self-contained recreation of the interactive maps at `https://hunt.kamille.ovh/maps/`,
rebuilt from the captured HAR archive plus a live inspection of the page's DOM and CSS.

Version: **1.0.0**

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
  "z":  true,                      // compounds only: boss lair (3 per map)
  "id": "m4dnPFMIE9Vi"
}
```

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

## 4. Layout

```
HuntMap/
├─ hunt.kamille.ovh_Archive [...].har   source capture
├─ har_extract/                         raw response bodies pulled out of the HAR
├─ huntmap/
│  ├─ index.html
│  ├─ css/styles.css
│  ├─ js/icons.js                       POI glyphs (original artwork)
│  ├─ js/app.js                         everything else
│  ├─ data/                             from the HAR, unmodified except as noted
│  │  ├─ maps.json  poi-types.json  data-1..4.json  translations.json
│  └─ images/                           1-4.webp (map art) + 1-4.svg (boundaries)
└─ .claude/launch.json
```

`data/translations.json` keeps only the `maps` sub-tree of each language and has its
em/en dashes replaced with hyphens. All other data files are byte-identical to the
HAR responses.

---

## 5. Attribution

Map imagery, boundary overlays, POI data and translations belong to
`hunt.kamille.ovh` and its contributors; Hunt: Showdown is Crytek's.
This is a local study rebuild of the front end, not a redistribution.
