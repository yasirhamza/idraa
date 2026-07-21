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

### Task 1: Graphite tokens, brand-contrast, DaisyUI bridge + inline fallbacks

**Files:**
- Modify: `src/idraa/static/css/app.css` (`:root` line ~13, `[data-theme="dark"]` line ~46, `.btn-primary` ~line 224, `.tabs-boxed .tab-active` ~line 243, utilities block ~line 73)
- Modify: `src/idraa/templates/macros/page_header.html` (line 52 fallback + line 49 `text-white`)
- Modify: `src/idraa/templates/macros/data_table.html` (line 73 fallback + line 72 `text-white`)
- Modify: `src/idraa/templates/setup/wizard.html:26,30`, `src/idraa/templates/scenarios/wizard/_shell.html:62`, `src/idraa/templates/fx_rates/form.html:33` (`text-white` → `text-brand-contrast`)
- Test: `tests/integration/test_design_language_p3.py` (new file)

**Interfaces:**
- Produces: `--color-brand` = `#37464F` / `#B8C6CC`; `--color-logo-accent` = `#C89141` (both scopes); `--color-brand-contrast` = `#FFFFFF` / `#0A0A0B`; utilities `.text-brand-contrast`, `.ring-brand`; DaisyUI bridge vars `--b1/--b2/--b3/--bc/--p/--pc`. Tasks 2–4 rely on these exact names/values.

