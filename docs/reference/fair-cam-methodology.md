# FAIR-CAM methodology

What `fair_cam` (the library) implements. Source documents, three-domain
model, sub-function tables, codebase alignment.

## Source documents

- **FAIR Institute Standard V1.0** (January 15, 2025), 49 pages, CC BY-NC-ND 4.0.
  URL: `https://www.fairinstitute.org/hubfs/Standards%20Artifacts/FAIR%20Controls%20Analytics%20Model%20(FAIR-CAM)%20Standard%20V1.0%20(January%202025).pdf`
- **An Overview of FAIR-CAM** — companion overview, FAIR Institute, CC BY-NC-ND 4.0.

**The Standard is authoritative; the Overview is the introductory companion.** The
Standard renames functional domains and some sub-functions vs. the Overview. **Use
Standard names below.**

## Three functional domains

1. **Loss Event Control (LEC)** — directly affect frequency/magnitude of loss events.
2. **Variance Management Control (VMC)** — affect the reliability/operational-performance of other controls.
3. **Decision Support Control (DSC)** — affect decision-making quality.

Per Standard §2.3: ALL controls are dependent on VMCs (for reliability) and DSCs
(for the decisions that brought them into being). The Standard does not enumerate
those dependencies because they're universal.

## LEC (Loss Event Control) — Standard §3

Boolean: **Prevention OR-trio**, **Detection AND-trio**, **Response weak-AND-trio**, **Detection-Response AND-pair**.

| Sub-function | Description | Unit of measurement |
| --- | --- | --- |
| LEC.Prevention.Avoidance | Reduce frequency of contact between threat agents and assets | % reduction in contact frequency with threat agents |
| LEC.Prevention.Deterrence | Reduce probability of harmful action after threat-agent contact | % reduction in probability that threat actors choose harmful action |
| LEC.Prevention.Resistance | Reduce likelihood that threat-agent action results in loss event | % probability of resisting potentially harmful actions |
| LEC.Detection.Visibility | Provide evidence of activity that may be anomalous/illicit | % probability the control provides access to necessary information |
| LEC.Detection.Monitoring | Review data provided by Visibility controls | Elapsed time between reviews |
| LEC.Detection.Recognition | Differentiate normal from abnormal activity | % probability a loss event is differentiated from normal |
| LEC.Response.EventTermination | Terminate threat-agent activities (was "Containment" in Overview) | Time between recognition and control-achievement |
| LEC.Response.Resilience | Maintain or restore normal operations | Time operating in degraded mode |
| LEC.Response.LossReduction | Reduce realized losses (was "Loss Minimization" in Overview) | Reduction of lost economic value (currency) |

FAIR-axis mapping: Avoidance → Contact Frequency; Deterrence → Probability of
Action; Resistance → Vulnerability; Detection+Response → Loss Magnitude (via
dwell-time minimization).

## VMC (Variance Management Control) — Standard §4

Boolean: **Identification AND Correction**.

| Sub-function | Description | Unit of measurement |
| --- | --- | --- |
| VMC.Prevention.ReduceChangeFrequency | Reduce frequency of changes that could introduce variance | Forecast/measured % reduction in change frequency |
| VMC.Prevention.ReduceVarianceProbability | Reduce probability that changes degrade controls | Forecast/measured % reduction in variance |
| VMC.Identification.ThreatIntelligence | Identify changes in threat landscape that diminish control efficacy | Elapsed time between landscape changes and awareness |
| VMC.Identification.ControlMonitoring | Identify variance in control conditions | Elapsed time between control-condition changes and recognition |
| VMC.Correction.TreatmentSelectionAndPrioritization | Select and prioritize control variance corrections | Elapsed time from identification until corrective actions begin |
| VMC.Correction.Implementation | Correct variant conditions | Elapsed time from corrective-action initiation until completion |

## DSC (Decision Support Control) — Standard §5

Three top-level functions: **Misaligned-Decision Prevention**, **Identification**,
**Correction** (the Standard added Correction explicitly; the Overview only had
Prevention + Identification).

Boolean: each Prevention sub-function has **AND-relationship with all other
Prevention sub-functions** (deficiency in any one increases mis-aligned-decision
probability). **Identification AND Correction**.

### Misaligned-Decision Prevention sub-functions

