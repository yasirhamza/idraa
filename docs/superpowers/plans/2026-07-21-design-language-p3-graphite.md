# Design-Language P3 — Graphite Palette + Sonar-Arcs Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the graphite palette and sonar-arcs logomark on main, retire the parallel DaisyUI `base-*` color system, and close the hamburger-clearance and SME-clip cosmetic items.

**Architecture:** Pure token/template/theme work — no routes, models, or FAIR math. All colors flow through `app.css` custom properties; PDF/workbook themes are hand-synced constants pinned by tests. The generated `tailwind.css` sheet must be rebuilt whenever `app.css` or template classes change.

**Tech Stack:** Jinja2 templates, Tailwind-built static CSS, reportlab (pdf_theme), openpyxl (workbook_theme), pytest + Playwright.

**Spec:** `docs/superpowers/specs/2026-07-21-design-language-p3-graphite-design.md` (owner decisions are SETTLED — palette hexes, mark geometry, and the base-* mapping table are not open questions).

**Worktree:** `wt-design-p3`, branch `feat/59-design-language-p3-graphite`.

## Global Constraints

- Light brand `#37464F`, dark brand `#B8C6CC`, logo accent brass `#C89141` — exact hexes, everywhere.
- Brass is decorative-only (2.77:1 on white): never text, never interactive.
- `data-logomark` anchor and `logomark(size=28, with_wordmark=False)` signature unchanged.
- `help/_article.html` must NOT be touched (shared with the drawer partial).
- NO `PDFColors.logo_ink` — PDF arcs stroke `PDFColors.brand`.
- After ANY `app.css` or template-class change: `SESSION_SECRET=<any-16+chars> uv run python -m idraa.tasks.build_css` and commit the regenerated `src/idraa/static/css/tailwind.css` in the same commit.
- Run pytest FOREGROUND only (no `run_in_background`), always `SESSION_SECRET=p3-graphite-implement uv run pytest ... -q --no-cov`.

---

### Task 1: Graphite tokens + inline fallbacks

**Files:**
- Modify: `src/idraa/static/css/app.css` (`:root` line ~13, `[data-theme="dark"]` line ~46)
- Modify: `src/idraa/templates/macros/page_header.html:52`
- Modify: `src/idraa/templates/macros/data_table.html:73`
- Test: `tests/integration/test_design_language_p3.py` (new file)

**Interfaces:**
- Produces: `--color-brand` = `#37464F` (light) / `#B8C6CC` (dark); `--color-logo-accent` = `#C89141` (both scopes). Tasks 2–3 rely on these exact values.

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_design_language_p3.py`:

```python
"""Design-language Phase 3 acceptance tests (issue #59): graphite palette,
sonar-arcs logomark, base-* retirement, hamburger clearance."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.asyncio

APP_CSS_PATH = Path(__file__).resolve().parents[2] / "src" / "idraa" / "static" / "css" / "app.css"
TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "src" / "idraa" / "templates"


async def test_graphite_brand_tokens() -> None:
    """P3: the palette is graphite — light #37464F / dark #B8C6CC — and the
    brass logo accent #C89141 exists in BOTH theme scopes."""
    css = APP_CSS_PATH.read_text(encoding="utf-8")
    root, _, dark = css.partition('[data-theme="dark"]')
    assert "--color-brand:          #37464F" in root
    assert "--color-brand:          #B8C6CC" in dark
    assert root.count("--color-logo-accent:    #C89141") == 1
    assert dark.count("--color-logo-accent:    #C89141") == 1
    assert "#0F4C81" not in css
```

- [ ] **Step 2: Run it — expect FAIL** (`#0F4C81` still present):

`SESSION_SECRET=p3-graphite-implement uv run pytest tests/integration/test_design_language_p3.py -q --no-cov` → FAIL

- [ ] **Step 3: Implement**

In `app.css` `:root` (whitespace-aligned like neighbors):

