# Loss-magnitude forms model (Epic D)

**Status:** canonical reference for Epic D (#497). Defines how a library entry's
Loss Magnitude is differentiated per archetype by the FAIR *forms of loss*, how
the active forms are composed into the two stored lognormals, and the rules that
gate a lognormal entry. Consumed by the D-i `loss_form_profile` data model, the
D-ii research sweep, and the D-iii recalibration.

> **Primary-cite note.** The six-forms taxonomy and the Primary/Secondary
> stakeholder classification below trace to **Freund, J. & Jones, J. (2015),
> *Measuring and Managing Information Risk: A FAIR Approach*, Butterworth-Heinemann,
> Ch. 3 "The FAIR Risk Ontology" — "The Six Forms of Loss"** and are
> cross-confirmed by **The Open Group, *Risk Taxonomy (O-RT), Version 3.0.1*
> (C20B), §"Loss Magnitude / Forms of Loss."** The exact page/section is pinned
> during D-i authoring against the source copy and verified by the methodology
> reviewer (per the primary-cited gate); this doc's derivations must be
> re-derivable from those sources.

## 1. The six FAIR forms of loss

FAIR decomposes Loss Magnitude into six mutually-exclusive **forms of loss**.
Each is a distinct cost channel a security leader reasons about:

| Form | What it is |
|---|---|
| **productivity** | Loss of the organization's ability to generate value — business interruption / downtime. |
| **response** | Costs of managing the event — incident response, containment, forensics, notification, credit monitoring, legal defense. |
| **replacement** | Costs to replace or reconstitute a lost/damaged asset — equipment, data, facilities. |
| **fines & judgments** | Legal and regulatory penalties, judgments, sanctions levied as a result of the event. |
| **competitive advantage** | Loss of market position from erosion of a competitive differentiator — IP / trade-secret compromise. |
| **reputation** | Loss of future revenue from stakeholders' diminished perception — churn, brand damage. |

This is the **differentiation axis** for Epic D: two archetypes in the same
sector (e.g. `ot-network-scanning-reconnaissance` vs `ransomware-on-historian`)
fire *different forms at different scales*, so anchoring Loss Magnitude by
sector alone (Epic C) flattens genuine loss-effect differences. Because every
form maps to a FAIR node, this stays FAIR-grounded — no portfolio-finance or
adjacent-domain overclaim.

## 2. Primary vs. Secondary is the stakeholder test — NOT a fixed partition

FAIR's Primary/Secondary distinction is about **which stakeholder bears the loss
and how it arises**, not a fixed assignment of the six forms to two buckets:

- **Primary Loss** — a loss borne by the **primary stakeholder** (the
  organization) occurring as a *direct* consequence of the event.
- **Secondary Loss** — a loss stemming from the reactions of **secondary
  stakeholders** (customers, regulators, shareholders, partners) to the event.

A single form can fall on **either side** depending on how it arises. Most
importantly, **response spans both**: internal containment/forensics is a
*primary* response cost, while customer notification, credit monitoring, and
legal defense against third parties are *secondary* response costs. Fines &
judgments and reputation are canonically secondary (they are secondary-stakeholder
reactions). Productivity and replacement are canonically primary. **Competitive
advantage is classified per the stakeholder test and cited per entry** — IP
erosion is often modeled as secondary (a market/competitor reaction) but can be
a direct primary loss; the placement is not assumed, it is justified.

Consequently the model does **not** hard-code a form → side partition. Each
active form in an entry's profile carries an explicit **`kind`** (`primary` /
`secondary`) set per the stakeholder test above. Typical defaults:

| Form | Typical `kind` |
|---|---|
| productivity | primary |
| replacement | primary |
| response | **split** — primary (internal) and/or secondary (notification/legal) |
| fines & judgments | secondary |
| competitive advantage | per stakeholder test, cited |
| reputation | secondary |

Any per-entry deviation from the typical default is justified in the profile's
`magnitude_basis` / citation.

## 3. The per-archetype loss-form profile

