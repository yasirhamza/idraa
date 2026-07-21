/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./src/idraa/templates/**/*.html",
    "./src/idraa/static/js/**/*.js",
    // No .py glob: audited — the only Python-referenced classes (`alert-error`, `hidden`) are
    // DaisyUI components and/or already present in templates, so scanning .py adds no real
    // class and only emits junk arbitrary-value selectors from Python slices/dicts
    // (e.g. `controls[col_start:col_end]` -> `.\[col_start\:col_end\]`).
  ],
  // Pattern safelist for SPLIT prefix/suffix classes (plan-gate B2): a template writes
  // `text-{{ suffix }}` and the suffix is Python-returned (format_delta -> numeric-pos;
  // status_pill -> status-success), so the full class exists as a literal NOWHERE and no
  // content scan can see it. The families below are this config's own custom `colors` keys.
  safelist: [
    { pattern: /^(text|bg|border)-(status|ink|numeric|surface|brand|border)(-[a-z0-9]+)?$/ },
  ],
  darkMode: ["selector", '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        surface: { 0: "var(--color-surface-0)", 1: "var(--color-surface-1)", 2: "var(--color-surface-2)" },
        border: { subtle: "var(--color-border-subtle)", strong: "var(--color-border-strong)" },
        ink: { 1: "var(--color-ink-1)", 2: "var(--color-ink-2)", 3: "var(--color-ink-3)" },
        brand: "var(--color-brand)",
        status: {
          critical: "var(--color-status-critical)", warning: "var(--color-status-warning)",
          success: "var(--color-status-success)", info: "var(--color-status-info)",
        },
        numeric: { pos: "var(--color-numeric-pos)", neg: "var(--color-numeric-neg)" },
      },
      fontFamily: { sans: ["var(--font-sans)"], mono: ["var(--font-mono)"] },
      fontSize: {
        display: ["1.75rem", { lineHeight: "2.25rem", fontWeight: "600" }],
        h2: ["1.25rem", { lineHeight: "1.75rem", fontWeight: "600" }],
        h3: ["1rem", { lineHeight: "1.5rem", fontWeight: "600" }],
        body: ["0.875rem", { lineHeight: "1.25rem" }],
        meta: ["0.75rem", { lineHeight: "1rem", fontWeight: "500", letterSpacing: "0.05em" }],
        micro: ["0.6875rem", { lineHeight: "1rem", fontWeight: "500" }],
        "number-lg": ["1.5rem", { lineHeight: "1.75rem", fontWeight: "600" }],
        "number-md": ["0.875rem", { lineHeight: "1.25rem", fontWeight: "500" }],
      },
      borderRadius: { input: "4px", card: "6px", table: "0" },
    },
  },
  // @tailwindcss/forms runs in its default `base` strategy DELIBERATELY: the
  // app's text-input/select/textarea chrome builds on its global reset
  // (app.css layers bg/border tokens on top). Its checkbox/radio reset,
  // however, clobbers DaisyUI's .toggle/.checkbox/.radio — build_css.py
  // appends a daisyui-controls-restore block after it to undo exactly that
  // (UAT 2026-07-21). Do NOT switch to strategy:"class" casually — it strips
  // the text-input base app-wide.
  plugins: [require("@tailwindcss/forms"), require("@tailwindcss/typography")],
};
