---
title: "Register of departures from FAIR and FAIR-CAM"
status: living — every entry must stay in sync with the code it cites
last_reviewed: 2026-09-07
governs: Jones & Freund, *Measuring and Managing Information Risk*, 2nd ed. (Butterworth-Heinemann/Elsevier, 2026, ISBN 978-0-443-13484-5), ch. 3 p. 42 — "be extremely careful and prepared to defend your decision" on any change to the model's branches, relationships, or weights
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
makes evaluation in an open forum possible (publication is not itself
evaluation); this register covers the "prepared to defend" half.

**Scope rule.** An entry belongs here when Idraa's engine or authoring model
does something the FAIR Standard (Open FAIR risk taxonomy / analysis
standards), the FAIR-CAM Standard V1.0, or the Jones & Freund text either
prescribes differently, leaves unspecified, or does not contain at all.
Citations refer to the Second Edition (Jones & Freund, Butterworth-Heinemann/
Elsevier, 2026, ISBN 978-0-443-13484-5), whose cover lists Jones first; page
numbers are from that edition, not the 2015 first edition.
Things the Standard prescribes and Idraa follows are not listed (the code
labels those `PRESCRIBED` with a section citation). Removed contamination is
recorded in `fair-cam-methodology.md` ("What is NOT in FAIR / FAIR-CAM") and
is not repeated here.

## Provenance classes

Every entry carries one primary class, plus a secondary class where a
separable sub-part (a numeric value, a second operator, or an authoring
level) has its own provenance. The classes are ordered from
most to least consequential.

| Class | Meaning |
|---|---|
| **DEPARTURE** | A branch, relationship, or operator differs from what the Standard or the book prescribes. The page-42 case. Needs a rationale, a bound on the error, and a test. |
| **ADDED RELATIONSHIP** | A relationship the Standard describes only qualitatively, or not at all where Idraa introduces a non-neutral constraint such as the B2 revenue bound, is given an explicit functional form. The book's "changed the relationships" case. (Where the Standard is silent and Idraa adopts a neutral assumption such as independence, the entry is IMPLEMENTATION-DEFINED.) |
| **CALIBRATION** | A numeric value the Standard does not supply. The book's "added weighted values" case. Must say whether the value is cited, calibrated, or a convention, and whether it is identifiable. |
| **IMPLEMENTATION-DEFINED** | The Standard gives a semantic but no formula; Idraa chose one. Must be property-tested against the semantic. |
| **ESTIMATION LEVEL** | Which node of the tree the analyst authors. The book explicitly allows estimating at any level; listed for transparency, not as a deviation. |
| **VIEW-MODEL DERIVATION** | Not FAIR at all. Lives outside the FAIR math; the attribution table carries a modelled-estimates disclaimer on screen and its "not FAIR-grounded" note in the help, the others are documented in this register and the help (CLAUDE.md "No portfolio-finance overclaim"). |

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
- **Rationale, as it stands.** Until this register, nothing in the product
  told the analyst to discount for a conditional probability: the wizard
  only said to zero the range when secondary consequences are "negligible or
  unknown" (zeroing an unknown is the anti-conservative direction). The
  scenario-building help now says to scale the range by how often a loss
  event triggers secondary reactions and never to zero an unknown. Even with
  that guidance the model replaces a two-point mixture (nothing, or a
  secondary loss) with a smoothed range, which understates dispersion, and it
  assumes `P(secondary) = 1` whenever the analyst does not scale. This entry
  was **unlabelled until this register was written**; it is the item here
  whose defence is least complete. The #175 regulator-and-judgment reaction
  rule moved 0.03–0.13 of response budget onto Secondary Loss on 24 library
  entries (B5), so the error this departure introduces binds harder there:
  under a standard SLEF branch the reclassification would lower inherent ALE
  by a further 2.2%–14.9% at q = 0.5. Tracked with #173.
