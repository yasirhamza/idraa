# Design Language Phase 1 — Identity, Typography, Forms (idraa#59)

Owner-approved direction 2026-07-21, validated by a preview on a real UAT
snapshot (branch `preview/design-language`, commits `aba319d..b0f3bd1` — the
working prototype for this spec; it never merges). **Palette-agnostic**: every
change flows through existing tokens (`currentColor` / `--color-*`); color is
Phase 3, deliberately out of scope here.

## Scope

Three workstreams, one PR:

### 1 · Identity (logomark)
The loss-exceedance-curve mark (deck asset; `currentColor` SVG: falling curve
+ area fill + origin dot) becomes a first-class brand asset with slots:
- Sidebar brand lockup (mark + wordmark; mark-only when collapsed) — replace
  the `▣` placeholder. (Prototyped.)
- Favicon (SVG favicon + PNG fallback, static assets, base.html links).
- Login page header.
- PDF report header in `services/pdf_theme.py`/`pdf_report.py` (reportlab
  Drawing, brand color from PDFColors — the ONE sync-surface touch; no color
  values change).
The mark ships as a Jinja macro (`macros/logo.html`, size/label params) so
every slot renders the same path data from one source.

### 2 · Typography system ("instrument voice")
- **Eyebrow**: page-header breadcrumb becomes the mono, uppercase, tracked
  eyebrow with leading hairline rule (prototyped as CSS on
  `nav[aria-label="Breadcrumb"]`; productionize INSIDE the breadcrumb/page
  header macros as classes, not element-selector overrides).
- **Data voice**: numeric display classes (`text-number-lg/md`) get
  `font-family: var(--font-mono)` + `tabular-nums`; money/frequency/percentile
  INPUTS (`type=number`, `inputmode=decimal`) render mono with tabular-nums.
- **Field labels**: form labels + in-form `.text-meta` labels become mono
  uppercase 10px tracked (scoped to form fieldsets; hint texts follow —
  accepted in preview).
- Display headings: `letter-spacing:-0.02em`, `text-wrap:balance`.
- Ambient gradient: token-driven radial brand glow on `body` (prototyped) +
  `color-scheme` already handled at base.html (`<meta name="color-scheme">`
  exists — verify, don't duplicate).

### 3 · Forms restructure (the owner's core complaint)
- **Ruled sections**: every form fieldset legend = section header with
  hairline rule; consistent spacing scale between sections (tighter than
  today — density, not air).
- **Row alignment invariant**: multi-cell estimate rows top-align
  (`items-start` + label/button nudge — shipped to prod in PR #60; keep).
- **Readout recaps**: wizard step-6 review renders as
  boxed readout strips (label-over-value, mono values, 1px-gap grid — the
  deck's readout pattern) via a `macros/readout.html` macro.
- **Two-column geometry** where fields are short (low/mode/high triples
  already grid; audit remaining single-column stacks in scenario form,
  org settings, control form).
- Inputs: consistent `input-sm` heights, label-to-input gap 4px, mono
  numerics (workstream 2).

## Out of scope (later phases / never)
- Phase 2: dashboard empty-state density pass; logomark in chart/empty-state placeholders (generic empty_state.html macro is the natural host); run-form + run-detail readout strips; chart style layer (grid, area
  fills, endpoint markers, readout strip on run detail); wizard step-count
  polish.
- Phase 3: palette (WCAG-AA audit, DaisyUI `*-base-*` override story,
  brand-vs-warning collision, 3 sync surfaces + 4 pinning tests).
- Never: re-deriving any FAIR value in templates; touching engine/report
  MATH; changing chart data semantics (claim conventions per
  product-claim-conventions memory: ALE naming, "modeled reduction").

## Constraints
- CSS via `app.css` tokens + rebuilt `tailwind.css` (staleness gate);
  template-scoped classes, NOT element-selector overrides where a macro
  exists.
- Both themes styled via tokens only; no hex introduced outside `app.css` —
  SINGLE exception: `static/favicon.svg` pins the light `--color-brand` hex
  (`#0F4C81`) verbatim, because SVG favicons render in an isolated context
  with no CSS-var access; a comment in the asset pins it to the token and
  Phase 3 MUST update it with the palette.
- `test_theme_bootstrap.py` config pins must keep passing; PDF logo addition
  updates `test_pdf_theme.py` only additively (no color pin changes).
- Chart e2e suite runs explicitly before merge if `charts.js`/chart CSS is
  touched (should not be — Phase 2).
- Screenshots for the PR: reuse the UAT-snapshot harness
  (`wt-preview/uat-snap.db` recipe; snapshot NEVER committed).

## Acceptance
- Playwright shots (dashboard, wizard step 4, scenario edit, login, PDF page 1)
  match the blessed preview direction in both themes.
- Full local gate green; no visual regression on pages NOT in scope (spot
  Playwright sweep: controls list, library, run detail).
- PDF renders the mark in its header on both report tiers.

## Scope budget

- target_task_count: 6 (logo macro+slots; favicon+login; PDF header; eyebrow+
  type classes in macros; forms restructure; gate+screenshot sweep).
- review: standard 2-reviewer per task (no FAIR math — methodology only if a
  readout copy states a metric); final 4-reviewer PR-gate (cross-cutting UI
  infra).
- timeline: single PR, single session.

## Scope drift log

- (seed) 2026-07-21: scope per owner-blessed preview + "forms + density"
  feedback; dashboard density explicitly deferred to Phase 2 at spec time.
- 2026-07-21 plan-gate (owner-trimmed tier: quality + architecture only;
  methodology+security waived for UI-only work): 9 IMPORTANTs applied —
  empty-state logomark slot + run-form readouts moved to Phase 2; favicon
  hex exception codified here; display-number classes gain font-mono (the
  preview only had tabular-nums); sidebar macro call corrected to mark-only
  + x-show wordmark (Alpine var not visible server-side); PDF logomark gets
  the SVG->reportlab Y-flip + 2-col Table placement; Task-4 macro-vs-CSS
  rationale corrected (form_field IS the label macro; CSS is the net for
  ~13 raw-label fieldset templates); two-column audit made an explicit
  Task-4 step.
