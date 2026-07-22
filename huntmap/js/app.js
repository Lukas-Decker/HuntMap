/* ==================================================================
 * Hunt Showdown - interactive map (recreation)
 *
 * Data model (from maps/cache/*.json):
 *   data-N.json = { i, n, m, o, <category>: [ { c:[x,y], d?, u?[], x?[x,y], n?, z?, id } ] }
 *   c  = position in image pixels on a 4096 x 4096 map image
 *   z  = compounds only: true marks a LANDMARK (no boss lair).
 *        Confirmed against Hunt-ify: the 3 z=true entries per map
 *        are exactly its landmark list, the other 13 are the lairs.
 *   u  = screenshot URLs (absolute, or relative to the origin site)
 *   x  = secondary coordinate, treated here as the vantage point of the
 *        screenshot and only drawn while the POI is selected
 * ================================================================== */
(function () {
  'use strict';

  /* ---------------------------------------------------------------- */
  /* constants                                                        */
  /* ---------------------------------------------------------------- */

  var IMG_SIZE   = 4096;     // px, square
  var MAP_METERS = 1000;     // a Hunt map is 1 km across
  var M_PER_PX   = MAP_METERS / IMG_SIZE;
  var REMOTE     = 'https://hunt.kamille.ovh/';   // for relative screenshot paths

  var LS = {
    lang:   'hm.lang',
    map:    'hm.map',
    types:  'hm.types',
    extra:  'hm.extra',
    photo:  'hm.photo',
    opts:   'hm.opts',
    marks:  'hm.marks'
  };

  /* cookies (language is asked to persist independently of localStorage) */
  var CK = {
    lang:  'hm_lang',
    types: 'hm_types',     // POI type filters
    extra: 'hm_extra',     // names / compounds toggles
    photo: 'hm_photo',     // screenshot filter
    opts:  'hm_opts',      // settings panel
    map:   'hm_map'        // last viewed map
  };

  /* Flags come from js/flags.js as inline SVG. Emoji flags are impossible
     on Windows: Segoe UI Emoji has no regional-indicator glyphs and renders
     the pair as the letter codes instead. */
  function flagFor(code) {
    return window.HuntFlags
      ? window.HuntFlags.forLang(code)
      : '<span class="flag flag-text">' + esc(code) + '</span>';
  }

  /* ---------------------------------------------------------------- */
  /* state                                                            */
  /* ---------------------------------------------------------------- */

  var S = {
    lang: 'en',
    t: {},
    langNames: { en: 'English' },
    types: null,            // poi-types.json
    typeOrder: [],
    maps: [],
    mapId: 1,
    data: {},               // mapId -> raw json
    pois: [],               // flat list for the active map
    byId: {},
    on: {},                 // type -> bool
    extra: { names: true, compounds: true },
    photo: 'all',           // all | has | missing
    opts: { edgeFade: true, iconFrame: true, popupSize: 'm', poiClick: 'viewer', palette: 'warm' },
    marks: {},              // mapId -> { id: true }
    tool: 'select',
    selected: null,
    fullscreen: false
  };

  var map, layers = {}, panes = {};
  var baseLayer, boundaryLayer;
  var markers = {};          // poi id -> L.Marker
  var labelMarkers = [];
  var altLayer = null;
  var measures = [], routes = [], draft = null;
  var viewer = { urls: [], i: 0 };

  /* ---------------------------------------------------------------- */
  /* tiny helpers                                                     */
  /* ---------------------------------------------------------------- */

  var $  = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function t(key, vars) {
    var v = S.t[key];
    if (v == null) return key;
    if (vars) {
      Object.keys(vars).forEach(function (k) {
        v = v.replace(new RegExp('\\{' + k + '\\}', 'g'), vars[k]);
      });
    }
    return v;
  }

  function store(k, v) {
    try {
      if (v === undefined) {
        var raw = localStorage.getItem(k);
        return raw == null ? null : JSON.parse(raw);
      }
      localStorage.setItem(k, JSON.stringify(v));
    } catch (e) { /* private mode */ }
    return null;
  }

  /* language lives in a cookie, not localStorage, so the choice survives
     independently of the per-device map settings */
  function cookieGet(name) {
    var hit = document.cookie.split(';').map(function (c) { return c.trim(); })
      .filter(function (c) { return c.indexOf(name + '=') === 0; })[0];
    return hit ? decodeURIComponent(hit.slice(name.length + 1)) : null;
  }

  function cookieSet(name, value, days) {
    var d = new Date();
    d.setTime(d.getTime() + (days || 3650) * 864e5);
    document.cookie = name + '=' + encodeURIComponent(value)
      + ';expires=' + d.toUTCString() + ';path=/;SameSite=Lax';
  }

  function getJSON(url) {
    return fetch(url, { cache: 'no-cache' }).then(function (r) {
      if (!r.ok) throw new Error(url + ' -> ' + r.status);
      return r.json();
    });
  }

  /* a colour from the stylesheet, so JS-drawn overlays follow the theme */
  function ink(varName) {
    return window.HuntTheme.cssVar(varName, '#c9a24b');
  }

  /* themed colours for a POI type - see js/theme.js */
  function typeColors(type) {
    return window.HuntTheme.colors(type, S.types[type], S.opts.palette);
  }

  /* inline style block shared by markers, filter rows, panel and hits */
  function colorVars(type) {
    var c = typeColors(type);
    return '--poi-border:' + c.borderColor + ';--poi-fill:' + c.fillColor;
  }

  function imgUrl(u) {
    if (!u) return '';
    return /^https?:/i.test(u) ? u : REMOTE + u.replace(/^\/+/, '');
  }

  function toast(msg, kind) {
    var el = document.createElement('div');
    el.className = 'map-toast' + (kind ? ' is-' + kind : '');
    el.textContent = msg;
    $('#mapToasts').appendChild(el);
    setTimeout(function () { el.classList.add('is-out'); }, 2600);
    setTimeout(function () { el.remove(); }, 3100);
  }

  /* image pixel <-> leaflet latlng ------------------------------------ */
  function px(xy)  { return map.unproject([xy[0], xy[1]], 0); }
  function unpx(ll) { var p = map.project(ll, 0); return [p.x, p.y]; }
  function metersBetween(a, b) {
    return Math.hypot(a[0] - b[0], a[1] - b[1]) * M_PER_PX;
  }
  function fmtDist(m) {
    return m < 1000 ? Math.round(m) + ' m' : (m / 1000).toFixed(2) + ' km';
  }

  /* ---------------------------------------------------------------- */
  /* i18n                                                             */
  /* ---------------------------------------------------------------- */

  function applyI18n() {
    $$('[data-i18n]').forEach(function (el) {
      var v = S.t[el.dataset.i18n];
      if (v != null) el.textContent = v;
    });
    $$('[data-i18n-title]').forEach(function (el) {
      var v = S.t[el.dataset.i18nTitle];
      if (v != null) { el.title = v; el.setAttribute('aria-label', v); }
    });
    $('#searchInput').placeholder = t('search_location');
    $('#langCurrent').innerHTML = flagFor(S.lang);
    $('#langCurrent').title = S.langNames[S.lang] || S.lang;
    $$('#langMenu button').forEach(function (b) {
      b.classList.toggle('active', b.dataset.lang === S.lang);
    });
    document.documentElement.lang = S.lang;
    syncToggleAllLabel();
  }

  /* ---------------------------------------------------------------- */
  /* boot                                                             */
  /* ---------------------------------------------------------------- */

  function boot() {
    var CS = window.HuntStore;
    CS.migrate(CK.types, LS.types);
    CS.migrate(CK.extra, LS.extra);
    CS.migrate(CK.photo, LS.photo);
    CS.migrate(CK.opts, LS.opts);
    CS.migrate(CK.map, LS.map);

    S.lang = cookieGet(CK.lang) || store(LS.lang) || pickBrowserLang();
    S.photo = CS.get(CK.photo) || 'all';
    S.marks = store(LS.marks) || {};          // too big for a cookie
    Object.assign(S.opts,  CS.get(CK.opts)  || {});
    Object.assign(S.extra, CS.get(CK.extra) || {});

    Promise.all([
      /* generated by tools/build_i18n.py; the HAR-derived en/fr file is the
         fallback when it has not been built yet */
      getJSON('i18n/ui.json').catch(function () { return null; }),
      getJSON('data/translations.json'),
      getJSON('data/poi-types.json'),
      getJSON('data/maps.json')
    ]).then(function (res) {
      var bundle = res[0];
      var tr = (bundle && bundle.translations) || res[1];
      S.langNames = (bundle && bundle.languages)
        || { en: 'English', fr: 'Français' };
      if (!tr[S.lang]) S.lang = 'en';
      S.t = (tr[S.lang] && tr[S.lang].maps) || {};
      S.allT = tr;
      buildLangMenu();

      S.types = res[2];
      S.typeOrder = Object.keys(S.types);
      S.maps = res[3].maps || [];

      var saved = CS.get(CK.types);
      S.typeOrder.forEach(function (k) {
        S.on[k] = saved && k in saved ? !!saved[k] : true;
      });

      initMap();
      buildRail();
      applyI18n();
      wireUI();
      attachExperimental();
      applyOptions();

      var hash = parseHash();
      var start = hash.m || CS.get(CK.map) || 1;
      selectMap(start, hash.p);
    }).catch(function (e) {
      console.error(e);
      toast(t('err_load') || 'Failed to load map data. Please refresh.', 'error');
    });
  }

  /* best match for the browser's preferred language among what we shipped */
  function pickBrowserLang() {
    var want = (navigator.languages || [navigator.language || 'en']);
    var have = Object.keys(S.langNames || { en: 1 });
    for (var i = 0; i < want.length; i++) {
      var tag = String(want[i]);
      if (have.indexOf(tag) >= 0) return tag;                    // exact, e.g. pt-BR
      var base = tag.split('-')[0].toLowerCase();
      for (var j = 0; j < have.length; j++) {
        if (have[j].split('-')[0].toLowerCase() === base) return have[j];
      }
    }
    return 'en';
  }

  function buildLangMenu() {
    var menu = $('#langMenu');
    if (!menu) return;
    menu.innerHTML = Object.keys(S.langNames).map(function (code) {
      return '<button type="button" data-lang="' + esc(code) + '">'
           + '<span class="lang-flag">' + flagFor(code) + '</span>'
           + '<span class="lang-name">' + esc(S.langNames[code]) + '</span>'
           + '</button>';
    }).join('');
    menu.addEventListener('click', function (e) {
      var b = e.target.closest('button[data-lang]');
      if (b) setLang(b.dataset.lang);
    });
  }

  function setLang(code) {
    if (!S.allT[code]) return;
    S.lang = code;
    S.t = (S.allT[code] && S.allT[code].maps) || {};
    cookieSet(CK.lang, code);
    store(LS.lang, code);          // harmless mirror for older sessions
    $('#langSwitcher').classList.remove('is-open');
    applyI18n();
    refreshTypeLabels();
    updateCounts();
    buildSearchIndex();
    if (global_X()) global_X().refresh();
    if (S.selected) renderPanel(S.selected);
  }

  function parseHash() {
    var o = {};
    location.hash.replace(/^#/, '').split('&').forEach(function (kv) {
      var p = kv.split('=');
      if (p[0]) o[p[0]] = decodeURIComponent(p[1] || '');
    });
    if (o.m) o.m = parseInt(o.m, 10) || null;
    return o;
  }

  function writeHash() {
    var h = '#m=' + S.mapId + (S.selected ? '&p=' + S.selected.id : '');
    if (h !== location.hash) history.replaceState(null, '', h);
  }

  /* ---------------------------------------------------------------- */
  /* leaflet                                                          */
  /* ---------------------------------------------------------------- */

  function initMap() {
    map = L.map('map', {
      crs: L.CRS.Simple,
      minZoom: -3.5,
      maxZoom: 2,
      zoomSnap: 0.25,
      zoomDelta: 0.5,
      wheelPxPerZoomLevel: 90,
      zoomControl: false,
      attributionControl: false,
      doubleClickZoom: false,
      preferCanvas: false
    });

    [['basePane', 190], ['boundaryPane', 210], ['measurePane', 430],
     ['namesPane', 450], ['poiPane', 600], ['altPane', 590],
     ['xPane', 610]]
      .forEach(function (p) {
        panes[p[0]] = map.createPane(p[0]);
        panes[p[0]].style.zIndex = p[1];
      });
    panes.namesPane.style.pointerEvents = 'none';
    panes.boundaryPane.style.pointerEvents = 'none';

    /* debug handle: window.HuntMap.map / .S from the console */
    window.HuntMap = { map: map, S: S, px: px, unpx: unpx };

    map.on('zoomend', onZoom);
    map.on('move zoom', updateScale);
    map.on('movestart zoomstart', hideHoverNow);
    map.on('click', onMapClick);
    map.on('mousemove', onMapMove);
    map.on('contextmenu', function (e) { L.DomEvent.preventDefault(e.originalEvent); });
  }

  function mapBounds() {
    return L.latLngBounds(map.unproject([0, IMG_SIZE], 0), map.unproject([IMG_SIZE, 0], 0));
  }

  function onZoom() {
    var z = map.getZoom();
    var f = Math.max(0.72, Math.min(1.9, 1 + (z - fitZoom()) * 0.22));
    $('#map').style.setProperty('--poi-scale', f.toFixed(3));
    $('#map').classList.toggle('is-far', z < fitZoom() - 0.4);
  }

  function fitZoom() {
    var s = map.getSize();
    return Math.log2(Math.min(s.x, s.y) / IMG_SIZE);
  }

  function updateScale() {
    var z = map.getZoom();
    var pxPerM = Math.pow(2, z) / M_PER_PX;          // screen px for one metre
    var targets = [10, 25, 50, 100, 200, 250, 500, 1000];
    var pick = targets[0], best = 1e9;
    targets.forEach(function (m) {
      var w = m * pxPerM;
      if (w >= 40 && w <= 160 && Math.abs(w - 90) < best) { best = Math.abs(w - 90); pick = m; }
    });
    var el = $('#mapScale');
    el.style.width = Math.round(pick * pxPerM) + 'px';
    el.textContent = pick >= 1000 ? (pick / 1000) + ' km' : pick + ' m';
  }

  /* ---------------------------------------------------------------- */
  /* map selection + data                                             */
  /* ---------------------------------------------------------------- */

  function selectMap(id, focusPoi) {
    var meta = S.maps.filter(function (m) { return m.id === id; })[0] || S.maps[0];
    if (!meta) return;
    S.mapId = meta.id;
    window.HuntStore.set(CK.map, S.mapId);

    $('#mapTitle').textContent = meta.name;
    document.title = meta.name + ' - ' + t('title');
    $$('#mapSelector .map-card').forEach(function (c) {
      c.classList.toggle('is-selected', +c.dataset.id === S.mapId);
    });

    clearSelection();
    clearMeasures(true);
    clearRoutes(true);
    cancelDraft();
    hideHoverNow();

    var load = S.data[S.mapId]
      ? Promise.resolve(S.data[S.mapId])
      : getJSON('data/data-' + S.mapId + '.json').then(function (d) { S.data[S.mapId] = d; return d; });

    load.then(function (d) {
      buildPois(d);
      drawBase(meta);
      renderMarkers();
      renderLabels();
      updateCounts();
      buildSearchIndex();
      map.fitBounds(mapBounds(), { animate: false });
      onZoom();
      updateScale();
      if (focusPoi && S.byId[focusPoi]) focusOn(S.byId[focusPoi], true);
      if (global_X() && global_X().isEnabled()) global_X().onMap(S.mapId);
      writeHash();
    }).catch(function (e) {
      console.error(e);
      toast(t('err_load'), 'error');
    });
  }

  function buildPois(d) {
    S.pois = [];
    S.byId = {};
    S.typeOrder.forEach(function (type) {
      var key = S.types[type].categories;
      (d[key] || []).forEach(function (raw) {
        var p = {
          id: raw.id, type: type, c: raw.c, d: raw.d || '',
          u: raw.u || [], alt: raw.x || null, name: raw.n || '', landmark: !!raw.z
        };
        S.pois.push(p);
        S.byId[p.id] = p;
      });
    });
  }

  function drawBase(meta) {
    if (baseLayer) map.removeLayer(baseLayer);
    if (boundaryLayer) map.removeLayer(boundaryLayer);
    var b = mapBounds();
    baseLayer = L.imageOverlay('images/' + meta.id + '.webp', b, { pane: 'basePane' }).addTo(map);
    boundaryLayer = L.imageOverlay('images/' + meta.id + '.svg', b,
      { pane: 'boundaryPane', className: 'boundary-overlay' }).addTo(map);
    map.setMaxBounds(b.pad(0.25));
  }

  /* ---------------------------------------------------------------- */
  /* markers                                                          */
  /* ---------------------------------------------------------------- */

  function markerSize(type) {
    return Math.round(14.4 + 0.7 * (S.types[type].radius || 10));
  }

  function makeIcon(p) {
    var size = markerSize(p.type);
    var glyph = (window.HuntIcons.GLYPHS[p.type] || '');
    var cls = 'poi-marker'
      + (isMarked(p.id) ? ' is-marked' : '')
      + (p.landmark ? ' is-landmark' : '')
      + (p.u.length || p.type === 'compound' ? '' : ' no-photo');
    var html = '<span class="' + cls + '" style="' + colorVars(p.type) + ';'
      + 'font-size:' + Math.max(8, Math.round(size * 0.46)) + 'px">'
      + glyph
      + (p.u.length > 1 ? '<i class="poi-badge">' + p.u.length + '</i>' : '')
      + '</span>';
    return L.divIcon({
      className: 'poi-divicon',
      html: html,
      iconSize: [size, size],
      iconAnchor: [size / 2, size / 2]
    });
  }

  function renderMarkers() {
    Object.keys(markers).forEach(function (id) { map.removeLayer(markers[id]); });
    markers = {};

    S.pois.forEach(function (p) {
      if (p.type === 'compound') return;             // compounds get their own layer
      var m = L.marker(px(p.c), {
        icon: makeIcon(p),
        pane: 'poiPane',
        riseOnHover: true,
        keyboard: false,
        zIndexOffset: Math.round(20000 - p.c[1])
      });
      bindMarker(m, p);
      markers[p.id] = m;
    });

    S.pois.filter(function (p) { return p.type === 'compound'; }).forEach(function (p) {
      var m = L.marker(px(p.c), {
        icon: makeIcon(p), pane: 'poiPane', riseOnHover: true, keyboard: false
      });
      bindMarker(m, p);
      markers[p.id] = m;
    });

    applyFilters();
  }

  function bindMarker(m, p) {
    m.on('click', function (e) {
      L.DomEvent.stopPropagation(e);
      if (S.tool === 'ruler' || S.tool === 'route') return;
      selectPoi(p, S.opts.poiClick === 'viewer' && p.u.length ? 'viewer' : 'panel');
    });
    m.on('mouseover', function () { showHover(p, m); });
    m.on('mouseout', hideHover);
    m.on('contextmenu', function (e) {
      L.DomEvent.stopPropagation(e);
      L.DomEvent.preventDefault(e.originalEvent);
      toggleMark(p);
    });
  }

  function renderLabels() {
    labelMarkers.forEach(function (m) { map.removeLayer(m); });
    labelMarkers = [];
    S.pois.filter(function (p) { return p.type === 'compound' && p.name; }).forEach(function (p) {
      var m = L.marker(px(p.c), {
        pane: 'namesPane',
        interactive: false,
        icon: L.divIcon({
          className: 'compound-label',
          html: '<span class="marker-label_text' + (p.landmark ? ' is-landmark' : '') + '">' + esc(p.name) + '</span>',
          iconSize: [12, 12], iconAnchor: [6, 40]
        })
      });
      m._poi = p;
      labelMarkers.push(m);
    });
    applyFilters();
  }

  /* ---------------------------------------------------------------- */
  /* filters                                                          */
  /* ---------------------------------------------------------------- */

  function passesPhoto(p) {
    if (S.photo === 'has') return p.u.length > 0;
    if (S.photo === 'missing') return p.u.length === 0;
    return true;
  }

  function visible(p) {
    if (p.type === 'compound') return S.extra.compounds && S.on.compound !== false;
    return !!S.on[p.type] && passesPhoto(p);
  }

  function applyFilters() {
    S.pois.forEach(function (p) {
      var m = markers[p.id];
      if (!m) return;
      var show = visible(p);
      if (show && !map.hasLayer(m)) m.addTo(map);
      else if (!show && map.hasLayer(m)) map.removeLayer(m);
    });
    labelMarkers.forEach(function (m) {
      var show = S.extra.names;
      if (show && !map.hasLayer(m)) m.addTo(map);
      else if (!show && map.hasLayer(m)) map.removeLayer(m);
    });
    updateCounts();
  }

  function updateCounts() {
    var shown = 0, missing = 0;
    S.pois.forEach(function (p) {
      if (p.type === 'compound') return;
      if (visible(p)) shown++;
      if (S.on[p.type] && !p.u.length) missing++;
    });
    $('#filterCount').textContent = t('poi_count', { n: shown });
    $('#filterMissing').textContent = missing
      ? t('poi_count_missing', { n: missing })
      : t('photos_done');
    $('#filterMissing').classList.toggle('is-done', !missing);

    $$('#filterChips .filter-row').forEach(function (row) {
      var type = row.dataset.type;
      var n = S.pois.filter(function (p) { return p.type === type && passesPhoto(p); }).length;
      $('.filter-row-count', row).textContent = n;
      row.classList.toggle('is-empty', n === 0);
    });
  }

  function syncToggleAllLabel() {
    var any = S.typeOrder.some(function (k) { return k !== 'compound' && S.on[k]; });
    var b = $('#filterToggleAll');
    if (b) b.textContent = any ? t('disable_all') : t('enable_all');
  }

  /* ---------------------------------------------------------------- */
  /* rail                                                             */
  /* ---------------------------------------------------------------- */

  function buildRail() {
    var sel = $('#mapSelector');
    sel.innerHTML = S.maps.map(function (m) {
      var total = Object.keys(m.poiCounts || {}).reduce(function (a, k) {
        return a + m.poiCounts[k];
      }, 0);
      return '<button class="map-card" type="button" data-id="' + m.id + '">'
           +   '<span class="map-card-img" style="background-image:url(images/' + m.id + '.webp)"></span>'
           +   '<span class="map-card-scrim"></span>'
           +   '<span class="map-card-overlay">'
           +     '<span class="map-card-name">' + esc(m.name) + '</span>'
           +     '<span class="map-card-count">' + total + ' POI</span>'
           +   '</span>'
           +   '<span class="map-card-check">&#10003;</span>'
           + '</button>';
    }).join('');
    sel.addEventListener('click', function (e) {
      var c = e.target.closest('.map-card');
      if (c) selectMap(+c.dataset.id);
    });

    var chips = $('#filterChips');
    chips.innerHTML = sortedTypes()
      .map(function (k) {
        return '<button class="filter-row' + (S.on[k] ? ' is-on' : '') + '" type="button" data-type="' + k + '"'
             + ' style="' + colorVars(k) + '">'
             +   '<span class="filter-row-ico">' + (window.HuntIcons.GLYPHS[k] || '') + '</span>'
             +   '<span class="filter-row-label" data-poi-label="' + k + '"></span>'
             +   '<span class="filter-row-count"></span>'
             +   '<span class="switch' + (S.on[k] ? ' is-on' : '') + '"></span>'
             + '</button>';
      }).join('');

    chips.addEventListener('click', function (e) {
      var row = e.target.closest('.filter-row');
      if (!row) return;
      var k = row.dataset.type;
      S.on[k] = !S.on[k];
      row.classList.toggle('is-on', S.on[k]);
      $('.switch', row).classList.toggle('is-on', S.on[k]);
      window.HuntStore.set(CK.types, S.on);
      applyFilters();
      syncToggleAllLabel();
    });

    $('#filterExtra').addEventListener('click', function (e) {
      var row = e.target.closest('.filter-row');
      if (!row) return;
      var k = row.dataset.extra;
      S.extra[k] = !S.extra[k];
      row.classList.toggle('is-on', S.extra[k]);
      $('.switch', row).classList.toggle('is-on', S.extra[k]);
      window.HuntStore.set(CK.extra, S.extra);
      applyFilters();
    });

    $('#filterToggleAll').addEventListener('click', function () {
      var any = S.typeOrder.some(function (k) { return k !== 'compound' && S.on[k]; });
      S.typeOrder.forEach(function (k) { if (k !== 'compound') S.on[k] = !any; });
      $$('#filterChips .filter-row').forEach(function (row) {
        row.classList.toggle('is-on', !any);
        $('.switch', row).classList.toggle('is-on', !any);
      });
      window.HuntStore.set(CK.types, S.on);
      applyFilters();
      syncToggleAllLabel();
    });

    $$('.filter-image-seg .seg-btn').forEach(function (b) {
      b.addEventListener('click', function () {
        S.photo = b.dataset.photo;
        $$('.filter-image-seg .seg-btn').forEach(function (o) {
          o.classList.toggle('is-on', o === b);
        });
        window.HuntStore.set(CK.photo, S.photo);
        applyFilters();
      });
    });
    $$('.filter-image-seg .seg-btn').forEach(function (b) {
      b.classList.toggle('is-on', b.dataset.photo === S.photo);
    });

    $$('#filterExtra .filter-row').forEach(function (row) {
      var on = S.extra[row.dataset.extra];
      row.classList.toggle('is-on', on);
      $('.switch', row).classList.toggle('is-on', on);
    });

    refreshTypeLabels();
  }

  /* filter rows read A-Z by the label actually shown, so the order follows
     the active language instead of the order the data happens to be in */
  function sortedTypes() {
    var names = S.t.poi_types || {};
    var label = function (k) {
      return String(names[k] || (S.types[k] && S.types[k].label) || k);
    };
    return S.typeOrder
      .filter(function (k) { return k !== 'compound'; })
      .sort(function (a, b) {
        return label(a).localeCompare(label(b), S.lang, { sensitivity: 'base' });
      });
  }

  function refreshTypeLabels() {
    var names = S.t.poi_types || {};
    $$('[data-poi-label]').forEach(function (el) {
      var k = el.dataset.poiLabel;
      el.textContent = names[k] || S.types[k].label;
    });
    reorderTypeRows();
  }

  /* Re-sort the existing rows in place after a language change. buildRail()
     would also do it, but it binds its click handler to the container, so
     calling it twice would double-fire every toggle. */
  function reorderTypeRows() {
    var chips = $('#filterChips');
    if (!chips) return;
    var order = sortedTypes();
    order.forEach(function (k) {
      var row = $('.filter-row[data-type="' + k + '"]', chips);
      if (row) chips.appendChild(row);
    });
  }

  /* ---------------------------------------------------------------- */
  /* selection + side panel                                           */
  /* ---------------------------------------------------------------- */

  function selectPoi(p, mode) {
    S.selected = p;
    $$('.poi-divicon .poi-marker.is-active').forEach(function (e) { e.classList.remove('is-active'); });
    var m = markers[p.id];
    if (m && m._icon) {
      var span = m._icon.querySelector('.poi-marker');
      if (span) span.classList.add('is-active');
    }
    drawAlt(p);
    renderPanel(p);
    writeHash();
    if (mode === 'viewer' && p.u.length) openViewer(p.u, 0);
  }

  function clearSelection() {
    S.selected = null;
    $$('.poi-divicon .poi-marker.is-active').forEach(function (e) { e.classList.remove('is-active'); });
    drawAlt(null);
    $('#panelBody').hidden = true;
    $('#panelEmpty').hidden = false;
  }

  function focusOn(p, zoom) {
    if (!S.on[p.type] && p.type !== 'compound') {
      S.on[p.type] = true;
      var row = $('#filterChips .filter-row[data-type="' + p.type + '"]');
      if (row) { row.classList.add('is-on'); $('.switch', row).classList.add('is-on'); }
      window.HuntStore.set(CK.types, S.on);
      applyFilters();
    }
    map.setView(px(p.c), zoom ? Math.max(map.getZoom(), -0.5) : map.getZoom(), { animate: true });
    selectPoi(p, 'panel');
  }

  function drawAlt(p) {
    if (altLayer) { map.removeLayer(altLayer); altLayer = null; }
    if (!p || !p.alt) return;
    altLayer = L.layerGroup([], { pane: 'altPane' }).addTo(map);
    L.polyline([px(p.c), px(p.alt)], {
      pane: 'altPane', color: ink('--accent-hi'), weight: 1.5, dashArray: '4 4', opacity: 0.8, interactive: false
    }).addTo(altLayer);
    L.marker(px(p.alt), {
      pane: 'altPane', interactive: false,
      icon: L.divIcon({
        className: 'alt-divicon',
        html: '<span class="alt-marker">' + window.HuntIcons.ui('camera') + '</span>',
        iconSize: [22, 22], iconAnchor: [11, 11]
      })
    }).addTo(altLayer);
  }

  function renderPanel(p) {
    var names = S.t.poi_types || {};
    var def = S.types[p.type];
    var title = p.name || names[p.type] || def.label;
    var shots = p.u.map(function (u, i) {
      return '<button class="shot" type="button" data-i="' + i + '">'
           + '<img loading="lazy" src="' + esc(imgUrl(u)) + '" alt=""></button>';
    }).join('');

    var html =
      '<header class="panel-head" style="' + colorVars(p.type) + '">'
      +  '<span class="panel-badge">' + (window.HuntIcons.GLYPHS[p.type] || '') + '</span>'
      +  '<div class="panel-head-text">'
      +    '<h2>' + esc(title) + '</h2>'
      +    '<span class="panel-sub">' + esc(names[p.type] || def.label) + (p.type === 'compound' ? (p.landmark ? ' &middot; landmark' : ' &middot; boss lair') : '') + '</span>'
      +  '</div>'
      +  '<button class="panel-close" type="button" aria-label="Close">&times;</button>'
      + '</header>'
      + (p.d ? '<p class="panel-desc">' + esc(p.d) + '</p>' : '')
      + (shots
          ? '<section class="panel-sec"><h3>' + esc(t('photos')) + ' <em>' + p.u.length + '</em></h3>'
            + '<div class="shots">' + shots + '</div></section>'
          : (p.type === 'compound' ? ''
              : '<p class="panel-nophoto">' + esc(t('poi_count_missing', { n: 1 })) + '</p>'))
      + '<section class="panel-sec panel-meta">'
      +   '<div class="meta-row"><span>X / Y</span><b>' + p.c[0] + ' / ' + p.c[1] + '</b></div>'
      +   (p.alt ? '<div class="meta-row"><span>' + esc(t('photos')) + ' @</span><b>' + p.alt[0] + ' / ' + p.alt[1] + '</b></div>' : '')
      +   '<div class="meta-row"><span>ID</span><b class="mono">' + esc(p.id) + '</b></div>'
      + '</section>'
      + '<div class="panel-actions">'
      +   '<button class="btn btn-ghost" id="pnMark">' + window.HuntIcons.ui('star')
      +     '<span>' + esc(t(isMarked(p.id) ? 'unhighlight_poi' : 'highlight_poi')) + '</span></button>'
      +   '<button class="btn btn-ghost" id="pnLink">' + window.HuntIcons.ui('link') + '<span>Copy link</span></button>'
      + '</div>'
      + '<p class="panel-note">' + esc(t('highlight_note')) + '</p>';

    var body = $('#panelBody');
    body.innerHTML = html;
    body.hidden = false;
    $('#panelEmpty').hidden = true;

    $('.panel-close', body).addEventListener('click', clearSelection);
    $$('.shot', body).forEach(function (b) {
      b.addEventListener('click', function () { openViewer(p.u, +b.dataset.i); });
    });
    $('#pnMark').addEventListener('click', function () { toggleMark(p); renderPanel(p); });
    $('#pnLink').addEventListener('click', function () {
      var url = location.origin + location.pathname + '#m=' + S.mapId + '&p=' + p.id;
      navigator.clipboard && navigator.clipboard.writeText(url);
      toast('Link copied');
    });
  }

  /* highlights ------------------------------------------------------- */
  function isMarked(id) {
    return !!(S.marks[S.mapId] && S.marks[S.mapId][id]);
  }
  function toggleMark(p) {
    S.marks[S.mapId] = S.marks[S.mapId] || {};
    if (S.marks[S.mapId][p.id]) delete S.marks[S.mapId][p.id];
    else S.marks[S.mapId][p.id] = true;
    store(LS.marks, S.marks);
    var m = markers[p.id];
    if (m) {
      m.setIcon(makeIcon(p));
      if (S.selected && S.selected.id === p.id && m._icon) {
        var s = m._icon.querySelector('.poi-marker');
        if (s) s.classList.add('is-active');
      }
    }
  }

  /* ---------------------------------------------------------------- */
  /* hover preview                                                    */
  /* ---------------------------------------------------------------- */

  var hoverTimer = null;

  function showHover(p, m) {
    clearTimeout(hoverTimer);
    var pop = $('#imagePopup');
    var names = S.t.poi_types || {};
    var cap = p.name || p.d || names[p.type] || S.types[p.type].label;
    var img = $('img', pop);

    if (p.u.length) { img.src = imgUrl(p.u[0]); img.hidden = false; }
    else { img.removeAttribute('src'); img.hidden = true; }

    $('figcaption', pop).textContent = cap;
    pop.hidden = false;
    pop.dataset.size = S.opts.popupSize;

    var pt = map.latLngToContainerPoint(m.getLatLng());
    var stage = $('#mapStage').getBoundingClientRect();
    var w = pop.offsetWidth, h = pop.offsetHeight;
    var left = pt.x + 20, top = pt.y - h / 2;
    if (left + w > stage.width - 8) left = pt.x - w - 20;
    top = Math.max(8, Math.min(stage.height - h - 8, top));
    pop.style.left = left + 'px';
    pop.style.top = top + 'px';
  }

  function hideHover() {
    hoverTimer = setTimeout(hideHoverNow, 60);
  }

  function hideHoverNow() {
    clearTimeout(hoverTimer);
    var pop = $('#imagePopup');
    pop.hidden = true;
    $('img', pop).removeAttribute('src');
  }

  /* ---------------------------------------------------------------- */
  /* image viewer                                                     */
  /* ---------------------------------------------------------------- */

  function openViewer(urls, i) {
    viewer.urls = urls;
    viewer.i = i || 0;
    $('#imageViewer').hidden = false;
    paintViewer();
  }
  function paintViewer() {
    $('#ivImage').src = imgUrl(viewer.urls[viewer.i]);
    $('#ivCounter').textContent = (viewer.i + 1) + ' / ' + viewer.urls.length;
    var multi = viewer.urls.length > 1;
    $('#ivPrev').hidden = !multi;
    $('#ivNext').hidden = !multi;
  }
  function stepViewer(d) {
    viewer.i = (viewer.i + d + viewer.urls.length) % viewer.urls.length;
    paintViewer();
  }
  function closeViewer() { $('#imageViewer').hidden = true; $('#ivImage').removeAttribute('src'); }

  /* ---------------------------------------------------------------- */
  /* tools: ruler / route / spotlight / fullscreen                    */
  /* ---------------------------------------------------------------- */

  function setTool(name) {
    if (name === 'fullscreen') { toggleFullscreen(); return; }
    if (name === 'help') { openModal('#helpModal'); return; }

    cancelDraft();
    S.tool = S.tool === name && name !== 'select' ? 'select' : name;

    $$('.tool-btn').forEach(function (b) {
      b.classList.toggle('is-on', b.dataset.tool === S.tool);
    });
    $('#map').classList.toggle('is-measuring', S.tool === 'ruler' || S.tool === 'route');

    $('#spotlightOverlay').hidden = S.tool !== 'spotlight';

    var hint = $('#toolHint');
    if (S.tool === 'ruler') showHint(t('ruler_hint'));
    else if (S.tool === 'route') showHint(t('route_hint'), t('route_finish'));
    else hint.hidden = true;
  }

  function showHint(text, finishLabel) {
    var hint = $('#toolHint');
    $('#toolHintText').textContent = text;
    var fin = $('#toolHintFinish');
    fin.hidden = !finishLabel;
    if (finishLabel) fin.textContent = finishLabel;
    $('#toolHintCancel').textContent = t('cancel');
    hint.hidden = false;
  }

  function onMapClick(e) {
    if (S.tool === 'select') { clearSelection(); writeHash(); return; }
    if (S.tool === 'ruler') {
      draft = draft || { kind: 'ruler', pts: [] };
      draft.pts.push(unpx(e.latlng));
      if (draft.pts.length === 2) { commitMeasure(draft.pts); draft = null; }
      else showHint(t('ruler_hint'));
      renderDraft();
    } else if (S.tool === 'route') {
      draft = draft || { kind: 'route', pts: [] };
      draft.pts.push(unpx(e.latlng));
      showHint(t('route_points', { n: draft.pts.length }), t('route_finish'));
      renderDraft();
    }
  }

  function onMapMove(e) {
    if (S.tool === 'spotlight') {
      var pt = map.latLngToContainerPoint(e.latlng);
      var hole = $('#spotlightHole');
      hole.setAttribute('cx', pt.x);
      hole.setAttribute('cy', pt.y);
    }
    if (draft) { draft.hover = unpx(e.latlng); renderDraft(); }
  }

  var draftLayer = null;
  function renderDraft() {
    if (draftLayer) { map.removeLayer(draftLayer); draftLayer = null; }
    if (!draft || !draft.pts.length) return;
    var pts = draft.pts.concat(draft.hover ? [draft.hover] : []);
    draftLayer = L.layerGroup([], { pane: 'measurePane' }).addTo(map);
    L.polyline(pts.map(px), {
      pane: 'measurePane', color: ink('--accent-hi'), weight: 2, dashArray: '5 5', interactive: false
    }).addTo(draftLayer);
    pts.forEach(function (p) {
      L.circleMarker(px(p), {
        pane: 'measurePane', radius: 3.5, color: ink('--accent-hi'), fillColor: ink('--bg-deep'), fillOpacity: 1, weight: 2,
        interactive: false
      }).addTo(draftLayer);
    });
    var total = 0;
    for (var i = 1; i < pts.length; i++) total += metersBetween(pts[i - 1], pts[i]);
    if (total > 0) {
      L.marker(px(pts[pts.length - 1]), {
        pane: 'measurePane', interactive: false,
        icon: L.divIcon({ className: 'dist-divicon', html: '<span class="dist-label">' + fmtDist(total) + '</span>',
          iconSize: [0, 0], iconAnchor: [-8, 8] })
      }).addTo(draftLayer);
    }
  }

  function cancelDraft() {
    draft = null;
    if (draftLayer) { map.removeLayer(draftLayer); draftLayer = null; }
  }

  function commitMeasure(pts) {
    var g = drawFixed(pts, ink('--accent-hi'), false);
    measures.push(g);
    cancelDraft();
    showHint(t('ruler_hint'));
  }

  function finishRoute() {
    if (!draft || draft.pts.length < 2) { cancelDraft(); return; }
    var g = drawFixed(draft.pts, ink('--gold'), true);
    routes.push(g);
    cancelDraft();
    showHint(t('route_hint'), t('route_finish'));
  }

  function drawFixed(pts, color, isRoute) {
    var g = L.layerGroup([], { pane: 'measurePane' }).addTo(map);
    var line = L.polyline(pts.map(px), {
      pane: 'measurePane', color: color, weight: 2.5, opacity: 0.95
    }).addTo(g);
    pts.forEach(function (p) {
      L.circleMarker(px(p), {
        pane: 'measurePane', radius: 3.5, color: color, fillColor: ink('--bg-deep'), fillOpacity: 1, weight: 2
      }).addTo(g);
    });
    var total = 0;
    for (var i = 1; i < pts.length; i++) total += metersBetween(pts[i - 1], pts[i]);
    L.marker(px(pts[pts.length - 1]), {
      pane: 'measurePane',
      icon: L.divIcon({
        className: 'dist-divicon',
        html: '<span class="dist-label' + (isRoute ? ' is-route' : '') + '">' + fmtDist(total) + '</span>',
        iconSize: [0, 0], iconAnchor: [-8, 8]
      })
    }).addTo(g);

    line.on('contextmenu', function (e) {
      L.DomEvent.preventDefault(e.originalEvent);
      L.DomEvent.stopPropagation(e);
      map.removeLayer(g);
      var arr = isRoute ? routes : measures;
      var k = arr.indexOf(g);
      if (k >= 0) arr.splice(k, 1);
      toast(t(isRoute ? 'ctx_route_delete' : 'ctx_ruler_delete'));
    });
    return g;
  }

  function clearMeasures(silent) {
    measures.forEach(function (g) { map.removeLayer(g); });
    measures = [];
    if (!silent) toast(t('ruler_clear'));
  }
  function clearRoutes(silent) {
    routes.forEach(function (g) { map.removeLayer(g); });
    routes = [];
    if (!silent) toast(t('route_clear'));
  }

  function toggleFullscreen() {
    S.fullscreen = !S.fullscreen;
    document.body.classList.toggle('is-fs', S.fullscreen);
    $$('.tool-btn').forEach(function (b) {
      if (b.dataset.tool === 'fullscreen') b.classList.toggle('is-on', S.fullscreen);
    });
    setTimeout(function () { map.invalidateSize(); onZoom(); updateScale(); }, 220);
  }

  /* ---------------------------------------------------------------- */
  /* search                                                           */
  /* ---------------------------------------------------------------- */

  var searchIndex = [];

  function buildSearchIndex() {
    var names = S.t.poi_types || {};
    searchIndex = S.pois.map(function (p) {
      var label = p.name || p.d || names[p.type] || S.types[p.type].label;
      return {
        p: p,
        label: label,
        type: names[p.type] || S.types[p.type].label,
        hay: (label + ' ' + (names[p.type] || '') + ' ' + p.type).toLowerCase()
      };
    });
  }

  function runSearch(q) {
    var box = $('#searchResults');
    q = q.trim().toLowerCase();
    if (!q) { box.hidden = true; box.innerHTML = ''; return; }
    var hits = searchIndex.filter(function (r) { return r.hay.indexOf(q) >= 0; }).slice(0, 24);
    if (!hits.length) {
      box.innerHTML = '<div class="search-empty">' + esc(t('poi_not_found')) + '</div>';
      box.hidden = false;
      return;
    }
    box.innerHTML = hits.map(function (r, i) {
      return '<button class="search-hit" type="button" data-i="' + i + '"'
           + ' style="' + colorVars(r.p.type) + '">'
           + '<span class="hit-dot"></span>'
           + '<span class="hit-label">' + esc(r.label) + '</span>'
           + '<span class="hit-type">' + esc(r.type) + '</span></button>';
    }).join('');
    box.hidden = false;
    $$('.search-hit', box).forEach(function (b) {
      b.addEventListener('click', function () {
        focusOn(hits[+b.dataset.i].p, true);
        box.hidden = true;
        $('#searchInput').blur();
      });
    });
  }

  /* ---------------------------------------------------------------- */
  /* options                                                          */
  /* ---------------------------------------------------------------- */

  function applyOptions() {
    document.body.classList.toggle('no-edge-fade', !S.opts.edgeFade);
    document.body.classList.toggle('no-icon-frame', !S.opts.iconFrame);
    $('#optEdgeFade').checked = S.opts.edgeFade;
    $('#optIconFrame').checked = S.opts.iconFrame;
    $$('#optPopupSize .seg-btn').forEach(function (b) {
      b.classList.toggle('is-on', b.dataset.size === S.opts.popupSize);
    });
    $$('#optPoiClick .seg-btn').forEach(function (b) {
      b.classList.toggle('is-on', b.dataset.click === S.opts.poiClick);
    });
    $$('#optPalette .seg-btn').forEach(function (b) {
      b.classList.toggle('is-on', b.dataset.palette === S.opts.palette);
    });
    window.HuntStore.set(CK.opts, S.opts);
  }

  /* re-paint everything that carries a POI colour */
  function repaintPalette() {
    S.pois.forEach(function (p) {
      var m = markers[p.id];
      if (m) m.setIcon(makeIcon(p));
    });
    $$('#filterChips .filter-row').forEach(function (row) {
      row.setAttribute('style', colorVars(row.dataset.type));
    });
    if (S.selected) {
      renderPanel(S.selected);
      var sm = markers[S.selected.id];
      if (sm && sm._icon) {
        var span = sm._icon.querySelector('.poi-marker');
        if (span) span.classList.add('is-active');
      }
    }
  }

  function openModal(sel)  { $(sel).hidden = false; }
  function closeModals()   { $$('.map-modal').forEach(function (m) { m.hidden = true; }); }

  /* ---------------------------------------------------------------- */
  /* experimental bridge                                              */
  /* ---------------------------------------------------------------- */

  /* experimental POIs live in their own module; it gets this narrow
     surface instead of reaching into app state directly */
  function attachExperimental() {
    if (!global_X()) return;
    global_X().attach({
      px: px,
      unpx: unpx,
      mapId: function () { return S.mapId; },
      t: function (k) { return S.t[k]; },
      getJSON: getJSON,
      toast: toast,
      addLayer: function (l) { l.addTo(map); },
      removeLayer: function (l) { map.removeLayer(l); },
      hasLayer: function (l) { return map.hasLayer(l); },
      showPanel: function (html) {
        clearSelection();
        var body = $('#panelBody');
        body.innerHTML = html;
        body.hidden = false;
        $('#panelEmpty').hidden = true;
        var close = $('.panel-close', body);
        if (close) close.addEventListener('click', clearSelection);
      }
    });
    var box = $('#optExperimental');
    if (box) {
      box.checked = global_X().isEnabled();
      $('#xPanel').hidden = !box.checked;
      box.addEventListener('change', function () {
        global_X().setEnabled(box.checked);
      });
      if (box.checked) global_X().onMap(S.mapId);
    }
  }

  function global_X() { return window.HuntExperimental; }

  /* ---------------------------------------------------------------- */
  /* wiring                                                           */
  /* ---------------------------------------------------------------- */

  function wireUI() {
    $('#railToggle').addEventListener('click', function () {
      document.body.classList.toggle('rail-open');
    });
    $('#railScrim').addEventListener('click', function () {
      document.body.classList.remove('rail-open');
    });

    $('#zoomIn').addEventListener('click',  function () { map.zoomIn(); });
    $('#zoomOut').addEventListener('click', function () { map.zoomOut(); });
    $('#zoomFit').addEventListener('click', function () { map.fitBounds(mapBounds()); });

    $$('.tool-btn').forEach(function (b) {
      b.addEventListener('click', function () { setTool(b.dataset.tool); });
    });
    $('#toolHintCancel').addEventListener('click', function () { setTool('select'); });
    $('#toolHintFinish').addEventListener('click', finishRoute);
    $('#fsExit').addEventListener('click', toggleFullscreen);

    $('#settingsBtn').addEventListener('click', function () { openModal('#settingsModal'); });
    $$('.map-modal-close').forEach(function (b) { b.addEventListener('click', closeModals); });
    $$('.map-modal').forEach(function (m) {
      m.addEventListener('click', function (e) { if (e.target === m) closeModals(); });
    });

    $('#optEdgeFade').addEventListener('change', function (e) {
      S.opts.edgeFade = e.target.checked; applyOptions();
    });
    $('#optIconFrame').addEventListener('change', function (e) {
      S.opts.iconFrame = e.target.checked; applyOptions();
    });
    $$('#optPopupSize .seg-btn').forEach(function (b) {
      b.addEventListener('click', function () { S.opts.popupSize = b.dataset.size; applyOptions(); });
    });
    $$('#optPoiClick .seg-btn').forEach(function (b) {
      b.addEventListener('click', function () { S.opts.poiClick = b.dataset.click; applyOptions(); });
    });
    $$('#optPalette .seg-btn').forEach(function (b) {
      b.addEventListener('click', function () {
        S.opts.palette = b.dataset.palette;
        applyOptions();
        repaintPalette();
      });
    });
    $('#clearRuler').addEventListener('click', function () {
      if (confirm(t('ruler_clear_confirm'))) clearMeasures();
    });
    $('#clearRoutes').addEventListener('click', function () {
      if (confirm(t('route_clear_confirm'))) clearRoutes();
    });
    $('#clearHighlights').addEventListener('click', function () {
      S.marks[S.mapId] = {};
      store(LS.marks, S.marks);
      renderMarkers();
      toast(t('unhighlight_poi'));
    });

    /* image viewer */
    $('#ivClose').addEventListener('click', closeViewer);
    $('#ivPrev').addEventListener('click', function () { stepViewer(-1); });
    $('#ivNext').addEventListener('click', function () { stepViewer(1); });
    $('#imageViewer').addEventListener('click', function (e) {
      if (e.target.id === 'imageViewer') closeViewer();
    });

    /* language */
    $('#langToggle').addEventListener('click', function (e) {
      e.stopPropagation();
      $('#langSwitcher').classList.toggle('is-open');
    });
    document.addEventListener('click', function () {
      $('#langSwitcher').classList.remove('is-open');
    });

    /* search */
    var si = $('#searchInput');
    si.addEventListener('input', function () { runSearch(si.value); });
    si.addEventListener('focus', function () { if (si.value) runSearch(si.value); });
    $('#searchClear').addEventListener('click', function () {
      si.value = ''; runSearch(''); si.focus();
    });
    document.addEventListener('click', function (e) {
      if (!e.target.closest('.search-box')) $('#searchResults').hidden = true;
    });

    /* keyboard */
    document.addEventListener('keydown', function (e) {
      var typing = /^(input|textarea)$/i.test(e.target.tagName);
      if (e.key === 'Escape') {
        if (!$('#imageViewer').hidden) return closeViewer();
        if (!$$('.map-modal').every(function (m) { return m.hidden; })) return closeModals();
        if (draft) return cancelDraft();
        if (S.tool !== 'select') return setTool('select');
        if (typing) { e.target.blur(); return; }
        clearSelection();
        return;
      }
      if (typing) return;
      if (!$('#imageViewer').hidden) {
        if (e.key === 'ArrowLeft')  stepViewer(-1);
        if (e.key === 'ArrowRight') stepViewer(1);
        return;
      }
      if (e.key === 'Enter' && draft && draft.kind === 'route') return finishRoute();
      if (e.key === '/') { e.preventDefault(); $('#searchInput').focus(); return; }
      var tools = { '1': 'select', '2': 'ruler', '3': 'route', '4': 'spotlight', '5': 'fullscreen', '6': 'help' };
      if (tools[e.key]) return setTool(tools[e.key]);
      if (e.key.toLowerCase() === 'f') return toggleFullscreen();
      if (e.key === '+' || e.key === '=') return map.zoomIn();
      if (e.key === '-') return map.zoomOut();
    });

    window.addEventListener('hashchange', function () {
      var h = parseHash();
      if (h.m && h.m !== S.mapId) selectMap(h.m, h.p);
      else if (h.p && S.byId[h.p] && (!S.selected || S.selected.id !== h.p)) focusOn(S.byId[h.p], true);
    });

    window.addEventListener('resize', function () {
      map.invalidateSize();
      onZoom();
      updateScale();
    });
  }

  /* ---------------------------------------------------------------- */
  document.addEventListener('DOMContentLoaded', boot);
})();