- **Where.** `fair_cam/risk_engine/fair_core.py`; scenario form and
  `templates/help/articles/build-a-scenario.html` ("Secondary Loss"). Since
  #174 the scaling instruction is also visible copy on the Secondary loss
  footer of `templates/scenarios/form.html` and on the wizard's SL fieldset
  (`templates/scenarios/wizard/_fair_params_form_inner.html`), pinned by
  `tests/services/test_wizard_questions.py`; the node-teaching article
  `templates/help/articles/fair-in-idraa-terms.html` (§4, Loss Magnitude)
  states the missing secondary frequency inline and links here.
- **Evaluate.** Compare a scenario authored with a discounted SL range
  against the same scenario run with an explicit `P(secondary)` and a
  Bernoulli gate on SL. Follow-up: **issue #173** (an optional
  secondary-loss probability defaulting to 1.0 would restore the branch
  without changing any existing run).

### A2. Vulnerability is authored directly; Threat Capability vs Resistance Strength is not elicited

- **Standard / book.** `Vulnerability = P(Threat Capability > Resistance
  Strength)` (older Open FAIR text says Control Strength; the help uses that
  name); the book allows the analyst to estimate at the Vulnerability level
  directly and skip the sub-branch.
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
  `vulnerability-semantics.md` and the legacy-residual banner (riskflow#343).

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
- **Where.** `fair_cam/risk_engine/native_control_aware.py` (the aggregate
  rollup sums independent scenario streams); `services/run_executor.py`;
  `templates/help/articles/run-and-read-analyses.html`.
- **Evaluate.** Raw-sample export lets a reader re-sum with any correlation
  they prefer.

### A5. Primary and Secondary Loss are sampled independently within one event

- **Standard / book.** FAIR sums the forms of loss per event; it does not
  say whether primary and secondary magnitudes co-vary.
- **Idraa.** Per iteration, Primary Loss and Secondary Loss are drawn
  independently and summed.
- **Class.** IMPLEMENTATION-DEFINED (independence assumption), and
  **anti-conservative**: for an archetype whose loss is split comparably
  across both sides, the sum's variance is roughly half what perfect
  co-variation would give (the ratio is `(a² + b²)/(a + b)²`, which
  approaches 1 as one side dominates; `loss-form-share-rubric.md` §1 records
  the direction), so the per-event tail is understated.
- **Rationale.** Same as A4: independence adds nothing the analyst did not
  author. The library's share rubric was tuned knowing this. The #175
  regulator-and-judgment reaction rule (B5) moved 17 of its 24 renumbered
  entries toward the Σp ≈ Σs point where this understatement is largest —
  the ratio falls by up to 0.160 on
  `telecom-lawful-intercept-nationstate-compromise` (0.668 → 0.508) and five
  entries now sit within 0.01 of the 0.5 floor — while 7 move away from it
  (up to +0.145). The direction is recorded, not claimed away.
- **Where.** `fair_cam/risk_engine/fair_core.py` (`loss_magnitude = primary +
  secondary`); `loss-form-share-rubric.md` §1.
- **Evaluate.** Compare the sampled per-event p95 against a comonotone
  (rank-correlated) re-sum from the raw-sample export.

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
- **Class.** IMPLEMENTATION-DEFINED (a change of sampling family with a
  hard ceiling), with a CALIBRATION sub-part (reading low/high as the 5th
  and 95th percentiles, `Z = 1.6449`). The library's TEF path is a slightly
  different convention: the curator's p5/p95 are promoted directly to the
  PERT's hard bounds (`tef-representation.md`). Whether the collapse lands
  in the `Beta(2/3, 10/3)` regime depends on σ exceeding `Z`, which is why
  the σ default (B3) carries a precondition tied to this entry.
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
  The cap bounds the *inherent* component: control multipliers scale the
  cap along with the distribution, so residual loss is bounded below
  `k × revenue`, not at it.
- **Where.** `services/loss_capacity.py`, `fair_cam/risk_engine/_truncation.py`.
- **Evaluate.** The truncation formula is verified three ways in the module
  docstring; the `max > p95` floor is enforced by the validator, not the minter.

### B3. Within-scenario loss dispersion default σ = 1.7

- **Standard / book.** Not supplied.
- **Idraa.** When no analyst pins a dispersion, loss magnitude carries
  σ = 1.7 in log space: a round number above the IRIS event-type-conditioned
  reads and below every size-conditioned read.
- **Class.** CALIBRATION (convention, not a measurement). IRIS never
  publishes the single-firm × single-scenario joint distribution. The value
  must stay above `Z = 1.6449` because B1's PERT collapse relies on the
  lognormal mode clamping to `low` (precondition noted in
  `services/calibration.py`).
- **Where.** `services/calibration.py` (`WITHIN_SCENARIO_SIGMA_DEFAULT`);
  `within-scenario-sigma-calibration.md`.
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

### B5. Library loss magnitudes = sector envelope × per-archetype form shares

- **Standard / book.** The six forms of loss are FAIR's; the book gives no
  arithmetic for building a magnitude from them.
- **Idraa.** Each library entry's `primary_loss` and `secondary_loss` are
  `E_sector × Σ(active primary-form shares)` and `E_sector × Σ(active
  secondary-form shares)`, with shares per (form, kind) assigned by an
  analyst-judged rubric under a `Σ(all shares) ≤ 1` coherence bound. The
  shares are self-described as "analyst judgment, vulnerability-grade, no
  per-value citation."
- **Class.** CALIBRATION (added weighted values), applied on top of the
  cited sector envelope.
- **Rationale.** Differentiates archetypes within a sector by which forms
  of loss they fire, instead of flattening every archetype to the sector
  envelope. The envelope citation survives the split (guarded).
- **Where.** `loss-form-share-rubric.md`, `loss-magnitude-forms.md`,
  `data/seed_library_entries*.json` (`loss_form_profile`).
- **Evaluate.** `tests/integration/test_library_loss_differentiation.py`
  pins reconstruction from envelope × shares;
  `tests/integration/test_loss_form_stakeholder_test.py` guards the
  regulator-and-judgment reaction conformance below.
- **Regulator-and-judgment reaction conformance (2026-09, issue #175).**
  Response is split across the primary/secondary boundary by the rule in
  `loss-form-share-rubric.md` §4: every entry firing a `fines` share (a
  regulator, court or counterparty reaction) carries a `response/secondary`
  share of 1/3 of its response budget (3/7 where the threat type resolves to
  the `data_disclosure` default: `data_disclosure` and `social_engineering`)
  (checked exactly on the 24 rule-derived entries against their pre-split
  response budgets; the 7 legacy authored `data_disclosure` splits, six of
  which rubric §4 per-entry adjustment moved, e.g. 0.22 P / 0.14 S, are held
  within ±0.04 of 3/7 with 0.0003 of headroom at the three entries splitting
  0.22 P / 0.14 S (0.14 of a 0.36 budget)), reclassified within the budget so
  Σshares and the inherent PL+SL mean are unchanged (PL falls, SL rises).
  Mean-neutrality holds under Idraa's model, in which Secondary Loss is
  applied on every loss event (departure A1); under a standard SLEF branch
  with q = P(secondary | primary event) the same reclassification would lower
  inherent ALE by `s(1−q)/(Σp₀ + q·Σs₀)`, 2.2%–14.9% at q = 0.5, so A1's error
  bound binds harder on these 24 entries (recorded in A1). Three judgment
  calls in the basis strings: `healthcare-record-alteration` takes the
  proceeding string although its bucket also includes reportable-use patient
  notification, `generative-ai-prompt-injection`'s data-obligation basis is
  inferred from its `fines` share rather than a named statute, and
  `edge-ransomware-perimeter-gateway`'s proceeding basis is inferred from its
  `fines` share on an entry whose description names no exfiltration, regulated
  data or proceeding (the `fines` share itself is an unadjusted `ransomware`
  §3 default — a curation gap, #181); none affects the numbers (the fraction
  is selected by threat type). The fractions are conventions of the same grade
  as the shares, not identifiable from the corpus (no observable separates
  IR-of-the-event cost from forced-response cost per entry), selected by
  threat type: 3/7 for `data_disclosure` (whose default already splits
  response 0.20 P : 0.15 S) and for `social_engineering` phishing→breach
  entries (routed to that default by the rubric), 1/3 for every other type
  (whose default carries response as primary-only). Consequences, stated so a
  reader can check them: residual ALE on an affected scenario falls between
  0.37% and 8.9% at Response effectiveness between 0.3 and 0.9 (about 10% at
  full effectiveness; a scenario carrying a currency-valued loss-reduction
  control, C10, falls further, outside the top of that band), because Idraa
  routes Response onto Secondary Loss at weight 0.5 vs Primary at 0.2
  (`composition_topology.py` `_MAGNITUDE_WEIGHTS`; C4:
  implementation-calibration, not identifiable — FAIR-CAM §3.3.3 grounds the
  direction, not the numbers, so every figure in this bullet is a point
  estimate that moves with the weight-robustness ensemble; the bands scale
  linearly in the fraction up to 2-dp rounding — at f = 1/2 a 1/3 entry's
  figure is ×1.5 and a 3/7 entry's ×7/6, moving the band to 0.6%–11.3% (the
  top entry changes with the fraction, since 1/3 entries gain more than 3/7
  ones: `law-enforcement-records-extortion-breach` goes 8.9% → 10.3% and
  `k12-edtech-vendor-breach` overtakes it at 11.3%) — and 1/3 sits on the
  conservative side of §3.3.3's "predominantly secondary" reading: less
  control credit, residual higher); since inherent ALE is unchanged, the
  modelled control benefit rises by exactly that amount on an uncapped/PERT
  scenario and the reported ROI (risk reduction ÷ control cost, D6) in the
  same proportion (+4% to +26% on a Response-only posture, less when other
  groups also reduce risk) — for a scenario once it carries the new split;
  runs already stored are computed from the scenario's own distributions and
  do not change until it is repaired, refreshed or re-adopted; the per-event
  tail narrows for 17 entries and widens for 7 (the split moves |Σp − Σs|
  further from zero), and one new cross-sector curve coincidence appears
  (`tolling-plant-ransomware-customer-liability` Σp 0.70 → 0.62 now matches
  `third-party-processor-breach`'s node on the shared envelope; within-sector
  differentiation is unaffected); an adopted scenario on one of the four
  catastrophic entries carries a capacity cap and samples the truncated
  lognormal, whose mean is concave in the split, so its inherent mean rises
  (up to ≈ 7.1% on `telecom-lawful-intercept…` as the cap approaches the
  capacity floor (B2: `max` must exceed the field's p95); ≈ 6.5% at 1.5× the
  floor) and its residual can rise instead of falling (up to ≈ 5.4% at
  Response effectiveness 0.3; ≈ 4.5% at 1.5× the floor), an effect below 0.15%
  once the cap exceeds ~100× the entry's pre-change PL p95; and 17 entries
  move toward the Σp ≈ Σs point where A5's independence understatement is
  largest. This pass covers the regulator-and-judgment sub-case only: the 39
  remaining entries firing `reputation` without `fines` are not audited here
  (follow-up issue #181). Competitive-advantage rows stay primary with a
  per-entry justification on the market-position definition (the compromised
  asset is the differentiator itself; the exploiting party is the threat
  agent), a placement Jones & Freund, 2nd ed. (2026), Ch. 3 (the six forms of
  loss) treats as nuanced but mainly primary for the trade-secret case, that
  the FAIR Institute's loss-magnitude map allows for either side
  (https://www.fairinstitute.org/blog/fair-risk-basics-what-is-loss-magnitude,
  accessed 2026-09-18), and that its Crash Course lists among the typically
  secondary forms
  (https://www.fairinstitute.org/blog/a-crash-course-on-capturing-loss-magnitude-with-the-fair-model,
  accessed 2026-09-18). Mean-neutrality is a property of the canonical entry:
  an org holding a per-field `ScenarioLibraryOverride` on only one of
  `primary_loss` / `secondary_loss` for a renumbered entry is not mean-neutral
  — the canonical side moves and the overridden side does not, shifting that
  org's adopted or refreshed PL+SL mean by the transferred share; such
  overrides must be re-authored, and the sweep counts them
  (`override_one_sided`) rather than classifying them. Audit baseline: 24
  entries renumbered (23 firing `fines` plus one notification-bearing
  `data_disclosure` entry with no `fines` share, rubric §4), 5 justified.
  Adopted scenarios are not rewritten by the migration; a read-only sweep
  classifies them, and any pristine or copy-stale scenario triggers a separate
  repair PR.

### B6. Qualitative likelihood and impact bands map to FAIR ranges

- **Standard / book.** Open FAIR's risk-analysis standard gives estimation
  guidance for converting qualitative registers but publishes no frequency
  example scale.
- **Idraa.** The qualitative-register converter maps each likelihood band to
  a frequency range (for example, very low = 0.01 to 0.1 events per year)
  and each impact band to a loss range, with the mode at the band's
  geometric midpoint. Structurally, a register likelihood is read as the
  **loss-event frequency**, bypassing the TEF × Vulnerability split. The
  band is ordinal and is interpreted as a frequency from the outset (the
  seed anchors on once-in-X-years matrix semantics); no annual probability
  is rescaled into a rate, so the frequency/probability separation in §E is
  preserved.
- **Class.** CALIBRATION (the band tables are a v3 convention, labelled so
  in the seed data) plus ESTIMATION LEVEL (authoring at LEF).
- **Rationale.** The bands are priors for calibrated review, not empirical
  claims; every converted scenario is flagged for analyst confirmation.
- **Where.** `data/seed_qualitative_bands.json`; the converter service.
- **Evaluate.** The seed file records the scale's provenance; converted
  scenarios carry their origin so a reviewer can re-elicit.

### B7. Overlays pre-fill the wizard with multiplicative adjustments

- **Standard / book.** Not a FAIR concept.
- **Idraa.** Named overlays (for example, critical infrastructure) carry
  multiplicative deltas on FAIR parameters. Since the 2026-05 cleanup they
  are **wizard-time pre-fill only**: applying one edits the form values the
  analyst then owns; nothing multiplies at run time.
- **Class.** CALIBRATION (the deltas), confined to authoring.
- **Rationale.** Keeps the stored scenario the single source of truth, so a
  run never depends on a calibration layer that could change under it.
- **Where.** `services/overlays.py`, `_starter_overlays_seed_data.py`;
  `fair-cam-methodology.md` ("What was excised in PR π").
- **Evaluate.** Every overlay row carries a methodology note (fail-loud on
  missing); runtime independence is guarded by the PR π deletion record.

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
- **Evaluate.** `fair_cam/tests/test_composition_operators.py` and
  `fair_cam/tests/composition/`; `TIME_UNIT_EXCLUDED` sub-functions take the
  elapsed-time branch (C2) or the currency path (C10) instead.

### C2. Elapsed-time sub-functions normalised by `exp(−t/τ)`

- **Standard.** §2.3 leaves time-unit normalisation to implementations; the
  alignment audit lists exponential decay as one option with no
  recommendation.
- **Idraa.** `opeff = exp(−elapsed_time / τ_sf)` with a per-sub-function τ
  table, each τ traced to a primary citation under a cite-or-drop rule.
- **Class.** IMPLEMENTATION-DEFINED (the form) + CALIBRATION (the τ table).
- **Distribution assumption.** Two of the three canonical τ values are
  anchored on a published **mean** (`τ = mean`), which equals the decay
  constant only under an exponential (constant-hazard) time-to-detect or
  time-to-contain distribution. Cyber dwell times are better described as
  lognormal or Weibull, so those two τ values carry a shape assumption the
  citation does not itself support; the third is median-anchored
  (`τ = median / ln 2`), which needs no shape assumption for the half-life
  point. The table records which anchor each value uses.
- **Where.** `elapsed-time-tau-calibration.md`,
  `fair_cam/calibration/elapsed_time_taus.py`.
- **Evaluate.** The riskflow#131 precedent (means plugged into a median-half-life
  formula) is exactly the failure this table's methodology section guards.

### C3. Missing capability defaults to 0.5

- **Standard.** Silent.
- **Idraa.** A `NULL` capability value contributes `0.5 × coverage` (times
  reliability). For elapsed-time sub-functions this is the algebraic
  half-life point `exp(−ln 2)`.
- **Class.** CALIBRATION (safe default).
- **Where.** `fair_cam/composition.py`.
- **Evaluate.** The identity `exp(-ln 2) = 0.5` is stated in the docstring;
  `fair_cam/tests/models/test_assignment_optional_capability.py::test_null_capability_value_accepted`
  covers the NULL branch, which the riskflow#131 reclassified sub-functions
  exercise by legitimately storing no capability value.

### C4. FAIR-axis routing weights (the page-42 "added weighted values")

- **Standard.** Says Prevention reduces loss-event likelihood (§3.1 p. 9)
  and Response limits loss magnitude (§3.3 p. 18); gives no magnitudes.
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
  `services/weight_robustness.py`; `control-weight-robustness.md`. If two
  groups ever target the same node their multipliers are applied as a
  product (currently inert: only Prevention and Response carry targets, on
  disjoint nodes).
- **Dead code warning.** `fair_cam/models/control.py` still carries a
  deprecated `get_fair_impact_factor` method with a second, contradictory,
  unlabelled weight table (0.8 / 0.7 / 0.6 / 0.5 / 0.4 / 0.9). It has zero
  callers and is not the engine's table; removal is tracked in issue #177.
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

- **Standard.** §5.1.x (pp. 36–45) prescribes AND across the nine Decision
  Support prevention sub-functions.
- **Idraa.** First relaxed to weak-AND (a strict AND makes any organisation
  without all nine functions score zero decision support), then to the
  **best-coherent-subset mean** (max over k of the top-k mean) because the
  plain mean is non-monotone in coalition membership and produced negative
  Shapley values on a production run (riskflow#453). Because prefix means of a
  descending sequence never increase, this is **identically `max` of the
  present member effectivenesses**: one strong decision-support function
  scores the whole group at its own level. The code says so; the register
  says so here.
- **Class.** DEPARTURE (changed relationship), labelled at the site.
- **Rationale.** Decision-support quality is read as the strongest coherent
  subset of present functions; a weaker extra function neither helps nor
  dilutes. Known cost: a max-aggregator overstates `E_dsc` whenever the
  other present functions are weaker, most of all for sparse authoring; the
  overstatement's effect on risk is bounded by `κ · (1 − r0)` (C7).
- **Where.** `fair_cam/risk_engine/group_composition.py` (`precompose_parts`).
- **Evaluate.** Monotonicity is the property the change exists to restore;
  the negative-Shapley regression from riskflow#453 is the test case.

### C7. Meta-controls act through a reliability coupling, `r_eff = r0 + (1 − r0)·κ·E_meta`, κ = 0.5

- **Standard.** §2.2 p. 5, §2.3 pp. 5–6, §4 p. 21: VMC and DSC affect risk
  *indirectly*, by changing the reliability of other controls; no
  functional form.
- **Idraa.** VMC/DSC groups carry **no direct FAIR-node targets** (retired in
  riskflow#439 on §2.2 "indirectly affect risk" grounds). Their composed strength
  `E_meta` recovers a fraction κ of every co-present Loss Event Control's
  reliability headroom. `E_vmc`, `E_dsc`, and `E_meta = OR(E_vmc, E_dsc)`
  are cross-family fusions with no Standard-prescribed operator. The DSC
  contribution is a **proxy**: the Standard frames DSC as improving
  decisions, not reliability. Meta-on-meta uplift is not modelled.
- **Class.** ADDED RELATIONSHIP (the form) + CALIBRATION (κ, midpoint by
  symmetry, non-identifiable, perturbed by the ensemble as `meta.kappa`).
- **Where.** `fair_cam/models/composition_topology.py`
  (`KAPPA_META_RELIABILITY`), `group_composition.py` (`finalize_composition`).
  A stale docstring in `control_aware.py` still describes the pre-riskflow#439 direct
  VMC node target; the topology table, not that docstring, is the source of
  truth (cleanup tracked on issue #177).
- **Evaluate.** `test_kappa_meta_reliability_pin`; the max-aggregation fix
  (riskflow#455) and the open attribution investigation (riskflow#434) are the live
  evaluation record.

### C8. VMC Correction is gated on Implementation; VMC Identification pair uses OR

- **Standard.** §4.3.1–4.3.2 (p. 28) prescribes AND(Treatment Selection,
  Implementation); §4.2 (p. 25) names Threat Intelligence and Controls
  Monitoring but prescribes no operator between them.
- **Idraa.** Absent Implementation → no correction regardless of selection;
  present → AND over present members (replaces zero-padding of an absent
  selection). Identification pair → OR, since the two members cover
  different variance sources.
- **Class.** DEPARTURE (the gate, labelled "Spec D3 deviation") and
  IMPLEMENTATION-DEFINED (the OR).
- **Where.** `group_composition.py`, `composition.py` docstrings.
- **Evaluate.** `fair_cam/tests/risk_engine/test_meta_reliability_coupling.py`
  covers the gated Correction and the Identification-pair OR through the
  composed meta effect; `fair_cam/tests/test_composition_topology.py` pins
  the group operator table.

### C9. Detection has no standalone node; Response acts on magnitude only

- **Standard.** §3.2 (pp. 15–17): Detection enables Response; §3.3 (p. 18):
  Response limits loss; §3.3.2 (p. 19): availability events manifest
  themselves.
- **Idraa.** Detection alone applies no multiplier (it gates the
  Detection∧Response pair); the pair's effect is routed to Primary and
  Secondary Loss only, not to frequency (riskflow#130 D4 re-route, fixing a
  double-count). **Availability bypass:** when the scenario's effect is
  Availability, the Detection gate is treated as intrinsically satisfied and
  the raw Response effectiveness is credited with no Detection control
  present. The §3.3.2 text grounds the direction; the mapping from the
  scenario's `effect = AVAILABILITY` to "self-detecting" is Idraa's.
- **Class.** IMPLEMENTATION-DEFINED (gate and routing), grounded in
  §3.2–3.3; ADDED RELATIONSHIP (the availability bypass).
- **Where.** `composition_topology.py` (`LEC_DETECTION`, `LEC_RESPONSE`);
  `fair_cam/risk_engine/control_aware.py` (`availability_self_detection`),
  wired from `services/run_executor.py` on `ScenarioEffect.AVAILABILITY`.
- **Evaluate.** `tests/contracts/test_weight_robustness_covariation.py::test_drpair_weights_are_inert`
  (the pair entry's weights are never read) and the riskflow#130 double-count
  regression; the availability path is exercised in
  `fair_cam/tests/risk_engine/` and the verification workbook mirrors it.

### C10. Currency-valued Loss Reduction is a per-event subtractor on Secondary Loss

- **Standard.** Loss Reduction is measured in currency (§2.4); no
  application rule.
- **Idraa.** Subtracted from each secondary-loss sample before flooring at
  zero; Primary Loss untouched.
- **Class.** IMPLEMENTATION-DEFINED (riskflow#130 D3).
- **Where.** `fair_core.py` (`secondary_loss_subtractor`).
- **Evaluate.** `fair_cam/tests/risk_engine/test_native_engine_subtractor.py`
  (`test_subtractor_shifts_then_floors_at_zero`,
  `test_subtractor_floors_negative_at_zero`); the subtractor is validated
  finite and non-negative at the engine boundary; the partial-floor and
  full-collapse regimes are described in `fair-cam-standard-alignment.md`
  (§ on per-event dollar reduction).

### C11. Several controls on one sub-function combine as independent OR

- **Standard.** Silent on how two controls fulfilling the same sub-function
  combine, and on how a single control's several assignments combine.
- **Idraa.** Both use `1 − Π(1 − x_i)`, the probability that at least one
  succeeds under independence (the same operator the Standard prescribes for
  the Prevention trio, but applied here without a prescription).
- **Class.** IMPLEMENTATION-DEFINED (independence across controls).
- **Rationale.** Independence is the neutral choice; correlated failure
  (two controls sharing a dependency) would make the OR optimistic. The
  Standard's own dependency statement (§2.3) is what the κ coupling (C7)
  models instead.
- **Where.** `fair_cam/composition.py` (`or_compose`, "within-sub-function
  across controls" and "per-control Layer-2 squash").
- **Evaluate.** `fair_cam/tests/test_composition_operators.py`.

---

## D. Outside FAIR (view-model derivations)

None of these is a FAIR node. Each is computed outside the FAIR math path.
The attribution table carries an explicit "not FAIR-grounded" methodology
note in the help; the others are documented as derivations here and in the
help articles rather than labelled on every screen that shows them.

| Item | What it is | Unvalidated conventions inside it | Where |
|---|---|---|---|
| **D1. Shapley attribution** | Cooperative-game split of total modelled risk reduction across controls (Shapley 1953; Castro et al. 2009 and Maleki et al. 2013 for the sampling estimator and its bound). | Permutation-sampling budget; the value function is the engine's closed-form composition, evaluated on representative values. | `services/shapley.py` (no FAIR math), `fair_cam/risk_engine/control_attribution.py` (the `v(S)` evaluator) |
| **D2. If-removed (leave-one-out) value** | Drop in modelled reduction when one control is removed. Never totalled. | none | same |
| **D3. Weight-robustness ensemble** | Logit-normal perturbation of C4 + C7 parameters; rank-stability verdicts. | σ = 0.6, K = 256, ±1-rank stability, 10% flip threshold, 0.90 stable fraction. All conventions; widening σ only makes verdicts more pessimistic. | `services/weight_robustness.py`, `control-weight-robustness.md` |
| **D4. ALE as the mean** | Mean of the annual-loss distribution. The book's headline quantity, but not a named Open FAIR node. | none | `services/run_view_model.py` |
| **D5. VaR, expected shortfall, loss-exceedance curve** | Standard tail statistics read off the sampled distribution. | Subject to the A3 tail approximation. | same |
| **D6. Aggregate ROI** | `total_risk_reduction / total_control_cost` for a run, shown as a bare point figure. A financial ratio, not a FAIR node; the numerator is the engine's with-minus-without ALE difference, so it inherits the composition-weight uncertainty of C4/C7 (what D3 propagates) without showing a range. | none beyond its inputs | `services/run_executor.py` (`aggregate_roi`), `templates/runs/detail.html` |
| **D7. ATT&CK coverage ratios** | Share of a scenario's mapped techniques covered by present controls. Labelled "not FAIR-grounded" in code. | none | `services/attack_coverage.py` |
| **D8. Appetite verdict and headroom** | Dashboard comparison of the loss-exceedance curve against a stated appetite. The headroom strip is labelled "not FAIR-grounded" in code; the verdict functions rely on this register. | interpolation on the LEC | `services/dashboard_view_model.py` |
| **D9. Expected-shortfall sampling error** | Monte Carlo sampling standard error of the sample Expected Shortfall at each tail level (first-order influence-function estimator, Scaillet 2004 / Manistre & Hancock 2005), shown as a 95% interval beside ES so a reader can judge iteration count. A property of the simulation, not of FAIR. | z = 1.96 normal band; reported as unavailable when fewer than two samples lie at or above VaR | `services/run_executor.py` (`_es_standard_error`), `services/_view_model_helpers.py` (`ES_CI_Z_95`) |

---

## E. Probability interpretation (conformance, not departure)

Listed so a reviewer can check the framing has not drifted.

- Probabilities are **subjective** (degrees of belief), elicited as
  calibrated 90% ranges; Idraa performs **forward Monte Carlo propagation
  only**. There is no prior-times-likelihood step, no posterior, and the
  engine never updates an estimate. New evidence is incorporated by
  re-eliciting. This matches Jones & Freund p. 42: the earliest versions of
  FAIR used Bayesian formulas, but the authors found that Monte Carlo
  "worked just as well and was easier to work with."
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
