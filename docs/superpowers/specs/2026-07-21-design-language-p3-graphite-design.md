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

### 1. Token swap (the palette lands) + brand-contrast + DaisyUI bridge

- `app.css` `:root`: `--color-brand: #37464F`; `[data-theme="dark"]`:
  `--color-brand: #B8C6CC`. Add `--color-logo-accent: #C89141` to BOTH
  scopes (same hue both themes).
- **`--color-brand-contrast` (plan-gate Arch-1 BLOCKER):** light `#FFFFFF`,
  dark `#0A0A0B`. The dark brand flip inverts brand from a dark fill to a
  LIGHT fill — every `color: #fff`-on-brand surface becomes ~1.7:1
  unreadable in dark. All white-on-brand foregrounds route through the new
  token: `app.css` `.btn-primary` and `.tabs-boxed .tab-active` (`color:
  var(--color-brand-contrast)`), a new `.text-brand-contrast` utility, and
  the six `text-white`-over-brand template sites
  (`macros/page_header.html:49`, `macros/data_table.html:72`,
  `setup/wizard.html:26,30`, `scenarios/wizard/_shell.html:62`,
  `fx_rates/form.html:33`) become `text-brand-contrast`. Also add
  `.ring-brand { --tw-ring-color: var(--color-brand); }` (consumed by §4's
  `ring-primary` retirement).
