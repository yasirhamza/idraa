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
  a compact single-row variant: one line of copy + the CTA inline, small
  logomark watermark (workstream 3), no tall card body.
- The two lone stat tiles ("Scenarios with runs", "Recent runs") merge into
  ONE `readout_strip` (existing macro) row.
- "Get started" band renders ONLY while `scenarios == 0`; once content
  exists it collapses to a dismissible single line (server-side condition —
  no new JS state).
- Inter-band vertical rhythm tightens (`space-y` scale down one step);
  populated bands keep today's inner spacing.
- With data present, layout must be visually unchanged except rhythm —
  pin with the populated-dashboard integration tests.

### 2 · Chart style layer (deck language on first-party SVG)
`services/chart_svg.py` renders LEC/EPC/bars; style only — **no data,
sampling, scale, or series-semantics change** (colors stay `chart_palette`):
- Grid: lighter hairlines (`--color-border-subtle` at reduced opacity),
  solid baseline only on the zero axis.
- Under-curve area fill: vertical gradient from series color ~18% alpha to
  ~2% (SVG `<linearGradient>` defs; both series).
- Emphasized endpoint/marker dots: existing markers get a 1.5px surface
  stroke halo (deck treatment).
- Tick labels: mono, 10px, `--color-ink-3` (they may already be — verify,
  normalize).
- Chart card caption row becomes an eyebrow (reuse P1 classes) where
  templates render chart titles (`macros/chart.html`).

### 3 · Empty-state logomark
The generic empty-state pattern (locate the macro/partial; if none exists,
the compact empty rows from workstream 1 host it) renders `logomark(size=20)`
at reduced opacity as a quiet watermark — identity in the quiet moments.

### 4 · Run-page readout strips
- Run detail (`templates/runs/…` — locate the KPI/summary block) renders its
  headline numbers through `readout_strip`: residual **ALE**, 1-in-20-year
  loss (VaR p95 wording as currently labeled), modeled reduction. **Copy
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
- `chart_palette.py` / `pdf_theme.py` / `workbook_theme.py` untouched
  (style layer lives in `chart_svg.py` + CSS only).
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
