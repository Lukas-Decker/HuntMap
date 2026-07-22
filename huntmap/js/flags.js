/* ------------------------------------------------------------------
 * flags.js - inline SVG flags for the language picker
 *
 * Emoji flags are not an option: Windows' Segoe UI Emoji has no
 * regional-indicator glyphs and deliberately renders the pair as the two
 * letter codes instead ("GB", "DE", ...), which is exactly what the picker
 * was showing. These are drawn instead, so they look the same everywhere.
 *
 * Simplified on purpose - they render at ~16px. Recognisable beats exact.
 * viewBox is 3x2 throughout.
 * ------------------------------------------------------------------ */
(function (global) {
  'use strict';

  function svg(body) {
    return '<svg class="flag" viewBox="0 0 3 2" aria-hidden="true">' + body + '</svg>';
  }

  var bandsH = function (a, b, c) {
    return '<rect width="3" height="2" fill="' + a + '"/>'
         + '<rect y="0.667" width="3" height="0.667" fill="' + b + '"/>'
         + '<rect y="1.333" width="3" height="0.667" fill="' + c + '"/>';
  };
  var bandsV = function (a, b, c) {
    return '<rect width="1" height="2" fill="' + a + '"/>'
         + '<rect x="1" width="1" height="2" fill="' + b + '"/>'
         + '<rect x="2" width="1" height="2" fill="' + c + '"/>';
  };
  var halves = function (top, bottom) {
    return '<rect width="3" height="1" fill="' + top + '"/>'
         + '<rect y="1" width="3" height="1" fill="' + bottom + '"/>';
  };

  var FLAGS = {

    GB: svg(
      '<rect width="3" height="2" fill="#012169"/>' +
      '<path d="M0 0l3 2M3 0L0 2" stroke="#fff" stroke-width=".4"/>' +
      '<path d="M0 0l3 2M3 0L0 2" stroke="#C8102E" stroke-width=".24"/>' +
      '<path d="M1.5 0v2M0 1h3" stroke="#fff" stroke-width=".67"/>' +
      '<path d="M1.5 0v2M0 1h3" stroke="#C8102E" stroke-width=".4"/>'),

    DE: svg(bandsH('#000', '#DD0000', '#FFCE00')),
    FR: svg(bandsV('#002395', '#fff', '#ED2939')),
    IT: svg(bandsV('#009246', '#fff', '#CE2B37')),
    RU: svg(bandsH('#fff', '#0039A6', '#D52B1E')),
    PL: svg(halves('#fff', '#DC143C')),
    UA: svg(halves('#0057B7', '#FFD700')),

    ES: svg(
      '<rect width="3" height="2" fill="#AA151B"/>' +
      '<rect y="0.5" width="3" height="1" fill="#F1BF00"/>'),

    JP: svg(
      '<rect width="3" height="2" fill="#fff"/>' +
      '<circle cx="1.5" cy="1" r="0.6" fill="#BC002D"/>'),

    TR: svg(
      '<rect width="3" height="2" fill="#E30A17"/>' +
      '<circle cx="1.25" cy="1" r="0.45" fill="#fff"/>' +
      '<circle cx="1.4" cy="1" r="0.36" fill="#E30A17"/>' +
      '<path d="M1.83 1l.52-.17-.32.44v-.54l.32.44z" fill="#fff"/>'),

    BR: svg(
      '<rect width="3" height="2" fill="#009B3A"/>' +
      '<path d="M1.5 0.25L2.75 1 1.5 1.75.25 1z" fill="#FEDF00"/>' +
      '<circle cx="1.5" cy="1" r="0.42" fill="#002776"/>'),

    KR: svg(
      '<rect width="3" height="2" fill="#fff"/>' +
      '<path d="M1.5 0.6a.4.4 0 0 1 0 .8.4.4 0 0 0 0 .8.8.8 0 0 0 0-1.6z" fill="#0047A0"/>' +
      '<path d="M1.5 0.6a.4.4 0 0 0 0 .8.4.4 0 0 1 0 .8.8.8 0 0 1 0-1.6z" fill="#CD2E3A"/>' +
      '<g fill="#000"><rect x=".35" y=".35" width=".42" height=".08"/>' +
      '<rect x=".35" y=".5" width=".42" height=".08"/>' +
      '<rect x="2.23" y="1.42" width=".42" height=".08"/>' +
      '<rect x="2.23" y="1.57" width=".42" height=".08"/></g>'),

    CN: svg(
      '<rect width="3" height="2" fill="#DE2910"/>' +
      '<g fill="#FFDE00">' +
      '<path d="M.5.3l.16.49h.51l-.42.3.16.49-.41-.3-.42.3.16-.49-.41-.3h.51z"/>' +
      '<circle cx="1.1" cy=".25" r=".08"/><circle cx="1.35" cy=".5" r=".08"/>' +
      '<circle cx="1.35" cy=".85" r=".08"/><circle cx="1.1" cy="1.1" r=".08"/>' +
      '</g>'),

    TW: svg(
      '<rect width="3" height="2" fill="#FE0000"/>' +
      '<rect width="1.5" height="1" fill="#000095"/>' +
      '<circle cx=".75" cy=".5" r=".33" fill="#fff"/>' +
      '<circle cx=".75" cy=".5" r=".22" fill="#000095"/>' +
      '<circle cx=".75" cy=".5" r=".17" fill="#fff"/>')
  };

  /* language code -> the region whose flag it ships for */
  var CC = {
    'en': 'GB', 'de': 'DE', 'fr': 'FR', 'es': 'ES', 'it': 'IT', 'pl': 'PL',
    'ru': 'RU', 'uk': 'UA', 'tr': 'TR', 'pt-BR': 'BR', 'ja': 'JP', 'ko': 'KR',
    'zh-Hans': 'CN', 'zh-Hant': 'TW'
  };

  global.HuntFlags = {
    /* markup for a language code; falls back to the bare code */
    forLang: function (code) {
      var cc = CC[code];
      return (cc && FLAGS[cc])
        || '<span class="flag flag-text">' + String(code).slice(0, 2).toUpperCase() + '</span>';
    },
    region: function (code) { return CC[code] || ''; }
  };
})(window);