- [ ] **Step 0: Verify sheet load order** — in `src/idraa/templates/base.html`, confirm the app sheet (`tailwind.css`) `<link>` comes AFTER the vendored DaisyUI `<link>` so equal-specificity `:root`/`[data-theme="dark"]` redefinitions win. If it does not, STOP and flag (do not silently reorder — the order is load-bearing for other overrides).

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_design_language_p3.py`:

```python
"""Design-language Phase 3 acceptance tests (issue #59): graphite palette,
brand-contrast, DaisyUI bridge, sonar-arcs logomark, color-class retirement,
hamburger clearance."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.asyncio

APP_CSS_PATH = Path(__file__).resolve().parents[2] / "src" / "idraa" / "static" / "css" / "app.css"
TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "src" / "idraa" / "templates"


async def test_graphite_brand_tokens() -> None:
    """P3: graphite palette — light #37464F / dark #B8C6CC — with the brass
    logo accent and the brand-contrast foreground in BOTH theme scopes.

    NOTE: rpartition, not partition — app.css line 3's header COMMENT
    mentions the [data-theme="dark"] selector; the real block is the last
    occurrence (plan-gate Q-1)."""
    css = APP_CSS_PATH.read_text(encoding="utf-8")
    root, _, dark = css.rpartition('[data-theme="dark"]')
    assert re.search(r"--color-brand:\s+#37464F", root)
    assert re.search(r"--color-brand:\s+#B8C6CC", dark)
    assert re.search(r"--color-logo-accent:\s+#C89141", root)
    assert re.search(r"--color-logo-accent:\s+#C89141", dark)
    assert re.search(r"--color-brand-contrast:\s+#FFFFFF", root)
    assert re.search(r"--color-brand-contrast:\s+#0A0A0B", dark)
    assert "#0F4C81" not in css


async def test_brand_contrast_routing() -> None:
    """Arch-1: no white-on-brand hardcodes survive — btn-primary and the
    active chart tab route their foreground through --color-brand-contrast
    (dark brand #B8C6CC is a LIGHT fill; white text would be ~1.7:1)."""
    css = APP_CSS_PATH.read_text(encoding="utf-8")
    assert "text-brand-contrast" in css
    assert "--tw-ring-color: var(--color-brand)" in css
    # Split on the rule's opening brace, not the bare selector — the selector
    # text also appears in an explanatory COMMENT above the rule (Q-11).
    for rule in (".btn-primary", ".tabs-boxed .tab-active"):
        block = css.split(rule + " {", 1)[1].split("}", 1)[0]
        assert "var(--color-brand-contrast)" in block, rule
        assert "#fff" not in block, rule


async def test_daisyui_bridge_vars() -> None:
    """Arch-3: DaisyUI component internals (--b1/--b2/--b3/--bc/--p/--pc)
    are re-grounded to token-equivalent OKLCH triplets in both scopes so
    alerts/stats/modals/tabs match token surfaces."""
    css = APP_CSS_PATH.read_text(encoding="utf-8")
    root, _, dark = css.rpartition('[data-theme="dark"]')
    for var in ("--b1:", "--b2:", "--b3:", "--bc:", "--p:", "--pc:"):
        assert var in root, f"{var} missing in :root"
        assert var in dark, f"{var} missing in dark scope"
    assert "21.0331% 0.005860 285.885153" in root   # light --bc = ink-1 #18181B
    assert "21.0331% 0.005860 285.885153" in dark   # dark --b1 = surface-1 #18181B
```

- [ ] **Step 2: Run it — expect FAIL:**

`SESSION_SECRET=p3-graphite-implement uv run pytest tests/integration/test_design_language_p3.py -q --no-cov` → FAIL

- [ ] **Step 3: Implement**

In `app.css` `:root` (whitespace-aligned like neighbors):

```css
  --color-brand:          #37464F; /* graphite (#59 P3) */
  --color-brand-contrast: #FFFFFF; /* foreground on brand fills — flips to near-black in dark (brand becomes a LIGHT fill) */
  --color-logo-accent:    #C89141; /* brass dot in the sonar-arcs logomark (same hue both themes; DECORATIVE only — 2.77:1 on white) */
```

In `[data-theme="dark"]`:

```css
  --color-brand:          #B8C6CC; /* graphite (#59 P3) */
  --color-brand-contrast: #0A0A0B;
  --color-logo-accent:    #C89141; /* brass dot in the sonar-arcs logomark (same hue both themes; DECORATIVE only) */
```

In the token-utilities block (next to `.text-brand`):

```css
.text-brand-contrast { color: var(--color-brand-contrast); }
.ring-brand { --tw-ring-color: var(--color-brand); }
```

DaisyUI bridge — add at the END of the `:root` block (with this comment) and the matching dark values at the end of `[data-theme="dark"]`:

```css
  /* DaisyUI internal bridge (#59 P3, plan-gate Arch-3): the vendored DaisyUI
     paints component internals (.alert, .stats, .modal-box, .badge-ghost,
     .table-zebra, .tabs-boxed, menus) from these theme vars in the
     oklch(var(--b1)/alpha) form. Re-ground them to the EXACT token values
     (OKLCH component triplets — keep the alpha composition; do NOT use
     --fallback-*, it flattens translucent variants). Triplets are derived
     from the token hexes; re-derive if a token hex ever changes. */
  --b1: 100.0000% 0.000000 89.875563;   /* surface-1 #FFFFFF */
  --b2: 96.7434% 0.001326 286.375246;   /* surface-2 #F4F4F5 */
  --b3: 87.1108% 0.005451 286.286023;   /* border-strong #D4D4D8 */
  --bc: 21.0331% 0.005860 285.885153;   /* ink-1 #18181B */
  --p:  38.4708% 0.024616 234.611538;   /* brand #37464F */
  --pc: 100.0000% 0.000000 89.875563;   /* brand-contrast #FFFFFF */
```

Dark scope:

```css
  /* DaisyUI internal bridge — dark (see :root comment). */
  --b1: 21.0331% 0.005860 285.885153;   /* surface-1 #18181B */
  --b2: 27.3936% 0.005477 286.032639;   /* surface-2 #27272A */
  --b3: 37.0323% 0.011880 285.805379;   /* border-strong #3F3F46 */
  --bc: 98.5104% 0.000000 89.875563;    /* ink-1 #FAFAFA */
  --p:  81.7627% 0.017546 225.240050;   /* brand #B8C6CC */
  --pc: 14.5249% 0.002132 286.131340;   /* brand-contrast #0A0A0B */
```

Foreground routing:
- `.btn-primary` block: `color: #fff;` → `color: var(--color-brand-contrast);`
- `.tabs-boxed .tab-active` block: `color: #fff !important;` → `color: var(--color-brand-contrast) !important;`
- While editing these two blocks, reword their stale comments (plan-gate
  Arch-11): app.css ~line 222 "Brand-navy primary actions" → "Brand primary
  actions"; ~lines 240-241 "…instead of brand navy" → "…instead of brand".
- `macros/page_header.html:49` and `macros/data_table.html:72`: `text-white` → `text-brand-contrast` (keep every other class).
- `setup/wizard.html:26,30`, `scenarios/wizard/_shell.html:62`, `fx_rates/form.html:33`: `bg-brand text-white` → `bg-brand text-brand-contrast`.
- `macros/page_header.html:52` and `macros/data_table.html:73`: `var(--color-brand, #0F4C81)` → `var(--color-brand, #37464F)`.

- [ ] **Step 4: Rebuild sheet + run tests — expect PASS**

```bash
SESSION_SECRET=p3-graphite-implement uv run python -m idraa.tasks.build_css
SESSION_SECRET=p3-graphite-implement uv run pytest tests/integration/test_design_language_p3.py tests/integration/test_design_language_p1.py -q --no-cov
```

Also verify the rebuilt `tailwind.css` contains `.text-brand-contrast` (grep).

- [ ] **Step 5: Commit** — `feat(design): graphite tokens, brand-contrast routing, DaisyUI bridge (#59 P3 T1)`

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
     --color-logo-accent. The media query follows the BROWSER's color scheme,
     not the in-app data-theme toggle — tab chrome is browser-themed, so this
     divergence is deliberate; do not "fix" it. -->
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
- Modify: `src/idraa/services/pdf_report.py` — rename `_BRAND_BLUE` → `_BRAND` (~line 138 + all uses) and update the stale `#0F4C81`/"brand blue"/"navy" COMMENTS (~lines 124, 531, 620, 950, 2047). Do NOT touch the chart-legend string "With controls (blue)" (~line 1353) — it refers to `chart_residual` #1E6BB0, which is correct and unchanged.
- Modify: `tests/unit/test_pdf_theme.py` (brand pin `#37464F`; ADD `logo_accent` to the `_TOKENS` drift-pin dict at ~line 11; rewrite `test_brand_logomark_drawing`)
- Modify: `tests/unit/test_workbook_theme.py` (brand hex pin), `tests/services/test_verification_workbook_formatting.py` — CAREFUL (plan-gate Q-2): the fill pin at line 332 is stored as ARGB **`FF0F4C81` → `FF37464F`** (a literal `#`-replace misses it); the nearby `FFE7EEF6` legacy-accent assert is UNTOUCHED; also rename the `..._brand_navy_...` test + re-word its "brand-navy (#0F4C81)" docstring to graphite. READ each pin's context first — only brand pins change, not other colors.

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

In the two workbook test files change every BRAND pin: `#0F4C81` → `#37464F`
AND the ARGB form `FF0F4C81` → `FF37464F` (test_verification_workbook_formatting.py:332).

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

Also in `test_pdf_theme.py`, extend the `_TOKENS` drift pin (plan-gate Q-8):
`"logo_accent": "#C89141",` so a future accent edit trips the palette test.

- [ ] **Step 5: Commit** — `feat(design): graphite PDF/workbook brand + sonar-arcs PDF port (#59 P3 T3)`

---

### Task 4: Retire DaisyUI color classes (base-* + semantic families, ~136 usages)

**Files:**
- Modify: every template under `src/idraa/templates/` matching the class families below (~30+ files; enumerate with the grep below)
- Test: append guard to `tests/integration/test_design_language_p3.py`

**Interfaces:** Consumes `.text-brand-contrast` / `.ring-brand` (Task 1). Purely mechanical per the spec's mapping table otherwise.

- [ ] **Step 1: Write the failing guard test**

```python
_DAISY_COLOR_CLASS_RE = (
    r"(?:bg|text|border|ring|from|to|divide)-"
    r"(?:base-|primary\b|secondary\b|accent\b|error\b|success\b|warning\b|info\b)"
)


async def test_no_daisyui_color_classes() -> None:
    """P3: DaisyUI color utilities (base-* AND the semantic families) are
    retired from templates — fills/text/borders route through the app.css
    tokens. Component classes (btn, alert, badge...) are exempt: their
    internals are re-grounded by the Task-1 bridge. Guard against
    re-introduction (Arch-2)."""
    import re

    offenders: list[str] = []
    for path in sorted(TEMPLATES_DIR.rglob("*.html")):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(_DAISY_COLOR_CLASS_RE, line):
                offenders.append(f"{path.relative_to(TEMPLATES_DIR)}:{i}")
    assert not offenders, f"DaisyUI color classes found: {offenders[:20]}"


async def test_no_legacy_brand_hex() -> None:
    """Arch-7: the retired brand navy may not reappear anywhere first-party
    (templates, CSS, services, JS) — vendored assets excluded."""
    src = Path(__file__).resolve().parents[2] / "src" / "idraa"
    offenders: list[str] = []
    for path in sorted(src.rglob("*")):
        if not path.is_file() or "static/vendor" in str(path).replace("\\", "/"):
            continue
        if path.suffix not in {".py", ".html", ".css", ".js", ".svg"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "0F4C81" in text.upper().replace("#", ""):
            offenders.append(str(path.relative_to(src)))
    assert not offenders, f"legacy brand hex #0F4C81/FF0F4C81 found in: {offenders}"


async def test_rebuilt_sheet_has_hover_surface() -> None:
    """Arch-4: the JIT build must generate hover:bg-surface-2 (destination of
    the 5 hover:bg-base-200 swaps, incl. 2 Alpine :class strings)."""
    sheet = APP_CSS_PATH.parent / "tailwind.css"
    assert "hover\\:bg-surface-2" in sheet.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run — expect FAIL** (offenders listed; the hex guard may already pass if Tasks 1–3 landed first — that is fine, it is a ratchet).

- [ ] **Step 3: Mechanical replacement** — exact mapping (whole-token replace, preserving surrounding classes; 2 sites are inside Alpine `:class` STRINGS — `controls/_assignment_row.html:176`, `scenarios/_attack_mapping_row.html:113` — edit inside the quoted JS string, keep quoting intact):

| from | to |
|---|---|
| `bg-base-100` | `bg-surface-1` |
| `hover:bg-base-200` | `hover:bg-surface-2` |
| `bg-base-200` | `bg-surface-2` |
| `border-base-300` | `border-border-strong` |
| `border-base-200` | `border-border-subtle` |
| `text-base-content/70` | `text-ink-2` |
| `text-base-content/60` | `text-ink-2` |
| `text-base-content/50` | READ the context: decorative glyph → `text-ink-3`, readable text → `text-ink-2` |
| `bg-primary` (NOT the `/5` site — see below) | `bg-brand` |
| `text-primary-content` | `text-brand-contrast` |
| `text-primary` | `text-brand` |
| `ring-primary` | `ring-brand` |
| `text-error` | `text-status-critical` |
| `border-error` (NOT the `/30` sites — see below) | `border-status-critical` |
| `text-success` | `text-status-success` |
| `text-warning` | `text-status-warning` |

**OPACITY-MODIFIED sites (plan-gate Q-10 — 4 usages; the hex-var tokens
silently DROP `/NN` modifiers, per the documented foot-gun at
`macros/page_header.html:24-27`, so a whole-token replace would render
solid fills):**

- `scenarios/wizard/_step_1_library_cards.html:7` —
  `[&:has(input:checked)]:bg-primary/5` →
  `[&:has(input:checked)]:bg-[color-mix(in_srgb,var(--color-brand)_5%,transparent)]`
  (Tailwind arbitrary-value; the JIT already generates arbitrary-variant
  utilities from this file — `ring-primary` on line 6 proves the scan
  reaches it). This is a THIRD edit-site kind (Tailwind arbitrary-variant
  string), distinct from the two Alpine `:class` sites.
- `overlays/import_result.html:29`, `library/import_result.html:29`,
  `scenarios/import_result.html:29` — `border-error/30` →
  `border-status-critical-faint`, a new hand-written utility added next to
  the other token utilities in `app.css`:
  `.border-status-critical-faint { border-color: color-mix(in srgb, var(--color-status-critical) 30%, transparent); }`

Replacement ORDER matters where prefixes overlap: handle the 4
opacity-modified sites FIRST, then `hover:bg-base-200` before
`bg-base-200`, `text-primary-content` before `text-primary`. After Step 3,
verify no `/NN`-modified token utility exists:
`grep -rEn '(bg-brand|border-status|text-status|bg-surface|text-ink)[a-z-]*/[0-9]+' src/idraa/templates` → empty.

Enumerate first: `grep -rEln '(bg|text|border|ring)-(base-|primary|error|success|warning)' src/idraa/templates`.
Then verify zero remain with the guard regex.

- [ ] **Step 4: Rebuild sheet + full design tests — expect PASS:**

```bash
SESSION_SECRET=p3-graphite-implement uv run python -m idraa.tasks.build_css
SESSION_SECRET=p3-graphite-implement uv run pytest tests/integration/test_design_language_p3.py tests/integration/test_design_language_p1.py -q --no-cov
```

- [ ] **Step 5: Commit** — `refactor(design): retire DaisyUI color classes for token utilities (#59 P3 T4)`

---

### Task 5: Hamburger clearance on hand-authored headers

**Pre-triage (plan-gate Arch-9 — the heuristic population was enumerated
and classified against the tree; the burger renders on EVERY page,
including login/setup, because `base.html:115` includes the sidebar
unconditionally):**

FIX — every heuristic candidate whose breadcrumb/`<h1>` is the first
content element and is not device-gated gets `pl-16 md:pl-0`.
**Allowlisting a colliding page is never permitted.** The 19 FIX files:
`help/index.html`; `library/import.html`, `library/import_preview.html`,
`library/import_result.html`, `library/import_expired.html`,
`library/delete_result.html`; `scenarios/import.html`,
`scenarios/import_preview.html`, `scenarios/import_result.html`,
`scenarios/import_expired.html`, `scenarios/confirm_delete.html`;
`overlays/import_preview.html`, `overlays/import_result.html`,
`overlays/import_expired.html`; `register_import/import_expired.html`;
`fx_rates/list.html`; `library/overrides/list.html`,
`library/overrides/view.html`; `setup/wizard.html`. (For each, READ the
file first and confirm the header is the first content element; apply the
clearance to the topmost header element(s) exactly as in Step 3.)

ALLOWLIST — 4 files, by named category:
- `auth/login.html` — h1 at y≈110, below-band (VERIFIED at 390px; its
  logomark starts at y=48, exactly abutting the burger band — borderline,
  note this in the allowlist comment so a padding tweak re-triggers
  scrutiny).
- `help/_article.html` — drawer partial (no `{% extends %}`).
- `library/overrides/form.html`, `qualitative_bands/form.html` —
  `only_on_md()`-gated (content renders only ≥ md, where the burger is
  hidden).

**Files:**
- Modify: the 19 FIX templates above
- Test: append to `tests/integration/test_design_language_p3.py`

**DO NOT touch `help/_article.html`** (shared with the drawer partial) or `help/article_page.html` (already clear via the breadcrumb macro).

- [ ] **Step 1: Write the failing tests** — NOTE (citations corrected at
plan-gate, Q-3): `/library/import` GET is ADMIN-gated
(`routes/library_import.py:51-52`), `/scenarios/import` GET is ADMIN-gated
(`routes/scenario_import.py:49-52`), `/help` needs only `require_user`
(`routes/help.py:26`) — so use the `authed_admin` fixture
(tests/conftest.py:243), which reaches all three pages:

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

Additionally (Arch-5 — durable guard; this bug class has recurred twice), a
sweep test with an explicit allowlist:

```python
# Allowlist categories (the burger renders on EVERY page — base.html
# includes the sidebar unconditionally): (1) h1 verified below the burger
# band at 390px; (2) drawer partial, never a full page; (3) only_on_md-
# gated content (burger is md:hidden). Allowlisting a COLLIDING page is
# never permitted; a new entry requires 390px verification.
_H1_CLEARANCE_ALLOWLIST = {
    "auth/login.html",  # h1 y~110 below-band; NOTE its logomark starts at
    # y=48 exactly abutting the burger band — re-verify if login padding
    # ever changes
    "help/_article.html",  # drawer partial (no extends)
    "library/overrides/form.html",  # only_on_md-gated
    "qualitative_bands/form.html",  # only_on_md-gated
}


async def test_hand_authored_h1_headers_have_clearance() -> None:
    """Arch-5: any template with an <h1> that neither uses the page_header
    macro nor carries pl-16 clearance must be allowlisted (verified
    below-band). A new top-of-page hand-authored header fails HERE, not in
    UAT."""
    offenders: list[str] = []
    for path in sorted(TEMPLATES_DIR.rglob("*.html")):
        rel = str(path.relative_to(TEMPLATES_DIR)).replace("\\", "/")
        text = path.read_text(encoding="utf-8")
        if "<h1" not in text or rel in _H1_CLEARANCE_ALLOWLIST:
            continue
        if "page_header" in text or "pl-16" in text:
            continue
        offenders.append(rel)
    assert not offenders, (
        f"templates with unprotected <h1> headers (add pl-16 md:pl-0 or "
        f"page_header, or allowlist WITH 390px verification): {offenders}"
    )
```

- [ ] **Step 2: Run — expect FAIL listing exactly the 19 FIX files (the sweep test) plus the three route assertions.**

- [ ] **Step 3: Implement.** `help/index.html`: `<header class="mb-8">` →
`<header class="mb-8 pl-16 md:pl-0">`. In every other FIX template, add
`pl-16 md:pl-0` to the topmost header element(s) — for the
breadcrumb-`<p>` + `<h1>` shape, BOTH elements (both sit in the burger's
fixed band; same clearance idea as page_header's `pl-16 pr-4 md:px-6` —
the reset differs because these headers sit inside padded containers). Add
a one-line Jinja comment above each:
`{# pl-16 below md clears the fixed ☰ (see macros/page_header.html) #}`.
The FIX/ALLOWLIST classification is already done in the pre-triage above —
do not re-derive it; if a FIX file's header turns out NOT to be the first
content element on reading, flag it rather than silently allowlisting.

- [ ] **Step 4: Rebuild sheet (`pl-16`/`md:pl-0` may be new to these files' class inventory) + run — expect PASS.**

- [ ] **Step 5: Commit** — `fix(ui): hamburger clearance on hand-authored page headers + durable guard (#59 P3 T5)`

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

- [ ] **Step 3: Implement the minimal hint constraint.** In the SAME file
(plan-gate Q-4), also map the 3 `text-gray-*` usages (~lines 77, 244) to ink
tokens: `text-gray-600` → `text-ink-2`; `text-gray-500` → `text-ink-3` if
decorative in context, else `text-ink-2` — completing the single-color-system
claim. Optionally extend the Task-4 guard regex with `|gray-` afterwards if
no other `-gray-` usages exist repo-wide (check first).

- [ ] **Step 4: Rebuild sheet + run the design tests — PASS. Then Playwright-verify at 1440×900** (script in Task 7 covers it; a quick manual check here is fine: load wizard step 4 with a library-referenced scenario, assert the SME name input's full placeholder is visible).

- [ ] **Step 5: Commit** — `fix(ui): constrain step-4 revenue hint width — 1440px SME-name clip (#59 P3 T6)`

---

### Task 7: Full verification sweep (main-loop task, not a subagent)

- [ ] **Step 1: Full local gate:** `SESSION_SECRET=p3-graphite-implement uv run pytest -q -m "not e2e"` + `uv run ruff check .` + `uv run ruff format --check .` + `uv run mypy src`
- [ ] **Step 2: Chart e2e explicitly:** `uv run pytest tests/e2e/test_chart_hydration_e2e.py -q --no-cov -m e2e`
- [ ] **Step 3: Playwright sweep** (local dev server, fresh sheet): dashboard, run detail, wizard step 4, /help, /library/import at 1440×900 and 390×844, light + dark. Assert: no DaisyUI default grays (spot-check computed colors), sonar-arcs mark present, hamburger-collision sweep (the §5 evaluate() script from the spec investigation) returns clear on ALL swept paths, SME name input unclipped.
- [ ] **Step 4: PDF eyeball:** render the executive PDF via the production renderer (pattern: `scratchpad/gen_sample_report.py` from the deck work — `_make_completed_single_run` + `build_executive_pdf_data` + `render_executive_pdf`, `ENVIRONMENT=test`) and confirm the header mark is sonar-arcs with brass dot and chrome is graphite. Check mark SIZE/placement (plan-gate Q-7): the arcs occupy a smaller, left-of-center portion of the 22×22 Drawing than the old corner-to-corner curve — if under-sized, bump the `brand_logomark(width=...)` call site, do not distort geometry.
- [ ] **Step 5: Open the follow-on GH issue** (spec Out-of-scope / plan-gate Arch-3): "DaisyUI status-family bridge (`--er/--su/--wa/--in`) — align alert/badge component internals with `--color-status-*` tokens", labeled design-language, referencing #59 and this plan.
- [ ] **Step 6: Commit any stragglers; branch ready for final gate.**