Each library entry carries a `loss_form_profile`: a list of active forms, one
entry per `(form, kind)` pair (set-like — no duplicates). Each element:

| field | meaning |
|---|---|
| `form` | one of the six machine keys (below) |
| `kind` | `primary` or `secondary` (stakeholder test, §2) |
| `magnitude_basis` | short description of the citeable basis for this form's magnitude |
| `citations` | the primary source(s) backing the magnitude |
| `verified` | the citation was adversarially re-fetched and confirmed (D-ii protocol) |
| `composition_role` | **descriptive only:** `dominant` (largest contributor) / `contributing` / `provenance_only` (immaterial, recorded but not composed). It does **NOT** switch the composition — see §4. |

**`form` machine keys** (the schema literals — display label → key): productivity
→ `productivity`, response → `response`, replacement → `replacement`, fines &
judgments → `fines`, competitive advantage → `competitive_advantage`, reputation
→ `reputation`. Use the key verbatim in seed data (e.g. `fines`, NOT
`fines_and_judgments`).

The profile is **provenance**: the engine never reads it. It records *how* the
stored `primary_loss` / `secondary_loss` lognormals were built, makes PL/SL →
form traceability a testable invariant, and drives the entry-detail "how this
loss was built" disclosure (D-iii).

## 4. Composition: active forms → one stored lognormal (always Fenton–Wilkinson)

The active **primary** forms are composed into the single stored `primary_loss`
lognormal, and the active **secondary** forms into `secondary_loss`. The sum of
lognormals is not itself lognormal, so composition uses **Fenton–Wilkinson
moment-matching** and nothing else — **no dominant-form shortcut** (dropped: it
discarded up to 10% of the composed mean at the threshold boundary, biasing
`ALE = LEF·(PL+SL)` downward and behaving inconsistently across the boundary).

Given active forms each a lognormal with **log-space** parameters `(μᵢ, σᵢ)`
(i.e. `μᵢ` is the mean of `ln X`, matching the stored
`{"distribution":"lognormal","mean":μ,"sigma":σ}` convention — `mean` is the
log-space μ, not the arithmetic mean). This composed lognormal is the
**pre-conversion envelope**: for `capped`-shape entries it is subsequently
collapsed into a bounded PERT on its (p5, p95) anchors — see
`docs/reference/loss-representation.md` for that conversion and which
entries keep the uncapped lognormal:

1. arithmetic moments: `mᵢ = exp(μᵢ + σᵢ²/2)`, `vᵢ = (exp(σᵢ²) − 1)·exp(2μᵢ + σᵢ²)`
2. sum: `M = Σ mᵢ`, `V = Σ vᵢ`
3. refit to a single lognormal: `σ_S² = ln(1 + V/M²)`, `μ_S = ln(M) − σ_S²/2`

FW is **mean-preserving** — `exp(μ_S + σ_S²/2) = M = Σ mᵢ` exactly — and reduces
to the identity for a single form.

### Worked anchor (numeric verification per project convention)

Two identical forms `(μ, σ) = (0, 1)`:

| quantity | value |
|---|---|
| `m = e^0.5` | 1.6487212707 |
| `v = (e − 1)·e` | 4.6707742705 |
| `M = 2m` | 3.2974425414 |
| `V = 2v` | 9.3415485409 |
| `M²` | 10.8731273138 |
| `1 + V/M²` | 1.8591409142 |
| `σ_S² = ln(1.8591409142)` | 0.6201145070 |
| `σ_S` | **0.7874734960** |
| `ln(M) = ln 2 + 0.5` | 1.1931471806 |
| `μ_S = ln(M) − σ_S²/2` | **0.8830899271** |

So `compose([(0,1),(0,1)]) → (μ_S = 0.883090, σ_S = 0.787473)`. (Note: NOT
`0.883115` — that value came from a mis-derived `ln(M)`.)

## 5. Independence assumption and its documented bias

