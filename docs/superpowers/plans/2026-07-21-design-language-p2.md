# Design Language Phase 2 Implementation Plan (idraa#59)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dashboard empty-state density, deck chart style, empty-state logomark, run-detail readouts (spec: `docs/superpowers/specs/2026-07-21-design-language-p2-design.md`).

**Architecture:** Dashboard density = template-conditional compact variants driven by EXISTING `DashboardData` fields (no view-model change). Chart style = `macros/chart.html` markup + `app.css` (chart_svg.py is geometry-only — it gains ONLY a pure-geometry `area_d` per series; colors stay `var(--chart-*)`). Run readouts = restyle the existing `verdict_strip` / SINGLE KPI blocks with the P1 readout visual, labels verbatim.

**Tech Stack:** Jinja2, app.css tokens, chart_svg geometry + numeric-pin tests, Playwright (chart e2e + screenshots).

## Global Constraints

- Worktree `wt-design-p2`, branch `feat/59-design-language-p2`. FOREGROUND everything; kill+rerun any auto-backgrounded command.
- All P1 constraints: tokens only (no new hex), macro-first, rebuild `tailwind.css` after template/class changes (`SESSION_SECRET=xxxxxxxxxxxxxxxxxx uv run python -m idraa.tasks.build_css`, commit the sheet), both themes, claim conventions (ALE / "modeled reduction"; never EAL / guaranteed).
- **No chart DATA/semantics change**: `chart_palette.py`, axis scales, sampling, series composition, `charts.js` behavior untouched. `test_chart_tokens.py` / `test_chart_macro_palette.py` pins must keep passing.
- Commits `feat(...): ... (#59)` + this branch's session trailers. Tests via `uv run pytest <path> -q --no-cov`.

---

### Task 1: Dashboard density + compact empty rows + logomark watermark

**Files:**
- Modify: `src/idraa/templates/macros/empty_state.html` — ADD macro (existing `empty_state` untouched; `data_table`/`data_grid` consumers unaffected):

```jinja
{% macro empty_row(body, cta=none, mark=True) -%}
{# Compact single-row empty state (design-language P2): one line + inline
   CTA + quiet logomark watermark. For dashboard bands where a tall card
   would be a void. #}
{% from "macros/logo.html" import logomark %}
<div class="flex items-center gap-3 bg-surface-1 border border-border-subtle rounded-card px-4 py-3" data-empty-row>
  {% if mark %}<span class="opacity-25 flex-none">{{ logomark(size=20) }}</span>{% endif %}
  <p class="text-meta text-ink-2 flex-1">{{ body }}</p>
  {% if cta %}<a href="{{ cta.href }}" class="btn btn-sm btn-primary flex-none">{{ cta.label }}</a>{% endif %}
</div>
{%- endmacro %}
```

- Modify: `src/idraa/templates/dashboard/index.html`:
  - Rhythm: `space-y-8` (index.html:22) → `space-y-5`.
  - Stat tiles (index.html:61-72): replace the 2-up `kpi_card` grid with ONE `readout_strip([{label:"Scenarios with runs", value:...}, {label:"Recent runs", value:...}])` (import from `macros/readout.html`; values via the existing count formatting).
  - Loss-distributions empty branch (index.html:121-128) → `empty_row("Run an analysis across 2+ scenarios to see the portfolio loss distributions.", {"href":"/analyses/new","label":"Run aggregate analysis"})` (keep the analyst/admin gate around the CTA exactly as today — pass `cta=none` for viewers).
  - "Get started" (index.html:26-51): keep the full card ONLY under the existing `scenario_count == 0`; no dismiss-state (spec: server-side condition only — the card already disappears once a scenario exists; ADD nothing).
- Modify: `src/idraa/templates/dashboard/_posture.html` — the `_p is none` cold branch (:24) → `empty_row("No aggregate run yet — run one to see your risk posture.", {"href":"/analyses/new","label":"Run aggregate analysis"})` with the same role gate. Inner sub-empties (revenue/appetite/control-value hints) UNCHANGED (they sit inside a populated band).
- Modify: `src/idraa/templates/dashboard/_recent_activity.html` — both empty branches (:42-43, :125-129) → `empty_row(...)` with today's copy verbatim.
- Test: `tests/integration/test_design_language_p2.py` (new): fresh-org fixture (reuse the setup/`authed_admin` idiom): `test_fresh_dashboard_is_compact` — GET `/`, assert ≥2 `data-empty-row`, assert the old tall-card copy renders inside compact rows (strings verbatim), assert `data-readout` present (merged stat strip); `test_populated_dashboard_unaffected` — reuse/extend the existing populated-dashboard test module's fixture (find via `grep -rln "build_dashboard\|dashboard" tests/integration | head`), assert posture band + charts still render (no `data-empty-row` for populated bands).

- [ ] TDD; rebuild css; run new tests + the existing dashboard integration module(s). Commit `feat(ui): dashboard density — compact empty rows + merged stat strip (#59)`.

### Task 2: Chart style layer

