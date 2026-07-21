/* ------------------------------------------------------------------
 * icons.js - POI glyphs
 * One inline SVG per POI type, drawn on a 24x24 grid.
 * Shapes are original, styled to read at 9-18px inside a marker chip.
 * ------------------------------------------------------------------ */
(function (global) {
  'use strict';

  var svg = function (body) {
    return '<svg class="poi-glyph" viewBox="0 0 24 24" aria-hidden="true">' + body + '</svg>';
  };

  var GLYPHS = {

    /* concentric ring - a drop-in point */
    spawn: svg(
      '<circle cx="12" cy="12" r="8.4" fill="none" stroke="currentColor" stroke-width="2"/>' +
      '<circle cx="12" cy="12" r="3.6"/>'
    ),

    /* crossed rifles over a rack */
    armory: svg(
      '<path d="M4.4 18.4 17.6 5.2l1.5 1.5L5.9 19.9z"/>' +
      '<path d="M6.5 5.2 19.6 18.4l-1.4 1.5L5.1 6.7z"/>' +
      '<rect x="3" y="19.6" width="18" height="1.8" rx=".6"/>'
    ),

    /* slim hunting tower with a ladder */
    tower: svg(
      '<path d="M8.4 2.6h7.2L14.6 4.4H9.4z"/>' +
      '<rect x="9.2" y="4.2" width="5.6" height="4.6" rx=".4"/>' +
      '<path d="M9.6 8.8 6.6 21.4H4.9L8.2 8.8z"/>' +
      '<path d="M14.4 8.8 17.4 21.4h1.7L15.8 8.8z"/>' +
      '<rect x="7.9" y="13.2" width="8.2" height="1.3" rx=".4"/>' +
      '<rect x="7.2" y="17" width="9.6" height="1.3" rx=".4"/>'
    ),

    /* wide watch tower, broad roof */
    big_tower: svg(
      '<path d="M3.6 6.4 12 2.3l8.4 4.1v.9H3.6z"/>' +
      '<rect x="6.3" y="7.6" width="11.4" height="5" rx=".4"/>' +
      '<rect x="7.3" y="12.6" width="1.8" height="9.1"/>' +
      '<rect x="11.1" y="12.6" width="1.8" height="9.1"/>' +
      '<rect x="14.9" y="12.6" width="1.8" height="9.1"/>' +
      '<rect x="7.3" y="16.2" width="9.4" height="1.3"/>'
    ),

    /* hammer resting on a bench */
    workbench: svg(
      '<rect x="2.8" y="9.2" width="18.4" height="2.6" rx=".5"/>' +
      '<rect x="4.6" y="11.8" width="2" height="8.6"/>' +
      '<rect x="17.4" y="11.8" width="2" height="8.6"/>' +
      '<rect x="5" y="15.2" width="14" height="1.5"/>' +
      '<rect x="7" y="4" width="10" height="2.2" rx=".6" transform="rotate(-10 12 5.1)"/>' +
      '<rect x="14.3" y="2.5" width="3.1" height="3.9" rx=".4" transform="rotate(-10 15.9 4.5)"/>'
    ),

    /* crosshair over a hide */
    wild_target: svg(
      '<circle cx="12" cy="12" r="6.6" fill="none" stroke="currentColor" stroke-width="1.9"/>' +
      '<circle cx="12" cy="12" r="2.2"/>' +
      '<path d="M12 1.8v3.6M12 18.6v3.6M1.8 12h3.6M18.6 12h3.6"/>'
    ),

    /* heavy skull */
    brute: svg(
      '<path d="M12 2.8c4.4 0 7.3 3.2 7.3 7.6 0 2.5-1 4.3-2.4 5.4v3.4c0 1.2-.9 2-2 2H9.1c-1.1 0-2-.8-2-2v-3.4C5.7 14.7 4.7 12.9 4.7 10.4 4.7 6 7.6 2.8 12 2.8z"/>' +
      '<circle class="knockout" cx="9.2" cy="10.4" r="2"/>' +
      '<circle class="knockout" cx="14.8" cy="10.4" r="2"/>' +
      '<rect class="knockout" x="10.9" y="13.4" width="2.2" height="3.2" rx=".6"/>'
    ),

    /* beetle seen from above */
    beetle: svg(
      '<ellipse cx="12" cy="13.4" rx="5.1" ry="7.2"/>' +
      '<circle cx="12" cy="5.4" r="2.7"/>' +
      '<path d="M10.6 3.4 8.6 1.2M13.4 3.4 15.4 1.2"/>' +
      '<path d="M6.9 9.2 2.6 7.4M6.6 13.4H2M6.9 17.6 2.8 19.8M17.1 9.2l4.3-1.8M17.4 13.4H22M17.1 17.6l4.1 2.2"/>' +
      '<rect class="knockout" x="11.4" y="7.4" width="1.2" height="12" rx=".6"/>'
    ),

    /* egg */
    easter_egg: svg(
      '<path d="M12 2.6c3.4 0 6 4.4 6 8.8 0 4.7-2.7 7.9-6 7.9s-6-3.2-6-7.9c0-4.4 2.6-8.8 6-8.8z"/>' +
      '<path class="knockout" d="M6.6 12.4h10.8v1.3H6.6zM7.4 16.2h9.2v1.3H7.4z"/>'
    ),

    /* knife */
    melee_weapon: svg(
      '<path d="M14.6 1.9 17 4.3 8.2 13.1 5.8 10.7z"/>' +
      '<rect x="8.6" y="12.4" width="3.4" height="2.6" rx=".5" transform="rotate(45 10.3 13.7)"/>' +
      '<rect x="9.2" y="15.4" width="2.6" height="6.8" rx="1" transform="rotate(45 10.5 18.8)"/>'
    ),

    /* register with a drawer */
    cash_register: svg(
      '<path d="M3.4 5.2h8.2v3.2l8.2 1.1v9.9H3.4z"/>' +
      '<rect class="knockout" x="6.4" y="12.4" width="6.2" height="3.4" rx=".6"/>' +
      '<rect x="1.6" y="19.9" width="20.8" height="2.5" rx=".7"/>' +
      '<rect x="1.6" y="1.7" width="9.6" height="2" rx=".7"/>'
    ),

    /* compound roofline */
    compound: svg(
      '<path d="M3.4 10.6 12 3.6l8.6 7v9.8H3.4z"/>' +
      '<rect class="knockout" x="10.2" y="14" width="3.6" height="6.4"/>'
    ),

    /* door with an arrow going out */
    extraction: svg(
      '<path d="M3.6 3.4H10v2.4H6v12.4h4v2.4H3.6z"/>' +
      '<path d="M8.4 10.4h6.2v-3l5.2 4.6-5.2 4.6v-3H8.4z"/>'
    )
  };

  /* Icons used in the side panel / rail rows (stroke style, 24x24). */
  var UI = {
    photo:    '<path d="M3 8h4l1.5-2h7L17 8h4v11H3z"/><circle cx="12" cy="13" r="3.4"/>',
    pin:      '<path d="M12 21s7-6.3 7-11a7 7 0 1 0-14 0c0 4.7 7 11 7 11z"/><circle cx="12" cy="10" r="2.6"/>',
    star:     '<path d="M12 3.2l2.6 5.6 6 .8-4.4 4.3 1.1 6.1L12 17.1l-5.3 2.9 1.1-6.1L3.4 9.6l6-.8z"/>',
    copy:     '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/>',
    link:     '<path d="M10 13.6a4 4 0 0 0 5.7 0l2.6-2.6a4 4 0 1 0-5.7-5.7L11.2 6.7"/><path d="M14 10.4a4 4 0 0 0-5.7 0l-2.6 2.6a4 4 0 1 0 5.7 5.7l1.4-1.4"/>',
    camera:   '<path d="M3 8h4l1.5-2h7L17 8h4v11H3z"/><circle cx="12" cy="13" r="3.4"/>',
    ruler:    '<path d="M3.6 14.5L14.5 3.6l5.9 5.9L9.5 20.4z"/><path d="M7 11l2 2M10 8l2 2M13 5l2 2"/>',
    close:    '<path d="M5 5l14 14M19 5L5 19"/>'
  };

  global.HuntIcons = { GLYPHS: GLYPHS, UI: UI, ui: function (n) {
    return '<svg class="ui-ico" viewBox="0 0 24 24" aria-hidden="true">' + (UI[n] || '') + '</svg>';
  } };
})(window);
