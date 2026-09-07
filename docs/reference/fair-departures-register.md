---
title: "Register of departures from FAIR and FAIR-CAM"
status: living — every entry must stay in sync with the code it cites
last_reviewed: 2026-09-07
governs: Jones & Freund, *Measuring and Managing Information Risk* (2015), ch. 3 p. 42 — "be extremely careful and prepared to defend your decision" on any change to the model's branches, relationships, or weights
---

# Register of departures from FAIR and FAIR-CAM

## Why this register exists

Jones & Freund close their description of the FAIR model with a warning
(ch. 3, p. 42): teams that added a branch, deleted a branch, changed a
relationship, or added weighted values usually broke the model, so anyone
who changes it must be "extremely careful and prepared to defend" the change,
and improvements should be "tested and evaluated in an open forum."

Idraa does change the model in places. Each change is labelled at the point
in the code where it happens, but until this register the labels were
scattered across docstrings, topology tables, and a dozen reference docs, so
nobody could answer "where does Idraa differ from the book and the Standard?"
in one place. This document is that place. The repository is public, which
covers the "open forum" half of the requirement; this register covers the
"prepared to defend" half.

**Scope rule.** An entry belongs here when Idraa's engine or authoring model
does something the FAIR Standard (Open FAIR risk taxonomy / analysis
standards), the FAIR-CAM Standard V1.0, or the Jones & Freund text either
prescribes differently, leaves unspecified, or does not contain at all.
Things the Standard prescribes and Idraa follows are not listed (the code
labels those `PRESCRIBED` with a section citation). Removed contamination is
recorded in `fair-cam-methodology.md` ("What is NOT in FAIR / FAIR-CAM") and
is not repeated here.

## Provenance classes

Every entry carries exactly one class. The classes are ordered from most to
least consequential.

| Class | Meaning |
|---|---|
| **DEPARTURE** | A branch, relationship, or operator differs from what the Standard or the book prescribes. The page-42 case. Needs a rationale, a bound on the error, and a test. |
| **ADDED RELATIONSHIP** | A relationship the Standard describes only qualitatively (or not at all) is given an explicit functional form. The book's "changed the relationships" case. |
| **CALIBRATION** | A numeric value the Standard does not supply. The book's "added weighted values" case. Must say whether the value is cited, calibrated, or a convention, and whether it is identifiable. |
| **IMPLEMENTATION-DEFINED** | The Standard gives a semantic but no formula; Idraa chose one. Must be property-tested against the semantic. |
| **ESTIMATION LEVEL** | Which node of the tree the analyst authors. The book explicitly allows estimating at any level; listed for transparency, not as a deviation. |
| **VIEW-MODEL DERIVATION** | Not FAIR at all. Lives outside the FAIR math and is labelled as such on every surface (CLAUDE.md "No portfolio-finance overclaim"). |

Each entry has: what the Standard or book says; what Idraa does; the class;
the rationale; where it lives; how to evaluate it.

---

## A. The FAIR risk tree

### A1. Secondary Loss is applied on every loss event (no Secondary Loss Event Frequency branch)

- **Standard / book.** Secondary risk is `SLEF × SLM`, where the Secondary
  Loss Event Frequency is `LEF × P(secondary loss | primary loss event)`.
  The book treats that conditional probability as its own estimate.
- **Idraa.** Loss Magnitude per event is `Primary Loss + Secondary Loss`,
  sampled independently and summed on every simulated loss event
  (`fair_cam/risk_engine/fair_core.py`, `loss_magnitude = primary + secondary`).
  There is no secondary-loss probability field anywhere in the scenario
  model, the wizard, or the engine.
- **Class.** DEPARTURE (deleted branch).
- **Rationale, as it stands.** The wizard tells the analyst to fold the
  conditional probability into the Secondary Loss range and to author zero
  when secondary consequences are negligible. That keeps the mean roughly
  right when the analyst does it, but it replaces a two-point mixture
  (nothing, or a secondary loss) with a smoothed range, which understates
  dispersion, and it silently assumes `P(secondary) = 1` whenever the analyst
  does not discount. This entry was **unlabelled until this register was
  written**; it is the one item here whose defence is incomplete.