- **DaisyUI internal bridge (plan-gate Arch-3):** the vendored DaisyUI
  paints component internals (`.alert`, `.stats`, `.modal-box`,
  `.badge-ghost`, `.table-zebra`, `.tabs-boxed`, dropdown/menu surfaces)
  from its own `--b1/--b2/--b3/--bc/--p/--pc` theme variables in the
  `oklch(var(--b1)/α)` form — in dark those are blue-tinted (`--b1` ≈
  `#1D232A`) and will visibly mismatch token surfaces (`#18181B`).
  `app.css` therefore redefines the six variables per theme scope as
  OKLCH component triplets EQUAL to the tokens (keeping the `oklch(var/α)`
  alpha composition intact — do NOT use `--fallback-*`, which flattens
  translucent variants):

  | var | light (token) | dark (token) |
  |---|---|---|
  | `--b1` (surface-1) | `100.0000% 0.000000 89.875563` (#FFFFFF) | `21.0331% 0.005860 285.885153` (#18181B) |
  | `--b2` (surface-2) | `96.7434% 0.001326 286.375246` (#F4F4F5) | `27.3936% 0.005477 286.032639` (#27272A) |
  | `--b3` (border-strong) | `87.1108% 0.005451 286.286023` (#D4D4D8) | `37.0323% 0.011880 285.805379` (#3F3F46) |
  | `--bc` (ink-1) | `21.0331% 0.005860 285.885153` (#18181B) | `98.5104% 0.000000 89.875563` (#FAFAFA) |
  | `--p` (brand) | `38.4708% 0.024616 234.611538` (#37464F) | `81.7627% 0.017546 225.240050` (#B8C6CC) |
  | `--pc` (brand-contrast) | `100.0000% 0.000000 89.875563` (#FFFFFF) | `14.5249% 0.002132 286.131340` (#0A0A0B) |

  Load-order prerequisite (implementer verifies): the app sheet must load
  AFTER the vendored DaisyUI sheet in `base.html` so the equal-specificity
  redefinitions win. DaisyUI's STATUS family (`--er/--su/--wa/--in` —
  alert-error/badge-success internals) is deliberately NOT bridged in P3;
  a follow-on issue is opened at PR time (see Out of scope).
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
  dot `#C89141`. Sync comment updated, including (plan-gate Arch-8) an
  explicit sentence that the favicon follows the BROWSER's scheme, not the
  in-app `data-theme` toggle — tab chrome is browser-themed, so this
  divergence is deliberate and must not be "fixed".
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
- `services/pdf_report.py` naming sweep (plan-gate Arch-6/Q-5): rename
  `_BRAND_BLUE` → `_BRAND` and update the `#0F4C81`/"brand blue"/"navy"
  comments (lines ~124, ~531, ~620, ~950, ~2047). The chart-legend string
  "With controls (blue)" stays — it refers to `chart_residual` (#1E6BB0,
  untouched chart palette), not brand.
- Pin-test updates: `tests/unit/test_pdf_theme.py` (brand hex pin, add
  `logo_accent` to the `_TOKENS` drift pin, and the
  `test_brand_logomark_drawing` shape/color assertions),
  `tests/unit/test_workbook_theme.py`, and
  `tests/services/test_verification_workbook_formatting.py` — NOTE
  (plan-gate Q-2): the workbook fill pin at line 332 is stored as ARGB
  `FF0F4C81` and becomes `FF37464F` (a literal `#`-prefixed replace misses
  it); the `FFE7EEF6` legacy-accent assert nearby is untouched; the
  `..._brand_navy_...` test name + docstring re-worded to graphite
  (plan-gate Q-5).

### 4. DaisyUI utility-class retirement (kill the parallel color system)

All DaisyUI COLOR-utility usages in `src/idraa/templates/` — the 111
`-base-` family (spec original) PLUS the ~25 semantic-family stragglers
found at plan-gate (Arch-2) — are replaced with token utilities. Mapping:

| DaisyUI class | count | token utility |
|---|---|---|
| `bg-base-100` | 25 | `bg-surface-1` |
| `bg-base-200` | 12 | `bg-surface-2` |
| `hover:bg-base-200` | 5 | `hover:bg-surface-2` (2 sites are inside Alpine `:class` strings — `controls/_assignment_row.html:176`, `scenarios/_attack_mapping_row.html:113`) |
| `border-base-300` | 16 | `border-border-strong` |
| `border-base-200` | 2 | `border-border-subtle` |
| `text-base-content/70` | 18 | `text-ink-2` |
| `text-base-content/60` | 32 | `text-ink-2` |
| `text-base-content/50` | 1 | `text-ink-3` if decorative in context, else `text-ink-2` |
| `bg-primary` (incl. Alpine `:class`) | 3 | `bg-brand` — EXCEPT the `bg-primary/5` site (below) |
| `text-primary-content` | 2 | `text-brand-contrast` |
| `text-primary` | 1 | `text-brand` |
| `ring-primary` | 1 | `ring-brand` (utility added in §1) |
| `text-error` | 7 | `text-status-critical` |
| `border-error` | 3 | `border-status-critical` — EXCEPT the `border-error/30` sites (below) |
| `text-success` | 5 | `text-status-success` |
| `text-warning` | 3 | `text-status-warning` |

**Opacity-modified sites (plan-gate Q-10):** the token utilities are
hex-var-backed, so Tailwind `/NN` opacity modifiers SILENTLY fail on them
(documented foot-gun, `macros/page_header.html:24-27`). The 4 such sites
get explicit color-mix treatments instead: the library-card
`[&:has(input:checked)]:bg-primary/5` becomes an arbitrary-value
`bg-[color-mix(in_srgb,var(--color-brand)_5%,transparent)]` under the same
variant, and the three `border-error/30` import-result cards use a new
hand-written `.border-status-critical-faint` utility (30% color-mix).
Exact edits in the plan (Task 4).

The /60 vs /70 distinction deliberately flattens to `ink-2`: the ink scale
has no AA-passing tier between ink-2 (7.0:1) and ink-3 (2.5:1), and /60
text was body-legible, so both map up, never down. DaisyUI COMPONENT
classes (`btn`, `badge`, `alert`, `join`, …) are untouched as classes —
their internals are re-grounded via the §1 OKLCH bridge.

Guards (one test, three assertions):
- zero template matches of
  `(bg|text|border|ring|from|to|divide)-(base-|primary\b|secondary\b|accent\b|error\b|success\b|warning\b|info\b)`
  (word-bounded so `text-base` the font-size and `link-error`-style
  component modifiers, if any appear, are judged deliberately);
- zero occurrences of `#0F4C81`/`FF0F4C81` anywhere under `src/idraa/`
  excluding `static/vendor/` (plan-gate Arch-7 — repo-wide old-hex guard);
- the rebuilt `tailwind.css` contains `hover\:bg-surface-2` (the JIT build
  generates it from the registered `theme.extend.colors`; verified in
  `tailwind.config.js:21-31` at plan-gate).

Remaining known non-token colors after this task: the 3 `text-gray-*`
usages in `_fair_params_form_inner.html` (folded into §6, which already
edits that file) — after which templates carry token utilities only.

### 5. Fixed-hamburger clearance (UAT 2026-07-21 bug class)

The mobile drawer toggle is `fixed top-4 left-4` and floats OVER page
content on EVERY page (`base.html` includes the sidebar unconditionally —
login and setup included); `macros/page_header.html` carries `pl-16`
clearance but pages that hand-author their headers collide. The original
Playwright sweep (sidebar destinations + import GET forms) found 3; the
plan-gate re-triage of the full heuristic population (`<h1` + no
page_header + no pl-16) classified 23 candidates: **19 FIX** (first-content
headers — the help index, the entire import
preview/result/expired families across library/scenarios/overlays/
register_import, `library/delete_result`, `scenarios/confirm_delete`,
`fx_rates/list`, `library/overrides/list` + `view`, `setup/wizard`) and
**4 ALLOWLIST** (login — h1 below-band, verified 390px; the help drawer
partial; two `only_on_md`-gated forms). The exact lists live in the plan
(Task 5 pre-triage); allowlisting a colliding page is never permitted.
`help/article_page.html` swept CLEAR (breadcrumb macro) and
`help/_article.html` is shared with the drawer partial — neither is
touched.

Fix: mobile-only left clearance — `pl-16 md:pl-0` on the page-top header
block. (Same clearance IDEA as `page_header.html:31`'s `pl-16 pr-4
md:px-6`; the reset differs because these headers sit inside padded
containers rather than full-bleed.) One string-pin regression test per
template, PLUS a durable allowlist sweep guard (plan-gate Arch-5 — this
bug class has now recurred twice): a unit test scans every template for
`<h1` where the file (a) extends `base.html`, (b) does not use the
`page_header` macro, (c) carries no `pl-16` clearance, and (d) is not in
an explicit allowlist of templates whose `<h1` demonstrably renders below
the burger band (import result/preview/expired pages, standalone no-
sidebar pages like login/setup, drawer partials). A future hand-authored
top-of-page `<h1>` then fails in CI, not in UAT. Route-gating facts for
the tests: `/library/import` GET is ADMIN-gated
(`routes/library_import.py:51-52`), `/scenarios/import` GET is ADMIN-gated
(`routes/scenario_import.py:49-52`), `/help` needs only `require_user`
(`routes/help.py:26`) — so the test uses the `authed_admin` fixture.

### 6. SME-name clip at 1440px (P1 drift-log item) + `text-gray-*` cleanup

Wizard step-4 (`_fair_params_form_inner.html`): at 1440px the SME name
input clips ("Library referen") when the single-line mono revenue hint
widens the High column. Fix per the P1 drift note: constrain the hint
(`max-width` + allow wrap) so the name column keeps its width. Playwright
check at 1440×900 that the full placeholder/value renders.

Same file (plan-gate Q-4): the 3 `text-gray-*` usages (lines ~77, ~244)
map to ink tokens (`text-gray-600` → `text-ink-2`, `text-gray-500` →
`text-ink-3` if decorative in context else `text-ink-2`), completing the
single-color-system claim in §4.

### 7. Verification (gate for the PR)

- Full local gate (ruff/format/mypy/pytest) green.
- Chart e2e suite explicitly (CSS changes touch chart surroundings):
  `uv run pytest tests/e2e/test_chart_hydration_e2e.py -q --no-cov -m e2e`.
- Playwright screenshot sweep: dashboard + run detail + wizard step 4 +
  /help at 1440×900 AND 390×844, both themes, fresh sheet — no unstyled
  DaisyUI grays, logomark correct, no hamburger collisions (re-run the §5
  sweep), no SME clip.
- PDF: render the executive report via the production renderer and eyeball
  the header mark + brand chrome (graphite, brass dot). Pay attention to
  mark SIZE/placement (plan-gate Q-7): the sonar-arcs geometry occupies a
  smaller, left-of-center portion of its 22×22 Drawing box than the old
  corner-to-corner curve; if the header looks under-sized, bump the
  `brand_logomark(width=...)` call site rather than distorting geometry.

## Out of scope (follow-ons)

- **PWA manifest + icons (M0)** — immediate follow-on PR after P3 so
  `theme_color`/icons derive from the shipped palette and mark.
- Marketing deck logo/palette refresh (idraa.org + idraa.io mirrors).
- Favicon PNG derivatives / apple-touch-icon (lands with the PWA PR).
- DaisyUI STATUS-family bridge (`--er/--su/--wa/--in` — alert-error /
  badge-success internals keep DaisyUI's own reds/greens, which differ
  slightly from the `--color-status-*` tokens). Tracked as a follow-on GH
  issue opened at PR time (plan-gate Arch-3 documentation requirement).
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
- +ADDED at plan-gate (2026-07-21, quality+architect reviews applied as
  one consolidated commit): `--color-brand-contrast` token + 8-site
  routing (Arch-1 BLOCKER — dark brand flip inverts fill lightness);
  DaisyUI `--b1/--b2/--b3/--bc/--p/--pc` OKLCH bridge (Arch-3); semantic
  color-utility retirement `primary/error/success/warning` + widened guard
  (Arch-2); `hover:bg-base-200` mapping row + Alpine-site notes (Arch-4);
  durable `<h1>`-clearance allowlist sweep test (Arch-5); pdf_report
  naming sweep (Arch-6/Q-5); repo-wide old-hex guard (Arch-7); favicon
  scheme-divergence comment (Arch-8); T1 test rpartition + regex
  assertions (Q-1 BLOCKER, Q-6); ARGB `FF0F4C81` workbook pin called out
  (Q-2); route-gating citations corrected (Q-3); `text-gray-*` cleanup
  folded into §6 (Q-4); PDF mark-size eyeball note (Q-7); `logo_accent`
  drift pin (Q-8).
- +ADDED at plan-gate round 2 (convergence pass): opacity-modified class
  carve-outs — `bg-primary/5` → color-mix arbitrary value,
  `border-error/30`×3 → `.border-status-critical-faint` (Q-10); §5
  clearance expanded from 3 to 19 FIX templates after full-population
  re-triage — the burger renders on every page, and several
  result/expired/list/setup headers are first-content (Arch-9); T1
  routing-test brace anchor (Q-11); allowlist-seed grep corrected (Q-12);
  favicon scheme sentence into the plan's verbatim block (Arch-10);
  app.css "brand navy" comment rewording (Arch-11).
