# Within-scenario loss-dispersion calibration (σ_default = 1.7)

The derivation, bounds argument, and anchor-review record for
`idraa.services.calibration.WITHIN_SCENARIO_SIGMA_DEFAULT`. Every number here
was executed against the cited in-repo constants, not hand-derived; the
figures appendix referenced below is `docs/superpowers/specs/
sigma-recalibration-figures.generated.txt` (operator-local, frozen at the
pre-recalibration baseline), produced by `scripts/sigma_recal_figures.py`.

## 1. What this constant is

The log-space dispersion a single scenario's loss magnitude carries when no
analyst has pinned one. It is a **v3 calibration convention** — a conservative
round number above the type-conditioned IRIS reads and below every
size-conditioned read — **not a measurement**: IRIS never publishes the
single-firm × single-scenario joint distribution, so no published statistic
exists at the conditioning level this parameter describes.

## 2. Why the superseded values were wrong

Library loss σ was inherited from the per-sector IRIS 2025 Figure A3 envelope
(`data/loss_form_envelopes.json`, σ = ln(p95/p50)/z₀.₉₅ ∈ 1.8377–3.4722).
That envelope is **cross-firm × cross-incident-type population dispersion**
— the spread across many differently-sized firms and many incident classes —
applied as within-scenario single-event uncertainty. The conditioning ladder
(all from `fair_cam/data/iris_2025.py`):

| conditioning | σ |
|---|---|
| sector envelope (Fig A3, p.35) — cross-firm × cross-type | 1.8377–3.4722 |
| within revenue tier (Table 1) — cross-firm, cross-type | 1.9687–2.9156 |
| within event type (Fig 15, 2024, p50/p90) — cross-firm | 1.3570 (system intrusion) / 1.6813 (ransomware) |

Conditioning on either single dimension collapses the spread materially; the
within-scenario quantity is bounded above by the tightest cut.

## 3. Derivation

σ = ln(p90/p50) / z₀.₉₀, z₀.₉₀ = 1.2815515655446004, over
`LOSS_BY_EVENT_TYPE_TREND` (IRIS 2025 Figure 15, 2024 reads):

- system_intrusion: ln(7,400,000 / 1,300,000) / z₀.₉₀ = **1.3570**
- ransomware: ln(27,600,000 / 3,200,000) / z₀.₉₀ = **1.6813**

Type-conditioning still pools across firms, so these reads **upper-bound**
within-scenario dispersion. Bound argument: **1.6813 ≤ 1.7 < 1.9687** (the
tightest size-conditioned read, `LOSS_BY_REVENUE_TIER_2024`, derived not
hardcoded in the guard test). 1.7 is the top of the admissible band, i.e. the
most conservative (widest) value consistent with the evidence.

## 4. Anchor-set exclusion (deliberate, test-asserted)

The same table's third 2024 read, `accidental_disclosure_insider_misuse` =
**4.2497**, is excluded from the anchor set: the constant's own source comment
says "Figure 15, combined panel" — it is a mixed accidental-disclosure /
insider-misuse bucket spanning trivial exposures to mass breaches, i.e. itself
a cross-*type* aggregate, not a single-scenario read (2024 p50 $6,900 against
p90 $1.6M). The exclusion is asserted in
`test_accidental_disclosure_exclusion_is_deliberate` so it can never read as
an oversight.

## 5. Estimator caveat

