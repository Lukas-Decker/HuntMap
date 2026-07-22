/* ------------------------------------------------------------------
 * store.js - tiny persistence layer shared by app.js and experimental.js
 *
 * Two backends:
 *   cookie      survives across sessions and is what the UI state uses
 *   localStorage for anything too big for a cookie (4 KB per cookie, and
 *                browsers cap the total per domain) - POI highlights and
 *                experimental position overrides stay there.
 * ------------------------------------------------------------------ */
(function (global) {
  'use strict';

  var DAYS = 3650;

  function readCookie(name) {
    var parts = document.cookie ? document.cookie.split(';') : [];
    for (var i = 0; i < parts.length; i++) {
      var c = parts[i].trim();
      if (c.indexOf(name + '=') === 0) {
        return decodeURIComponent(c.slice(name.length + 1));
      }
    }
    return null;
  }

  function writeCookie(name, value, days) {
    var d = new Date();
    d.setTime(d.getTime() + (days || DAYS) * 864e5);
    document.cookie = name + '=' + encodeURIComponent(value)
      + ';expires=' + d.toUTCString() + ';path=/;SameSite=Lax';
  }

  function delCookie(name) {
    document.cookie = name + '=;expires=Thu, 01 Jan 1970 00:00:00 GMT;path=/';
  }

  global.HuntStore = {

    /* raw string cookie (the language code uses this) */
    getRaw: readCookie,
    setRaw: writeCookie,
    del: delCookie,

    /* JSON in a cookie - for the small UI-state objects */
    get: function (name, fallback) {
      var raw = readCookie(name);
      if (raw == null) return fallback === undefined ? null : fallback;
      try {
        return JSON.parse(raw);
      } catch (e) {
        return fallback === undefined ? null : fallback;
      }
    },

    set: function (name, value) {
      try {
        var s = JSON.stringify(value);
        /* stay well under the 4 KB limit; if a value ever grows past it the
           write is skipped rather than silently truncated by the browser */
        if (s.length > 3500) return false;
        writeCookie(name, s);
        return true;
      } catch (e) { return false; }
    },

    /* JSON in localStorage - for the larger, device-local things */
    local: function (name, value) {
      try {
        if (value === undefined) {
          var raw = localStorage.getItem(name);
          return raw == null ? null : JSON.parse(raw);
        }
        localStorage.setItem(name, JSON.stringify(value));
      } catch (e) { /* private mode */ }
      return null;
    },

    /* one-time lift of an existing localStorage value into a cookie, so
       nobody loses the filters they already had set */
    migrate: function (cookieName, lsName) {
      if (readCookie(cookieName) != null) return null;
      var v = null;
      try {
        var raw = localStorage.getItem(lsName);
        v = raw == null ? null : JSON.parse(raw);
      } catch (e) { v = null; }
      if (v != null) global.HuntStore.set(cookieName, v);
      return v;
    }
  };
})(window);