| Sub-function | Description | Unit of measurement |
| --- | --- | --- |
| DSC.Prevention.DefinedExpectations | Clearly define expectations and/or objectives | Probability that clear expectations have been defined |
| DSC.Prevention.CommunicationOfExpectations | Communicate expectations to responsible personnel | Probability expectations have been clearly communicated |
| DSC.Prevention.SituationalAwareness.Data.AssetData | Provide data regarding assets relevant to decisions | Probability that asset data used for decisions is accurate |
| DSC.Prevention.SituationalAwareness.Data.ThreatData | Provide data regarding relevant threats | Probability that threat data used for decisions is accurate |
| DSC.Prevention.SituationalAwareness.Data.ControlData | Provide data regarding control conditions | Probability that controls-related data used for decisions is accurate |
| DSC.Prevention.SituationalAwareness.Analysis | Synthesize asset/threat/controls data, generate accurate results | Probability that analysis model generates accurate results given accurate data |
| DSC.Prevention.SituationalAwareness.Reporting | Provide decision-makers with analysis results | Probability that useful results are provided in time to support decisions |
| DSC.Prevention.EnsureCapability | Ensure decision-maker has skills/authority/resources | Probability responsible persons have skills/resources to act per expectations |
| DSC.Prevention.Incentives | Motivate personnel to make aligned decisions | Probability appropriate incentives are in place |

DSC's "Authority" in some Overview diagrams collapses into "Ensure Capability" in
the Standard.

### Misaligned-Decision Identification

DSC §5.2 — proactive search for decisions that didn't align with org
expectations/objectives. Covers expectation-setting, prioritization, solution
choice, compliance/performance.

### Misaligned-Decision Correction

DSC §5.3 — correct identified misaligned decisions (root-cause analysis, policy
updates, retraining, etc.).

## Operational Effectiveness (Maturity) — Standard §2.4

The Standard specifies **THREE attributes** for Operational Effectiveness (the
Overview only had Reliability):

1. **Capability (§2.4.1)** — control's inherent ability to perform its intended function. Design quality, real-world workload performance, alignment with industry best practices. (E.g., AV's malware-detection-via-signature-database is a measure of capability.)
2. **Coverage (§2.4.2)** — extent the control applies to relevant assets/threats/scenarios. Deployment breadth. (E.g., firewall on all internet-facing servers = broad coverage; firewall on only some = limited.)
3. **Reliability (§2.4.3)** — likelihood the control performs consistently and without failure. Stability over time, performance under stress, failure likelihood.

**The Standard deliberately does NOT publish a closed-form composition formula**
combining the three attributes (audit §2.3 + Standard §2.4). Composition is left
to implementations.

The Overview-era formula `OpEff = IntendedEfficacy × (1 - VF/365)^VD` (where VF =
variance frequency/year, VD = variance duration/day) is **explicitly retired** by
the v3 audit (`docs/reference/fair-cam-standard-alignment.md` §2.3): "The Standard
supersedes the Overview for all v3 design decisions." Do not cite this formula as
Standard-derived.

**v3's Layer 1 (intra-assignment) choice — PR kappa decision 2026-05-02**:
`OpEff = capability × coverage × reliability` (Option A — multiplicative). Loosely
consistent with the Standard's only worked example (§3.2.1 firewall on 1-of-4
entrances → 25% effectiveness) and boundary-correct (any axis at 0 → 0).
Implementation choice, not Standard-derived. See PR kappa spec for rationale.

## IRIS reference data

IRIS (Information Risk Insights Study, Cyentia Institute) is the open empirical
dataset v3 ships for sector × event-type loss-event frequency and magnitude
priors. v3 stores IRIS as immutable reference data (`iris_calibration_pin` table;
admin-only re-pin via versioned operation).

Wizard usage (post PR π): IRIS rows pre-fill the Scenario distribution form
fields when the user picks (sector, event_type). Once a Scenario exists, MC reads
its on-row distribution params, not IRIS — IRIS is a seed for human-readable
initial values, not a runtime dependency of the Monte Carlo path.

## Vulnerability anchor: control-naive inherent (methodology/vuln-inherent-framing)

The Scenario `vulnerability` distribution is the asset's **inherent**
(control-naive) susceptibility — the probability a threat event becomes a loss
event **before** crediting the org's own mitigating controls. The FAIR-CAM
control layer then reduces it (the `LEC Prevention → vulnerability` node
multiplier in `control_aware.py`, among others); `residual_risk` in the run
output is what you read after controls apply. Eliciting `vulnerability`
net-of-current-controls would double-count the control benefit (once baked into
the elicited number, once via the control multiplier).

**IRIS as the inherent anchor — explicit caveat.** The IRIS sector vulnerability
values (healthcare 0.45, financial 0.30, manufacturing 0.40) are **FAIR-modeled
priors, not measured conversion rates** — IRIS 2025 does not publish per-industry
vulnerability distributions; the priors are anchored to Figure 16 initial-access
shares (provenance in `fair_cam/parameters/_iris_2025_calibration.py`). They are
*used as* a controlled-world conversion rate inside the calibration's
`TEF = observed_LEF / vuln` translation — the prior divides the observed
post-control loss-event frequency to recover TEF. We **adopt that value as the
inherent anchor** for the wizard prefill (kept, relabelled "inherent baseline"),
as a pragmatic, documented modeling stance — NOT a measurement, and NOT a claim
that it equals true zero-control susceptibility. Consequences a reader must hold:

