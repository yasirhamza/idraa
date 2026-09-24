/* csrf.js — the one reader of the CSRF token for JS-initiated requests.
 *
 * GHSA-46jj-823j-mjj9 B4: tokens are bound to the session cookie, so a token
 * minted before login is dead after it. <body hx-boost> swaps only body +
 * title, so <meta name="csrf-token"> in <head> can still hold the pre-login
 * token after a boosted login — the csrf_token COOKIE is always the current
 * one (every GET re-mints under the current binding). Read the cookie first,
 * fall back to the meta tag.
 *
 * Duplicates: if a csrf_token cookie was ever planted from a subdomain,
 * document.cookie lists two. Starlette's cookie parser is last-wins, so take
 * the LAST exact-name match to agree with the server.
 */
(function () {
  "use strict";
  var NAME = "csrf_token";
  function idraaCsrfToken() {
    var found = "";
    var parts = document.cookie ? document.cookie.split(";") : [];
    for (var i = 0; i < parts.length; i++) {
      var p = parts[i].replace(/^\s+/, "");
      var eq = p.indexOf("=");
      if (eq > 0 && p.slice(0, eq) === NAME) found = decodeURIComponent(p.slice(eq + 1));
    }
    if (found) return found;
    var m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.content : "";
  }
  window.idraaCsrfToken = idraaCsrfToken;
  window.IDRAA_SESSION_CHANGED_MSG =
    "Your session changed (signed in or out elsewhere, or the app was updated). Reload the page and try again.";
})();
