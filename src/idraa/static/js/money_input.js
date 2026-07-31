/* Excel-like money entry for the wizard's PL/SL fields (owner UAT 2026-07-31).
 *
 * The previous UX stripped commas on focus and regrouped on blur: the
 * focus-time value swap threw away the user's click position (caret jumped
 * to the end), making leftmost-digit edits a fight, and the strict input
 * pattern rejected pastes like "$1 000 000".
 *
 * idraaMoneyLive(el, ev) reformats IN PLACE on every input event:
 *   - strips BENIGN FORMATTING ONLY (currency symbols, spaces, commas,
 *     apostrophes, underscores, parens). Anything else — letters, signs,
 *     exponents — is left IN PLACE so the field goes pattern-invalid and
 *     the user sees exactly what they pasted. Review B1: a blanket
 *     "delete every unknown character" turned "$1.5M" into 1.5 (a silent
 *     10^6 understatement) and "1e6" into 16; magnitude corruption must
 *     fail LOUDLY, never launder.
 *   - regroups the integer part with commas;
 *   - RESTORES THE CARET by digit-count (the caret stays anchored to the
 *     digit being edited, wherever the commas move);
 *   - Backspace over a group separator eats the DIGIT to its left
 *     (review I1: regrouping resurrects the comma, so deleting just the
 *     comma was a visible no-op and the second press ate the wrong
 *     digit). Forward-delete over a separator symmetrically eats the
 *     digit to its right.
 *   - never forces decimal places while typing (blur still commits the
 *     canonical 2-decimal display via the Alpine fmt(), whose strict
 *     Number() parse blanks non-numeric leftovers — the loud legacy
 *     failure mode).
 *
 * Loaded globally from base.html BEFORE any HTMX swap, so inline x-data
 * handlers may reference it without the PR #205 stale-global race.
 */
(function () {
  "use strict";

  // Benign formatting characters a paste may legitimately carry. NOT digits,
  // NOT letters/signs/exponents — those must survive to fail the pattern.
  var BENIGN = /[\s $€£¥,'_()]/g;

  function sanitize(raw) {
    var clean = raw.replace(BENIGN, "");
    var firstDot = clean.indexOf(".");
    if (firstDot !== -1) {
      // Keep only the first decimal point.
      clean =
        clean.slice(0, firstDot + 1) + clean.slice(firstDot + 1).replace(/\./g, "");
    }
    return clean;
  }

  window.idraaMoneyLive = function (el, ev) {
    var raw = el.value;
    var prev = el.__idraaMoneyPrev || "";
    var caret = el.selectionStart === null ? raw.length : el.selectionStart;
    // Digits (and dot) BEFORE the caret anchor the caret across regrouping.
    var digitsBefore = raw.slice(0, caret).replace(BENIGN, "").length;

    var clean = sanitize(raw);
    if (/[^0-9.]/.test(clean)) {
      // Unknown characters present (letters, e, +, -): leave the text
      // exactly as pasted/typed — pattern validation shows it invalid.
      el.__idraaMoneyPrev = raw;
      return raw;
    }

    // A delete that changed no digits removed only a group separator —
    // Excel-like: eat the adjacent digit instead of a visible no-op.
    var inputType = ev && ev.inputType;
    if (clean === sanitize(prev) && clean.length > 0) {
      if (inputType === "deleteContentBackward" && digitsBefore > 0) {
        clean = clean.slice(0, digitsBefore - 1) + clean.slice(digitsBefore);
        digitsBefore -= 1;
      } else if (inputType === "deleteContentForward" && digitsBefore < clean.length) {
        clean = clean.slice(0, digitsBefore) + clean.slice(digitsBefore + 1);
      }
    }

    var parts = clean.split(".");
    var grouped = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    var formatted =
      clean.indexOf(".") === -1 ? grouped : grouped + "." + (parts[1] || "");

    el.value = formatted;

    var pos = 0;
    var seen = 0;
    while (pos < formatted.length && seen < digitsBefore) {
      if (/[0-9.]/.test(formatted.charAt(pos))) seen += 1;
      pos += 1;
    }
    el.setSelectionRange(pos, pos);
    el.__idraaMoneyPrev = formatted;
    return formatted;
  };
})();
