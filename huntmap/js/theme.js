/* ------------------------------------------------------------------
 * theme.js - POI marker palettes
 *
 * "site"  = the colours shipped in data/poi-types.json, exactly as the
 *           original site uses them (cool neon on near-black blue).
 * "warm"  = a HuntWiki-harmonised set. The original palette leans on
 *           blues/cyans/purples that fight the smoked-brown chrome, so
 *           each type is re-cast into the tallow / rust / blood / moss
 *           / verdigris range while staying pairwise distinguishable.
 *
 * Only the two colours are overridden - label, category and radius
 * still come from poi-types.json, which is never modified.
 * ------------------------------------------------------------------ */
(function (global) {
  'use strict';

  var WARM = {
    /* big navigation anchors - brightest, coolest of the warm set */
    spawn:         { borderColor: '#6fa8c4', fillColor: '#22323c' },  /* pale slate blue */
    extraction:    { borderColor: '#7f9b53', fillColor: '#293320' },  /* moss green      */

    /* structures - bone and weathered timber */
    compound:      { borderColor: '#c2b49a', fillColor: '#342e25' },  /* bone            */
    armory:        { borderColor: '#9aa79b', fillColor: '#2b302b' },  /* gun-metal sage  */
    tower:         { borderColor: '#c9a24b', fillColor: '#3a2f18' },  /* tallow gold     */
    big_tower:     { borderColor: '#a97c33', fillColor: '#33260f' },  /* darker gold     */

    /* usables - warm cyan-ish so they read apart from the gold towers */
    workbench:     { borderColor: '#5fb0c4', fillColor: '#1e333a' },  /* verdigris       */
    cash_register: { borderColor: '#e6c876', fillColor: '#3d3116' },  /* bright tallow   */
    melee_weapon:  { borderColor: '#c98f8f', fillColor: '#3a2222' },  /* dried rose      */

    /* threats - the blood family */
    wild_target:   { borderColor: '#c9503b', fillColor: '#3a1a14' },  /* blood-hi        */
    brute:         { borderColor: '#d98a4a', fillColor: '#3b2413' },  /* burn orange     */

    /* oddities */
    beetle:        { borderColor: '#9a7fb3', fillColor: '#2c2336' },  /* muted violet    */
    easter_egg:    { borderColor: '#8fbf6a', fillColor: '#26321c' }   /* boon green      */
  };

  global.HuntTheme = {
    palettes: ['warm', 'site'],

    /* def = the poi-types.json entry; name = 'warm' | 'site' */
    colors: function (type, def, name) {
      var o = name === 'warm' ? WARM[type] : null;
      return {
        borderColor: (o && o.borderColor) || def.borderColor,
        fillColor: (o && o.fillColor) || def.fillColor
      };
    },

    /* read a themed colour out of the stylesheet so JS-drawn overlays
       (ruler, route) follow whatever the CSS says */
    cssVar: function (name, fallback) {
      var v = getComputedStyle(document.documentElement).getPropertyValue(name);
      return (v && v.trim()) || fallback;
    }
  };
})(window);
