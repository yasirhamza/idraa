# Design-language Phase 3 — graphite palette + sonar-arcs identity (issue #59)

Final phase of the design-language epic. P1 (logomark/typography/forms, PR #61)
and P2 (dashboard density/chart style/readouts, PR #62) shipped palette-
agnostic: every color routes through the `--color-*` tokens in
`src/idraa/static/css/app.css`. P3 sets the actual palette and the final
brand mark, and closes the two cosmetic items deferred from P1/P2 plus one
UAT-discovered bug class.

## Owner decisions (2026-07-21, settled — not open questions)

- **Palette = graphite (candidate F):** light brand `#37464F`, dark brand
  `#B8C6CC`. Chosen from 8 candidates rendered on live pages, then
  test-driven on the isolated preview app `idraa-graphite` ("I love
  graphite").
- **Logomark = sonar arcs:** two bilateral arcs over a brass dot — layered
  defenses warding off exposure above the asset (Idraa, from إدرأ = to
  avert/mitigate); also reads as a watchful eye. Replaces the P1
  loss-exceedance-curve mark everywhere (web SVG, favicon, PDF port). The
  owner rejected ALL decaying-curve marks as inherently asymmetrical — do
  not re-propose curve marks.
- **Brass accent `#C89141`:** logomark dot only. It is DECORATIVE — 2.77:1
  on white fails AA, so brass must never be used for text or interactive
  affordances on light surfaces. (The chart appetite marker `#B07A10` is a
  different, already-validated color and is untouched.)

## Reference implementation

Branch `preview/graphite` (commits `0323f4d` tokens, `c32afe7` logomark,
plus the etymology comment fix) is the throwaway preview that has been
live-verified by the owner on `idraa-graphite` (web light+dark, favicon,
PDF port, integration pins). It NEVER merges; P3 re-lands the same changes
on a fresh branch from `origin/main`, minus preview-only compromises — in
particular `PDFColors.logo_ink` exists on the preview branch only because
`PDFColors.brand` had to stay blue there; on main P3 flips `brand` itself,
so `logo_ink` must NOT exist (arcs stroke = `PDFColors.brand`).

## Scope

### 1. Token swap (the palette lands)

- `app.css` `:root`: `--color-brand: #37464F`; `[data-theme="dark"]`:
  `--color-brand: #B8C6CC`. Add `--color-logo-accent: #C89141` to BOTH
  scopes (same hue both themes).
- Update the two hardcoded `var(--color-brand, #0F4C81)` fallbacks
  (`macros/page_header.html:52`, `macros/data_table.html:73`) to the new
  light hex `#37464F` (they are light-theme-first inline fallbacks).
- Rebuild the generated sheet (`python -m idraa.tasks.build_css`).

Verified contrast (WCAG relative-luminance, recorded here as the AA audit):

| pair | ratio | verdict |
|---|---|---|
| #37464F on #FFFFFF | 9.76 | AAA |
| #37464F on #FAFAF9 (surface-0) | 9.35 | AAA |
| #FFFFFF on #37464F (btn-primary text) | 9.76 | AAA |
| #B8C6CC on #0A0A0B (dark surface-0) | 11.30 | AAA |
| #B8C6CC on #18181B (dark surface-1) | 10.11 | AAA |
| #0A0A0B on #B8C6CC (dark btn text) | 11.30 | AAA |
| #C89141 (brass) on #FFFFFF | 2.77 | decorative only |
| #C89141 on #37464F | 3.52 | decorative only |

### 2. Sonar-arcs logomark (web + favicon)

Geometry (32×32 viewBox, bilateral about x=16, vertically centered):

```svg
<path d="M9.5 19 A 9 9 0 0 1 22.5 19" fill="none" stroke="currentColor"
      stroke-width="2.5" stroke-linecap="round"/>
<path d="M5 14.5 A 15.5 15.5 0 0 1 27 14.5" fill="none" stroke="currentColor"
      stroke-width="2.5" stroke-linecap="round" opacity=".55"/>
<circle cx="16" cy="20.5" r="2.6" fill="var(--color-logo-accent)"/>
```

- `macros/logo.html`: replace the curve paths; keep the `data-logomark`
  anchor, the `currentColor → var(--color-brand)` arc inking, and the
  macro signature `logomark(size=28, with_wordmark=False)` unchanged.
- `static/favicon.svg`: same geometry; ink hardcoded (SVG favicons cannot
  read CSS custom properties — the P1-sanctioned exception) with an
  internal `@media (prefers-color-scheme: dark)` style switching ink
  `#37464F → #B8C6CC` so the mark stays visible on dark browser chrome;
  dot `#C89141`. Sync comment updated.
- Test pins in `tests/integration/test_design_language_p1.py`: sidebar pin
  becomes `"M9.5 19 A 9 9 0 0 1 22.5 19"`.

### 3. PDF + workbook sync surfaces

- `services/pdf_theme.py`: `PDFColors.brand = #37464F` (PDF is print →
  light values, per the module's own convention); add
  `PDFColors.logo_accent = #C89141`; `brand_logomark()` re-drawn as the
  two arcs + dot. reportlab `Path` has no arc primitive — each ~92° arc is
  a single cubic Bézier with precomputed control points (max deviation
  < 0.03 viewBox units): inner `M9.5 19 C 13.05 15.30, 18.95 15.30,
  22.5 19`, outer `M5 14.5 C 11.07 8.39, 20.93 8.39, 27 14.5`, outer at
  55% alpha, dot at (16, 20.5) r 2.6 filled `logo_accent`. Arc strokes =
  `PDFColors.brand` (NO `logo_ink` — see Reference implementation).
- `services/workbook_theme.py:18`: `brand = "#37464F"`.
- Pin-test updates: `tests/unit/test_pdf_theme.py` (brand hex pin +
  `test_brand_logomark_drawing` shape/color assertions),
  `tests/unit/test_workbook_theme.py`,
  `tests/services/test_verification_workbook_formatting.py` (brand hex
  pins).

### 4. DaisyUI `base-*` retirement (kill the parallel color system)

All 111 `-base-` color-utility usages in `src/idraa/templates/` are
replaced with token utilities, so the tokens are the ONLY color system
(per the project's kill-dead-optionality preference). Mapping:

| DaisyUI class | count | token utility |
|---|---|---|
| `bg-base-100` | 25 | `bg-surface-1` |
| `bg-base-200` | 17 | `bg-surface-2` |
| `border-base-300` | 16 | `border-border-strong` |
| `border-base-200` | 2 | `border-border-subtle` |
| `text-base-content/70` | 18 | `text-ink-2` |
| `text-base-content/60` | 32 | `text-ink-2` |
| `text-base-content/50` | 1 | `text-ink-3` if decorative in context, else `text-ink-2` |

The /60 vs /70 distinction deliberately flattens to `ink-2`: the ink scale
has no AA-passing tier between ink-2 (7.0:1) and ink-3 (2.5:1), and /60
text was body-legible, so both map up, never down. DaisyUI COMPONENT
classes (`btn`, `badge`, `alert`, `join`, …) are untouched — their brand
colors already route through tokens via the P1 overrides in app.css.

Guard: a unit test that scans `src/idraa/templates/**/*.html` and asserts
zero matches of `(bg|text|border|from|to|ring|divide)-base-`, so the
parallel system cannot creep back.

### 5. Fixed-hamburger clearance (UAT 2026-07-21 bug class)

The mobile drawer toggle is `fixed top-4 left-4` and floats OVER page
content; `macros/page_header.html` carries `pl-16` clearance but three
page families hand-author their headers and collide (found by Playwright
sweep at 390px of all sidebar destinations + import flows):

- `/help` (`help/index.html` H1). The full-page article route
  (`help/article_page.html`) swept CLEAR — its breadcrumb macro already
  clears the burger — and `help/_article.html` is shared byte-identical
  with the slide-over drawer partial, so neither is touched.
- `/library/import` (`library/import.html` breadcrumb `<p>` + H1).
- `/scenarios/import` (`scenarios/import.html` breadcrumb `<p>` + H1).

Fix: mobile-only left clearance (`pl-16 md:pl-0` on the page-top header
block, matching the page_header convention), one string-pin regression
test per template. All other swept pages are clear; the sweep script
pattern lives in the plan for reuse.

### 6. SME-name clip at 1440px (P1 drift-log item)

Wizard step-4 (`_fair_params_form_inner.html`): at 1440px the SME name
input clips ("Library referen") when the single-line mono revenue hint
widens the High column. Fix per the P1 drift note: constrain the hint
(`max-width` + allow wrap) so the name column keeps its width. Playwright
check at 1440×900 that the full placeholder/value renders.

### 7. Verification (gate for the PR)

- Full local gate (ruff/format/mypy/pytest) green.
- Chart e2e suite explicitly (CSS changes touch chart surroundings):
  `uv run pytest tests/e2e/test_chart_hydration_e2e.py -q --no-cov -m e2e`.
- Playwright screenshot sweep: dashboard + run detail + wizard step 4 +
  /help at 1440×900 AND 390×844, both themes, fresh sheet — no unstyled
  DaisyUI grays, logomark correct, no hamburger collisions (re-run the §5
  sweep), no SME clip.
- PDF: render the executive report via the production renderer and eyeball
  the header mark + brand chrome (graphite, brass dot).

## Out of scope (follow-ons)

- **PWA manifest + icons (M0)** — immediate follow-on PR after P3 so
  `theme_color`/icons derive from the shipped palette and mark.
- Marketing deck logo/palette refresh (idraa.org + idraa.io mirrors).
- Favicon PNG derivatives / apple-touch-icon (lands with the PWA PR).
- DaisyUI component-theme rebuild (btn/badge internals stay).
- Teardown of `idraa-graphite` + `preview/graphite` happens after prod
  deploy verification (operational, not part of the PR).

## Review tier

UI-only work: quality + architecture reviewers at plan-gate and final
PR-gate (owner-trimmed tier, standing for this epic — set 2026-07-21 at
P1). No FAIR math, calibration, or adapter surfaces are touched; the
methodology and security personas are waived per that standing decision.

## Scope budget

- target_task_count: 7 (6 implementer tasks + 1 main-loop verification
  sweep), single PR.
- Review budget: 2-reviewer plan-gate + 2-reviewer final PR-gate
  (owner-trimmed UI tier), per-task spot review by the main loop.
- Timeline budget: one session (2026-07-21), deploy same day.

## Scope drift log

- +ADDED vs the epic's original P3 line ("palette only"): sonar-arcs
  logomark landing (owner picked the mark during the P3 palette spin —
  identity and palette ship together so main never carries the graphite
  palette with the rejected curve mark).
- +ADDED: fixed-hamburger clearance on 3 hand-authored headers
  (/help, /library/import, /scenarios/import) — UAT-discovered during the
  graphite preview, same bug class as PR #64, caught by the P3 sweep.
- +ADDED: `--color-logo-accent` token (consequence of the mark decision).
- −CUT: PWA manifest/icons (owner asked "how" mid-session — deferred to
  the immediate follow-on PR so icons derive from the shipped palette).
- −CUT: marketing deck (idraa.org/io) logo+palette refresh — follow-on.
- UNCHANGED from the P1/P2 deferrals: DaisyUI base-* retirement, AA audit,
  pdf/workbook sync + pin tests, SME-name clip fix.
