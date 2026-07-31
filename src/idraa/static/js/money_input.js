/* Excel-like money entry for the wizard's PL/SL fields (owner UAT 2026-07-31).
 *
 * The previous UX stripped commas on focus and regrouped on blur: the
 * focus-time value swap threw away the user's click position (caret jumped
 * to the end), making leftmost-digit edits a fight, and the strict input
 * pattern rejected pastes like "$1 000 000".
 *
 * idraaMoneyLive(el, ev) reformats IN PLACE on every input event:
 *   - strips BENIGN FORMATTING ONLY (currency symbols, spaces, commas,
 *     apostrophes, underscores). Anything else — letters, signs, parens
 *     (accounting negation carries semantics, not decoration) — is left
 *     IN PLACE so the field goes pattern-invalid and the user sees what
 *     they pasted. Review B1: a blanket "delete every unknown character"
 *     turned "$1.5M" into 1.5 (a silent 10^6 understatement); magnitude
 *     corruption must fail LOUDLY, never launder. (One deliberate
 *     exception on the blur path: a pure exponent like "1e6" survives to
 *     strict Number(), which expands it UPWARD to 1,000,000 — explicit
 *     notation interpreted, never a downward launder.)
 *   - a value with MORE THAN ONE decimal point is left raw for the same
 *     reason (review round-2 I-dot): collapsing dots laundered European
 *     "1.234.567,89" into 1.23 — with dot thousands-separators the
 *     amount is ambiguous, so it must fail visibly, not guess.
 *   - regroups the integer part with commas;
 *   - RESTORES THE CARET by digit-count (the caret stays anchored to the
 *     digit being edited, wherever the commas move);
 *   - a SINGLE-CHARACTER Backspace/Delete that removed only a group
 *     separator eats the adjacent digit (review I1: regrouping
 *     resurrects the comma, so deleting just the comma was a visible
 *     no-op). Gated to length-1 deletions (review round-2 blocker: with
 *     a stale prev, a selection-delete of ".00" matched the digits-
 *     unchanged test and silently turned 1,000.00 into 100). The
 *     previous formatted value is tracked on el.__idraaMoneyPrev — the
 *     Alpine blur handler re-syncs it after fmt() writes el.value
 *     without an input event; the one prefill-time no-op before any
 *     input event is accepted.
 *   - never forces decimal places while typing (blur commits the
 *     canonical display via the Alpine fmt(): WHOLE dollars for integral
 *     values, stored cents preserved for fractional ones; strict Number()
 *     blanks non-numeric leftovers — the loud legacy failure mode).
 *
 * Loaded globally from base.html BEFORE any HTMX swap, so inline x-data
 * handlers may reference it without the PR #205 stale-global race.
 *
 * KEEP IN SYNC: the benign class below also appears in the fmt()/strip()
 * helpers of scenarios/wizard/_fair_params_form_inner.html.
 */
(function () {
  "use strict";

  // Benign formatting characters a paste may legitimately carry. NOT digits,
  // NOT letters/signs/parens — those must survive to fail the pattern.
  var BENIGN = /[\s$€£¥,'_]/g;

  function sanitize(raw) {
    return raw.replace(BENIGN, "");
  }

  // Shared helpers (owner UAT round 2: money entry on ALL surfaces).
  // idraaMoneySanitize: benign strip, letters/signs survive (never-launder).
  // idraaMoneyFmt: canonical WHOLE-DOLLAR display — the owner ruled decimals
  // out of every money surface (FX *rates* are not money and keep their own
  // inputs). Strict Number(): non-numeric leftovers blank loudly; a typed or
  // pasted sub-dollar fraction rounds on commit (magnitude preserved to <$1
  // — deliberate precision policy, not a launder).
  window.idraaMoneySanitize = function (s) {
    return s === null || s === undefined ? "" : String(s).replace(BENIGN, "");
  };

  window.idraaMoneyFmt = function (v) {
    if (v === "" || v === null || v === undefined) return "";
    var s = window.idraaMoneySanitize(String(v));
    var n = s === "" ? NaN : Number(s);
    if (isNaN(n)) return "";
    // Whole-dollar DISPLAY policy — but stored sub-dollar precision must
    // survive an untouched hydrate/blur round-trip (review I1: rounding here
    // would persist 1290.67 as 1291 on a no-edit re-save). Integral renders
    // whole; fractional keeps cents (server mirror: format_money_attr).
    var frac = n !== Math.trunc(n);
    return n.toLocaleString("en-US", {
      minimumFractionDigits: 0,
      maximumFractionDigits: frac ? 2 : 0,
    });
  };

  // Reusable Alpine component for SINGLE money fields (controls cost, org
  // profile, expert-form loss params, overrides, qualitative magnitude
  // bands…). The input keeps its NAME and posts the comma-grouped display
  // value — EVERY server parser of a money field must therefore sanitize
  // via utils/money.py sanitize_money_str (the server mirror of BENIGN).
  // Adding a money field without its server-side sanitize is the bug class
  // review round-1 found five of (B2-B6). Registered on alpine:init — this
  // file loads non-defer before the deferred Alpine bundle, the factory
  // pattern loss_preview.js already uses (no #205 race; the factory is
  // registered once and survives HTMX swaps).
  document.addEventListener("alpine:init", function () {
    window.Alpine.data("moneyField", function (initial) {
      return {
        display: "",
        init: function () {
          // Numeric initials render canonically; a NON-numeric initial (a 422
          // re-render echoing invalid input) is preserved VERBATIM so Alpine
          // hydration cannot blank what the server deliberately echoed — the
          // field stays pattern-invalid and correctable (never-launder).
          var f = window.idraaMoneyFmt(initial);
          this.display =
            f === "" && initial !== null && initial !== undefined && String(initial).trim() !== ""
              ? String(initial)
              : f;
        },
        get raw() {
          return window.idraaMoneySanitize(this.display);
        },
        live: function (el, ev) {
          this.display = window.idraaMoneyLive(el, ev);
        },
        commit: function (el) {
          this.display = window.idraaMoneyFmt(this.display);
          el.__idraaMoneyPrev = this.display;
        },
      };
    });
  });

  window.idraaMoneyLive = function (el, ev) {
    var raw = el.value;
    var prev = el.__idraaMoneyPrev || "";
    var caret = el.selectionStart === null ? raw.length : el.selectionStart;
    // Digits (and dot) BEFORE the caret anchor the caret across regrouping.
    var digitsBefore = raw.slice(0, caret).replace(BENIGN, "").length;

    var clean = sanitize(raw);
    if (/[^0-9.]/.test(clean) || (clean.match(/\./g) || []).length > 1) {
      // Unknown characters or ambiguous dot-separators: leave the text
      // exactly as pasted/typed — pattern validation shows it invalid.
      el.__idraaMoneyPrev = raw;
      return raw;
    }

    // A SINGLE-CHARACTER delete that changed no digits removed only a group
    // separator — Excel-like: eat the adjacent digit instead of a no-op.
    var inputType = ev && ev.inputType;
    if (
      clean === sanitize(prev) &&
      clean.length > 0 &&
      raw.length === prev.length - 1
    ) {
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
