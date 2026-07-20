# Design Language Phase 1 Implementation Plan (idraa#59)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the deck-derived identity (logomark), instrument-voice typography, and forms restructure across the app — palette-agnostic (spec: `docs/superpowers/specs/2026-07-21-design-language-p1-design.md`).

**Architecture:** All styling flows through `app.css` tokens + macro-level classes (never element-selector overrides where a macro exists). The committed branch `preview/design-language` (commits `aba319d`, `9d5c379`, `b0f3bd1`) is the WORKING PROTOTYPE — implementers lift its exact SVG/CSS via `git show preview/design-language:<path>` and productionize per task instructions. One new Jinja macro file per reusable pattern (`logo.html`, `readout.html`).

**Tech Stack:** Jinja2 macros, `app.css` custom properties, standalone Tailwind build (`python -m idraa.tasks.build_css`), reportlab (PDF header), pytest + httpx template-render tests, Playwright screenshot harness.

## Global Constraints

- Worktree `wt-design-p1`, branch `feat/59-design-language-p1`. FOREGROUND every test/build command — never background.
- **Palette-agnostic:** no new hex outside `app.css`; SVG uses `currentColor`; brand color only via `var(--color-brand)` / `PDFColors.brand`.
- Both themes via tokens only. `test_theme_bootstrap.py` pins must keep passing; `test_pdf_theme.py` changes are ADDITIVE only (no color-pin edits).
- After ANY template/class change: `SESSION_SECRET=x uv run python -m idraa.tasks.build_css` and commit the rebuilt `tailwind.css` (staleness gate).
- Chart JS/CSS untouched (Phase 2). Claim conventions: any new label copy says "ALE" / "modeled reduction" (never EAL / guaranteed reduction).
- Commit messages `feat(...): ... (#59)` + the branch's session trailers.
- Tests: `uv run pytest <path> -q --no-cov`.

---

### Task 1: Logo macro + sidebar + favicon + login