- **Where.** `fair_cam/risk_engine/fair_core.py`; scenario form and
  `templates/help/articles/build-a-scenario.html` ("Secondary Loss").
- **Evaluate.** Compare a scenario authored with a discounted SL range
  against the same scenario run with an explicit `P(secondary)` and a
  Bernoulli gate on SL. Follow-up: tracked as a GitHub issue (an optional
  secondary-loss probability defaulting to 1.0 would restore the branch
  without changing any existing run).

### A2. Vulnerability is authored directly; Threat Capability vs Resistance Strength is not elicited

- **Standard / book.** `Vulnerability = P(Threat Capability > Resistance
  Strength)`; the book allows the analyst to estimate at the Vulnerability
  level directly and skip the sub-branch.
- **Idraa.** The wizard authors the **inherent, control-naive** Vulnerability
  as a bounded probability; the `threat_capability` / `resistance_strength`
  fields exist on the engine's parameter dataclass but are never populated by
  the app. Controls then reduce Vulnerability in the simulation.
- **Class.** ESTIMATION LEVEL. Not a deviation; the book's own option.
- **Rationale.** Control credit must not be counted twice (once in the
  authored figure, once in the FAIR-CAM layer). The inherent framing is the
  only one compatible with the sample-level multiplier mechanism.
