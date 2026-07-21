# Design Language Phase 2 — Dashboard Density, Chart Style, Readouts (idraa#59)

Owner-approved continuation 2026-07-21 ("Looks good. proceed" after Phase 1
deployed, Fly v9). Same posture as P1: **palette-agnostic** (all colors via
existing tokens incl. `--chart-*`; Phase 3 owns color), same owner-trimmed
review tier (quality + architecture; a narrow methodology CHECK only on
readout copy that names metrics). Reference prototype for chart language:
the deck (gh-pages branch) + `preview/design-language`.

## Scope

Four workstreams, one PR:

### 1 · Dashboard empty-state density (the owner's "too much white space")
Current dashboard (`templates/dashboard/index.html` + `_posture.html`,
`_coverage_budget.html`, `_recent_activity.html`) gives an EMPTY band the
same real estate as a full one. Principle: **density scales with content**.
- Empty-state bands (posture "No aggregate run yet", loss-distributions
  "Run an analysis across 2+ scenarios", activity "No runs yet") collapse to
  a compact single-row variant: one line of copy + the CTA inline, no tall
  card body; the POSTURE row alone carries the small logomark watermark
  (workstream 3) — the other compact rows use mark=False to avoid logo
  over-repetition.
- The two lone stat tiles ("Scenarios with runs", "Recent runs") merge into
  ONE `readout_strip` (existing macro) row.
- "Get started" band renders ONLY while `scenarios == 0` (it already
  disappears once a scenario exists — no dismiss state, no new JS).
- Inter-band vertical rhythm tightens (`space-y` scale down one step);
  populated bands keep today's inner spacing.
- With data present, layout must be visually unchanged except rhythm —
  pin with the populated-dashboard integration tests.

### 2 · Chart style layer (deck language on first-party SVG)
`services/chart_svg.py` renders LEC/EPC/bars; style only — **no data,
sampling, scale, or series-semantics change** (colors stay `chart_palette`):
- Grid: gridlines dim to opacity 0.6; a NEW dedicated full-opacity baseline
  `<line>` at the plot bottom (the current tick loops have no
  distinguishable zero-axis element — plan-gate finding).
- Under-curve area fill: vertical gradient from series color ~18% alpha to
  ~2% (SVG `<linearGradient>` defs) on BOTH series — including the dashed
  with-controls/residual overlay; each fill's gradient MUST derive from the
  same conditional that picks that path's stroke (single-run curves stroke
  residual — their fill is residual too).
- Emphasized endpoint/marker dots: existing markers get a 1.5px surface
  stroke halo (deck treatment).
- Tick labels: mono, 10px, `--color-ink-3` (`.chart-tick` is 12px non-mono
  today — a REAL app-wide change; both-theme screenshots must confirm no
  tick clipping).
- Chart card caption row becomes an eyebrow (reuse P1 classes) where the
  CALLING templates render chart titles (dashboard/index.html:106,
  runs/_results_panel.html:68/:110).

### 3 · Empty-state logomark
The compact empty-row pattern from workstream 1 hosts `logomark(size=20)`
at reduced opacity as a quiet watermark on the POSTURE row only — identity
in the quiet moments without repetition.

### 4 · Run-page readout strips
- Run detail's REAL headline surfaces (verified at plan-gate): the AGGREGATE
  `verdict_strip` cells — "Residual ALE (mean)", "Control value / yr
  (mean)", "Return on control spend" — and the SINGLE-run "Cost vs risk
  reduction" 4-up (Total annual cost / Risk reduction (ALE) / Net benefit /
  ROI). These get the readout VISUAL (typography-level restyle); VaR/p95
  figures live in the dist table and are NOT headline cells. **Copy
  rule (claim conventions): reuse the page's EXISTING metric labels
  verbatim; if any label must be authored anew it says "ALE" /
  "modeled reduction" — never EAL / guaranteed.** A methodology CHECK
  (single reviewer pass over the copy diff only) gates this workstream.
- Run form (analysis setup) summary block: same treatment IF such a summary
  exists; otherwise record "no run-form summary surface" in the drift log
  and skip (do not invent a new surface).

## Out of scope
- Phase 3: palette / DaisyUI base-* override / sync-surface color moves.
- Chart DATA semantics, axis scales, sampling, series composition, tooltip
  logic (`charts.js` behavior) — presentation attributes only.
