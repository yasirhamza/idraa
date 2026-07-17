/* localize_time.js — convert server-rendered UTC <time> elements to the
 * browser's local timezone.
 *
 * UAT 2026-05-21: operators reported timestamps were rendering as UTC with
 * no indication, making conversion to their wall clock guesswork. This
 * script walks <time data-localize="..."> elements and replaces their
 * inner text with Intl-formatted local strings.
 *
 * Server contract (see `idraa.app._format_datetime` / `_format_date`):
 *   <time datetime="<ISO 8601 UTC>" data-localize="datetime|date">
 *     <fallback YYYY-MM-DD [HH:MM UTC] string>
 *   </time>
 *
 * Runs on DOMContentLoaded for the initial page AND after every htmx swap
 * so dynamically-inserted partials get localized too.
 */
(function () {
  "use strict";

  function localize(root) {
    if (!root || typeof root.querySelectorAll !== "function") return;
    var els = root.querySelectorAll("time[data-localize]");
    els.forEach(function (el) {
      var iso = el.getAttribute("datetime");
      if (!iso) return;
      var d = new Date(iso);
      if (isNaN(d.getTime())) return;
      var mode = el.getAttribute("data-localize");
      try {
        if (mode === "date") {
          el.textContent = d.toLocaleDateString();
        } else {
          el.textContent = d.toLocaleString();
        }
      } catch (_) {
        // Locale formatting failed — leave the server fallback text in place.
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      localize(document);
    });
  } else {
    localize(document);
  }

  // HTMX-swapped content: re-localize within the swapped subtree only so
  // we don't re-process the whole document on every partial swap.
  document.addEventListener("htmx:afterSwap", function (e) {
    if (e && e.detail && e.detail.elt) localize(e.detail.elt);
  });
})();