- **Where.** `fair-cam-methodology.md` ("Vulnerability anchor: control-naive
  inherent"), `vulnerability-semantics.md`.
- **Evaluate.** Guarded by the library audit heuristic in
  `vulnerability-semantics.md` and the legacy-residual banner (#343).

### A3. Per-iteration product form `risk = LEF × LM`

- **Standard / book.** This IS the canonical form (and pyfair's).
- **Idraa.** Same. Listed only because its tail statistics are an
  approximation once LEF exceeds 1, which is a **disclosure, not a
  departure**. See `product-form-tail-approximation.md`.

### A4. Aggregate runs sum independent scenarios

- **Standard / book.** FAIR defines per-scenario risk; it does not prescribe
  a portfolio aggregation rule.
- **Idraa.** An aggregate run sums the per-scenario annual-loss samples with
  no correlation structure between scenarios.
- **Class.** IMPLEMENTATION-DEFINED (independence assumption).
- **Rationale.** Independence is the only assumption that adds nothing the
  analyst did not author. It understates the tail of the sum when scenarios
  share a driver (one ransomware campaign hitting several assets), which the
  help article states plainly. No portfolio-finance correction is applied,
  by policy.
- **Where.** `services/run_executor.py` aggregate path;
  `templates/help/articles/run-and-read-analyses.html`.
- **Evaluate.** Raw-sample export lets a reader re-sum with any correlation
  they prefer.

---

## B. Distribution and calibration choices

### B1. Lognormal elicitation collapsed to a capped PERT for sampling

- **Standard / book.** Open FAIR and pyfair sample TEF and loss magnitude
  from a BetaPERT on (min, most likely, max). The book elicits calibrated
  90% ranges and does not mandate a family.
- **Idraa.** The analyst's low/high are read as the 5th/95th percentiles of
  a lognormal; the fit is then converted to a **bounded PERT** whose `high`
  is the 95th percentile of that lognormal, so the analyst's high estimate is
  a hard ceiling of the sampled values. For the library the analytic
  lognormal mode falls below `low` for every capped entry, so the PERT is
  `Beta(2/3, 10/3)` with density rising toward `low`.
- **Class.** CALIBRATION (convention).
- **Rationale.** A runtime clip of a lognormal dumps its tail mass into a
  spike at the cap; a bounded family avoids the artefact. The cost is a
  documented drop in expected loss relative to the uncapped lognormal
  (1.6× to 8.2× by sector), accepted on the argument that the removed tail
  was economically impossible for a single organisation.
- **Where.** `loss-representation.md`, `tef-representation.md`,
  `fair_cam/quantile_pooling/`.
- **Evaluate.** `tests/integration/test_library_loss_differentiation.py`
  pins that every capped entry reconstructs from its cited envelope.

### B2. Catastrophic losses keep the lognormal, bounded at organisational capacity

- **Standard / book.** No capacity concept.
- **Idraa.** Entries curated as catastrophic sample a right-truncated
  lognormal on `[0, k × annual revenue)` with `k = 1.0` by default
  (`Settings.capacity_k`); truncation is by inverse CDF, not clipping. When
  revenue is unknown there is no cap and no quantile fallback.
- **Class.** CALIBRATION (`k` is a convention) plus ADDED RELATIONSHIP (the
  revenue bound).
- **Rationale.** A single loss component larger than a year of revenue is
  not a modelled outcome for the owning organisation; a quantile-based cap
  would remove the same tail slice from every scenario regardless of size.
- **Where.** `services/loss_capacity.py`, `fair_cam/risk_engine/_truncation.py`.
- **Evaluate.** The truncation formula is verified three ways in the module
  docstring; the `max > p95` floor is enforced by the validator, not the minter.

### B3. Within-scenario loss dispersion default σ = 1.7

- **Standard / book.** Not supplied.
- **Idraa.** When no analyst pins a dispersion, loss magnitude carries
  σ = 1.7 in log space: a round number above the IRIS event-type-conditioned
  reads and below every size-conditioned read.
- **Class.** CALIBRATION (convention, not a measurement). IRIS never
  publishes the single-firm × single-scenario joint distribution.
- **Where.** `within-scenario-sigma-calibration.md`.
- **Evaluate.** The derivation table in that doc; the σ-recalibration
  tripwire and pin/readout surfaces (#126).

### B4. Multiple expert estimates pooled as a mixture

- **Standard / book.** The book discusses calibrated estimation and
  reconciling experts but prescribes no pooling arithmetic.
- **Idraa.** Each expert's fitted distribution is kept as a component of a
  mixture (a port of the MIT-licensed evaluator/collector R package), rather
  than averaging the ranges before fitting.
- **Class.** IMPLEMENTATION-DEFINED.
- **Rationale.** Averaging ranges first destroys between-expert
  disagreement; a mixture preserves it.
- **Where.** `fair_cam/quantile_pooling/`.
- **Evaluate.** Fixture parity against the pinned upstream commit in
  `fair_cam/tests/quantile_pooling/`.

---

## C. The FAIR-CAM control layer

FAIR-CAM V1.0 gives the three control domains, their sub-functions, the
units each sub-function is measured in, the Boolean topology within most
groups, and qualitative statements about how the domains affect risk. It
gives **no numeric weights and, for several groups, no operator formula**.
Everything numeric in this section is therefore Idraa's.

### C1. Operational effectiveness = capability × coverage × reliability

- **Standard.** §2.4 names the three factors; no combining rule.
- **Idraa.** Multiplicative, per assignment.
- **Class.** IMPLEMENTATION-DEFINED.
- **Where.** `fair_cam/composition.py`.

### C2. Elapsed-time sub-functions normalised by `exp(−t/τ)`

- **Standard.** §2.3 leaves time-unit normalisation to implementations; the
  alignment audit lists exponential decay as one option with no
  recommendation.
- **Idraa.** `opeff = exp(−elapsed_time / τ_sf)` with a per-sub-function τ
  table, each τ traced to a primary citation under a cite-or-drop rule.
- **Class.** IMPLEMENTATION-DEFINED (the form) + CALIBRATION (the τ table).
- **Where.** `elapsed-time-tau-calibration.md`,
  `fair_cam/calibration/elapsed_time_taus.py`.
- **Evaluate.** The #131 precedent (means plugged into a median-half-life
  formula) is exactly the failure this table's methodology section guards.

### C3. Missing capability defaults to 0.5

- **Standard.** Silent.
- **Idraa.** A `NULL` capability value contributes `0.5 × coverage` (times
  reliability). For elapsed-time sub-functions this is the algebraic
  half-life point `exp(−ln 2)`.
- **Class.** CALIBRATION (safe default).
- **Where.** `fair_cam/composition.py`.

### C4. FAIR-axis routing weights (the page-42 "added weighted values")

- **Standard.** Says Prevention reduces loss-event likelihood (§3.1) and
  Response limits loss magnitude (§3.3); gives no magnitudes.
- **Idraa.** Group effectiveness `E` reaches a FAIR node as the multiplier
  `1 − E·w`, with canonical weights:

  | parameter | value | node |
  |---|---|---|
  | `prevention.tef` | 0.8 | Threat Event Frequency |
  | `prevention.vuln` | 0.9 | Vulnerability |
  | `magnitude.secondary` | 0.5 | Secondary Loss |
  | `magnitude.primary` | 0.2 | Primary Loss |

  Every entry in the topology table is labelled `implementation-calibration`.
- **Class.** CALIBRATION, **not identifiable** at single-organisation scale
  (see `control-weight-identifiability.md`).
- **Rationale and defence.** The values are SME-anchored, not validated, and
  cannot be validated from one organisation's loss history. Rather than
  present them as facts, the weight-robustness ensemble perturbs all four
  (plus κ, C7) and reports control value as a **range** and control ranking
  with a **stability verdict**. Per-organisation calibration of these weights
  is refused by policy because it would relocate the guess, not remove it.
- **Where.** `fair_cam/models/composition_topology.py` (`GROUP_NODE_MAPPING`);
  `services/weight_robustness.py`; `control-weight-robustness.md`.
- **Evaluate.** `fair_cam/tests/test_composition_topology.py` pins the
  values; the ensemble's σ-sensitivity fixture shows how verdicts move.

### C5. Weak-AND operator = equal-weighted arithmetic mean

- **Standard.** §3.3.1–3.3.3 gives the *semantic*: a deficient Response
  sub-function diminishes but does not eliminate the group. No formula.
- **Idraa.** `mean(x_i)`, property-proven (bounded, non-inhibiting,
  monotone, idempotent, distinct from weak-OR).
- **Class.** IMPLEMENTATION-DEFINED. The code's provenance constant says
  "NOT Standard-grounded" about the formula.
- **Where.** `fair_cam/composition.py` (`WEAK_AND_OPERATOR_PROVENANCE`).

### C6. DSC Prevention composed by best-coherent-subset mean, not Boolean AND

- **Standard.** §5.1.x prescribes AND across the nine Decision Support
  prevention sub-functions.
- **Idraa.** First relaxed to weak-AND (a strict AND makes any organisation
  without all nine functions score zero decision support), then to the
  **best-coherent-subset mean** (max over k of the top-k mean) because the
  plain mean is non-monotone in coalition membership and produced negative
  Shapley values on a production run (#453).
- **Class.** DEPARTURE (changed relationship), labelled at the site.
- **Rationale.** Decision-support quality is read as the strongest coherent
  subset of present functions; a weaker extra function neither helps nor
  dilutes. Known cost: overstates `E_dsc` for sparse authoring, bounded by
  `κ · (1 − r0)` (C7).
- **Where.** `fair_cam/risk_engine/group_composition.py` (`precompose_parts`).
- **Evaluate.** Monotonicity is the property the change exists to restore;
  the negative-Shapley regression from #453 is the test case.

### C7. Meta-controls act through a reliability coupling, `r_eff = r0 + (1 − r0)·κ·E_meta`, κ = 0.5

- **Standard.** §2.2, §2.3, §4: VMC and DSC affect risk *indirectly*, by
  changing the reliability of other controls; no functional form.
- **Idraa.** VMC/DSC groups carry **no direct FAIR-node targets** (retired in
  #439 on §2.2 "indirectly affect risk" grounds). Their composed strength
  `E_meta` recovers a fraction κ of every co-present Loss Event Control's
  reliability headroom. `E_vmc`, `E_dsc`, and `E_meta = OR(E_vmc, E_dsc)`
  are cross-family fusions with no Standard-prescribed operator. The DSC
  contribution is a **proxy**: the Standard frames DSC as improving
  decisions, not reliability. Meta-on-meta uplift is not modelled.
- **Class.** ADDED RELATIONSHIP (the form) + CALIBRATION (κ, midpoint by
  symmetry, non-identifiable, perturbed by the ensemble as `meta.kappa`).
- **Where.** `fair_cam/models/composition_topology.py`
  (`KAPPA_META_RELIABILITY`), `group_composition.py` (`finalize_composition`).
- **Evaluate.** `test_kappa_meta_reliability_pin`; the max-aggregation fix
  (#455) and the open attribution investigation (#434) are the live
  evaluation record.

### C8. VMC Correction is gated on Implementation; VMC Identification pair uses OR

- **Standard.** §4.3 prescribes AND(Treatment Selection, Implementation);
  §4.2 names Threat Intelligence and Controls Monitoring but prescribes no
  operator between them.
- **Idraa.** Absent Implementation → no correction regardless of selection;
  present → AND over present members (replaces zero-padding of an absent
  selection). Identification pair → OR, since the two members cover
  different variance sources.
- **Class.** DEPARTURE (the gate, labelled "Spec D3 deviation") and
  IMPLEMENTATION-DEFINED (the OR).
- **Where.** `group_composition.py`, `composition.py` docstrings.

### C9. Detection has no standalone node; Response acts on magnitude only

- **Standard.** §3.2: Detection enables Response; §3.3: Response limits loss.
- **Idraa.** Detection alone applies no multiplier (it gates the
  Detection∧Response pair); the pair's effect is routed to Primary and
  Secondary Loss only, not to frequency (#130 D4 re-route, fixing a
  double-count).
- **Class.** IMPLEMENTATION-DEFINED, grounded in §3.2–3.3.
- **Where.** `composition_topology.py` (`LEC_DETECTION`, `LEC_RESPONSE`).

### C10. Currency-valued Loss Reduction is a per-event subtractor on Secondary Loss

- **Standard.** Loss Reduction is measured in currency (§2.4); no
  application rule.
- **Idraa.** Subtracted from each secondary-loss sample before flooring at
  zero; Primary Loss untouched.
- **Class.** IMPLEMENTATION-DEFINED (#130 D3).
- **Where.** `fair_core.py` (`secondary_loss_subtractor`).

---

## D. Outside FAIR (view-model derivations)

None of these is a FAIR node. Each is labelled "not FAIR-grounded" on every
surface that shows it and is computed outside the FAIR math path.

| Item | What it is | Unvalidated conventions inside it | Where |
|---|---|---|---|
| **D1. Shapley attribution** | Cooperative-game split of total modelled risk reduction across controls (Shapley 1953; Castro et al. 2009 sampling). | Permutation-sampling budget; the value function is the engine's closed-form composition, evaluated on representative values. | `services/shapley.py` (no FAIR math), `fair_cam/risk_engine/control_attribution.py` (the `v(S)` evaluator) |
| **D2. If-removed (leave-one-out) value** | Drop in modelled reduction when one control is removed. Never totalled. | none | same |
| **D3. Weight-robustness ensemble** | Logit-normal perturbation of C4 + C7 parameters; rank-stability verdicts. | σ = 0.6, K = 256, ±1-rank stability, 10% flip threshold, 0.90 stable fraction. All conventions; widening σ only makes verdicts more pessimistic. | `services/weight_robustness.py`, `control-weight-robustness.md` |
| **D4. ALE as the mean** | Mean of the annual-loss distribution. The book's headline quantity, but not a named Open FAIR node. | none | `services/run_view_model.py` |
| **D5. VaR, expected shortfall, loss-exceedance curve** | Standard tail statistics read off the sampled distribution. | Subject to the A3 tail approximation. | same |

---

## E. Probability interpretation (conformance, not departure)

Listed so a reviewer can check the framing has not drifted.

- Probabilities are **subjective** (degrees of belief), elicited as
  calibrated 90% ranges; Idraa performs **forward Monte Carlo propagation
  only**. There is no prior-times-likelihood step, no posterior, and the
  engine never updates an estimate. New evidence is incorporated by
  re-eliciting. This matches Jones & Freund p. 42: early FAIR used Bayesian
  formulas; Monte Carlo "worked just as well and was easier."
- The model's structure owes its decomposition-into-factors shape to
  Bayesian-network thinking (p. 42), and Idraa keeps that structure intact.
- **Frequencies and probabilities stay distinct**: TEF and LEF are rates
  (may exceed 1); Vulnerability is a probability in [0, 1]. Neither is
  converted into the other.
- "Factor analysis" in the name is descriptive, not the statistical
  technique (p. 42). No surface in Idraa claims otherwise.

---

## Maintenance rule

A change that adds a branch, removes a branch, changes a relationship or
operator, or introduces a numeric weight or default **must add or update an
entry here in the same pull request**, with its provenance class, and the
methodology reviewer checks for it (see the `methodology-reviewer` skill's
"Model-structure change" item). An entry whose defence is incomplete says so
in its own text, as A1 does, rather than being omitted.
