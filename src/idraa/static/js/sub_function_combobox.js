/* sub_function_combobox.js — Alpine.js combobox for the FAIR-CAM
 * sub-function picker on the controls form's assignment rows.
 *
 * Implements the W3C ARIA combobox-with-listbox pattern:
 *   https://www.w3.org/WAI/ARIA/apg/patterns/combobox/
 *
 * Wraps a hidden native <select> so:
 *   - Form-data shape stays `assignments[N][sub_function]=<slug>`
 *   - Server-side HTML5 required validation still gates submit
 *   - HTMX hx-get on the hidden select still fires on change (which
 *     triggers the unit-aware capability widget swap)
 *
 * Loaded by base.html via <script>. Registered as an Alpine global via
 * `window.subFunctionCombobox = function (...) {...}` so the template
 * can reference it directly in `x-data`.
 */

(function () {
  "use strict";

  /**
   * @param {Object} cfg
   * @param {string} cfg.initialValue - current sub-function slug (may be empty)
   * @param {number} cfg.index - assignment row index (used for element ids)
   * @param {Array}  [cfg.groups] - sub-function option groups; if omitted,
   *                                falls back to window.SUB_FUNCTION_GROUPS
   * @returns {Object} Alpine component state
   */
  window.subFunctionCombobox = function (cfg) {
    return {
      // --- state ---
      open: false,
      query: "",
      selectedValue: cfg.initialValue || "",
      highlightedValue: "",
      index: cfg.index,
      groups: cfg.groups || window.SUB_FUNCTION_GROUPS || [],

      // --- derived ---
      get buttonLabel() {
        if (!this.selectedValue) return "";
        const opt = this._findOption(this.selectedValue);
        return opt ? opt.label : "";
      },
      get filteredGroups() {
        const q = this.query.trim().toLowerCase();
        if (!q) return this.groups;
        return this.groups
          .map((g) => ({
            ...g,
            options: g.options.filter(
              (o) =>
                o.label.toLowerCase().includes(q) ||
                o.description.toLowerCase().includes(q) ||
                o.value.toLowerCase().includes(q),
            ),
          }))
          .filter((g) => g.options.length > 0);
      },
      get flatFiltered() {
        return this.filteredGroups.flatMap((g) => g.options);
      },
      get hasMatches() {
        return this.flatFiltered.length > 0;
      },
      get selectedDescription() {
        if (!this.selectedValue) return "";
        const opt = this._findOption(this.selectedValue);
        return opt ? opt.description : "";
      },

      // --- open/close ---
      toggle() {
        this.open ? this.close() : this.openPanel();
      },
      openPanel() {
        this.open = true;
        this.query = "";
        this.highlightedValue =
          this.selectedValue ||
          (this.flatFiltered[0] ? this.flatFiltered[0].value : "");
        // Move focus into the search box so typing filters immediately.
        this.$nextTick(() => {
          if (this.$refs.search) this.$refs.search.focus();
        });
      },
      // `refocus` returns focus to the trigger button — correct ARIA behaviour
      // when the user dismisses an OPEN panel from the keyboard (Escape) or by
      // committing a selection, so they stay "on" the combobox. It must NOT
      // fire on click-outside: Alpine's `.outside` listener runs on EVERY
      // document click outside the element regardless of open state, so an
      // unconditional refocus stole focus to this row's button whenever the
      // user tapped any other field — landing on an arbitrary row's
      // sub-function entry with multiple rows. (mobile focus-jump bug.)
      close({ refocus = false } = {}) {
        this.open = false;
        this.query = "";
        if (refocus) {
          this.$nextTick(() => {
            if (this.$refs.button) this.$refs.button.focus();
          });
        }
      },

      // --- event handlers ---
      onSearchInput() {
        const first = this.flatFiltered[0];
        this.highlightedValue = first ? first.value : "";
      },
      onButtonKeydown(ev) {
        if (ev.key === "ArrowDown" || ev.key === "Enter" || ev.key === " ") {
          ev.preventDefault();
          this.openPanel();
        }
      },
      onSearchKeydown(ev) {
        switch (ev.key) {
          case "ArrowDown":
            ev.preventDefault();
            this._moveHighlight(1);
            break;
          case "ArrowUp":
            ev.preventDefault();
            this._moveHighlight(-1);
            break;
          case "Enter":
            if (this.highlightedValue) {
              ev.preventDefault();
              this.commit(this.highlightedValue);
            }
            break;
          case "Escape":
            ev.preventDefault();
            this.close({ refocus: true });
            break;
          case "Home": {
            ev.preventDefault();
            const f = this.flatFiltered[0];
            if (f) this.highlightedValue = f.value;
            break;
          }
          case "End": {
            ev.preventDefault();
            const fl = this.flatFiltered;
            if (fl.length) this.highlightedValue = fl[fl.length - 1].value;
            break;
          }
        }
      },

      // --- selection ---
      commit(value) {
        const opt = this._findOption(value);
        if (!opt) return;
        this.selectedValue = value;
        this.open = false;
        this.query = "";
        // Sync the hidden native select + fire HTMX row-swap (unit widget).
        this.$nextTick(() => {
          const select = this.$refs.hiddenSelect;
          if (select) {
            select.value = value;
            select.dispatchEvent(new Event("change", { bubbles: true }));
          }
          if (this.$refs.button) this.$refs.button.focus();
        });
      },

      _findOption(value) {
        for (const g of this.groups) {
          for (const o of g.options) {
            if (o.value === value) return o;
          }
        }
        return null;
      },
      _moveHighlight(direction) {
        const flat = this.flatFiltered;
        if (!flat.length) return;
        const idx = flat.findIndex((o) => o.value === this.highlightedValue);
        let next;
        if (idx === -1) {
          next = direction > 0 ? 0 : flat.length - 1;
        } else {
          next = (idx + direction + flat.length) % flat.length;
        }
        this.highlightedValue = flat[next].value;
        this.$nextTick(() => {
          const root = this.$refs.listbox;
          if (!root) return;
          const el = root.querySelector(
            `[data-value="${CSS.escape(flat[next].value)}"]`,
          );
          if (el && typeof el.scrollIntoView === "function") {
            el.scrollIntoView({ block: "nearest" });
          }
        });
      },
    };
  };
})();