```css
  --color-brand:          #37464F; /* graphite (#59 P3) */
  --color-logo-accent:    #C89141; /* brass dot in the sonar-arcs logomark (same hue both themes; DECORATIVE only — 2.77:1 on white) */
```

In `[data-theme="dark"]`:

```css
  --color-brand:          #B8C6CC; /* graphite (#59 P3) */
  --color-logo-accent:    #C89141; /* brass dot in the sonar-arcs logomark (same hue both themes; DECORATIVE only) */
```

In `macros/page_header.html:52` and `macros/data_table.html:73`, change
`var(--color-brand, #0F4C81)` → `var(--color-brand, #37464F)`.

- [ ] **Step 4: Rebuild sheet + run test — expect PASS**

```bash
SESSION_SECRET=p3-graphite-implement uv run python -m idraa.tasks.build_css
SESSION_SECRET=p3-graphite-implement uv run pytest tests/integration/test_design_language_p3.py tests/integration/test_design_language_p1.py -q --no-cov
```

- [ ] **Step 5: Commit** — `feat(design): graphite brand tokens + logo-accent token (#59 P3 T1)`

---

### Task 2: Sonar-arcs logomark — web SVG + favicon

**Files:**
- Modify: `src/idraa/templates/macros/logo.html`
- Modify: `src/idraa/static/favicon.svg` (full rewrite)
- Modify: `tests/integration/test_design_language_p1.py:47` (path pin)
- Test: append to `tests/integration/test_design_language_p3.py`

**Interfaces:**
- Consumes: `--color-logo-accent` (Task 1).
- Produces: the mark geometry `M9.5 19 A 9 9 0 0 1 22.5 19` (inner arc), `M5 14.5 A 15.5 15.5 0 0 1 27 14.5` (outer), dot (16, 20.5) r 2.6 — Task 3's PDF port mirrors these numbers.

- [ ] **Step 1: Write the failing tests**

In `test_design_language_p3.py` append (needs `httpx.AsyncClient` + the
`client` fixture, same import style as `test_design_language_p1.py`):

```python
async def test_sonar_arcs_mark_and_favicon(client) -> None:
    """P3: the logomark is the sonar-arcs mark — two bilateral arcs over the
    brass dot — in the login page SVG and in the favicon (which carries a
    dark-scheme media query so the mark survives dark browser chrome)."""
    r = await client.get("/login")
    assert r.status_code == 200
    assert "M9.5 19 A 9 9 0 0 1 22.5 19" in r.text
    assert "M5 14.5 A 15.5 15.5 0 0 1 27 14.5" in r.text
    assert "var(--color-logo-accent)" in r.text

    fav = (await client.get("/static/favicon.svg")).text
    assert "M9.5 19 A 9 9 0 0 1 22.5 19" in fav
    assert "#C89141" in fav
    assert "prefers-color-scheme: dark" in fav
    assert "#B8C6CC" in fav
```

Also flip the P1 pin at `test_design_language_p1.py:47`:
`assert "M9.5 19 A 9 9 0 0 1 22.5 19" in r.text`

- [ ] **Step 2: Run — expect FAIL** (old curve still rendered).

- [ ] **Step 3: Implement `macros/logo.html`** (full replacement of the SVG body; keep macro signature + `data-logomark` + wordmark span):

```html
{# logomark(size=28, with_wordmark=False) — issue #59 P3; sonar-arcs mark
   (owner pick 2026-07-21): two bilateral arcs over a brass dot — layered
   defenses warding off exposure above the asset (Idraa, from إدرأ = to
   avert/mitigate); also reads as a watchful eye. Arcs are palette-agnostic
   (currentColor → var(--color-brand)); the dot uses var(--color-logo-accent).
   Bilateral about x=16. `data-logomark` is the test/integration anchor — do
   not rename without updating tests/integration/test_design_language_p1.py
   and _p3.py. #}
{% macro logomark(size=28, with_wordmark=False) -%}
<span class="inline-flex items-center gap-2.5" data-logomark>
  <svg viewBox="0 0 32 32" width="{{ size }}" height="{{ size }}" class="flex-none overflow-visible" style="color:var(--color-brand)" aria-hidden="true">
    <path d="M9.5 19 A 9 9 0 0 1 22.5 19" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/>
    <path d="M5 14.5 A 15.5 15.5 0 0 1 27 14.5" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" opacity=".55"/>
    <circle cx="16" cy="20.5" r="2.6" fill="var(--color-logo-accent)"/>
  </svg>
  {% if with_wordmark %}<span class="text-h3 font-semibold tracking-tight">Idraa</span>{% endif %}
</span>
{%- endmacro %}
```