- As an inherent anchor it is **biased low by construction**: it is a
  controlled-world prior, and true inherent (pre-control) susceptibility is
  generally higher than a post-control conversion rate. Note this is an
  **optimistic (risk-understating) bias, not a conservative one** — in risk
  terminology "conservative" means erring toward *more* risk, and a low
  vulnerability anchor errs toward *less* (it understates residual ALE). Do
  not read "anchored low" as "erring on the safe side".
- Modelled controls therefore represent deltas from an **industry-typical
  baseline**, not from a literal zero-control state. An analyst who reads
  "inherent" as zero-control and then models their full control stack is
  crediting controls already implicit in the IRIS-observed average — the
  "inherent baseline" badge copy manages that expectation at the point of entry.
- The IRIS `TEF = LEF / vuln` derivation is **unchanged** by this framing (no
  calibration constant moved); only the wizard's labelling of the prefilled
  `vulnerability` field changed.

**Seed library values — deferred to Epic C.** The ~44 seeded library scenarios
(31 base + 13 extension) carry `vulnerability` distributions with no
control-posture provenance and were
curated at typical/controlled posture (modes ≈ the IRIS controlled range). Under
the inherent frame they are anchored low and should be re-curated upward with an
explicit posture declaration. That value re-curation is **deferred to the Epic C
library re-curation (#335), tracked at #338**, which is already re-sweeping the
library and owns structured epistemic provenance (the `loss_tier` work); this
change only records the methodology decision, it does not re-pin the seeded
numbers.

## Codebase alignment

- `src/riskflow/models/enums.py::ControlDomain` (`LOSS_EVENT`, `VARIANCE_MANAGEMENT`, `DECISION_SUPPORT`) = the three FAIR-CAM functional domains. Naming aligns with Overview-era LE/VM/DS (not Standard's LEC/VMC/DSC); the values omit the trailing "_CONTROL" suffix. Acceptable abbreviation; the renaming should NOT propagate into v3 enum migrations unless the spec demands it.
- `fair_cam/controls/comprehensive_controls_library.py` + `fair_cam/risk_engine/control_aware.py` (separate `RiskParameters` dataclass distinct from `FAIRParameters`) are the FAIR-CAM-aware reduction machinery — apply Operational Effectiveness per (LEC sub-function, control) to reduce base FAIR risk.

## Layer separation in v3 (post PR π)

```
Wizard fills Scenario distribution params from:
       • IRIS row matching (sector, event_type)        [pre-fill]
       • OverlayDefinition catalog (Apply button)      [pre-fill]
       • User edits in the form                        [authoritative]
       ↓ Stored on the Scenario row
       ↓ FAIR-CAM control-aware reduction              (Phase 1.3+)
            • Compute OpEff per assignment (Capability × Coverage × Reliability — PR kappa Option A)
            • Compose across assignments per Boolean topology (PR kappa Layer 2 — TBD)
            • Apply FAIR-axis multipliers per sub-function (PR kappa Layer 3 — TBD)
       ↓ Monte Carlo (FAIREngine.calculate_risk via _scenario_to_fair_parameters)
```

The base-parameter layer is now just "what's on the Scenario row." Composition no
longer happens at runtime.

## What was excised in PR π (2026-05-06)

The calibration runtime that PR-α through PR-η accreted is gone:

- `fair_cam/parameters/overlays.py` — DELETED. Cross-cutting overlay math (CRITICAL_INFRASTRUCTURE etc.) no longer multiplies into MC inputs. OverlayDefinition rows survive as a wizard-time HTMX "Apply overlay" button that pre-fills form fields; runtime applies nothing.
- `fair_cam/parameters/sub_sector_overlays.py` — DELETED. Sub-sector multipliers retired.
- `fair_cam/parameters/overrides.py` — DELETED. Per-org calibration override layer retired.
- `src/riskflow/services/scenario_calibration.py` — DELETED. The runtime composition pipeline (IRIS → override → overlay → sub-sector) is gone.
- `/calibration-overrides` route group — DELETED. Admin CRUD UI for the old override/overlay tables retired.

Monte Carlo now reads scenario distributions directly via
`run_executor._scenario_to_fair_parameters`. IRIS data + OverlayDefinition catalog
become wizard helpers (form pre-fill + Apply-overlay HTMX buttons), not runtime
composition layers.

## Useful invariants from the Standard

- **Each FAIR-CAM function has a distinct unit of measurement.** % probabilities, % reductions, elapsed times, currency. NEVER munge them together (e.g., "average control rating" across functions of different units is meaningless).
- **Controls fulfill multiple functions simultaneously.** EDR fulfills LEC Resistance + Detection (Visibility/Monitoring/Recognition) + Response Termination. When measuring an EDR's risk reduction, decompose by function and measure each independently.
- **All controls depend on VMCs and DSCs** — Standard §2.3 explicitly states this; no need to re-document in implementation.
- **Boolean composition matters** — failing-AND eliminates the function; failing-OR is partial degradation.
- **"Weak AND" exists in Response sub-functions** (Event Termination + Resilience + Loss Reduction) — a deficiency diminishes overall response efficacy but doesn't fully inhibit it. Code modeling this should NOT enforce strict AND.
- **Population susceptibility** — when modeling attacker probing N systems, account for per-system variance, not just average. Standard implies but does not formalize the math; FAIR-CAM-enabled software handles it.

## Known limitations (disclosure)

- **Product-form LEF×LM tail approximation** — the engine combines LEF and LM per iteration as a product (`risk = lef * loss_magnitude`), the faithful canonical FAIR/pyfair form. The mean is exact, but every tail statistic (VaR, Expected Shortfall, loss-exceedance curve, p2.5/p97.5 band) is an approximation whose error grows once LEF exceeds 1. This is a disclosure, NOT a bug or a Standard deviation. See `docs/reference/product-form-tail-approximation.md`.

## Naming convention proposal for v3 (when this lands)

When v3 introduces FAIR-CAM-aware Control modeling beyond the current Phase 1.2
Control entity, prefer the Standard's naming (`LEC` / `VMC` / `DSC`) for new code;
honor the existing `LOSS_EVENT` / `VARIANCE_MANAGEMENT` / `DECISION_SUPPORT` enum
values from Phase 1.2 to avoid breaking migrations. A single mapping:

```
LEC ↔ LOSS_EVENT
VMC ↔ VARIANCE_MANAGEMENT
DSC ↔ DECISION_SUPPORT
```

When a future task introduces the FAIR-CAM sub-function granularity (Avoidance,
Deterrence, ..., Reporting, etc.), use Standard names verbatim. Use
`EVENT_TERMINATION` (not "containment") and `LOSS_REDUCTION` (not "loss
minimization") to align with the Standard.

## What is NOT in FAIR / FAIR-CAM (cleanup record from PR ψ + PR φ)

The following metrics are **NOT** part of FAIR Standard, Open FAIR, FAIR-CAM, or
pyfair, despite appearing in earlier v3 design docs and the legacy
`fair_cam.aggregation.RiskAggregationEngine` as if FAIR-grounded:

- **Diversification benefit** — actuarial / Solvency II portfolio capital concept. Two competing definitions (variance-ratio vs VaR-sub-additivity). Borrowed from general portfolio finance.
- **Risk concentration index (Herfindahl)** — antitrust / market concentration measure. Sometimes used in portfolio diversity reporting; not a FAIR concept.
- **Top risk contributors (separate sorted list)** — generic portfolio reporting. Redundant with a per-scenario sorted bar chart.
- **Per-scenario contribution percentage** — portfolio decomposition metric. The CHART (sorted per-scenario ALE bar) is legitimate portfolio reporting; the "% contribution to aggregate ALE" framing borrows portfolio-decomposition language and overclaims FAIR rigor.
- **Correlation impact** (as a single scalar) — placeholder field in the legacy engine that returned `0.0` whenever no correlation matrix was supplied. Not meaningful in its current form.

PR ψ scrubbed these from canonical specs and deprecated the legacy module that
computed them. The cleanup record:
`docs/plans/2026-05-03-pr-psi-methodology-hygiene-design.md`.

**Modules deleted in PR φ (2026-05-04):** Following the same contamination
pattern, these whole packages were excised: `fair_cam.analytics`, `fair_cam.excel`,
`fair_cam.export`, `fair_cam.integration.telemetry`, `fair_cam.master_data`,
`fair_cam.threat_modeling`, `fair_cam.utils`, `fair_cam.visualization`. Plus
`fair_cam.distributions.enhanced_distributions` (fabricated IRIS-2025 calibrations
+ invented DistributionType categories). All 9 deletions had zero v3 callers and
zero tests. Net: ~9685 LOC removed; fair_cam shrank by roughly half. Cleanup
record: `docs/plans/2026-05-04-pr-phi-fair-cam-cleanup-sweep-design.md`.

**Modules deleted in PR π (2026-05-06):** `fair_cam/parameters/overlays.py`,
`fair_cam/parameters/sub_sector_overlays.py`, `fair_cam/parameters/overrides.py`
— the calibration runtime composition layer. v3 callers
(`services/scenario_calibration.py`, `routes/calibration_overrides.py`, related
repos + templates + tests) deleted in lockstep. Cleanup record:
`docs/plans/2026-05-06-pr-pi-simple-scenario-mc-design.md`.