**Files:**
- Modify: `src/idraa/services/chart_svg.py` — each series dict gained by `dual_curve`/`epc_curve` (and thus the single wrappers) adds `area_d`: the series `path_d` extended `L <last_x>,<baseline> L <first_x>,<baseline> Z` where baseline = plot-bottom y (the same margin math `_y_scale` uses). Pure geometry, None-safe (empty series → no area_d key or None).
- Modify: `src/idraa/templates/macros/chart.html`:
  - Add per-figure `<defs>` with two `<linearGradient id="grad-{{ figure_id }}-inherent" x1=0 y1=0 x2=0 y2=1>` (stops: `stop-color="var(--chart-inherent)"` opacity .18 → .02; same for residual). `figure_id` = the existing per-figure unique id if one exists, else derive from csv_name/loop — MUST be unique per SVG on a page with multiple charts.
  - Before each stroked series `<path>`, render `{% if s.area_d %}<path d="{{ s.area_d }}" fill="url(#grad-...)" stroke="none"/>{% endif %}` (inherent + residual; NOT for the dashed with-controls overlay if that would double-ink — area only on the two primary series of each figure).
  - Gridlines (:149-156, :391-398 etc.): add `opacity="0.6"` to non-baseline gridlines; the bottom axis line keeps full opacity.
  - Markers: tolerance + CI markers gain halo `stroke="var(--color-surface-1)" stroke-width="1.5"` beneath/atop per deck treatment (tolerance marker is stroke-only — give it a surface under-disc: `<circle ... fill="var(--color-surface-1)"/>` under the existing circle).
- Modify: `src/idraa/static/css/app.css` — verify `.chart-tick` is mono 10px `--color-ink-3`; normalize if not (read current rules first).
- Tests: extend `tests/unit/test_chart_svg.py` — `test_area_d_closes_to_baseline` (approx pins: area_d ends with the two baseline points + Z; baseline y == plot bottom). Extend `tests/unit/test_chart_macros.py` — substring pins: `linearGradient`, `fill="url(#grad-`, `opacity="0.6"`. Existing palette/regex pins must stay green.

- [ ] TDD; rebuild css; run: test_chart_svg.py + test_chart_macros.py + tests/integration/test_chart_macro_palette.py + tests/integration/test_dual_svg_charts.py. Commit `feat(charts): deck style layer — area gradients, halo markers, quiet grid (#59)`.

### Task 3: Run-detail readout restyle (methodology copy-check gated)

**Files:**
- Modify: `src/idraa/templates/runs/components/verdict_strip.html` — restyle the existing 3-up grid (:45-104) to the readout visual: mono uppercase 10px labels, mono `text-number-md` values, 1px-gap `bg-border-subtle` grid (borrow classes from `macros/readout.html`; do NOT force the data through `readout_strip(items)` — the cells carry rich sub-lines, so apply the CLASSES to the existing markup instead). **Labels byte-verbatim**: "Residual ALE (mean)", "Control value / yr (mean)", "Return on control spend", every sub-line unchanged.
- Modify: `src/idraa/templates/runs/detail.html` — the SINGLE-only "Cost vs risk reduction" 4-up block (:98-125): same class-level restyle, labels verbatim ("Total annual cost", "Risk reduction (ALE)", "Net benefit", "ROI").
- Test: extend `tests/integration/test_design_language_p2.py`: `test_verdict_strip_labels_verbatim` — render a completed-run detail (reuse `tests/integration/test_run_detail_components.py` fixtures/idiom) and assert the three aggregate labels + mono classes present; run the FULL existing run-detail modules (test_run_detail_components.py, test_run_detail_aggregate.py) unchanged.
- **Methodology copy-check (narrow):** after implementation, the reviewer (Task-3 review) diffs ONLY the copy: zero label/wording changes allowed; if any new string was introduced it must use ALE / "modeled reduction" conventions.

- [ ] TDD; rebuild css; run the above. Commit `feat(ui): run-detail readout restyle — labels verbatim (#59)`.

### Task 4: Gate + chart e2e + screenshots + drift log

- [ ] Full local gate FOREGROUND: `uv run python scripts/run_local_gate.py` — green.
- [ ] Chart e2e EXPLICITLY (fast gate skips it): `uv run pytest tests/e2e/test_chart_hydration_e2e.py -q --no-cov` FOREGROUND — all 5 tests green (hover/slider/tooltip/CSV must survive the new area/defs markup; charts.js untouched). Record the count in the drift log.
- [ ] Screenshots both themes: (a) FRESH org (wipe local db, /setup) — dashboard fits ~one 1440×900 viewport, `data-empty-row` compact bands, watermark marks; (b) UAT snapshot (copy `../wt-preview/uat-snap.db`, login preview@local.test / Preview12345!) — dashboard populated (rhythm-only delta), run detail readouts + styled charts, gradient fills visible.
- [ ] Drift log: spec correction (style layer lives in chart.html+CSS, chart_svg geometry-only + `area_d`); "no run-form summary surface" verdict; e2e count; any implementer deviations.
- [ ] Commit `docs(design): design-language P2 drift log (#59)`.

---

## Self-review notes
- Spec coverage: density (T1), chart style (T2), empty-state mark (T1 watermark), run readouts (T3 — run-form surface confirmed absent, drift-logged in T4), acceptance (T4). Spec's "chart_svg.py + CSS only" location claim corrected here + drift log.
- The `figure_id` uniqueness requirement in T2 is the one discovery risk — implementer must derive a per-figure unique gradient id (multiple SVGs per page).
- No view-model/service changes anywhere; `DashboardData` fields consumed as-is.