**Implement `static/favicon.svg`** (full file):

```svg
<!-- Idraa favicon (issue #59 P3; sonar-arcs mark, owner pick 2026-07-21).
     Static asset — SVG favicons cannot read CSS custom properties, so the ink
     hexes are hardcoded here as the ONE sanctioned exception to the
     palette-agnostic rule. They MUST be kept in sync with the light/dark
     --color-brand tokens in src/idraa/static/css/app.css (the internal media
     query below handles dark browser chrome); the dot matches
     --color-logo-accent. -->
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
  <style>
    .ink { stroke: #37464F; }
    @media (prefers-color-scheme: dark) { .ink { stroke: #B8C6CC; } }
  </style>
  <path class="ink" d="M9.5 19 A 9 9 0 0 1 22.5 19" fill="none" stroke-width="2.5" stroke-linecap="round"/>
  <path class="ink" d="M5 14.5 A 15.5 15.5 0 0 1 27 14.5" fill="none" stroke-width="2.5" stroke-linecap="round" opacity=".55"/>
  <circle cx="16" cy="20.5" r="2.6" fill="#C89141"/>
</svg>
```

- [ ] **Step 4: Run both design test files — expect PASS.**

- [ ] **Step 5: Commit** — `feat(design): sonar-arcs logomark + dark-aware favicon (#59 P3 T2)`

---

### Task 3: PDF + workbook brand sync

