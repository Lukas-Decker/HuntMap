/* ------------------------------------------------------------------
 * experimental.js - game-mined POIs and the reposition editor
 *
 * Data comes from huntmap/experimental/<mapId>.json, produced by
 * tools/extract_cargo_pois.py out of the Hunt level files. Positions are
 * derived from raw 3D world coordinates, so they are close but not
 * authoritative - hence the editor.
 *
 * Nothing here touches huntmap/data/. Manual moves live in localStorage
 * and are exported as their own JSON file.
 *
 * The app hands over a small bridge object (see attach) rather than this
 * module reaching into app internals.
 * ------------------------------------------------------------------ */
(function (global) {
  'use strict';

  var LS_ON = 'hm.x.on';
  var LS_TYPES = 'hm.x.types';
  var LS_MOVES = 'hm.x.moves';

  var api = null;             // bridge from app.js
  var data = {};              // mapId -> raw json
  var pois = [];              // flat list for the active map
  var markers = {};
  var meta = {};              // type -> {label, radius, colors, note}
  var order = [];
  var on = {};                // type -> bool
  var moves = {};             // mapId -> { id: [x, y] }
  var enabled = false;
  var editing = false;

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

  var $ = function (s) { return document.querySelector(s); };
  var $$ = function (s) {
    return Array.prototype.slice.call(document.querySelectorAll(s));
  };

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  /* label for a type: game translation if we have one, else the mined label */
  function label(type) {
    var x = (api.t('x_poi_types') || {});
    return (x && x[type]) || (meta[type] && meta[type].label) || type;
  }

  function colors(type) {
    var m = meta[type] || {};
    return { borderColor: m.borderColor || '#c9a24b',
             fillColor: m.fillColor || '#3b2c12' };
  }

  /* ---------------------------------------------------------------- */
  /* data                                                             */
  /* ---------------------------------------------------------------- */

  function load(mapId) {
    if (data[mapId]) return Promise.resolve(data[mapId]);
    return api.getJSON('experimental/' + mapId + '.json').then(function (d) {
      data[mapId] = d;
      return d;
    });
  }

  function build(mapId) {
    var d = data[mapId];
    pois = [];
    /* deliberately NOT clearing `markers` here: render() needs it to find and
       remove the live layers first, otherwise they are orphaned on the map */
    if (!d) return;
    meta = d.type_meta || {};
    order = Object.keys(meta);
    var mv = moves[mapId] || {};
    order.forEach(function (type) {
      (d.types[type] || []).forEach(function (r) {
        var moved = mv[r.id];
        pois.push({
          id: r.id, type: type,
          c: moved ? moved.slice() : r.c.slice(),
          home: r.c.slice(),
          world: r.world, layer: r.layer, entity: r.entity, name: r.name,
          moved: !!moved
        });
      });
    });
    order.forEach(function (k) { if (!(k in on)) on[k] = true; });
  }

  /* ---------------------------------------------------------------- */
  /* markers                                                          */
  /* ---------------------------------------------------------------- */

  function size(type) {
    return Math.round(14.4 + 0.7 * ((meta[type] && meta[type].radius) || 10));
  }

  function icon(p) {
    var s = size(p.type);
    var c = colors(p.type);
    var glyph = (global.HuntIcons.GLYPHS[p.type]
                 || global.HuntIcons.GLYPHS.cash_register || '');
    return L.divIcon({
      className: 'x-divicon',
      html: '<span class="x-marker' + (p.moved ? ' is-moved' : '') + '" style="'
          + '--poi-border:' + c.borderColor + ';--poi-fill:' + c.fillColor + ';'
          + 'font-size:' + Math.max(8, Math.round(s * 0.46)) + 'px">'
          + glyph + '</span>',
      iconSize: [s, s], iconAnchor: [s / 2, s / 2]
    });
  }

  /* toggle the moved styling on a live marker without rebuilding its icon */
  function mark(m, p) {
    var span = m._icon && m._icon.querySelector('.x-marker');
    if (span) span.classList.toggle('is-moved', !!p.moved);
  }

  function render() {
    Object.keys(markers).forEach(function (id) { api.removeLayer(markers[id]); });
    markers = {};
    if (!enabled) { updateCount(); return; }

    pois.forEach(function (p) {
      var m = L.marker(api.px(p.c), {
        icon: icon(p), pane: 'xPane', riseOnHover: true, keyboard: false,
        draggable: editing
      });
      m.on('click', function (e) {
        L.DomEvent.stopPropagation(e);
        if (!editing) selectX(p);
      });
      m.on('dragend', function () {
        var xy = api.unpx(m.getLatLng());
        p.c = [Math.round(xy[0] * 10) / 10, Math.round(xy[1] * 10) / 10];
        p.moved = true;
        moves[api.mapId()] = moves[api.mapId()] || {};
        moves[api.mapId()][p.id] = p.c.slice();
        store(LS_MOVES, moves);
        /* NOT setIcon(): rebuilding the icon here re-inits the element that
           Leaflet's Draggable is still finishing with, and finishDrag then
           throws on the detached node. Just restyle the existing one. */
        mark(m, p);
        updateMoveCount();
        api.toast(label(p.type) + ' moved to ' + p.c[0] + ' / ' + p.c[1]);
      });
      markers[p.id] = m;
    });
    applyFilters();
  }

  function applyFilters() {
    pois.forEach(function (p) {
      var m = markers[p.id];
      if (!m) return;
      var show = enabled && !!on[p.type];
      if (show && !api.hasLayer(m)) api.addLayer(m);
      else if (!show && api.hasLayer(m)) api.removeLayer(m);
    });
    updateCount();
  }

  function updateCount() {
    var el = $('#xCount');
    if (!el) return;
    var n = enabled ? pois.filter(function (p) { return on[p.type]; }).length : 0;
    el.textContent = n + ' experimental shown';
    $$('#xChips .filter-row').forEach(function (row) {
      var type = row.dataset.xtype;
      var c = pois.filter(function (p) { return p.type === type; }).length;
      var cnt = row.querySelector('.filter-row-count');
      if (cnt) cnt.textContent = c;
    });
  }

  function updateMoveCount() {
    var el = $('#xMoveCount');
    if (!el) return;
    var n = Object.keys(moves[api.mapId()] || {}).length;
    el.textContent = n ? n + ' position(s) changed on this map'
                       : 'No positions changed yet';
  }

  /* ---------------------------------------------------------------- */
  /* side panel                                                       */
  /* ---------------------------------------------------------------- */

  function selectX(p) {
    var c = colors(p.type);
    var off = Math.hypot(p.c[0] - p.home[0], p.c[1] - p.home[1]) * (1000 / 4096);
    api.showPanel(
      '<header class="panel-head" style="--poi-border:' + c.borderColor
      + ';--poi-fill:' + c.fillColor + '">'
      + '<span class="panel-badge">'
      + (global.HuntIcons.GLYPHS[p.type] || '') + '</span>'
      + '<div class="panel-head-text"><h2>' + esc(label(p.type)) + '</h2>'
      + '<span class="panel-sub">Experimental &middot; ' + esc(p.entity) + '</span></div>'
      + '<button class="panel-close" type="button" aria-label="Close">&times;</button>'
      + '</header>'
      + '<p class="panel-desc">Mined from the game level files. This position is '
      + 'derived from a 3D world coordinate and is not community-verified.</p>'
      + '<section class="panel-sec panel-meta">'
      + '<div class="meta-row"><span>Map X / Y</span><b>' + p.c[0] + ' / ' + p.c[1] + '</b></div>'
      + (p.moved
          ? '<div class="meta-row"><span>Moved by</span><b>' + off.toFixed(1) + ' m</b></div>'
            + '<div class="meta-row"><span>Imported at</span><b>' + p.home[0] + ' / ' + p.home[1] + '</b></div>'
          : '')
      + '<div class="meta-row"><span>World X / Y / Z</span><b class="mono">'
      + p.world.join(', ') + '</b></div>'
      + '<div class="meta-row"><span>Layer</span><b class="mono">' + esc(p.layer) + '</b></div>'
      + '<div class="meta-row"><span>ID</span><b class="mono">' + esc(p.id) + '</b></div>'
      + '</section>'
      + (p.moved
          ? '<div class="panel-actions"><button class="btn btn-ghost" id="xRevert">'
            + '<span>Revert this position</span></button></div>'
          : ''));

    var rev = $('#xRevert');
    if (rev) {
      rev.addEventListener('click', function () {
        revert(p);
        selectX(p);
      });
    }
  }

  function revert(p) {
    p.c = p.home.slice();
    p.moved = false;
    if (moves[api.mapId()]) delete moves[api.mapId()][p.id];
    store(LS_MOVES, moves);
    var m = markers[p.id];
    if (m) { m.setLatLng(api.px(p.c)); mark(m, p); }
    updateMoveCount();
  }

  /* ---------------------------------------------------------------- */
  /* rail                                                             */
  /* ---------------------------------------------------------------- */

  function buildRail() {
    var box = $('#xChips');
    if (!box) return;
    box.innerHTML = order.map(function (k) {
      var c = colors(k);
      return '<button class="filter-row' + (on[k] ? ' is-on' : '') + '" type="button"'
           + ' data-xtype="' + esc(k) + '"'
           + ' style="--poi-border:' + c.borderColor + ';--poi-fill:' + c.fillColor + '"'
           + ' title="' + esc((meta[k] && meta[k].note) || '') + '">'
           + '<span class="filter-row-ico">'
           + (global.HuntIcons.GLYPHS[k] || global.HuntIcons.GLYPHS.cash_register || '')
           + '</span>'
           + '<span class="filter-row-label">' + esc(label(k)) + '</span>'
           + '<span class="filter-row-count"></span>'
           + '<span class="switch' + (on[k] ? ' is-on' : '') + '"></span>'
           + '</button>';
    }).join('');
    updateCount();
  }

  function setEditing(v) {
    editing = v && enabled;
    document.body.classList.toggle('x-editing', editing);
    var b = $('#xEditBtn');
    if (b) b.classList.toggle('is-on', editing);
    var tools = $('#xEditTools');
    if (tools) tools.hidden = !editing;
    /* Leaflet only builds Marker.dragging when the marker is constructed with
       draggable:true, so flipping the flag afterwards does nothing. Rebuild
       the layer instead - 126 divIcons is cheap. */
    render();
    updateMoveCount();
    if (editing) api.toast('Edit mode: drag a marker to reposition it');
  }

  /* ---------------------------------------------------------------- */
  /* export / import                                                  */
  /* ---------------------------------------------------------------- */

  function exportMoves() {
    var payload = {
      _note: 'HuntMap experimental POI position overrides. Map id -> POI id -> [x, y] '
           + 'in 4096x4096 map-image pixels.',
      generated: new Date().toISOString(),
      moves: moves
    };
    var blob = new Blob([JSON.stringify(payload, null, 1)],
                        { type: 'application/json' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'huntmap-experimental-moves.json';
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
    api.toast('Exported position overrides');
  }

  function importMoves() {
    var inp = document.createElement('input');
    inp.type = 'file';
    inp.accept = 'application/json,.json';
    inp.addEventListener('change', function () {
      var f = inp.files && inp.files[0];
      if (!f) return;
      var fr = new FileReader();
      fr.onload = function () {
        try {
          var parsed = JSON.parse(String(fr.result));
          var got = parsed && parsed.moves ? parsed.moves : parsed;
          if (!got || typeof got !== 'object') throw new Error('bad shape');
          Object.keys(got).forEach(function (mid) {
            moves[mid] = Object.assign(moves[mid] || {}, got[mid]);
          });
          store(LS_MOVES, moves);
          build(api.mapId());
          render();
          updateMoveCount();
          api.toast('Imported position overrides');
        } catch (e) {
          api.toast('Could not read that file', 'error');
        }
      };
      fr.readAsText(f);
    });
    inp.click();
  }

  function resetMoves() {
    var mid = api.mapId();
    if (!Object.keys(moves[mid] || {}).length) {
      api.toast('Nothing to reset on this map');
      return;
    }
    if (!confirm('Reset every experimental position on this map back to the '
                 + 'imported coordinates?')) return;
    delete moves[mid];
    store(LS_MOVES, moves);
    build(mid);
    render();
    updateMoveCount();
    api.toast('Positions reset');
  }

  /* ---------------------------------------------------------------- */
  /* wiring                                                           */
  /* ---------------------------------------------------------------- */

  function wire() {
    var chips = $('#xChips');
    if (chips) {
      chips.addEventListener('click', function (e) {
        var row = e.target.closest('.filter-row');
        if (!row) return;
        var k = row.dataset.xtype;
        on[k] = !on[k];
        row.classList.toggle('is-on', on[k]);
        row.querySelector('.switch').classList.toggle('is-on', on[k]);
        store(LS_TYPES, on);
        applyFilters();
      });
    }
    var all = $('#xToggleAll');
    if (all) {
      all.addEventListener('click', function () {
        var any = order.some(function (k) { return on[k]; });
        order.forEach(function (k) { on[k] = !any; });
        buildRail();
        store(LS_TYPES, on);
        applyFilters();
        all.textContent = any ? 'Enable all' : 'Disable all';
      });
    }
    var edit = $('#xEditBtn');
    if (edit) edit.addEventListener('click', function () { setEditing(!editing); });
    var ex = $('#xExport');
    if (ex) ex.addEventListener('click', exportMoves);
    var im = $('#xImport');
    if (im) im.addEventListener('click', importMoves);
    var rs = $('#xReset');
    if (rs) rs.addEventListener('click', resetMoves);
  }

  /* ---------------------------------------------------------------- */
  /* public                                                           */
  /* ---------------------------------------------------------------- */

  var X = {
    attach: function (bridge) {
      api = bridge;
      enabled = !!store(LS_ON);
      moves = store(LS_MOVES) || {};
      var savedTypes = store(LS_TYPES);
      if (savedTypes) on = savedTypes;
      wire();
      return X;
    },

    isEnabled: function () { return enabled; },
    isEditing: function () { return editing; },

    setEnabled: function (v) {
      enabled = !!v;
      store(LS_ON, enabled);
      var panel = $('#xPanel');
      if (panel) panel.hidden = !enabled;
      if (!enabled) {
        setEditing(false);
        render();
        return Promise.resolve();
      }
      return X.onMap(api.mapId());
    },

    /* called by the app whenever the active map changes */
    onMap: function (mapId) {
      if (!enabled) { pois = []; render(); return Promise.resolve(); }
      return load(mapId).then(function () {
        build(mapId);
        buildRail();
        render();
        updateMoveCount();
        var panel = $('#xPanel');
        if (panel) panel.hidden = false;
      }).catch(function () {
        var panel = $('#xPanel');
        if (panel) panel.hidden = false;
        var box = $('#xChips');
        if (box) {
          box.innerHTML = '<p class="x-note">No experimental data for this map. '
            + 'Run <code>python tools/extract_cargo_pois.py</code>.</p>';
        }
      });
    },

    /* let the app relabel after a language switch */
    refresh: function () {
      if (!enabled) return;
      buildRail();
      if (editing) return;          // setIcon would disturb the drag handlers
      Object.keys(markers).forEach(function (id) {
        var p = pois.filter(function (q) { return q.id === id; })[0];
        if (p) markers[id].setIcon(icon(p));
      });
    },

    search: function () {
      if (!enabled) return [];
      return pois.map(function (p) {
        return { p: p, label: label(p.type), type: 'Experimental',
                 hay: (label(p.type) + ' ' + p.type + ' ' + p.entity).toLowerCase() };
      });
    },

    focus: function (p) { selectX(p); }
  };

  global.HuntExperimental = X;
})(window);