The type-conditioned reads are p50/**p90** (z₀.₉₀); the tier and envelope
reads are p50/**p95** (z₀.₉₅). On matched sectors the estimator difference
reaches **0.53** — larger than the entire conditioning effect the bound
argument spans (1.9687 − 1.6813 = 0.287). The p90-based reads run high; a
p95-equivalent within-type read is ≈1.5. This is why §1 says *convention*,
not *measured collapse*: the value survives the estimator mismatch, the
precision claim would not.

## 6. Volatility caveat

Figure 15 is a single-year read of a volatile trend: the 2008 system_intrusion
read gives σ ≈ 4.56. These are anchor lines plus bounds, not precision.

## 7. What σ = 1.7 still implies

p95/p50 = **16.383×**, p5–p95 span = **268×**, mean/median = **4.2419×** —
wider than a typical Hubbard-calibrated 90% credible interval, i.e. the
choice is conservative. Distribution assumption (loss severity lognormal,
heavy right tail): Hubbard & Seiersen, *How to Measure Anything in
Cybersecurity Risk*, 2nd ed. 2023, ch. 6; Jones & Freund, *Measuring and
Managing Information Risk: A FAIR Approach* — the same anchors carried by
`fair_cam/quantile_pooling/_lognormal_native.py`.

## 8. Anchor-held rule and the mode-clamp precondition

The re-derivation holds **the statistic that was authored, detected by the
exact existing label, never assumed**:

- **18 catastrophic lognormal fields** — median authored (envelope×share):
  μ unchanged, σ → 1.7.
- **149 capped non-vendor fields** — median authored: μ = ln(√(low·high))
  held; `low, high = exp(μ ∓ z₀.₉₅·1.7)`, mode = low. The *realized*
  BetaPERT median (0.11182 quantile of range) also moves and is disclosed
  separately — never claim an unqualified "medians are held".
- **5 vendor fields** (`loss_tier == "vendor"`; their `magnitude_basis`
  documents mean-preserving authoring verbatim) — **mean** authored against
  IC3 2025 BEC per-complaint mean $123,005: μ = ln(123,005) − 1.7²/2.
  Post-recalibration all five share median $29,014 (risers ×1.05–×5.46);
  E[loss] = $123,005 exactly (appendix external check, rel. err 7.1e-16).

**Precondition (fail-loud in the builder):** σ_default > z₀.₉₅ = 1.6449.
1.7 clears by 3.35%. Below the threshold the capped-PERT collapse gains an
interior mode — a shape-regime change that is a new design, not a constant
edit. Note §5's p95-equivalent read ≈1.5 sits *below* the threshold.

## 9. Validation posture (D9'')

**No external numeric gate exists at the within-scenario conditioning level**
— a cross-firm anchor as a pass condition demands the restoration of the
contaminated σ (the withdrawn gate needed σ = 2.394, inside the contaminated
band). What stands instead:

- **σ-sensitivity** (appendix `[sigma sensitivity]`): library expected loss
  is −94.17% at σ=1.357, −92.80% at 1.5, −90.16% at the chosen 1.7, −84.68%
  at 1.9687, −67.62% at 2.394 — **large across the entire defensible
  bracket**, so the drop is not an artifact of choosing 1.7.
- **IC3 mean-preservation** (appendix `[external check]`): the one external,
  citation-traced pass/fail check available — passes at 7.1e-16.
- The owner-signed σ decision itself, methodology-gated.

## 10. Capped-PERT provenance (D4' record)

All **154** capped PERT loss ranges are mechanically
`exp(μ ∓ z₀.₉₅·σ_envelope)` of the superseded fits — implied σ exactly an
envelope value on 154/154, mode == low on 154/154. They were never authored
bounds; the "authored worst-plausible bound" description in the rev-1 design
was false and is corrected here for the permanent record.

---

## Anchor-review decision table (plan Task 0 Step 4)

**Check unit.** The named admissible anchors (`LOSS_BY_EVENT_TYPE_TREND`
p50/p90, 2024) are **incident-total** statistics, so the check compares the
**event total** (PL median + SL median, both components at σ_default) against
the closest type-conditional p90 — judging an SL component alone against a
whole-event anchor would be a category mismatch. Closest class: `ransomware`
for the wiper (destructive-payload class), `system_intrusion` for all others.

| slug | event median (PL+SL) | p95@1.7 | anchor p90 | check | decision |
|---|---|---|---|---|---|
| unauthorized-plc-modification | $880,000 | $14.42M | $7.4M | PASS | **KEEP** |
| safety-system-bypass | $940,000 | $15.40M | $7.4M | PASS | **KEEP** |
| chemical-process-safety-attack | $980,000 | $16.06M | $7.4M | PASS | **KEEP** |
| field-instrument-spoofing | $600,000 | $9.83M | $7.4M | PASS | **KEEP** |
| solarwinds-class-supply-chain | $646,200 | $10.59M | $7.4M | PASS | **KEEP** |
| denial-of-control | $116,800 | $1.91M | $7.4M | FAIL | **KEEP + justification (a)** |
| grid-protective-relay-manipulation | $134,320 | $2.20M | $7.4M | FAIL | **KEEP + justification (a)** |
| pipeline-scada-integrity | $89,060 | $1.46M | $7.4M | FAIL | **KEEP + justification (a)** |
| nation-state-ics-supply-chain | $121,180 | $1.99M | $7.4M | FAIL | **KEEP + justification (a, b)** |
| telecom-lawful-intercept-nationstate-compromise | $409,260 | $6.71M | $7.4M | FAIL (−9.4%) | **KEEP + justification (c)** |
| destructive-wiper-nationstate | $465,500 | $7.63M | $27.6M | FAIL (×3.6) | **ADJUST (d)** |
| bec-fraud-financial (vendor) | mean $123,005 | — | IC3 | — | **MEAN-HOLD** |
| manufacturing-billing-fraud (vendor) | mean $123,005 | — | IC3 | — | **MEAN-HOLD** |
| professional-payroll-bec (vendor) | mean $123,005 | — | IC3 | — | **MEAN-HOLD** |
| telecom-sim-swap-fraud (vendor) | mean $123,005 | — | IC3 | — | **MEAN-HOLD** |
| agri-coop-bec-fraud (vendor) | mean $123,005 | — | IC3 | — | **MEAN-HOLD** |

**Justification (a) — energy-sector OT entries.** These medians derive from
the energy_utilities envelope p50 ($146K) × curated shares. The check anchor
($1.3M/$7.4M) is a **cross-sector** intrusion statistic dominated by IT
breaches at larger firms; the entries' failure against it reflects **sector
conditioning, not under-calibration** — the envelope IS the calibration
(#517), and Epic C's adversarially-verified research sweep established that
no citable OT-specific per-event loss anchor exists (Amendment A1: per-form
magnitudes unsourceable). Raising these medians toward the cross-sector
anchor would erase the sector conditioning without a citation. Within-family
ordering (chemical > safety-bypass > plc > field-instrument ≫
denial-of-control) is the deliberate Epic C/#518 severity ordering and stays
intact. The catastrophic character is carried by TEF rarity, the 16.4×
p95/p50 span, and (post-PR2) the capacity-relative tail.

**Justification (b).** nation-state-ics-supply-chain sits below
denial-of-control by curated share; a supply-chain implant discovered
pre-activation is a contained event — the tail, not the median, carries
activation. Consistent with the ordering rationale; no citable anchor
supports moving it.

**Justification (c).** telecom-lawful-intercept misses the cross-sector
anchor by 9.4%. The reference class (telecom-infrastructure espionage) has no
citable per-event loss anchor; a sub-10% miss against a cross-sector
statistic is inside any honest error bar of both numbers.

**Adjustment (d) — destructive-wiper-nationstate, the one entry that fails
its own premise.** The entry exists (attack-coverage W1) precisely because
"NotPetya's Maersk loss dwarfs the transportation_logistics sector p95" — yet
its median inherits that same sector envelope (p50 $490K × 0.85), so with the
fat tail removed the stored median contradicts the entry's founding
rationale. The **closest named admissible anchor** (D5) is the IRIS 2025
ransomware type-conditional p50_2024 = **$3,200,000** (Fig 15): a
no-recovery destructive wiper is at least as severe as the ransomware class
median (same destructive mechanism, no recovery path). Adjustment: set the
**event median** to the cited $3.2M, preserving the entry's internal PL:SL
share (416.5:49): PL median = 3,200,000 × 416.5/465.5 = **$2,863,158**;
SL median = 3,200,000 × 49/465.5 = **$336,842**. σ = 1.7 both fields.
Resulting event p95 = $52.4M ≥ the $27.6M anchor p90 — check passes.
`_ANCHOR_OVERRIDES` (builder): `("destructive-wiper-nationstate",
"primary_loss"): 2863158.0`, `("destructive-wiper-nationstate",
"secondary_loss"): 336842.0`. This changes the appendix B-LIB-MEAN
after-value; the builder cross-check reports both columns per the plan.

**SL components on passing entries** (plc SE, safety SE, chemical SE,
solarwinds SE, nation-ics SE, telecom SE, wiper SE): judged at event level
per the check-unit rule above; no per-component adjustment.
