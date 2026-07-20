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

`favicon.svg` = same three SVG elements with the LIGHT brand hex `#0F4C81` verbatim (spec-sanctioned single exception — SVG favicons cannot read CSS vars) + a comment pinning it to `--color-brand` for the Phase 3 palette swap. base.html: `<link rel="icon" type="image/svg+xml" href="/static/favicon.svg?v={{ static_version }}">`. Sidebar: `collapsed` is an ALPINE client variable — invisible to Jinja — so call `{{ logomark(size=28) }}` (mark only, always visible) and keep a SEPARATE `<span x-show="!collapsed" class="text-h3 font-semibold tracking-tight">Idraa</span>` wordmark exactly as the preview branch does. `with_wordmark=True` is for the login page only.
- [ ] **Step 4: run → PASS**, plus `uv run pytest tests/integration/test_scenario_routes.py -q --no-cov` (chrome regression).
- [ ] **Step 5: rebuild css; commit** `feat(ui): logomark macro — sidebar, favicon, login (#59)`.

### Task 2: Eyebrow + typography classes in macros

**Files:**
- Modify: `src/idraa/templates/macros/breadcrumb.html` (add classes: `font-mono uppercase tracking-[0.14em] text-[10.5px]`, leading rule `<span class="inline-block w-[22px] h-px bg-brand opacity-75 mr-2"></span>`, current page `text-brand`)
- Modify: `src/idraa/static/css/app.css` — port the preview's typography block (`git show preview/design-language:src/idraa/static/css/app.css`, section "Design-language PREVIEW") BUT DELETE the `header nav[aria-label="Breadcrumb"]` element-selector rules (now macro classes); KEEP body gradient + `.text-display`; the `.text-number-lg,.text-number-md` rule must ADD `font-family: var(--font-mono)` alongside tabular-nums (the preview block has tabular-nums ONLY — porting verbatim misses the spec's mono mandate; this is a deliberate delta from the prototype).
- Test: extend `tests/integration/test_design_language_p1.py`

- [ ] Failing test: `test_breadcrumb_is_eyebrow` — GET a page with breadcrumb (e.g. `/scenarios`), assert `uppercase` + `tracking-[0.14em]` classes present in the breadcrumb nav; `test_body_gradient_token` — assert `radial-gradient` appears in `app.css` served (or read file) referencing `--color-brand`.
- [ ] Implement; verify NO `nav[aria-label` selector remains in app.css; rebuild css.
- [ ] Run new tests + `tests/unit/test_theme_bootstrap.py` (must stay green). Commit `feat(ui): eyebrow breadcrumb + instrument type classes (#59)`.

### Task 3: PDF header logomark

**Files:**
- Modify: `src/idraa/services/pdf_theme.py` (add `def brand_logomark(width: float = 22.0) -> Drawing` using `reportlab.graphics.shapes` Path/Polygon/Circle scaled from the 32-unit viewBox — reportlab's origin is BOTTOM-left (Y-up) vs SVG's top-left: apply `y' = 32 - y` to every coordinate (or a Drawing transform `(1,0,0,-1,0,32)`) or the curve renders mirrored; colors: `PDFColors.brand` stroke, `Color(r,g,b,alpha=0.14)` fill)
- Modify: `src/idraa/services/pdf_report.py` — the shared `_draw_cover` (~lines 528-556) builds the header for BOTH tiers — one edit covers both. Place the Drawing left of the wordmark via a 2-column borderless `Table([[drawing, wordmark_paragraph]])` (a bare Drawing appended to the flowable list stacks ABOVE, not beside). The existing cover test asserts extracted TEXT ('Idraa'), so the Table wrap is safe.
- Test: `tests/unit/test_pdf_theme.py` — ADDITIVE test `test_brand_logomark_drawing`: returns Drawing, width==22, contains 3 shapes, stroke color equals `PDFColors.brand`.

- [ ] TDD as above; run FULL `tests/unit/test_pdf_theme.py` + `tests/unit/test_pdf_report.py` (existing pins must not change). Commit `feat(pdf): logomark in report header (#59)`.

### Task 4: Forms — instrument labels + numeric inputs + section rules

**Files:**
- Modify: `src/idraa/static/css/app.css` — port the preview "Forms as instruments" block verbatim (`form fieldset label / .text-meta` mono-uppercase 10px; `form input[type="number"], form input[inputmode="decimal"]` mono tabular; legend padding; `.space-y-3` tightening). These stay CSS as the NET: `macros/form_field.html` IS the label/input macro (22 consumers, and it gets explicit classes below) but ~13 fieldset-bearing templates author raw `<label>`/`.text-meta` outside it; the element rules catch those, redundantly-but-harmlessly double-covering form_field output. Document exactly this in the block comment (a future dev must not read 'no macro exists' and proliferate raw labels). ALSO in this task: (a) audit `scenarios/form.html`, org settings, and the control form for single-column stacks of SHORT fields; convert to the existing `grid grid-cols-1 sm:grid-cols-3 gap-4` idiom where mechanical, record each no-change verdict in the drift log; (b) confirm inputs are uniformly `input-sm`-height with ~4px label gap (normalize only where trivially off); (c) note: numeric-input mono is largely already present via form_field/unit_aware partials — the CSS rule is a net, disclose redundancy.
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