FW as applied sums the arithmetic variances (`V = Σ vᵢ`), i.e. it assumes the
forms are **independent**. Real loss forms are **positively correlated** — a
severe incident drives productivity, response, and replacement up *together* —
so the true variance is `Var(Σ Xᵢ) = Σ Var + 2 Σ Cov > Σ Var`. The independence
composition therefore **understates σ and thins the upper tail (p95 / VaR)**,
which is **anti-conservative** for the product's headline output.

**Resolution (methodology, 2026-07-05):** independence is the honest baseline
(when one form dominates — the common case — the composed σ ≈ the dominant
form's σ, so the understatement is negligible there), and this assumption plus
its anti-conservative direction is stated explicitly here. Where an archetype
has **two-or-more comparable, plausibly-correlated forms**, the D-iii author MAY
widen the variance sum with a **documented positive-correlation term**:

`V = Σ Varᵢ + 2·ρ·Σ_{i<j} √(Varᵢ·Varⱼ)`

where the `Varᵢ` are the **arithmetic** form variances and `ρ` is the
**arithmetic (Pearson) correlation of the loss values** — NOT the log-space
correlation of the underlying normals, and NOT plugged in as a log-space
quantity. `ρ` is stated and justified per entry.

**No σ-floor is applied.** Flooring `σ_S` at the dominant form's σ is *rejected*:
an independent sum of comparable forms genuinely has a *lower* coefficient of
variation than each component (summing reduces relative spread), so a floor
would wrongly widen a legitimate independent sum — it breaks the two-equal-form
anchor above (it would force `σ_S = 1.0` where 0.787473 is correct).

## 6. Gating rules

- **composed-envelope-only, then shape-dispatched.** After recalibration every
  entry's `primary_loss` and `secondary_loss` are derived from the
  Fenton–Wilkinson composed lognormal envelope described above — there is no
  path back to an uncomposed, per-form PERT. What's stored downstream of that
  envelope depends on the entry's `loss_shape` (`docs/reference/loss-representation.md`):
  91 of the current 102 entries are `capped`, so the composed lognormal is
  collapsed to a bounded PERT on its (p5, p95) anchors; the remaining 11 are
  `catastrophic` and keep the untruncated lognormal. An entry ships only if
  **every active form** in its profile has a primary-cited, adversarially-verified
  `magnitude_basis` (D-ii protocol). A form with no citeable basis is either
  dropped (subject to the materiality bar) or the whole archetype is deferred —
  never back-filled with an uncited guess.
- **materiality bar for dropping a form.** A form may be dropped as immaterial
  only if its expected magnitude is **< 10% of the composed side's mean**. A form
  at or above that bar without a citeable basis **blocks the archetype** — it may
  not be silently gutted (this prevents a materially-large secondary form, e.g.
  fines, being dropped to force a ship).
- **No cross-sector tail borrowing** (carried from Epic C §1). A form's magnitude
  basis derives from *that* entry's own sector/form-appropriate cited source,
  never imported from an unrelated sector. The only permitted family refinement is
  Epic C's within-family sub-sector multiplier — a multiplier over the row's own
  NAICS-parent baseline, itself cited (≥2 citations, one marked as supporting the
  multiplier) and bounded (≤10 without explicit methodology sign-off).

## 7. Engine boundary

Loss forms are an **authoring-time** derivation. The engine is **unchanged**: it
consumes exactly two stored distributions per entry (`primary_loss`,
`secondary_loss` — PERT for `capped`-shape entries, lognormal for
`catastrophic`-shape entries; see `loss-representation.md`), samples them
independently, and sums them at sample time
(`fair_cam/risk_engine/fair_core.py:511`, `loss_magnitude = samples["primary_loss"]
+ samples["secondary_loss"]`). There is **no runtime six-form decomposition** —
the profile is provenance, read by nothing in `risk_engine/` or the
ORM→fair_cam bridge (`services/run_executor.py`). This keeps Epic D entirely
inside the "engine is the source of truth for FAIR math; v3 owns curation"
boundary.