- Wizard step-count polish; mobile-specific work.
- The 1440px SME-name clip fix from the P1 drift log — separate small fix,
  not this PR.

## Constraints
- All P1 constraints carry over: tokens only, no new hex, macro-first,
  rebuilt `tailwind.css` per template change, both themes, claim
  conventions.
- **Chart e2e**: the fast gate skips Playwright chart tests — run
  `uv run pytest -m e2e tests/e2e/ -k chart` (or the chart e2e modules
  found by `ls tests/e2e | grep -i chart`) explicitly FOREGROUND before the
  PR; record the count in the drift log.
- `chart_palette.py` / `pdf_theme.py` / `workbook_theme.py` untouched.
  Style layer lives in `macros/chart.html` + `app.css`; `chart_svg.py` is
  geometry-only and gains ONLY the pure-geometry `area_d` per series
  (plan-gate correction of this spec's earlier wording).
- Populated-dashboard behavior pinned by existing tests; empty-state
  variants get new integration tests (fresh-org fixture).
- Screenshot acceptance on BOTH the empty fresh-org DB AND the UAT
  snapshot (populated), both themes.

## Acceptance
- Fresh org: dashboard fits ~one viewport at 1440×900 (no giant voids);
  every CTA still reachable; logomark watermarks present.
- Populated (UAT snapshot): dashboard unchanged except rhythm; charts show
  gradient fills + halo markers + mono ticks; run detail shows readout
  strip with verbatim labels.
- Chart e2e suite green; full local gate green.

## Scope budget

- target_task_count: 5 (dashboard density; chart style; empty-state mark —
  folded into dashboard task if the surface is shared; run readouts;
  gate + chart-e2e + screenshots + drift log).
- review: quality reviewer per task; architecture at plan-gate + final;
  methodology copy-check on workstream 4 only. Single PR.
- timeline: single session.

## Scope drift log

- (seed) 2026-07-21: scope = P1 spec's Phase-2 deferrals + owner's
  white-space complaint; run-form readout conditional on the surface
  actually existing.
- 2026-07-21 execution (T1-T3 all reviewed 0/0; full gate 4461 green; chart
  e2e run EXPLICITLY: 5/5 passed — hover/slider/tooltip/CSV survive the new
  area markup): disclosed deviations — posture cold-row copy is the
  plan-authored merged string, not byte-concatenation of the old 3-element
  card (substring assertions preserved); baseline line on the 4 curve
  macros only (bar/band macros are not curve axes); gradient defs declared
  unconditionally on single-curve figures (unused def inert); tolerance
  under-disc same-radius surface fill; gradient stops use stop-opacity;
  SINGLE cost-summary ROI mirrors kpi_card's real ratio format (no ×) while
  verdict_strip's pre-existing × stays — each surface mirrors its own prior
  behavior. "No run-form summary surface" CONFIRMED (analyses/new.html is a
  plain form) — workstream 4's run-form clause skipped as pre-authorized.
  Fresh-org dashboard height 1393px at 1440x900 (was ~1574px): voids
  collapsed; the remainder is the STRUCTURAL coverage&budget band +
  get-started card (both pre-declared) — "~one viewport" partially met,
  accepted per the plan-gate disposition.
- 2026-07-21 plan-gate R1 (quality + architecture): 10 IMPORTANTs applied —
  §4 rewritten to the real headline cells (no VaR-p95 headline exists);
  baseline instruction made executable (dedicated line); area fill on both
  series with stroke-derived gradient ids; gradient ids scale-scoped
  (`grad-{uid}-{scale}-{series}` — the dual figure emits TWO svgs per uid);
  charts.js verified SAFE for area insertion (data-role selectors only;
  area gets no data-series attr + pointer-events none); EPC baseline
  confirmed plot-bottom; readout_strip generalized to derive columns from
  item count (also fixes the wizard 1-item stray cells); empty_row gains
  chrome=False for in-card sites (card-in-card fix); role-gating via the
  existing `{% set %}` boolean precedent; chart-card titles → eyebrows
  mapped into T2; watermark on posture row only; rhythm one step
  (space-y-6); CI-marker halo dropped (no such marker); .chart-tick change
  disclosed as a real change (12px non-mono today). Coverage&budget band
  stays out of scope (structural, not empty-state) — T4 verifies the
  fresh-org viewport fit and drift-logs if it voids.