**Files:**
- Create: `src/idraa/templates/macros/logo.html`
- Modify: `src/idraa/templates/layouts/_sidebar.html` (brand block — replace the preview's inline SVG with the macro; prototype: `git show preview/design-language:src/idraa/templates/layouts/_sidebar.html`)
- Create: `src/idraa/static/favicon.svg`; Modify: `src/idraa/templates/base.html` `<head>` (favicon links)
- Modify: `src/idraa/templates/auth/login.html` (mark above the card)
- Test: `tests/integration/test_design_language_p1.py` (new)

**Interfaces — Produces:** `{% from "macros/logo.html" import logomark %}`; `logomark(size=28, with_wordmark=False)` → inline SVG (`currentColor`), optional `<span>Idraa</span>` wordmark.

- [ ] **Step 1: failing tests** — in the new test module (reuse `authed_analyst` fixture idiom from `tests/integration/test_scenario_routes.py`):

```python
async def test_sidebar_renders_logomark(authed_analyst, db_session):
    client, _ = authed_analyst
    r = await client.get("/")
    assert 'data-logomark' in r.text          # macro root attr
    assert 'M3 7 C 11 8, 12 24, 29 26' in r.text  # curve path
async def test_login_and_favicon(client):     # unauthenticated client fixture
    r = await client.get("/login")
    assert 'data-logomark' in r.text
    r2 = await client.get("/static/favicon.svg")
    assert r2.status_code == 200 and "svg" in r2.text
```

- [ ] **Step 2: run → FAIL.**
- [ ] **Step 3: implement.** `logo.html`:

```jinja
{% macro logomark(size=28, with_wordmark=False) -%}
<span class="inline-flex items-center gap-2.5" data-logomark>
  <svg viewBox="0 0 32 32" width="{{ size }}" height="{{ size }}" class="flex-none overflow-visible" style="color:var(--color-brand)" aria-hidden="true">
    <path d="M3 7 C 11 8, 12 24, 29 26" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"/>
    <path d="M3 7 C 11 8, 12 24, 29 26 L 29 29 L 3 29 Z" fill="currentColor" opacity=".14"/>
    <circle cx="3" cy="7" r="2.6" fill="currentColor"/>
  </svg>
  {% if with_wordmark %}<span class="text-h3 font-semibold tracking-tight">Idraa</span>{% endif %}
</span>
{%- endmacro %}
```

`favicon.svg` = same three SVG elements, hardcoded `#0F4C81`?? NO — favicons cannot use CSS vars; use `fill/stroke="#2563a8"`-class neutral? DECISION: favicon uses the LIGHT brand hex from app.css verbatim (`#0F4C81`) with a comment that Phase 3 re-exports it; this is the sanctioned single exception, placed in a static asset (not a template). base.html: `<link rel="icon" type="image/svg+xml" href="/static/favicon.svg?v={{ static_version }}">`. Sidebar: replace `▣` block with `{{ logomark(size=28, with_wordmark=not collapsed) }}` adapted to the Alpine `collapsed` x-show spans exactly as the preview does (keep both spans; wordmark span gets `x-show="!collapsed"`).
- [ ] **Step 4: run → PASS**, plus `uv run pytest tests/integration/test_scenario_routes.py -q --no-cov` (chrome regression).
- [ ] **Step 5: rebuild css; commit** `feat(ui): logomark macro — sidebar, favicon, login (#59)`.

### Task 2: Eyebrow + typography classes in macros

**Files:**
- Modify: `src/idraa/templates/macros/breadcrumb.html` (add classes: `font-mono uppercase tracking-[0.14em] text-[10.5px]`, leading rule `<span class="inline-block w-[22px] h-px bg-brand opacity-75 mr-2"></span>`, current page `text-brand`)
- Modify: `src/idraa/static/css/app.css` — port the preview's typography block (`git show preview/design-language:src/idraa/static/css/app.css`, section "Design-language PREVIEW") BUT DELETE the `header nav[aria-label="Breadcrumb"]` element-selector rules (now macro classes); KEEP body gradient, `.text-display`, `.text-number-*` mono/tabular rules.
- Test: extend `tests/integration/test_design_language_p1.py`

- [ ] Failing test: `test_breadcrumb_is_eyebrow` — GET a page with breadcrumb (e.g. `/scenarios`), assert `uppercase` + `tracking-[0.14em]` classes present in the breadcrumb nav; `test_body_gradient_token` — assert `radial-gradient` appears in `app.css` served (or read file) referencing `--color-brand`.
- [ ] Implement; verify NO `nav[aria-label` selector remains in app.css; rebuild css.
- [ ] Run new tests + `tests/unit/test_theme_bootstrap.py` (must stay green). Commit `feat(ui): eyebrow breadcrumb + instrument type classes (#59)`.

### Task 3: PDF header logomark

**Files:**
- Modify: `src/idraa/services/pdf_theme.py` (add `def brand_logomark(width: float = 22.0) -> Drawing` using `reportlab.graphics.shapes` Path/Polygon/Circle scaled from the 32-unit viewBox; colors: `PDFColors.brand` stroke, same at 14% alpha fill — use `Color(r,g,b,alpha=0.14)` from the brand hex)
- Modify: `src/idraa/services/pdf_report.py` — READ the existing header-render block first (search "Idraa" wordmark drawing/Paragraph in the page-header/first-page builder); place the Drawing left of the wordmark, baseline-aligned, both report tiers.
- Test: `tests/unit/test_pdf_theme.py` — ADDITIVE test `test_brand_logomark_drawing`: returns Drawing, width==22, contains 3 shapes, stroke color equals `PDFColors.brand`.

- [ ] TDD as above; run FULL `tests/unit/test_pdf_theme.py` + `tests/unit/test_pdf_report.py` (existing pins must not change). Commit `feat(pdf): logomark in report header (#59)`.

### Task 4: Forms — instrument labels + numeric inputs + section rules

**Files:**
- Modify: `src/idraa/static/css/app.css` — port the preview "Forms as instruments" block verbatim (`form fieldset label / .text-meta` mono-uppercase 10px; `form input[type="number"], form input[inputmode="decimal"]` mono tabular; legend padding; `.space-y-3` tightening). These stay CSS (they target plain elements across ~12 form templates — no macro exists for label/input primitives; document this exception in the block comment).
- Modify: `src/idraa/templates/macros/form_field.html` — label span gains explicit classes matching the CSS (macro-first for the one place a macro DOES exist).
- Test: extend `tests/integration/test_design_language_p1.py`: `test_wizard_step4_labels_mono` (walk to step 4 via the `_wizard_step3_test_helpers` idiom OR assert on `/scenarios/new` form: label markup carries mono-uppercase classes / CSS block present); `test_numeric_inputs_mono` — app.css contains the `inputmode="decimal"` mono rule.

- [ ] TDD; rebuild css; run new tests + `tests/integration/test_wizard_mobile_2d.py` + `tests/integration/test_scenario_routes.py`. Commit `feat(ui): forms-as-instruments treatment (#59)`.

### Task 5: Readout macro + wizard review recap

**Files:**
- Create: `src/idraa/templates/macros/readout.html`:

```jinja
{% macro readout_strip(items) -%}
{# items: list of {label, value, accent(bool, optional)} — deck readout pattern #}
<div class="grid grid-cols-1 sm:grid-cols-3 gap-px bg-border-subtle border border-border-subtle rounded-card overflow-hidden" data-readout>
  {% for it in items %}
  <div class="bg-surface-1 px-3.5 py-2.5">
    <div class="font-mono uppercase tracking-[0.12em] text-[10px] text-ink-3">{{ it.label }}</div>
    <div class="font-mono font-semibold text-number-md mt-1 {{ 'text-brand' if it.accent else 'text-ink-1' }}" style="font-variant-numeric:tabular-nums">{{ it.value }}</div>
  </div>
  {% endfor %}
</div>
{%- endmacro %}
```

- Modify: `src/idraa/templates/scenarios/wizard/step_6_review.html` — render the existing per-fieldset recap numbers through `readout_strip` (READ the template first; map the current SME-input summary values into items; copy semantics unchanged — labels reuse the template's existing wording verbatim; do NOT invent metric names).
- Test: `test_wizard_review_uses_readout` — walk a draft to step 6 (reuse `_persist_fair_rows_via_steps_3_and_4` helper), assert `data-readout` present and the strip contains the entered High value.

- [ ] TDD; rebuild css; run the wizard integration modules (`ls tests/integration | grep -i wizard`). Commit `feat(ui): readout strips on wizard review (#59)`.

### Task 6: Gate + screenshot sweep + drift log

- [ ] FOREGROUND full gate: `uv run python scripts/run_local_gate.py` — green.
- [ ] Screenshot acceptance: run the UAT-snapshot harness (recipe in `wt-preview`: `uat-snap.db` + mint `preview@local.test`; snapshot file NEVER committed — verify `.gitignore` or path outside repo): dashboard / wizard step 4 / scenario edit / login, light+dark, plus PDF page 1 via `gen_sample_report.py` recipe. Eyeball against the preview shots; attach to PR.
- [ ] Regression spot-sweep: controls list, library, run detail render (200 + no missing-class artifacts).
- [ ] Spec drift log: dated entries for every deviation implementers disclosed (incl. the favicon hex exception decision). Commit `docs(design): design-language P1 drift log (#59)`.

---

## Self-review notes
- Spec coverage: identity slots (T1 sidebar/favicon/login; T3 PDF; empty-state mark deferred → RECORD in drift log as deliberate Phase 2 fold since chart empty-states are Phase 2 surfaces). Typography (T2+T4), forms (T4+T5 + PR #60 invariant already on main), acceptance (T6).
- Favicon hex: flagged inline as the one sanctioned exception (static asset can't read CSS vars) — plan-gate should confirm.
- Type consistency: `logomark(size, with_wordmark)`, `readout_strip(items)` used consistently; `data-logomark`/`data-readout` are the test anchors.