**Files:**
- Modify: `src/idraa/services/pdf_theme.py` (PDFColors.brand, +logo_accent, brand_logomark rewrite, source-of-truth comment block)
- Modify: `src/idraa/services/workbook_theme.py:18`
- Modify: `tests/unit/test_pdf_theme.py` (brand pin + `test_brand_logomark_drawing`)
- Modify: `tests/unit/test_workbook_theme.py`, `tests/services/test_verification_workbook_formatting.py` (brand hex pins `#0F4C81` → `#37464F`; READ each pin's context first — only brand pins change, not other colors)

**Interfaces:**
- Consumes: mark geometry from Task 2 (same numbers, Bézier-approximated).
- Produces: `PDFColors.brand == HexColor("#37464F")`, `PDFColors.logo_accent == HexColor("#C89141")`, `workbook_theme` brand `"#37464F"`.

- [ ] **Step 1: Update the pins first (failing tests)** — in `test_pdf_theme.py` change the brand pin to `#37464F` and rewrite the logomark test:

```python
def test_brand_logomark_drawing():
    """#59 P3: brand_logomark() is the reportlab port of the sonar-arcs
    logomark SVG (macros/logo.html) — inner arc + outer arc (55%) + brass
    dot, scaled from the 32-unit viewBox to the requested width."""
    from reportlab.graphics.shapes import Drawing

    d = pdf_theme.brand_logomark()
    assert isinstance(d, Drawing)
    assert d.width == 22.0
    assert len(d.contents) == 3
    stroke_colors = [getattr(shape, "strokeColor", None) for shape in d.contents]
    assert pdf_theme.PDFColors.brand in stroke_colors
    fill_colors = [getattr(shape, "fillColor", None) for shape in d.contents]
    assert pdf_theme.PDFColors.logo_accent in fill_colors
```

In the two workbook test files change every `#0F4C81` BRAND pin to `#37464F`.

- [ ] **Step 2: Run the three test files — expect FAIL.**

- [ ] **Step 3: Implement.** `PDFColors`: `brand = _H("#37464F")` and add
`logo_accent = _H("#C89141")  # brass dot in the sonar-arcs logomark (decorative)`.
`workbook_theme.py:18`: `brand = "#37464F"`.
Replace the logomark comment block + function body in `pdf_theme.py`:

```python
# Source-of-truth SVG (src/idraa/templates/macros/logo.html), 0 0 32 32 viewBox —
# sonar-arcs mark (owner pick 2026-07-21), bilateral about x=16:
#   <path d="M9.5 19 A 9 9 0 0 1 22.5 19" fill="none" stroke="currentColor"
#         stroke-width="2.5" stroke-linecap="round"/>
#   <path d="M5 14.5 A 15.5 15.5 0 0 1 27 14.5" fill="none" stroke="currentColor"
#         stroke-width="2.5" stroke-linecap="round" opacity=".55"/>
#   <circle cx="16" cy="20.5" r="2.6" fill="var(--color-logo-accent)"/>
_LOGOMARK_VIEWBOX = 32.0


def brand_logomark(width: float = 22.0) -> Drawing:
    """Reportlab port of the sonar-arcs logomark (macros/logo.html's inline SVG).

    Three shapes, scaled from the 32x32 SVG viewBox to ``width``: inner arc,
    outer arc at 55% opacity, and the brass dot. reportlab's ``Path`` has no
    circular-arc primitive, so each ~92° arc is a single cubic Bezier
    (control points precomputed from the arc's center/radius via the standard
    k = 4/3*tan(dtheta/4) construction; max deviation < 0.03 viewBox units,
    invisible at render sizes).

    CRITICAL: reportlab's Drawing origin is BOTTOM-left (Y-up); SVG's is
    TOP-left (Y-down). Every viewBox coordinate is mapped through
    ``y' = 32 - y`` (applied in viewBox space, before scaling) — skipping
    this mirrors the mark vertically.
    """
    scale = width / _LOGOMARK_VIEWBOX

    def sx(x: float) -> float:
        return x * scale

    def sy(y: float) -> float:
        return (_LOGOMARK_VIEWBOX - y) * scale

    brand = PDFColors.brand
    outer_translucent = Color(brand.red, brand.green, brand.blue, alpha=0.55)

    d = Drawing(width, width)

    # Shape 1: inner arc (SVG: M9.5 19 A 9 9 0 0 1 22.5 19).
    inner = Path(
        strokeColor=brand,
        strokeWidth=2.5 * scale,
        strokeLineCap=1,  # round
        fillColor=None,
    )
    inner.moveTo(sx(9.5), sy(19))
    inner.curveTo(sx(13.05), sy(15.30), sx(18.95), sy(15.30), sx(22.5), sy(19))
    d.add(inner)

    # Shape 2: outer arc at 55% (SVG: M5 14.5 A 15.5 15.5 0 0 1 27 14.5).
    outer = Path(
        strokeColor=outer_translucent,
        strokeWidth=2.5 * scale,
        strokeLineCap=1,  # round
        fillColor=None,
    )
    outer.moveTo(sx(5), sy(14.5))
    outer.curveTo(sx(11.07), sy(8.39), sx(20.93), sy(8.39), sx(27), sy(14.5))
    d.add(outer)

    # Shape 3: brass dot (the asset under modeled exposure).
    dot = Circle(
        sx(16), sy(20.5), 2.6 * scale, fillColor=PDFColors.logo_accent, strokeColor=None
    )
    d.add(dot)

    return d
```

- [ ] **Step 4: Run the three files + full unit dir — expect PASS:**

`SESSION_SECRET=p3-graphite-implement uv run pytest tests/unit/test_pdf_theme.py tests/unit/test_workbook_theme.py tests/services/test_verification_workbook_formatting.py -q --no-cov`

- [ ] **Step 5: Commit** — `feat(design): graphite PDF/workbook brand + sonar-arcs PDF port (#59 P3 T3)`

---

### Task 4: Retire DaisyUI `base-*` color classes (111 usages)

**Files:**
- Modify: every template under `src/idraa/templates/` matching `-base-` color utilities (~30 files; enumerate with the grep below)
- Test: append guard to `tests/integration/test_design_language_p3.py`

**Interfaces:** none produced; purely mechanical per the spec's mapping table.

- [ ] **Step 1: Write the failing guard test**

```python
_BASE_CLASS_RE = r"(?:bg|text|border|from|to|ring|divide)-base-"


async def test_no_daisyui_base_color_classes() -> None:
    """P3: the DaisyUI base-* color system is retired — all fills/text/borders
    route through the app.css tokens (single color system). Guard against
    re-introduction."""
    import re

    offenders: list[str] = []
    for path in sorted(TEMPLATES_DIR.rglob("*.html")):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(_BASE_CLASS_RE, line):
                offenders.append(f"{path.relative_to(TEMPLATES_DIR)}:{i}")
    assert not offenders, f"DaisyUI base-* color classes found: {offenders[:20]}"
```

- [ ] **Step 2: Run — expect FAIL with ~111 offender lines.**

- [ ] **Step 3: Mechanical replacement** — exact mapping (whole-token replace, preserving surrounding classes):

| from | to |
|---|---|
| `bg-base-100` | `bg-surface-1` |
| `bg-base-200` | `bg-surface-2` |
| `border-base-300` | `border-border-strong` |
| `border-base-200` | `border-border-subtle` |
| `text-base-content/70` | `text-ink-2` |
| `text-base-content/60` | `text-ink-2` |
| `text-base-content/50` | READ the context: decorative glyph → `text-ink-3`, readable text → `text-ink-2` |

Enumerate first: `grep -rEln '(bg|text|border)-base-' src/idraa/templates`.
Apply with sed or per-file edits; then verify zero remain:
`grep -rEn '(bg|text|border|from|to|ring|divide)-base-' src/idraa/templates` → empty.

- [ ] **Step 4: Rebuild sheet + full design tests — expect PASS:**

```bash
SESSION_SECRET=p3-graphite-implement uv run python -m idraa.tasks.build_css
SESSION_SECRET=p3-graphite-implement uv run pytest tests/integration/test_design_language_p3.py tests/integration/test_design_language_p1.py -q --no-cov
```

- [ ] **Step 5: Commit** — `refactor(design): retire DaisyUI base-* color classes for token utilities (#59 P3 T4)`

---

### Task 5: Hamburger clearance on hand-authored headers

**Files:**
- Modify: `src/idraa/templates/help/index.html` (header block, line ~8)
- Modify: `src/idraa/templates/library/import.html` (breadcrumb `<p>` + `<h1>`, lines ~5–8)
- Modify: `src/idraa/templates/scenarios/import.html` (breadcrumb `<p>` + `<h1>`, lines ~6–9)
- Test: append to `tests/integration/test_design_language_p3.py`

**DO NOT touch `help/_article.html`** (shared with the drawer partial) or `help/article_page.html` (already clear via the breadcrumb macro).

- [ ] **Step 1: Write the failing tests** — NOTE: `/library/import` is
ADMIN-gated (`require_role(UserRole.ADMIN)`, routes/library.py:304), so use
the `authed_admin` fixture (tests/conftest.py:243), which can reach all
three pages:

```python
async def test_hand_authored_headers_clear_hamburger(
    authed_admin,
    db_session,
) -> None:
    """UAT 2026-07-21: pages that hand-author their headers (skip
    macros/page_header.html) must carry the same mobile clearance for the
    fixed ☰ (`pl-16 md:pl-0`) so the burger never overlaps their titles."""
    client, _ = authed_admin
    for path in ("/help", "/library/import", "/scenarios/import"):
        r = await client.get(path)
        assert r.status_code == 200, path
        assert "pl-16 md:pl-0" in r.text, f"{path} header lacks hamburger clearance"
```

- [ ] **Step 2: Run — expect FAIL on all three paths.**

- [ ] **Step 3: Implement.** `help/index.html`: `<header class="mb-8">` →
`<header class="mb-8 pl-16 md:pl-0">`. In each import template, add
`pl-16 md:pl-0` to BOTH the breadcrumb `<p class="text-sm ... mb-1">` and the
`<h1 class="text-2xl font-bold mb-2">` (both sit in the burger's fixed band;
matches the `page_header` macro's clearance convention). Add a one-line Jinja
comment above each: `{# pl-16 below md clears the fixed ☰ (see macros/page_header.html) #}`.

- [ ] **Step 4: Rebuild sheet (`pl-16`/`md:pl-0` may be new to these files' class inventory) + run — expect PASS.**

- [ ] **Step 5: Commit** — `fix(ui): hamburger clearance on hand-authored page headers (#59 P3 T5)`

---

### Task 6: SME-name clip at 1440px

**Files:**
- Modify: `src/idraa/templates/scenarios/wizard/_fair_params_form_inner.html` (READ first; find the revenue-hint element that widens the High column)
- Test: append to `tests/integration/test_design_language_p3.py`

Context (P1 drift log): at 1440px the SME name input clips ("Library
referen") when the single-line mono revenue hint widens the High column.
Fix: constrain the hint — add `max-w-[28ch] whitespace-normal` (or the
minimal equivalent that lets the hint wrap instead of propagating width) to
the hint element. The exact class depends on the current markup — READ the
template, find the hint (mono, revenue-related, inside the High column),
and apply the smallest change that stops width propagation. Verify no other
wizard-step-4 test pins the hint markup (`grep -rn "revenue" tests/`).

- [ ] **Step 1: Write the failing string-pin test** (pin the constraint class you add):

```python
async def test_sme_hint_width_constrained() -> None:
    """P1 drift-log fix: the step-4 revenue hint must not propagate width
    into the grid column that holds the SME name input (1440px clip)."""
    tpl = (
        TEMPLATES_DIR / "scenarios" / "wizard" / "_fair_params_form_inner.html"
    ).read_text(encoding="utf-8")
    assert "max-w-[28ch]" in tpl
```

(Adjust the pinned class to whatever minimal fix Step 3 lands — the test
and fix land together; the pin must match the shipped markup.)

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement the minimal hint constraint.**

- [ ] **Step 4: Rebuild sheet + run the design tests — PASS. Then Playwright-verify at 1440×900** (script in Task 7 covers it; a quick manual check here is fine: load wizard step 4 with a library-referenced scenario, assert the SME name input's full placeholder is visible).

- [ ] **Step 5: Commit** — `fix(ui): constrain step-4 revenue hint width — 1440px SME-name clip (#59 P3 T6)`

---

### Task 7: Full verification sweep (main-loop task, not a subagent)

- [ ] **Step 1: Full local gate:** `SESSION_SECRET=p3-graphite-implement uv run pytest -q -m "not e2e"` + `uv run ruff check .` + `uv run ruff format --check .` + `uv run mypy src`
- [ ] **Step 2: Chart e2e explicitly:** `uv run pytest tests/e2e/test_chart_hydration_e2e.py -q --no-cov -m e2e`
- [ ] **Step 3: Playwright sweep** (local dev server, fresh sheet): dashboard, run detail, wizard step 4, /help, /library/import at 1440×900 and 390×844, light + dark. Assert: no DaisyUI default grays (spot-check computed colors), sonar-arcs mark present, hamburger-collision sweep (the §5 evaluate() script from the spec investigation) returns clear on ALL swept paths, SME name input unclipped.
- [ ] **Step 4: PDF eyeball:** render the executive PDF via the production renderer (pattern: `scratchpad/gen_sample_report.py` from the deck work — `_make_completed_single_run` + `build_executive_pdf_data` + `render_executive_pdf`, `ENVIRONMENT=test`) and confirm the header mark is sonar-arcs with brass dot and chrome is graphite.
- [ ] **Step 5: Commit any stragglers; branch ready for final gate.**
