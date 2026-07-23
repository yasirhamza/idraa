---
title: FAIR-CAM Standard V1.0 Alignment Audit
status: HISTORICAL — pre-PR-ι audit; the candidate schema it evaluates has since shipped (see banner below)
last_reviewed: 2026-04-30
source_documents:
  - docs/FAIR Controls Analytics Model (FAIR-CAM) Standard V1.0 (January 2025).pdf
  - docs/reference/fair-cam-controls-library.csv
  - docs/reference/data-model-specification.md
  - alembic/versions/355450b21719_phase_1_2_controls.py
  - src/idraa/models/control.py
  - src/idraa/services/run_executor.py
  - fair_cam/models/control.py
  - fair_cam/risk_engine/native_control_aware.py
  - fair_cam/risk_engine/control_aware.py
---

# FAIR-CAM Standard V1.0 Alignment Audit

> **HISTORICAL DOCUMENT.** This audit was written pre-PR-ι, against the
> Phase 1.2 flat-triple schema it critiques (`control_strength` /
> `control_reliability` / `control_coverage` on `Control`, the classical-action
> `ControlFunction` enum, and the flat `_snapshot_control` audit shape). PR ι
> subsequently shipped the Standard-conformant schema this document argues for:
> effectiveness now lives on `ControlFunctionAssignment` (capability_value /
> coverage / reliability, per sub-function), the classical-action
> `ControlFunction` enum was dropped, and the run snapshot moved to the
> per-assignment (`snapshot_version: 2`) shape. **`docs/reference/data-model-specification.md`
> is the current source of truth for the shipped schema.** The "candidate" /
> "Decision deferred to PR ι plan-gate" language throughout this document
> describes decisions that were made during PR ι/κ/λ and are now settled —
> kept here for historical rationale, not as an open design question.

## 1. Purpose and scope

This document is the canonical conformance reference for RiskFlow v3's control data model. It was produced after Phase 1.2 shipped a `controls` table that deviates from the FAIR Controls Analytics Model Standard V1.0 (January 2025, hereafter "the Standard"). The deviation was missed at v3 kickoff because no Standard-conformance review gate existed. This audit closes that gap before further control-related features are built.

The document has three audiences. Developers implementing PRs ι, κ, and λ treat it as the governing spec for any schema change touching controls. Architects reviewing those PRs use it as the baseline against which conformance claims are verified. Future contributors use it to understand why the control model is shaped the way it is, rather than the simpler flat shape that shipped in Phase 1.2.

Three subsequent PRs cite this document directly. PR ι (Phase 1.5b-α) reshapes the v3 database schema to be Standard-conformant. PR κ (Phase 1.5b-β) reshapes the fair_cam library to match. PR λ (Phase 1.5c) builds the control library and wizard on the Standard-aligned schema. All three treat the sub-function inventory in §3 and the conformance tables in §4 and §5 as binding references. No claim in those sections may be changed without updating this document and re-running the associated PR plans.

**Framing note**: This audit enumerates the state of affairs and the landscape of options for unresolved decisions. It does not pre-decide architectural questions that belong to the PR ι and PR κ paranoid review gates. Sections that surface unresolved decisions end with an explicit "Decision deferred to PR [n] plan-gate" statement.

---

## 2. The Standard in brief

### 2.1 Three functional domains

The Standard defines three mutually exclusive top-level domains (§2.2, page 5). Loss Event Control (LEC) contains controls that directly affect the frequency or magnitude of loss events. Variance Management Control (VMC) contains controls that affect the reliability of other controls — they are not themselves loss-affecting, but they govern how consistently LEC and DSC controls perform. Decision Support Control (DSC) contains controls that affect the quality of management decisions that bring other controls into existence and keep them effective.

The Standard explicitly states that VMC and DSC are universal dependencies of all controls: "ALL controls depend on VMCs (for reliability) and DSCs (for the decisions that brought them into being)" (§2.3, pages 5-6). This dependency is universal and therefore not enumerated per-control — it is a structural axiom of the model.

### 2.2 Operational Effectiveness — three independent attributes (§2.4)

The Standard defines Operational Effectiveness (also called Control Maturity) as the combined measure of three independent attributes (§2.4, page 6):

**Capability** (§2.4.1, page 6): "A control's inherent ability to perform its intended function in addressing specific aspects of risk." This evaluates design quality, real-world performance, and alignment with industry best practices. Capability is specific to a sub-function — a control may have high capability for Resistance but low capability for Monitoring.

**Coverage** (§2.4.2, pages 6-7): "Measures the extent to which a control or set of controls applies to the assets, threats, or risk scenarios within the organization." Coverage is a breadth measure: a firewall deployed on all internet-facing servers has higher coverage than one deployed on only some.

**Reliability** (§2.4.3, page 7): "Refers to the likelihood that a control will perform its intended function consistently and without failure when needed." Reliability considers both operational stability and resilience to environmental issues.

These three attributes are independent — a control can have high capability but low coverage, or high coverage but low reliability. The Standard treats them as distinct inputs to any effectiveness evaluation, not as interchangeable scalars on a single axis.

### 2.3 The composition formula gap

The Standard deliberately does not publish a closed-form formula composing Capability, Coverage, and Reliability into a single Operational Effectiveness score. Section §2.4 describes the three attributes and their importance but specifies no arithmetic relationship between them. This is an intentional design choice: different deployment contexts weight the attributes differently, and the Standard leaves composition to implementations.

This has a direct implication for v3 and fair_cam: any formula that combines the three scalars is an implementation decision, not a Standard requirement. The Overview-era formula `OpEff = IntendedEfficacy × (1 - VF/365)^VD` referenced in the methodology memory file (`reference_fair_cam_methodology.md`) is a specific simplification from a pre-Standard document. The Standard supersedes the Overview for all v3 design decisions. The composition choice is surfaced as an open decision in §8 of this document.

### 2.4 Multi-function-per-control normativity

The Standard repeatedly treats multi-function controls as the norm, not the exception. Section §3.2 (page 14) states: "many Detection control technologies provide visibility, monitoring, and recognition in one package. For example, anti-malware technologies capture activity on systems and/or networks (providing Visibility), evaluate the captured data (providing Monitoring), and have heuristic and signature databases with which to recognize malicious code (providing Recognition)."

Section §5.1.3.1.3 (page 41) reinforces this: "many controls serve multiple control function purposes (e.g., anti-malware solutions commonly fulfill Resistive, Visibility, Monitoring, Recognition, and Containment functions), it is valuable to identify which functions a control fulfills as well as its Operational Performance levels in serving those functions."

The normative pattern is therefore: one control entity, multiple sub-function assignments, each assignment carrying its own Capability/Coverage/Reliability triple. A flat triple stored at the control level is fundamentally incoherent under this pattern because it cannot represent differentiated performance across sub-functions.

### 2.5 Boolean composition rules

The Standard uses Boolean AND and OR operators to define how sub-function groups compose within a domain (§2.1, page 4):

- **OR composition**: Either sub-function sufficing means the group succeeds. Loss Event Prevention is an OR-trio (Avoidance OR Deterrence OR Resistance) — any one prevention mechanism is sufficient for the group to function.
- **AND composition**: All sub-functions must operate for the group to succeed. Loss Event Detection is an AND-trio with AND coupling to Response — failing Visibility makes Recognition irrelevant; failing Detection makes Response impossible.
- **weak AND**: Used for Loss Event Response (§3.3). "Deficiencies in one diminish overall Response efficacy but won't necessarily inhibit it entirely." This is a deliberate softening of strict AND for the response domain.

Boolean composition means the group's effective performance is NOT simply the average of its sub-function values. AND logic implies worst-link or analogous semantics; OR logic implies at-least-one-succeeds semantics. The specific arithmetic operator for each composition type is an implementation decision not resolved by the Standard (see §8).

### 2.6 Distinct units of measurement

The 26 Standard sub-functions span four distinct unit types. Combining values across sub-functions that have different unit types produces a meaningless result. The Standard is explicit about this throughout §3 and §4.

| Unit type | Sub-functions using it |
|-----------|----------------------|
| Probability (%) | Avoidance, Resistance, Visibility, Recognition, all DSC Prevention sub-functions |
| % reduction | Avoidance (contact frequency reduction), Deterrence (probability-of-action reduction), VMC Reduce Change Frequency, VMC Reduce Variance Probability |
| Elapsed time | Monitoring, Event Termination, Resilience, VMC Threat Intelligence, VMC Control Monitoring, VMC Treatment Selection, VMC Implementation, DSC Identifying Misaligned Decisions, DSC Correcting Misaligned Decisions |
| Currency | Loss Reduction (only) |

An "average control rating across functions of different units is meaningless" — this is the implication of §2.4's attribute-specific unit definitions and §3's per-sub-function unit declarations.

Note: unit type and FAIR axis are independent dimensions. The "% reduction" unit type spans multiple FAIR axes — Avoidance reduces contact frequency, while Deterrence reduces probability of action, VMC Reduce Change Frequency reduces change frequency, and VMC Reduce Variance Probability reduces control degradation probability. Sub-functions sharing a unit type may target different FAIR axes and are not interchangeable in composition.

---

## 3. Standard sub-function inventory (canonical reference)

The following table is the complete, authoritative enumeration of all 26 Standard sub-functions. PRs ι, κ, and λ cite this table as the binding reference for `FairCamSubFunction` enum values, slug naming, and unit type assignments.

The `FairCamSubFunction` enum is a flat list of 26 string values. The DSC three-level nesting visible in the Standard (Provide Situational Awareness → Provide Data → three children) is a composition-rule structure documented in §2.5 and §3.3 below, not a structure in the enum itself. The path-style display names in the table below are a presentation grouping for this document — they are not enum structure.

Where the Standard's diagrams use different terminology than the section titles, both names are noted in the display name column. The slug (enum value) follows section-title nomenclature.

| Domain | Function group | Sub-function slug | Display name | Unit type | Standard § | Page | Boolean composition |
|--------|---------------|-------------------|--------------|-----------|-----------|------|---------------------|
| LEC | Prevention | `lec_prev_avoidance` | Avoidance | % reduction in contact frequency | §3.1.1 | 9-10 | OR (with Deterrence, Resistance) |
| LEC | Prevention | `lec_prev_deterrence` | Deterrence | % reduction in probability of action | §3.1.2 | 11 | OR (with Avoidance, Resistance) |
| LEC | Prevention | `lec_prev_resistance` | Resistance | % probability of resisting harmful action | §3.1.3 | 12-13 | OR (with Avoidance, Deterrence) |
| LEC | Detection | `lec_det_visibility` | Visibility | % probability of providing necessary information | §3.2.1 | 14-15 | AND (with Monitoring, Recognition); AND with Response |
| LEC | Detection | `lec_det_monitoring` | Monitoring | Elapsed time between reviews | §3.2.2 | 16 | AND (with Visibility, Recognition); AND with Response |
| LEC | Detection | `lec_det_recognition` | Recognition | % probability of differentiating anomalous activity | §3.2.3 | 17 | AND (with Visibility, Monitoring); AND with Response |
| LEC | Response | `lec_resp_event_termination` | Event Termination | Elapsed time from recognition to control achieved | §3.3.1 | 18-19 | weak AND (with Resilience, Loss Reduction); AND with Detection |
| LEC | Response | `lec_resp_resilience` | Resilience | Elapsed time operating in degraded mode | §3.3.2 | 19 | weak AND (with Event Termination, Loss Reduction); AND with Detection |
| LEC | Response | `lec_resp_loss_reduction` | Loss Reduction | Reduction of lost economic value (currency) | §3.3.3 | 20 | weak AND (with Event Termination, Resilience); AND with Detection |
| VMC | Variance Prevention | `vmc_prev_reduce_change_freq` | Reduce Change Frequency | % reduction in frequency of changes | §4.1.1 | 23 | OR (with Reduce Variance Probability) |
| VMC | Variance Prevention | `vmc_prev_reduce_variance_prob` | Reduce Variance Probability | % reduction in probability of control degradation | §4.1.2 | 24 | OR (with Reduce Change Frequency) |
| VMC | Variance Identification | `vmc_id_threat_intelligence` | Threat Intelligence | Elapsed time between threat landscape change and awareness | §4.2.1 | 25-26 | AND with Variance Correction |
| VMC | Variance Identification | `vmc_id_control_monitoring` | Control Monitoring | Elapsed time between control variance and recognition | §4.2.2 | 26-27 | AND with Variance Correction |
| VMC | Variance Correction | `vmc_corr_treatment_selection` | Treatment Selection and Prioritization | Elapsed time from variance identification to corrective action start | §4.3.1 | 28 | AND (internally with Implementation); AND with Identification |
| VMC | Variance Correction | `vmc_corr_implementation` | Implementation | Elapsed time from corrective action start to completion | §4.3.2 | 28-29 | AND (internally with Treatment Selection); AND with Identification |
| DSC | Misaligned Decision Prevention | `dsc_prev_defined_expectations` | Defined Expectations (Defined Expectations and Objectives in §5.1 diagram) | Probability clear expectations have been defined | §5.1.1 | 36-37 | AND (with all other §5.1 sub-functions); AND with §5.2 |
| DSC | Misaligned Decision Prevention | `dsc_prev_communication` | Communication of Expectations (Understanding of Expectations and Objectives in §5.1 diagram) | Probability expectations have been communicated to decision-makers | §5.1.2 | 37-38 | AND (with all other §5.1 sub-functions) |
| DSC | Misaligned Decision Prevention / Situational Awareness / Data | `dsc_prev_sa_data_asset` | Provide Asset Data | Probability asset data used in a decision is accurate | §5.1.3.1.1 | 38-39 | AND (within §5.1) |
| DSC | Misaligned Decision Prevention / Situational Awareness / Data | `dsc_prev_sa_data_threat` | Provide Threat Data | Probability threat data used in a decision is accurate | §5.1.3.1.2 | 40 | AND (within §5.1) |
| DSC | Misaligned Decision Prevention / Situational Awareness / Data | `dsc_prev_sa_data_controls` | Provide Controls Data | Probability controls-related data used in a decision is accurate | §5.1.3.1.3 | 41 | AND (within §5.1) |
| DSC | Misaligned Decision Prevention / Situational Awareness | `dsc_prev_sa_analysis` | Analysis | Probability analysis model generates accurate results given accurate data | §5.1.3.2 | 42-43 | AND (within §5.1) |
| DSC | Misaligned Decision Prevention / Situational Awareness | `dsc_prev_sa_reporting` | Reporting | Probability useful analysis results are provided to decision-makers in time | §5.1.3.3 | 43-44 | AND (within §5.1) |
| DSC | Misaligned Decision Prevention | `dsc_prev_ensure_capability` | Ensure Capability (Authority in §5.1 diagram — diagram shorthand for skills, authority, and resources; §5.1.4, page 44) | Probability responsible persons have skills and resources to act in alignment | §5.1.4 | 44-45 | AND (with all other §5.1 sub-functions) |
| DSC | Misaligned Decision Prevention | `dsc_prev_incentives` | Incentives | Probability appropriate incentives are in place for well-aligned decisions | §5.1.5 | 45-46 | AND (with all other §5.1 sub-functions) |
| DSC | Identifying Misaligned Decisions | `dsc_id_misaligned` | Identifying Misaligned Decisions | Elapsed time from misaligned decision to its identification | §5.2 | 47-48 | AND with §5.3 Correcting; AND with §5.1.1 Defined Expectations |
| DSC | Correcting Misaligned Decisions | `dsc_corr_misaligned` | Correcting Misaligned Decisions (VIRTUAL) | Elapsed time from recognition of misaligned decision to correction | §5.3 | 49-50 | AND with §5.2 Identifying; VIRTUAL — no distinct controls |

**Total: 26 sub-functions across 3 domains (25 with distinct controls + 1 virtual).**

### 3.1 LEC — 9 sub-functions

The LEC domain divides into three function groups with specific Boolean coupling. Prevention (§3.1) is an OR-trio: any one of Avoidance, Deterrence, or Resistance suffices for the prevention group to function. This reflects the reality that different prevention mechanisms operate independently — an attacker may bypass Avoidance but still be deterred, or bypass Deterrence but still be resisted.

Detection (§3.2) is a strict AND-trio. Visibility without Monitoring produces data no one reviews. Monitoring without Visibility has nothing to review. Recognition without either of those cannot distinguish anything. Detection as a group is also in an AND relationship with Response (§3.2, page 14) — Response cannot occur without Detection having functioned. This is the most consequential Boolean coupling in the Standard because it means a zero-effectiveness Visibility control collapses Detection to zero regardless of how good Monitoring and Recognition are.

Response (§3.3) uses a deliberate "weak AND" for its three sub-functions. Event Termination, Resilience, and Loss Reduction are all desirable but a deficiency in one does not necessarily prevent the others from providing value. For example, poor Event Termination still allows Loss Reduction measures (like invoking cyber insurance) to function. The Standard flags this explicitly to prevent modelers from applying strict AND semantics to Response and generating unrealistically pessimistic results.

The Standard's verbatim definition of Event Termination (§3.3.1, page 18): "The amount of time that expires between recognition that a loss event has occurred and the point at which control over the event has been achieved."

### 3.2 VMC — 6 sub-functions

The VMC domain's structure mirrors the LEC domain's Prevention/Detection/Response triad but operates on control variance rather than loss events. Variance Prevention (§4.1) is an OR-pair: reducing change frequency and reducing variance probability are independent mechanisms, either of which contributes. Variance Identification (§4.2) and Variance Correction (§4.3) are in a strict AND relationship with each other — identification without correction leaves variance unaddressed, and correction without identification is impossible.

Within Variance Correction, Treatment Selection and Implementation are themselves in a strict AND relationship: selecting the right treatment without implementing it achieves nothing, and implementing without selection produces random corrective actions.

All six VMC sub-functions have elapsed-time units (except the two Prevention sub-functions, which are % reduction). This makes VMC the domain most resistant to probability-based composition algebra — time-to-awareness and time-to-correction cannot be multiplied with probabilities to produce a meaningful scalar.

### 3.3 DSC — 11 sub-functions (10 with distinct controls + 1 virtual)

The DSC domain is the most structurally complex, with three-level nesting under Provide Situational Awareness. All five §5.1 sub-function groups (Defined Expectations, Communication, Provide Situational Awareness, Ensure Capability, Incentives) are in a strict AND relationship with each other — a deficiency in any one increases the probability of misaligned decisions. The Provide Situational Awareness function (§5.1.3) is itself composite, containing Provide Data (§5.1.3.1) and its three children (Asset Data, Threat Data, Controls Data), plus Analysis (§5.1.3.2) and Reporting (§5.1.3.3). This three-level nesting lives in the composition rules, not in the flat `FairCamSubFunction` enum.

Identifying Misaligned Decisions (§5.2) and Correcting Misaligned Decisions (§5.3) are in a strict AND relationship. Defined Expectations (§5.1.1) is also in an AND relationship with Identifying — you cannot identify misalignment without having defined what alignment looks like.

Correcting Misaligned Decisions (§5.3) is a VIRTUAL function. The Standard states (page 50): "there are no distinct controls that serve this function" — it is "wholly dependent upon those other functions" within Variance Correction or LEC Response. The slug `dsc_corr_misaligned` appears in the enum for completeness, but no distinct control should be assigned to it. PR ι should enforce this with a model-level Pydantic validator and a DB CHECK constraint as a backstop. [interpretation, not direct citation]: the Standard does not specify which other-domain function fulfils the virtual role in any given scenario; that mapping is context-specific.

**Forward-compat note on computed virtuals**: The schema should reserve a `derived_from_assignment_id: UUID NULLABLE` column on `ControlFunctionAssignment`. This enables future computed-virtual assignments where effectiveness for `dsc_corr_misaligned` is derived from a referenced LEC Response or VMC Variance Correction assignment — without requiring a schema migration. The enforcement constraint becomes: `sub_function != 'dsc_corr_misaligned' OR derived_from_assignment_id IS NOT NULL`. Decision deferred to PR ι plan-gate: ship the column reserved-but-unused now, or commit to no-virtual-rows-ever and revisit if needed?

**§3.3 vs §10.1.3 reconciliation**: Section §10.1.3 previously listed virtual-function enforcement as an open question. That question is resolved here: enforcement is required via Pydantic validator plus DB CHECK constraint (with the derived_from carve-out above). §10.1.3 is updated accordingly.

---

## 4. v3 Phase 1.2 Control entity — conformance audit

### 4.1 Schema (alembic 355450b21719, 2026-04-25)

Source: `alembic/versions/355450b21719_phase_1_2_controls.py`.

The `controls` table was created with the following columns:

```
name                    VARCHAR(255)     NOT NULL
description             TEXT             NULLABLE
domain                  -- Removed in issue #90; derived at access time as `Control.domains: frozenset[ControlDomain]`
function                ENUM             NOT NULL  (PREVENTIVE, DETECTIVE, CORRECTIVE, COMPENSATING)
type                    ENUM             NOT NULL  (TECHNICAL, ADMINISTRATIVE, PHYSICAL)
control_strength        FLOAT            NOT NULL
control_reliability     FLOAT            NOT NULL
control_coverage        FLOAT            NOT NULL
cost_model              JSON             NOT NULL
nist_csf_functions      JSON             NOT NULL
iso_27001_domains       JSON             NOT NULL
compliance_mappings     JSON             NOT NULL
skill_requirements      JSON             NOT NULL
technology_dependencies JSON             NOT NULL
applicable_industries   JSON             NOT NULL
applicable_org_sizes    JSON             NOT NULL
status                  ENUM             NOT NULL  (DRAFT, ACTIVE, DEPRECATED, DELETED)
version                 VARCHAR(32)      NOT NULL
created_by              UUID             NULLABLE  FK → users.id ON DELETE SET NULL
id                      UUID             NOT NULL  PK
created_at              TIMESTAMPTZ      NOT NULL  default CURRENT_TIMESTAMP
updated_at              TIMESTAMPTZ      NOT NULL  default CURRENT_TIMESTAMP
organization_id         UUID             NOT NULL  FK → organizations.id ON DELETE RESTRICT
```

### 4.2 Field-by-field conformance mapping

| v3 column | Type | Standard conformance | Citation | Notes |
|-----------|------|---------------------|----------|-------|
| `name` | VARCHAR(255) | Standard-orthogonal | — | Not in Standard; valid auxiliary identifier |
| `description` | TEXT | Standard-orthogonal | — | Not in Standard; valid auxiliary |
| `domain` | _Removed in issue #90_ | n/a (no longer a column) | §2.2, page 5 | Single-valued `domain` column dropped. v3 now derives `Control.domains: frozenset[ControlDomain]` at query time via `subfunction_to_domain()` applied to each `ControlFunctionAssignment.sub_function`, aligning with the Standard's per-sub-function domain placement and making multi-domain controls first-class. Deviation γ (string-value differences vs. fair_cam) still applies wherever domain values cross the v3 ↔ fair_cam boundary. |
| `function` | ENUM (PREVENTIVE / DETECTIVE / CORRECTIVE / COMPENSATING) | **Standard-deviant** | §3.1-3.3, §4.1-4.3, §5.1-5.3 | These are classical IT security control action types from ISO/NIST textbooks, orthogonal to the Standard's 26 sub-functions. The Standard enumerates no such classification. See Deviation β (§4.3.2). |
| `type` | ENUM (TECHNICAL / ADMINISTRATIVE / PHYSICAL) | Standard-orthogonal | — | Not in Standard; common IT security taxonomy; valid auxiliary |
| `control_strength` | FLOAT | **Standard-deviant** | §2.4, page 6; §3.2, page 14; §5.1.3.1.3, page 41 | One scalar for "strength" collapses the three independent attributes (Capability, Coverage, Reliability) and conflates them across all sub-functions a control fulfills. See Deviation α (§4.3.1). |
| `control_reliability` | FLOAT | **Standard-deviant** | §2.4.3, page 7 | Reliability is sub-function-specific under the Standard. A flat scalar ignores that reliability differs per function assignment and cannot represent the multi-function pattern. See Deviation α. |
| `control_coverage` | FLOAT | **Standard-deviant** | §2.4.2, pages 6-7 | Coverage is sub-function-specific. A flat scalar cannot represent differentiated coverage across the sub-functions a control fulfills. See Deviation α. |
| `cost_model` | JSON | Standard-orthogonal | — | Not in Standard; valid financial auxiliary |
| `nist_csf_functions` | JSON list | Standard-orthogonal | — | Framework mapping; not in Standard |
| `iso_27001_domains` | JSON list | Standard-orthogonal | — | Framework mapping; not in Standard |
| `compliance_mappings` | JSON | Standard-orthogonal | — | Framework mapping; not in Standard |
| `skill_requirements` | JSON list | Standard-orthogonal | — | Operational metadata; not in Standard |
| `technology_dependencies` | JSON list | Standard-orthogonal | — | Operational metadata; not in Standard |
| `applicable_industries` | JSON list | Standard-orthogonal | — | Scoping metadata; not in Standard |
| `applicable_org_sizes` | JSON list | Standard-orthogonal | — | Scoping metadata; not in Standard |
| `status` | ENUM (DRAFT/ACTIVE/DEPRECATED/DELETED) | Standard-orthogonal | — | Lifecycle metadata; not in Standard |
| `version` | VARCHAR(32) | Standard-orthogonal | — | Versioning metadata; not in Standard |
| `created_by` | UUID FK | Standard-orthogonal | — | Audit metadata; not in Standard |
| `id` | UUID PK | Standard-orthogonal | — | Persistence identifier; not in Standard |
| `organization_id` | UUID FK | Standard-orthogonal | — | Multi-tenancy guard; not in Standard |
| `created_at`, `updated_at` | TIMESTAMPTZ | Standard-orthogonal | — | Audit timestamps; not in Standard |

**Summary: 0 Standard-conformant columns (the previously-conformant `domain` column was removed in issue #90; v3 now derives the multi-domain set `Control.domains: frozenset[ControlDomain]` per the Standard's per-sub-function placement, §2.2 page 5), 4 Standard-deviant columns (`function`, `control_strength`, `control_reliability`, `control_coverage`), 17 Standard-orthogonal columns.**

### 4.3 Two deviations summary

#### 4.3.1 Deviation α — Effectiveness shape

The v3 `controls` table stores effectiveness as a flat `(control_strength, control_reliability, control_coverage)` triple at the Control entity level. This deviates from the Standard in two compounding ways.

First, the Standard defines Capability, Coverage, and Reliability as attributes of a specific sub-function assignment, not of a control in the abstract (§2.4, page 6). A Next-Generation Firewall has different capability for Resistance (§3.1.3) than it does for Visibility (§3.2.1) — these are fundamentally different operational functions and the Standard treats them as independently measurable.

Second, the Standard explicitly treats multi-function controls as the norm (§3.2, page 14; §5.1.3.1.3, page 41). Storing one triple per Control entity cannot represent a control that simultaneously fulfills Resistance with 0.9 capability, Visibility with 0.7 capability, and Monitoring with 0.5 capability — each requiring separate coverage and reliability assessments.

The practical effect of Deviation α is that the v3 adapter in `src/idraa/services/run_executor.py:70-82` passes a single scalar through to fair_cam's composition formula, which then applies it uniformly regardless of which sub-function is being evaluated. This understates effectiveness for high-performing sub-functions and overstates it for weaker ones.

#### 4.3.2 Deviation β — Function taxonomy

The `ControlFunction` enum (defined in `src/idraa/models/enums.py:28-33`) contains four values: PREVENTIVE, DETECTIVE, CORRECTIVE, COMPENSATING. These are classical IT security control action classifications drawn from ISO 27001 and NIST frameworks — they describe a control's temporal relationship to a security event (prevents before, detects during, corrects after, compensates for a gap).

The Standard's taxonomy is entirely different. It defines 26 sub-functions organized by functional domain, Boolean composition, and unit of measurement (§3-§5). "Detective" maps loosely to LEC Detection (§3.2), but "Preventive" conflates LEC Prevention (§3.1) with VMC Variance Prevention (§4.1) and has no mapping to the nine DSC sub-functions. "Corrective" loosely maps to LEC Response (§3.3) and VMC Variance Correction (§4.3) but not distinctly. "Compensating" has no Standard mapping at all.

The current adapter (`src/idraa/services/run_executor.py:70-82`) silently drops v3's `function` value — it is not carried through to fair_cam because fair_cam's own `ControlFunction` enum (an Overview-era 9-value set) also does not map to v3's 4-value classical taxonomy. The result is that function classification, despite being a non-nullable column, contributes nothing to risk calculations.

---

## 5. fair_cam library Control — conformance audit

### 5.1 Schema (`fair_cam/models/control.py:239-300`)

The `Control` dataclass is defined at `fair_cam/models/control.py:238`. Its fields are:

```python
control_id: str
name: str
description: str
domain: ControlDomain           # LOSS_EVENT / VARIANCE_MANAGEMENT / DECISION_SUPPORT
control_function: ControlFunction  # Overview-era 9-value enum
fair_cam_mappings: List[FairCamMapping]
control_type: ControlType       # conflated enum (see §5.2)
control_strength: float
control_reliability: float
control_coverage: float
nist_mappings: List[str]
cis_mappings: List[str]
iso27001_mappings: List[str]
depends_on: List[str]
enables: List[str]
dependencies: List[ControlDependency]
cost_model: CostModel
implementation_complexity: ComplexityLevel
response_time_seconds: float
recovery_time_hours: float
degradation_rate: float
effectiveness_metrics: List[EffectivenessMetric]
performance_metrics: List[PerformanceMetric]
performance_baseline: Optional[PerformanceBaseline]
performance_gaps: List[PerformanceGap]
last_performance_review: Optional[datetime]
next_performance_review: Optional[datetime]
performance_status: str
variance_managed_by: List[str]
variance_manager_for: List[str]
failure_frequency_per_year: float
mean_time_to_recovery_hours: float
availability_target: float
created_date: datetime
last_updated: datetime
tags: List[str]
```

### 5.2 Field-by-field conformance mapping

| fair_cam field | Type | Standard conformance | Citation | Notes |
|----------------|------|---------------------|----------|-------|
| `control_id` | str (UUID) | Standard-orthogonal | — | Persistence identifier |
| `name`, `description` | str | Standard-orthogonal | — | Valid auxiliary |
| `domain` | ControlDomain enum | **Standard-conformant** | §2.2, page 5 | Correct three-domain ontology. BUT: string values are `"loss_event"` / `"variance"` / `"decision"` — the truncated "variance" and "decision" diverge from v3's `"variance_management"` / `"decision_support"`, requiring the explicit `_DOMAIN_MAP` in the adapter. |
| `control_function` | ControlFunction enum (9 values) | **Standard-deviant** | §3.1-3.3, §4.1-4.3, §5.1-5.3 | THREAT_PREVENTION / VULNERABILITY_REDUCTION / IMPACT_MITIGATION (LEC); PERFORMANCE_MONITORING / CONFIGURATION_MANAGEMENT / MAINTENANCE_SCHEDULING (VMC); RISK_VISIBILITY / COMPLIANCE_REPORTING / STRATEGIC_PLANNING (DSC). These are Overview-era domain summaries, not Standard sub-functions. Three values per domain is a 3:9+ mismatch with the Standard's taxonomy. |
| `fair_cam_mappings` | List[FairCamMapping] | **Standard-deviant** | §3, §4, §5 throughout | FairCamMapping enum contains FAIR-axis concepts (CONTACT_FREQUENCY, PROBABILITY_OF_ACTION, etc.) rather than Standard sub-function identifiers. Partially overlaps Standard intent but uses a different vocabulary. |
| `control_type` | ControlType enum | **Standard-deviant** | — | Conflates classical action types (PREVENTIVE/DETECTIVE/CORRECTIVE) with implementation types (ADMINISTRATIVE/TECHNICAL/PHYSICAL) into one enum. The Standard uses neither; this enum has six values and mixes two orthogonal taxonomies. |
| `control_strength` | float | **Standard-deviant** | §2.4, page 6 | Same Deviation α as v3: single flat scalar for "strength" cannot represent sub-function-specific Capability. |
| `control_reliability` | float | **Standard-deviant** | §2.4.3, page 7 | Flat scalar cannot represent sub-function-specific Reliability. |
| `control_coverage` | float | **Standard-deviant** | §2.4.2, pages 6-7 | Flat scalar cannot represent sub-function-specific Coverage. |
| `nist_mappings`, `cis_mappings`, `iso27001_mappings` | List[str] | Standard-orthogonal | — | Framework mappings; valid auxiliary |
| `depends_on`, `enables`, `dependencies` | List[str] / List[ControlDependency] | Standard-orthogonal | — | Dependency tracking; partially motivated by Standard §2.3 universal dependencies but implemented as explicit rather than structural |
| `cost_model` | CostModel | Standard-orthogonal | — | Financial auxiliary |
| `implementation_complexity` | ComplexityLevel | Standard-orthogonal | — | Operational metadata |
| `response_time_seconds`, `recovery_time_hours` | float | Partially Standard-motivated | §3.3.1-3.3.2 | LEC Response sub-functions (Event Termination, Resilience) are time-measured. These fields capture that spirit but are flat scalars on the Control entity rather than per-assignment values. |
| `degradation_rate` | float (daily decay) | Standard-deviant | §2.4.3, page 7 | Reliability degradation is a valid modeling concept but implemented as a universal rate on the whole Control at `fair_cam/models/control.py:276`, not per sub-function. |
| `failure_frequency_per_year`, `mean_time_to_recovery_hours`, `availability_target` | float | VMC-motivated | §4.2.2, §4.3.2 | Capture VMC Identification/Correction time concepts in an approximate way. |
| Performance, variance, review fields | various | Standard-orthogonal | — | Operational monitoring fields; not in Standard |

### 5.3 Deviations

fair_cam shares Deviation α (flat effectiveness triple) and Deviation β (non-Standard function taxonomy) with v3 in a structurally identical way. Additionally, fair_cam introduces two further deviations:

**Deviation γ — Domain string values**: fair_cam stores `ControlDomain.VARIANCE_MANAGEMENT` as the string `"variance"` and `ControlDomain.DECISION_SUPPORT` as the string `"decision"` (`fair_cam/models/control.py:16-25`). v3 stores its corresponding StrEnum values as `"variance_management"` and `"decision_support"`. Same conceptual domain, different string serialization. The adapter handles this with the explicit `_DOMAIN_MAP` lookup. PR κ should normalize fair_cam's strings to match v3.

**Deviation δ — Conflated ControlType enum**: fair_cam's `ControlType` enum (`fair_cam/models/control.py:60-67`) conflates classical security action types (PREVENTIVE, DETECTIVE, CORRECTIVE) with implementation types (ADMINISTRATIVE, TECHNICAL, PHYSICAL) into one six-value enum. These are orthogonal taxonomies. Post-PR ι, v3 retains only the implementation-type axis (`ControlType` at `src/idraa/models/enums.py:28-31` with TECHNICAL/ADMINISTRATIVE/PHYSICAL); the classical-action `ControlFunction` enum was dropped on the rationale that no v3 surface consumed it. PR κ should likewise reduce fair_cam's six-value `ControlType` to the three implementation values, dropping the classical-action values entirely to align with v3.

### 5.4 Found design decision: TWO live composition formulas

The Standard leaves the composition formula to implementations (§2.4, page 6). fair_cam has not made a single concrete choice — it has two live formulas that produce materially different numbers for the same inputs. Neither formula is called from the other; they exist in separate code paths.

**Formula 1 — Multiplicative** (`fair_cam/models/control.py:316-319`):

```python
def calculate_risk_reduction_factor(self) -> float:
    current_effectiveness = self.get_current_effectiveness()
    return current_effectiveness * self.control_reliability * self.control_coverage
```

Where `get_current_effectiveness()` (`fair_cam/models/control.py:308-314`) applies daily degradation:

```python
def get_current_effectiveness(self) -> float:
    days_since_update = (datetime.now() - self.last_updated).days
    degraded_effectiveness = self.control_strength * (1 - (self.degradation_rate * days_since_update))
    return max(0.0, min(1.0, degraded_effectiveness))  # clamped to [0, 1] at line 314
```

Full composition: `risk_reduction = control_strength × (1 - degradation_rate × days) × control_reliability × control_coverage`.

**Call path for Formula 1**: called from `fair_cam/excel/excel_processor.py:518` (Excel output) and `fair_cam/visualization/advanced_plots.py:118` (visualization). **Not called from the risk engine.**

**Formula 2 — Weighted additive** (`fair_cam/controls/effectiveness.py:24-38`):

```python
# ControlEffectivenessCalculator.calculate_base_effectiveness()
base = strength * 0.4 + reliability * 0.4 + coverage * 0.2
# then scaled by (current_effectiveness / strength)
```

**Call path for Formula 2**: called from `fair_cam/risk_engine/control_aware.py:43, 142`. **This is the formula the Monte Carlo simulation actually uses.**

**Numerical divergence at sample inputs** — for `(strength=0.9, reliability=0.5, coverage=0.5)`:
- Formula 1 (multiplicative): `0.9 × 0.5 × 0.5 = 0.225`
- Formula 2 (weighted additive): `0.9 × 0.4 + 0.5 × 0.4 + 0.5 × 0.2 = 0.36 + 0.20 + 0.10 = 0.66`

The two formulas differ by approximately **3×** at this sample input. The divergence is not a rounding artefact — it is structural. The Excel processor and visualization layer report effectiveness numbers that are different from what the Monte Carlo simulation engine uses for the same Control object.

**Additional finding — `roi_analyzer.py`**: `fair_cam/analytics/roi_analyzer.py:150, 278, 395` calls something named `calculate_base_effectiveness` as a method on `Control` directly. The `Control` dataclass has no such method — `calculate_base_effectiveness` is a method on `ControlEffectivenessCalculator`, not on `Control`. This is either a latent bug in fair_cam (dead code path, or a method that existed and was removed) or the file reference is incorrect. **Flagged for PR κ to verify and fix if real.**

**PR κ scope finding**: One formula must be excised or both must be reconciled with explicit separation. The Monte Carlo path (Formula 2) is the one that affects risk numbers; the Excel/visualization path (Formula 1) affects reports. Both paths feeding different numbers from the same Control is a correctness problem regardless of which formula is ultimately preferred. Decision deferred to PR κ plan-gate and paranoid review (see §10.2).

---

## 6. v3↔fair_cam adapter — current behavior

Source: `src/idraa/services/run_executor.py:58-82`.

The function `_v3_to_fair_cam_control` performs the following translations:

| v3 field | fair_cam field | Translation |
|----------|---------------|-------------|
| `v3_ctrl.id` (UUID) | `control_id` (str) | `str(v3_ctrl.id)` — UUID to string |
| `v3_ctrl.name` | `name` | Identity |
| `v3_ctrl.domain` (ControlDomain StrEnum) | `domain` (ControlDomain Enum) | Explicit `_DOMAIN_MAP` lookup (necessary due to Deviation γ — string values differ) |
| `v3_ctrl.type` (ControlType StrEnum) | `control_type` (ControlType Enum) | `FairCamControlType(v3_ctrl.type.value)` — requires matching string values; works because both enums share "technical"/"administrative"/"physical" |
| `v3_ctrl.control_strength` | `control_strength` | Identity (float pass-through) |
| `v3_ctrl.control_reliability` | `control_reliability` | Identity (float pass-through) |
| `v3_ctrl.control_coverage` | `control_coverage` | Identity (float pass-through) |
| `v3_ctrl.cost_model` (dict) | `cost_model` (CostModel dataclass) | Replaced with `FairCamCostModel()` default — v3 cost data is lost in translation |
| _(not mapped)_ | `response_time_seconds` | `_response_time_default(v3_ctrl.domain)` — heuristic: LEC=60s, VMC=60s, DSC=3600s |
| _(not mapped)_ | `recovery_time_hours` | Hardcoded `1.0` |
| _(not mapped)_ | `degradation_rate` | Hardcoded `0.0` (no decay in Phase 1) |

**Silent drop**: `v3_ctrl.function` (the PREVENTIVE/DETECTIVE/CORRECTIVE/COMPENSATING value) is NOT carried to fair_cam. This is the correct behavior given Deviation β — fair_cam's `ControlFunction` enum contains Overview-era values (THREAT_PREVENTION etc.) that do not map to v3's classical taxonomy. The adapter omits the field and lets fair_cam use its default (`ControlFunction.THREAT_PREVENTION`). The practical effect is that `control_function` on the fair_cam side always defaults regardless of the v3 value set.

**Run-time snapshot**: `_snapshot_control` at `src/idraa/services/run_executor.py:181-192` captures `control_strength`, `control_reliability`, `control_coverage`, `domain`, `function`, and `type` into the run's JSON audit record. Historical snapshots in `risk_analysis_runs` are written with this shape. After PRs ι/κ, the snapshot writer needs updating to capture per-assignment effectiveness values; historical snapshots are preserved unchanged (they are immutable audit records). See §9.5 for affected surfaces.

---

## 7. Re-shape proposals

### 7.1 PR ι (Phase 1.5b-α) — v3 schema reshape

#### 7.1.1 New tables and enums — candidate schema (plan-gate decides four open questions)

The schema described here is a **candidate**. Four architectural decisions are explicitly deferred to the PR ι plan-gate; they are bracketed below and cross-referenced to §10.1.

**`FairCamSubFunction` enum** (26 values): The slugs in the table in §3 are the authoritative naming convention — `lec_prev_avoidance`, `lec_det_visibility`, `vmc_id_control_monitoring`, `dsc_prev_defined_expectations`, etc. The full list is the 26 rows in §3.

**`ControlFunctionAssignment` join table**: One row per (control, sub-function) pair. Candidate fields:

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `control_id` | UUID FK → controls.id ON DELETE CASCADE | |
| `sub_function` | FairCamSubFunction enum NOT NULL | Standard §3-§5 sub-function slug |
| `capability_value` | FLOAT NOT NULL | The primary effectiveness scalar; units per §3 table |
| `coverage` | FLOAT NOT NULL [0.0, 1.0] | [M1: see coverage placement decision below] |
| `reliability` | FLOAT NOT NULL [0.0, 1.0] | Probability control performs consistently as designed |
| `confirmed_by_user_at` | TIMESTAMPTZ NULLABLE | NULL means backfilled, not yet human-confirmed (see §7.1.4) |
| `derived_from_assignment_id` | UUID NULLABLE FK → control_function_assignments.id | Reserved for computed-virtual assignments (see §3.3) |
| `measured_at` | TIMESTAMPTZ NULLABLE | When this effectiveness was last measured or assessed |
| `measured_by` | UUID NULLABLE FK → users.id ON DELETE SET NULL | [m4: see below — depends on whether measurement is human-entered or system-computed] |
| `organization_id` | UUID NOT NULL | Denormalized for query performance |
| `created_at`, `updated_at` | TIMESTAMPTZ | Audit timestamps |

**VF/VD columns are NOT included in PR ι.** Per-assignment variance modeling (`variance_freq_per_year`, `variance_duration_days`) is deferred to Phase 2 when the schema and risk engine can support it coherently. See §10.3.

**Four decisions deferred to PR ι plan-gate:**

**M1 — Coverage placement**: The Standard's §2.4.2 firewall example (pages 6-7) discusses coverage as control-level deployment breadth ("deployed on all internet-facing assets"). The candidate schema places `coverage` on the assignment. Assignment-level placement enables per-sub-function coverage variation (e.g., a NGFW's IPS function covers 100% of assets but its logging covers 60%). Control-level placement is simpler and matches the Standard's framing for the worked example. Candidate placements: (a) assignment-level as shown above, (b) control-level column on `controls` table. Decision deferred to PR ι plan-gate.

**M2 — VF/VD placement**: Dropped from PR ι entirely. When Phase 2 adds variance episode modeling, the decision of whether VF/VD belongs at the control level (a NGFW outage affects all sub-functions simultaneously) or the assignment level (per-function variance rates) should be made with the risk engine's requirements in hand. A separate `control_variance_episode` table is a third candidate. Decision deferred to Phase 2.

**M4 — Unique constraint on `(control_id, sub_function)`**: A unique constraint enforces one assignment per sub-function per control. The Standard §2.4.2 page 7 speaks of coverage variation across assets/scenarios — this might imply multiple rows for per-asset coverage profiles. If historical tracking of assessment snapshots is desired, the unique constraint conflicts. Candidate approaches: (a) unique constraint as stated, (b) add a `version: int` to the constraint key, (c) no unique constraint and manage at the application layer. Decision deferred to PR ι plan-gate.

**M5 — ScenarioControl granularity**: The current `ScenarioControl(scenario_id, control_id)` join attaches all of a Control's assignments when a Control is added to a scenario. If a Control has five assignments, does picking the Control in a scenario activate all five? Or should the join migrate to `ScenarioControl(scenario_id, assignment_id)` to allow per-sub-function scenario attachment? This has PR ι schema impact either way. Decision deferred to PR ι plan-gate.

**m4 — `measured_by` pairing**: Whether to include `measured_by: UUID FK → users.id` alongside `measured_at` depends on whether measurement is human-entered (yes, pair them, consistent with `created_by`/`updated_by` pattern) vs. system-computed (no — stamp automatically). Decision deferred to PR ι plan-gate.

**`ControlFunction` enum disposition**: The existing `ControlFunction` enum (PREVENTIVE/DETECTIVE/CORRECTIVE/COMPENSATING) must be renamed `ClassicalControlAction` and the column on the `controls` table renamed accordingly. This preserves the information for users who think in classical terms while making clear it is not a Standard-conformant taxonomy. Alternatively, it may be dropped if no active feature consumes it. The decision is left to the PR ι plan gate with the following input: the current adapter (`_v3_to_fair_cam_control`) does NOT carry the value through, and no template or report currently surfaces it.

#### 7.1.2 `controls` table changes

Columns to drop from `controls`:
- `control_strength` (effectiveness moves to `control_function_assignments`)
- `control_reliability` (same)
- `control_coverage` (same)

Column to rename or drop:
- `function` (PREVENTIVE/DETECTIVE/CORRECTIVE/COMPENSATING) → rename to `classical_control_action` or drop; see §7.1.1 above.

Coverage placement (M1 above): if plan-gate selects control-level coverage, add `coverage FLOAT NOT NULL [0.0, 1.0]` to `controls` at the same migration.

All other columns remain unchanged.

#### 7.1.3 Adapter behavior after PR ι — transitional bridge

Until PR κ reshapes fair_cam, `_v3_to_fair_cam_control` must continue producing a `FairCamControl` with the flat triple. The adapter will pick a representative assignment to populate `(control_strength, control_reliability, control_coverage)` for fair_cam consumption. Selection rule: use the assignment whose `sub_function` maps most directly to the control's `domain` — for `LOSS_EVENT`, prefer `lec_prev_resistance`; for `VARIANCE_MANAGEMENT`, prefer `vmc_id_control_monitoring`; for `DECISION_SUPPORT`, prefer `dsc_prev_sa_analysis`. Fall back to the highest `capability_value` assignment within the domain.

**The numerical values produced by this bridge are transitional artifacts and must not be trusted for risk calculations during the PR ι → PR κ window.** The selection rule is domain-derived but dimensionally wrong (e.g., VMC prefers `vmc_id_control_monitoring` which has an elapsed-time unit, but this is passed through to fair_cam's `control_strength` which is a [0,1]-bounded scalar). Tests must lock in the selection logic without asserting downstream risk-number stability. Plan-gate decides whether to suppress simulation runs against controls that have only unconfirmed-backfill assignments during this window.

This rule must be documented in code with a `# TODO(PR κ): remove when fair_cam uses per-assignment shape` comment.

#### 7.1.4 Migration backfill

For each existing `Control` row:
1. Create one `ControlFunctionAssignment` row using the existing `control_strength`, `control_reliability`, `control_coverage` values.
2. The `sub_function` for the backfill row is derived from the control's `domain` using the same representative mapping as the adapter: LEC → `lec_prev_resistance`, VMC → `vmc_id_control_monitoring`, DSC → `dsc_prev_sa_analysis`.
3. `measured_at` is set to `null` (historical assessments are not available).
4. `confirmed_by_user_at` is set to `null` — this marks the row as backfilled, not yet human-confirmed.

**Backfill semantics**: Backfilling existing controls into a single representative sub-function silently changes risk numbers. A SIEM imported as LEC and backfilled to `lec_prev_resistance` will contribute to the Prevention OR-trio regardless of whether a human has confirmed that assignment is correct. Backfilled rows with `confirmed_by_user_at = NULL` must be treated by the composition engine in one of two ways — both are valid; plan-gate decides:

- Option (a): exclude unconfirmed assignments from composition entirely until a human confirms them. Safe but may produce zero-contribution results for all existing controls until users confirm.
- Option (b): preserve current numerical behavior (include in composition) but surface a visible warning indicator in the UI and in run reports.

Every backfilled row is flagged unconfirmed. Operations dashboards and the wizard should surface "N controls have unconfirmed sub-function assignments after the schema migration; please review."

The migration is a data migration, not just a DDL migration, and must be written as an Alembic `upgrade()` with both `op.create_table` (new join table) and `op.execute` (backfill INSERT from existing rows). The downgrade must delete `control_function_assignments` rows and restore the three columns on `controls` from the representative assignment.

#### 7.1.5 Files affected

Production files requiring changes:
- `alembic/versions/` — new migration file (1 new file)
- `src/idraa/models/control.py` — drop three fields, add relationship to `ControlFunctionAssignment`
- `src/idraa/models/` — new `control_function_assignment.py` model file
- `src/idraa/models/enums.py` — add `FairCamSubFunction` enum (26 values); rename `ControlFunction` → `ClassicalControlAction` or drop
- `src/idraa/schemas/control.py` — update `ControlForm` Pydantic schema; add `ControlFunctionAssignmentForm`
- `src/idraa/services/controls.py` — update CRUD operations for new shape
- `src/idraa/services/controls_importer.py` — update defaults at lines 93-95; importer now creates assignment rows
- `src/idraa/services/run_executor.py` — update `_v3_to_fair_cam_control` and `_snapshot_control`
- `src/idraa/services/scenario_calibration.py` — called from `run_executor.py:32`; may require updates if it reads control effectiveness directly

Test files requiring changes:
- `tests/conftest.py` — update control fixtures
- `tests/integration/test_controls_crud.py`
- `tests/integration/test_controls_import.py`
- `tests/integration/test_scenario_form_with_controls.py`
- `tests/integration/test_simulation_e2e.py`
- `tests/unit/test_control_model.py`
- `tests/unit/test_controls_service.py`
- `tests/unit/test_run_executor.py`
- `tests/unit/test_run_model.py`
- `tests/unit/test_run_service.py`
- `tests/unit/test_v3_to_fair_cam_control_adapter.py` (127 lines — will shrink significantly when adapter becomes simpler after PR κ; for now tests must cover the representative-assignment selection rule)

#### 7.1.6 Out of scope for PR ι

- fair_cam reshape (deferred to PR κ)
- Control library seed data (deferred to PR λ)
- Control wizard UI (deferred to PR λ); specific templates deferred:
  - `templates/scenarios/form.html:285` — currently renders `control.control_strength`; needs new shape rendering in PR λ
  - `templates/scenarios/wizard/step_4_controls.html` — control picker; UI update is PR λ
  - `templates/controls/*.html` — Control CRUD UI; updates are PR λ's wizard work (PR ι ships schema only)
- Boolean-composition-aware calculation in the risk engine (deferred to PR κ)
- VF/VD columns on `ControlFunctionAssignment` (deferred to Phase 2; see §10.3)

### 7.2 PR κ (Phase 1.5b-β) — fair_cam Standard alignment

#### 7.2.1 fair_cam `Control` reshape

`fair_cam/models/control.py` must be reshaped to use a `ControlFunctionAssignment`-equivalent structure mirroring the v3 PR ι model. The flat `(control_strength, control_reliability, control_coverage)` triple on the `Control` dataclass must move to a `List[ControlFunctionAssignment]` field, where each assignment carries `sub_function: FairCamSubFunction`, `capability_value: float`, `coverage: float`, `reliability: float`.

`fair_cam/data/comprehensive_controls_library.py` must be re-authored. Each entry currently uses the legacy triple with a single `control_function` value from the Overview-era 9-value enum. After the reshape, entries will use a list of `ControlFunctionAssignment` objects tagged with Standard sub-function slugs. The canonical seed source for this re-authoring is `docs/reference/fair-cam-controls-library.csv` (61 control entries with Standard sub-function tags).

`fair_cam/risk_engine/control_aware.py` must update its composition logic. The two live composition formulas documented in §5.4 must be resolved: one must be excised or both must be given explicit, documented separation. This is the primary open question for the PR κ paranoid review (see §10.2).

#### 7.2.2 Adapter simplification

After PR κ, `_v3_to_fair_cam_control` becomes a near-identity translation: both sides use the same per-assignment shape, and the function converts `ControlFunctionAssignment` objects from the v3 ORM type to the fair_cam dataclass type. The representative-assignment selection rule from PR ι is retired. The `_DOMAIN_MAP` translation remains (Deviation γ string values still differ until explicitly normalized in PR κ).

#### 7.2.3 fair_cam test impact

All 137 fair_cam tests are expected to require rewriting against the new per-assignment `Control` shape. `tests/unit/test_v3_to_fair_cam_control_adapter.py` (currently 127 lines) becomes significantly simpler as the complex selection rule is eliminated.

### 7.3 PR λ (Phase 1.5c) — control library and wizard

Full spec is deferred to PR λ's own design document, following the template established by PR θ (Phase 1.5a scenario library, internal design doc 2026-04-28-phase-1.5a-scenario-library-design).

High-level scope:
- `control_library_entries` table (composite PK id+version) with Standard-aligned schema (per-assignment effectiveness).
- Per-org `control_library_overrides` (soft-delete tombstone pattern, mirroring scenario library).
- `source: VARCHAR DEFAULT 'user'` discriminator on both `control_library_entries` and existing `scenario_library_entries` (the latter fixes the F25 alembic downgrade predicate from PR θ).
- 5-step control wizard: library pick → basic info → assignments (per-sub-function effectiveness inline editor) → scope (target scenarios) → review.
- Seed library from `docs/reference/fair-cam-controls-library.csv` (61 entries with Standard sub-function tags; native ingestion because CSV uses Standard nomenclature).

Inline carryovers from PR θ (must land in PR λ):
- Extract `refresh_calibration` from `services/scenarios.py` (746 LOC exceeds 600-line hygiene threshold) to `services/scenario_refresh.py`.
- E2E auth fixtures (`seed_user_login_e2e`, `seed_admin_login_e2e`) restoring 5 skip-marked Playwright tests.

### 7.4 Data contracts at the boundary

The current codebase has Pydantic schemas at `src/idraa/schemas/` for HTTP-form-input boundaries and SQLAlchemy ORM models at `src/idraa/models/` for the persistence schema, but cross-system data contracts are mostly implicit in code. The v3↔fair_cam adapter (§6) constructs a `FairCamControl` field-by-field with no formal input DTO; the `Scenario.library_pin` JSON shape from PR θ §6.9.4 is documented narratively but not Pydantic-validated; the `risk_analysis_runs.snapshot` JSON shape is implicit in `_snapshot_control` writers and consumers (§9.5).

PRs ι/κ/λ traverse three reshape boundaries (v3 schema, fair_cam schema, library→instance projection). Without formal contracts, each consumer rediscovers the implicit shape, and silent drift across consumers becomes a real risk during the migration window. This section enumerates the contracts each PR must codify as Pydantic models alongside its ORM/migration work.

#### 7.4.1 Contract catalog

| Contract | Owning PR | Current state | Formalization target |
|----------|-----------|---------------|---------------------|
| `ControlFunctionAssignmentDTO` | PR ι | Does not exist (table is new) | Pydantic model used by wizard, importer, API, adapter |
| `ControlSnapshot` v2 | PR ι | Implicit dict in `_snapshot_control` | Versioned Pydantic model with `snapshot_version: Literal[2]` discriminator |
| v3→fair_cam adapter input (transitional) | PR ι | Manual field-by-field construction at `services/run_executor.py:70-82` | Pydantic model for the legacy-triple bridge during PR ι→PR κ window |
| v3→fair_cam adapter input (post-reshape) | PR κ | N/A until PR κ | Pydantic model mirroring fair_cam's reshaped `Control`, used as identity pass-through |
| `ControlLibraryEntry` DTO | PR λ | N/A until PR λ | Pydantic model for canonical seed entries; mirrors `ScenarioLibraryEntry` shape from PR θ |
| `ControlLibraryEntry` → `Control` instance projection | PR λ | N/A until PR λ | Pydantic model defining which entry fields project to Control vs to ControlFunctionAssignment rows |
| `Control.library_pin` JSON shape | PR λ | N/A until PR λ | Pydantic model mirroring `Scenario.library_pin` from PR θ §6.9.4 |
| `Scenario.library_pin` JSON shape | (carryover) | Documented narratively in PR θ §6.9.4; not Pydantic-validated | Phase 2 — formalize when audit-grade pin lookup gets per-row validation |

#### 7.4.2 PR ι contracts to codify

**`ControlFunctionAssignmentDTO`** — single source of truth for the per-assignment shape across the wizard, importer, API, and adapter. Sketch:

```python
class ControlFunctionAssignmentDTO(BaseModel):
    sub_function: FairCamSubFunction
    capability_value: float = Field(ge=0.0)  # unit derived from sub_function
    coverage: float = Field(ge=0.0, le=1.0)  # plan-gate decides if this lives here or on Control
    reliability: float = Field(ge=0.0, le=1.0)
    measured_at: datetime | None = None
    measured_by: UUID | None = None  # plan-gate decides pairing — see §10.1.10
    confirmed_by_user_at: datetime | None = None  # null = backfilled, unconfirmed
    derived_from_assignment_id: UUID | None = None  # forward-compat for computed-virtual rows

    @field_validator("sub_function")
    def reject_virtual_unless_derived(cls, v, info):
        if v == FairCamSubFunction.DSC_CORR_MISALIGNED and info.data.get("derived_from_assignment_id") is None:
            raise ValueError("Virtual function dsc_corr_misaligned requires derived_from_assignment_id")
        return v
```

**`ControlSnapshot` v2** — versioned snapshot shape with `Literal` discriminator. Old (v1) snapshots remain readable; new snapshots use v2. Sketch:

```python
class ControlSnapshotV1(BaseModel):
    snapshot_version: Literal[1] = 1
    control_id: str
    name: str
    control_strength: float
    control_reliability: float
    control_coverage: float
    domain: str
    function: str
    type: str

class ControlSnapshotV2(BaseModel):
    snapshot_version: Literal[2] = 2
    control_id: str
    name: str
    domain: str
    type: str
    assignments: list[ControlFunctionAssignmentDTO]

ControlSnapshot = Annotated[
    ControlSnapshotV1 | ControlSnapshotV2,
    Field(discriminator="snapshot_version"),
]
```

#### 7.4.3 PR κ contracts to codify

**v3→fair_cam adapter input (post-reshape)** — when fair_cam's `Control` becomes per-assignment, the adapter becomes near-identity. The contract collapses from a translation function to a Pydantic model that v3 emits and fair_cam accepts directly. The adapter's `_DOMAIN_MAP` translation may remain (Deviation γ — fair_cam's truncated domain string values) or be retired if PR κ normalizes them.

#### 7.4.4 PR λ contracts to codify

**`ControlLibraryEntry` DTO** — canonical seed shape mirroring PR θ's `ScenarioLibraryEntry`. Composite PK `(id, version)`; immutable snapshot semantics. Per-assignment effectiveness embedded as a list of `ControlFunctionAssignmentDTO` instances.

**`ControlLibraryEntry → Control` projection** — the contract a wizard uses to spawn a per-org `Control` from a canonical library entry. Defines which fields project to the `Control` row (name, description, type, framework mappings, costs) vs to `ControlFunctionAssignment` rows (effectiveness values per sub-function). Mirrors PR θ's `ScenarioLibraryService.resolve_for_clone()`.

**`Control.library_pin` JSON shape** — `{entry_id, entry_version, override_id?, override_version?}`. Identical structure to `Scenario.library_pin` from PR θ §6.9.4. Enables hash-stable re-clone of a Control from a pinned library entry version.

#### 7.4.5 Why formalize at the PR boundary, not in this audit

This audit catalogs the contracts but does not codify them. Codification is implementation work — Pydantic models, validators, tests — that belongs in each PR's spec and code. Formalizing here would inflate the audit beyond its load-bearing role (Standard interpretation + deviations + landscape). PR ι/κ/λ specs cite this catalog as the contract checklist their plan-gates must satisfy.

The cost of NOT formalizing is silent drift during the three-PR migration window: the wizard, the importer, the adapter, and the snapshot reader could each interpret the per-assignment shape slightly differently, and the discrepancies surface only when risk numbers diverge across surfaces. Pydantic-codified DTOs catch this at the boundary instead of in production data.

---

## 8. Composition formula options (decision deferred to PR κ plan-gate)

The Standard delegated composition to implementations (§2.4, page 6). v3 and fair_cam must agree on a formula before PR κ ships. The following options enumerate the landscape; no recommendation is made here. The PR κ paranoid review gate owns this decision with a backtesting requirement.

### 8.1 Option A — Multiplicative (current fair_cam Excel/visualization path)

`OpEff(sub_function) = capability × coverage × reliability` per assignment, applied uniformly. This is the formula in `calculate_risk_reduction_factor()` at `fair_cam/models/control.py:316-319`.

**Pros**: Simple; matches existing formula in the Excel and visualization paths; no Boolean-topology awareness required; easy to test and explain. The Standard's only worked example — a firewall deployed on 1-of-4 entrances producing 25% effectiveness (§3.2.1) — is loosely consistent with multiplicative reduction across coverage.

**Cons**: Ignores Boolean composition — an AND-trio where one sub-function scores 0.0 should collapse the group, but multiplicative composition gives a small positive value if the other two are nonzero. Improvements in non-weakest dimensions are invisible under worst-link AND (a separate concern from multiplicative). Ignores unit heterogeneity — Monitoring's elapsed-time unit and Resistance's probability unit do not occupy the same space; applying the same reliability scalar to both is dimensionally incoherent.

### 8.2 Option B — Weighted additive (current fair_cam Monte Carlo path)

`base = capability × 0.4 + reliability × 0.4 + coverage × 0.2`, then scaled by `(current_effectiveness / capability)`. This is the formula in `ControlEffectivenessCalculator.calculate_base_effectiveness()` at `fair_cam/controls/effectiveness.py:24-38`, called from `fair_cam/risk_engine/control_aware.py:43, 142`.

**Pros**: Already implemented in the live Monte Carlo path; weights express a prior that capability and reliability matter equally and more than breadth. Additive composition means partial performance in any attribute still contributes.

**Cons**: Weights (0.4, 0.4, 0.2) are not Standard-derived — they are an implementation choice with no normative basis. Like Option A, ignores Boolean composition topology. The scaling by `(current_effectiveness / capability)` introduces a ratio that can be numerically unstable if `capability → 0`.

### 8.3 Option C — Boolean-composition-aware (operator sub-decision required)

Within each assignment: `effective_value = f(capability, coverage, reliability)` (any of Options A or B above as the intra-assignment formula).

Then apply Boolean composition across assignments:

- For AND groups (Detection trio, Response-Detection pair, VMC Identification-Correction pair, all DSC §5.1 sub-functions): a strict-AND operator. `min(...)` (worst-link) is one valid operator but produces non-monotonic behavior under reliability uncertainty — improvements in non-weakest dimensions are invisible. Multiplicative `product(x_i)` is another operator loosely supported by the Standard's firewall example. The plan-gate must choose.
- For OR groups (Prevention trio, Variance Prevention pair): `1 - product(1 - x_i)` (P(any-succeeds) semantics). This correctly models substitutable prevention mechanisms.
- For weak AND groups (Response trio per §3.3): the operator must be softer than strict AND. Valid candidates include:
  - (a) weighted arithmetic mean `sum(w_i × x_i)`
  - (b) ~~Hamacher t-norm with softening parameter γ > 0~~ — **DISQUALIFIED (PR κ)**.
    T-norms are strict on zeros by axiom (`T(0,y) = 0` for all γ > 0), contradicting
    Standard §3.3 weak-AND boundary semantics ("deficiencies in one diminish overall
    Response efficacy but won't necessarily inhibit it entirely"). Listing this as a
    weak-AND candidate in the audit was a subtle error.
  - (c) ~~weighted complement-product `1 - product((1-x_i)^w_i)`~~ — **DISQUALIFIED (PR κ)**.
    At uniform weights this collapses to `1 - product(1-x_i)`, which is the OR formula.
    Output is ≥ max(x_i) under typical weights — that's weak-OR semantics, not weak-AND.

  Note: `geometric_mean(x_i)` is NOT a correct weak-AND operator — for inputs including 0.0, geometric mean produces 0.0, which is strict-AND behavior, directly contradicting Standard §3.3.1-3.3.2.

**Time-unit sub-functions**: sub-functions with elapsed-time units (Monitoring, Event Termination, Resilience, all VMC/DSC time-measured sub-functions) do not fit the [0,1] probability algebra. These require normalization before participating in Boolean composition. The normalization function (e.g., `p_effective = exp(-λ × elapsed_time)`) is itself implementation-defined. λ values per sub-function category are a sub-decision that must be resolved within this option before it can be used.

**Pros**: Most faithful to the Standard's Boolean topology; correctly models Detection-failure-collapses-Response dependency; differentiates OR prevention from AND detection.

**Cons**: Multiple sub-decisions required before a concrete implementation can be specified; time-unit normalization introduces un-Standard-defined parameters; significantly higher implementation complexity.

### 8.4 Option D — Per-sub-function FAIR-axis decomposition

Each of the 26 sub-functions is statically declared to target a specific FAIR factor. For example: `lec_prev_avoidance` → Contact Frequency multiplier; `lec_prev_resistance` → Vulnerability multiplier; `lec_resp_loss_reduction` → Loss Magnitude subtractor; VMC and DSC effects → reliability modifier on LEC control effectiveness.

fair_cam already implements a partial version of this approach at `fair_cam/models/control.py:374-402` (`get_fair_impact_factor()`), which returns domain-specific FAIR-axis multipliers. This is evidence the option is implementable within the current fair_cam structure.

**Pros**: Most rigorous; directly maps sub-function outputs to FAIR model axes; best alignment with Standard's intent of tracing control effects to FAIR causal nodes; eliminates unit-homogeneity confusion by operating on the appropriate FAIR node.

**Cons**: Requires FAIR-axis bridge logic in fair_cam; pyfair's internal model structure may not expose the necessary hooks for per-node control multipliers; significantly higher complexity for PR κ and all downstream tests; the static FAIR-axis declarations for all 26 sub-functions require normative justification.

---

**Decision deferred to PR κ paranoid review.** The chosen formula must be backtested against at least two reference scenarios with known outcomes (per `project_calibration_framework_review_gate.md`). Whichever option is selected, the time-unit normalization choice (per-sub-function λ values or equivalent) is a sub-decision that must be resolved within the option before implementation begins.

---

### §8.3 decision: PR μ.1 (2026-05-15)

PR μ.1 SELECTS `opeff = exp(-elapsed_time / τ_sf)` for ELAPSED_TIME
sub-functions per the §8.3 Option C sub-decision. Per-sub-function τ
values pinned in `fair_cam.calibration.elapsed_time_taus`; methodology
in `docs/reference/elapsed-time-tau-calibration.md`.

**Honest framing per plan-gate Spec-B1**: this is v3's IMPLEMENTATION
CHOICE, not a Standard recommendation. The §8 preamble explicitly
states "no recommendation is made here." PR μ.1 resolved the open
sub-decision in line with one of the example shapes audit §8.3
enumerates.

CURRENCY sub-function (`lec_resp_loss_reduction`) is handled as a Loss-
Magnitude subtractor on per-event secondary loss per Standard §3.3.3 +
audit §8.4 — NOT as opeff. Implementation in
`fair_cam.controls.effectiveness.calculate_control_risk_adjustment` (the
calculator populates `loss_reduction_per_event` on `ControlAdjustment`)
and `fair_cam.risk_engine.control_aware._apply_control_adjustments`
(single source of truth for the subtractor's ALE effect per plan-gate
Arch-B2).

**Citation scope (honest framing per plan-gate Spec-B1):** §3.3.3 / audit
§8.4 ground the EXISTENCE of a currency subtractor on secondary loss
("reduction of lost economic value (currency)") only. They do NOT
prescribe the specific derivation step of subtracting the same constant
from each PERT support bound. Applying the §3.3.3 subtractor as a
per-bound subtraction on the PERT magnitude support is a v3
implementation choice, not a Standard-derived result. The §8 preamble
explicitly states "no recommendation is made here."

**issue #258 — SUPERSEDES PR μ.1 finding CR-B8/CR-I10 (commits 4391a2a /
07b345a) IN PART.** That gate (Arch-B4/CR-B8/CR-I10) replaced a std-key
denylist with a `MAGNITUDE_KEYS` allowlist `{most_likely, mean, mode,
median}` "to avoid distorting the PERT shape", recording: "Other keys
(low, high, std, sigma, alpha, beta, etc.) receive the multiplier only —
dispersion is preserved." That decision conflated PERT support bounds
(`low`/`high` — dollar amounts on the magnitude axis) with true
dispersion parameters (`stdev`/`sigma`/`alpha`/`beta`).

The original gate optimised for **shape preservation**. New evidence it
did not anticipate: applying the subtractor to `mode` ONLY drives the
adjusted `mode` below `low` for a real PERT secondary loss, which
pyfair's `_check_pert` rejects with a `FairException` (production crash,
issue #258). Under a fixed per-event dollar reduction, **monotonicity
preservation** (`low ≤ mode ≤ high`, the precondition pyfair enforces)
is the binding constraint and supersedes shape preservation.

Post-fix the subtractor is APPLIED to per-event secondary-loss
`SUBTRACTOR_KEYS = MAGNITUDE_KEYS | {low, high}` with a `max(0, …)`
floor: the WHOLE PERT magnitude support (`low`, `mode`, `high`) shifts
leftward by the same constant. A monotone floor of a uniform shift cannot
invert the ordering, so `low ≤ mode ≤ high` is preserved.

- **No-floor regime** (shift < `low`): a pure leftward translation —
  variance and skew preserved (genuine shape preservation).
- **Partial-floor regime** (shift > `low`, < `high`): the left support
  compresses to 0 while the right tail keeps its full shift. This is a
  shape distortion (left-tail compression), NOT a pure translation, and
  it is accepted: a control that eliminates more loss than the low bound
  carries genuinely truncates the small-loss tail.
- **Full-collapse regime** (shift ≥ `high`): the triple floors to
  `{0, 0, 0}`. pyfair's `FairBetaPert._run_range_check` rejects
  `low ≥ high` (strict), so `_build_fair_model` emits a `constant=0`
  Secondary Loss Event Magnitude instead of a degenerate PERT.

**ALE effect:** shifting the whole support down by `total_loss_reduction`
lowers mean secondary-loss ALE by ≈ `total_loss_reduction × SLEF` (vs.
the prior central-anchor-only understatement). This is the intentional
FAIR-coherent per-event-dollar-reduction semantic.

---

## 9. Migration considerations

### 9.1 Existing seeded controls

Phase 1.2 demo data is small (the `controls_importer.py` default values of `control_strength=0.7`, `control_reliability=0.8`, `control_coverage=0.8` were applied to any CSV imports). The Alembic PR ι migration backfills one `ControlFunctionAssignment` row per existing Control using the domain-derived representative sub-function rule (§7.1.4). Every backfilled row is flagged `confirmed_by_user_at = NULL`. Operations dashboards should surface "N controls have unconfirmed sub-function assignments after the schema migration; please review." No user-facing data is lost; the three effectiveness values are preserved in the assignment row.

### 9.2 Existing scenarios that reference controls (`ScenarioControl` join)

The `scenario_controls` table uses a composite PK `(scenario_id, control_id)` with CASCADE on `scenario_id` and RESTRICT on `control_id`. This structure is unaffected by the schema reshape. Scenarios continue to reference Controls by UUID; the Control entity UUID is stable across the migration. If plan-gate selects assignment-level ScenarioControl granularity (M5, §10.1.5), a further migration is required to migrate `scenario_controls` to reference `control_function_assignment_id` instead of `control_id`.

### 9.3 Existing scenario library entries with `suggested_control_ids`

`ScenarioLibraryEntry.suggested_control_ids: list[str]` was added in Phase 1.5a (PR θ) as a forward-compatibility column. It stores control library entry IDs (not v3 Control UUIDs). These references become valid when PR λ seeds `control_library_entries` with UUIDs that the scenario library entries can reference. No migration of the `suggested_control_ids` values themselves is needed for PRs ι or κ.

### 9.4 Audit log compatibility

All control mutations continue routing through `AuditWriter`. PR ι must add new audit action constants: `control_function_assignment.create`, `control_function_assignment.update`, `control_function_assignment.delete`. The existing `control.create`, `control.update`, `control.delete` actions remain unchanged. The `audit_log` table schema requires no modification.

### 9.5 Hash-stability of historical Monte Carlo runs

Historical run snapshots stored in `risk_analysis_runs` capture control state via `_snapshot_control` at `src/idraa/services/run_executor.py:181-192`. The current snapshot captures the flat triple. After PR ι, `_snapshot_control` must be updated to capture per-assignment effectiveness values for new runs. The snapshot schema version should be bumped (add a `"snapshot_version": 2` key) so downstream code can distinguish old flat-triple snapshots from new per-assignment snapshots.

Historical snapshots are immutable audit records and must not be modified.

Surfaces that read snapshot data and must be version-aware:

| Surface | File | PR ι action | Deferred to |
|---------|------|-------------|-------------|
| Snapshot writer | `src/idraa/services/run_executor.py:181-192` | Update to write per-assignment shape + version key | PR ι |
| Run-detail template | `templates/runs/detail.html` (or equivalent — verify path) | Must render both snapshot versions | PR λ |
| Run-comparison views | (if any exist — verify) | Must handle version mismatch in comparison | PR λ |
| Export/report paths | Any that read historical snapshot JSON | Must detect `snapshot_version` key | PR λ |
| Audit-log presenter | Any that reads snapshot JSON for display | Must detect `snapshot_version` key | PR λ |

---

## 10. Open questions and explicit deferrals

### 10.1 Open questions for PR ι plan gate

1. **`ClassicalControlAction` column disposition**: Rename `ControlFunction` → `ClassicalControlAction` and keep the column as optional metadata, or drop it entirely? Dropping is cleaner but risks breaking any template or API consumer that reads the value. A search of templates and API handlers should precede the plan-gate decision.

2. **`FairCamSubFunction` slug naming convention**: The table in §3 uses underscore-separated slugs with domain prefix (e.g., `lec_prev_avoidance`). Confirm this is the enum value (not the display name), and that the slugs are stable — they will appear in serialized run snapshots and cannot be changed without a migration.

3. **Virtual sub-function enforcement** (resolved from §3.3): Enforce that `dsc_corr_misaligned` cannot be assigned to a distinct control via Pydantic validator (schema layer) plus DB CHECK constraint (backstop). Constraint form: `sub_function != 'dsc_corr_misaligned' OR derived_from_assignment_id IS NOT NULL`. PR ι ships this constraint. Implementation note: `derived_from_assignment_id` column should be included reserved-but-unused to enable future computed-virtual assignments without a schema migration — unless plan-gate decides to commit to no-virtual-rows-ever.

4. **Unique constraint on `(control_id, sub_function)`** (M4): One assignment per sub-function per control (unique constraint), or allow multiple rows with a version key, or no constraint? Standard §2.4.2 page 7 speaks of coverage variation across assets/scenarios. Decision deferred to PR ι plan-gate.

5. **ScenarioControl granularity** (M5): Current `ScenarioControl(scenario_id, control_id)` vs. migrating to `ScenarioControl(scenario_id, assignment_id)`. Both have schema impact in PR ι. Decision deferred to PR ι plan-gate.

6. **Coverage placement** (M1): Assignment-level (enables per-sub-function coverage variation) vs. control-level (simpler, matches Standard's worked example framing). Decision deferred to PR ι plan-gate.

7. **VF/VD placement**: Dropped from PR ι entirely. Lands in Phase 2. See §10.3.

8. **Backfill `confirmed_by_user_at` semantics**: Exclude unconfirmed assignments from composition (option a) vs. include with warning indicator (option b). Decision deferred to PR ι plan-gate.

9. **Computed-virtual `derived_from_assignment_id` column**: Ship reserved-but-unused in PR ι, or commit to no-virtual-rows-ever? Decision deferred to PR ι plan-gate.

10. **`measured_by` pairing** (m4): Include `measured_by: UUID FK → users.id ON DELETE SET NULL` alongside `measured_at`? Depends on whether measurement is human-entered or system-computed. Decision deferred to PR ι plan-gate.

### 10.2 Open questions for PR κ plan gate

1. **TWO live composition formulas reconciliation**: Which formula gets retired — multiplicative (Formula 1, Excel/viz path) or weighted additive (Formula 2, Monte Carlo path) — or are both needed with explicit, documented separation? The Excel/visualization layer currently reports different effectiveness numbers than the Monte Carlo simulation for the same Control. This is a correctness problem; PR κ must resolve it. Decision deferred to PR κ paranoid review.

**Resolved (PR κ, 2026-05-02)**: Deleted `calculate_base_effectiveness` (Option B);
Excel/viz/roi/Monte-Carlo all converge on Option A via `calculate_risk_reduction_factor`.

2. **`roi_analyzer.py:150, 278, 395` apparent bug**: These lines call `calculate_base_effectiveness` as a method on `Control` directly, but `Control` has no such method. Verify whether this is a latent bug (dead code path), a method that was removed, or a mis-attribution. Fix if real.

**Resolved (PR κ, 2026-05-02)**: Confirmed real; auto-fixed by deletion of
`calculate_base_effectiveness` — broken callsites now dispatch to
`calculate_risk_reduction_factor` (Option A).

3. **Composition formula choice** (Options A/B/C/D from §8): Must be backtested against at least two reference scenarios with known outcomes before implementation (per `project_calibration_framework_review_gate.md`). Time-unit normalization (per-sub-function λ values or equivalent) is a sub-decision within whichever option is selected.

**Resolved (PR κ, 2026-05-02)**: Layered framing — Layer 1 = Option A multiplicative;
Layer 2 = Boolean topology with product/OR/weighted-mean operators; Layer 3 deferred
to PR μ. Backtested (six scenarios per `2026-05-02-pr-kappa-fair-cam-reshape-and-composition-design.md` §9).

4. **Decay/degradation placement**: `degradation_rate` is currently per-Control at `fair_cam/models/control.py:276`. After the reshape, this should move to per-assignment, as different sub-functions degrade at different rates. PR κ plan-gate should confirm the target placement.

**Resolved (PR κ, 2026-05-02)**: Per-assignment with default 0.0; per-Control field
dropped (T7).

5. **Time-unit normalization parameters**: The half-life function or equivalent for elapsed-time sub-functions requires per-sub-function calibration. What are the default values, and how are they configurable per organization?

**Deferred to PR μ.** PR κ excludes ELAPSED_TIME / CURRENCY assignments from Layer 2
operand lists per spec §3.2.4; engine path uses 0.5 safe-default.

6. **Composition formula and pyfair interface**: Does fair_cam's `ControlAwareRiskCalculator` pass Boolean-composition-aware reduction factors into pyfair's simulation, or does pyfair receive the composed group-level scalar? The interface boundary between fair_cam's control layer and pyfair's FAIR model must be confirmed before implementation.

**Resolved (PR κ, 2026-05-02)**: pyfair receives per-control cumulative reduction
factors (unchanged from pre-PR-κ). Layer 2 group-level outputs are diagnostic only
in PR κ; PR μ wires them into the engine path.

7. **Backfill of `comprehensive_controls_library.py`**: The re-authoring of fair_cam's hardcoded library entries requires matching each entry to the CSV's sub-function tags. The Python library at `fair_cam/data/comprehensive_controls_library.py` has 20
hardcoded entries; the canonical CSV at `docs/reference/fair-cam-controls-library.csv`
has 61 entries with Standard sub-function tags. PR κ re-authors the 20-entry Python
library to per-assignment shape; PR λ ingests the 61-entry CSV via the existing
v3 importer.

### 10.3 Phase 2+ deferrals

- Per-assignment variance modeling: `variance_freq_per_year` and `variance_duration_days` are deferred entirely from PR ι (not scaffolded). Phase 2 adds them when the schema and risk engine are ready; VF/VD placement (control-level, assignment-level, or separate table) is a Phase 2 decision.
- Multi-control Boolean composition graph at the Scenario level: the Standard describes AND/OR composition at the scenario level (multiple controls serving the same sub-function compose via OR; controls across AND-coupled groups must all function). This full graph evaluation is Phase 2.
- Library version-diff UI: showing changes between versions of a canonical control entry.
- Standards cross-reference UI: per-entry display of Standard §-numbers and normative text.
- IC3 (Integrated Control Confidence) modeling: per the memory note `feedback_ic3_separate_brainstorm.md`, IC3 requires a separate scope discussion before any code lands. It is not part of PR ι, κ, or λ.

---

## 11. Cross-references

- **v3 design doc**: `docs/plans/2026-04-23-riskflow-v3-design.md` — the data-model section predates this Standard review. Control-related claims in that document are superseded by this audit where they conflict.
- **Data-model spec**: `docs/reference/data-model-specification.md` — also pre-Standard-review. Section on Control entity is superseded by §4 of this document.
- **Methodology memory**: contains Overview-era formulas (`OpEff = IntEff × (1 - VF/365)^VD`). The Standard supersedes the Overview for all v3 design decisions. Section §2.3 of this document documents the supersession.
- **PR θ spec (Scenario Library)**: internal design doc 2026-04-28-phase-1.5a-scenario-library-design — the vertical-slice template that PR λ mirrors for the control library and wizard.
- **Standards-aligned seed source**: `docs/reference/fair-cam-controls-library.csv` — 61 control entries with Standard sub-function tags; native ingestion format for PR λ seed migration.
- **Standard source PDF**: `docs/FAIR Controls Analytics Model (FAIR-CAM) Standard V1.0 (January 2025).pdf` — 49 pages, dated 2025-01-15. All §-number and page citations in this document refer to this source.

---

## 12. Glossary

**Control**: "Anything that can be used to reduce the frequency or magnitude of loss." (§1.3.1, page 2). Intentionally broad — includes laws, regulations, policies, standards, processes, technologies, people, software, and physical structures.

**Control Function**: "How a control directly or indirectly affects the frequency or magnitude of loss." (§1.3.2, page 2). Not to be confused with the classical IT security taxonomy (preventive/detective/corrective) — the Standard's control functions are the 26 sub-functions enumerated in §3-§5.

**Functional Domain**: "High-level categories of control functions." (§1.3.3, page 2). Three domains: LEC, VMC, DSC.

**LEC (Loss Event Control)**: Controls that directly reduce the frequency or magnitude of loss events. The primary domain for operational security controls.

**VMC (Variance Management Control)**: Controls that manage variance in the performance of other controls — they affect reliability of LEC and DSC controls, not loss events directly.

**DSC (Decision Support Control)**: Controls that affect the quality of management decisions that bring other controls into existence and keep them effective.

**Capability**: "A control's inherent ability to perform its intended function in addressing specific aspects of risk." (§2.4.1, page 6). Considers design quality, real-world performance, and alignment with best practices. Sub-function-specific.

**Coverage**: "Measures the extent to which a control or set of controls applies to the assets, threats, or risk scenarios within the organization." (§2.4.2, pages 6-7). Breadth of deployment. Sub-function-specific. Placement (control-level vs. assignment-level) is a PR ι plan-gate decision.

**Reliability**: "Refers to the likelihood that a control will perform its intended function consistently and without failure when needed." (§2.4.3, page 7). Sub-function-specific.

**Operational Effectiveness**: The combined measure of Capability, Coverage, and Reliability for a specific sub-function assignment. Also called Control Maturity (§2.4, page 6). The Standard does not publish a closed-form composition formula.

**Variance Frequency (VF)**: How often a VMC-managed variance condition occurs (times per year). Deferred to Phase 2.

**Variance Duration (VD)**: How long a variance condition persists when it occurs (days). Deferred to Phase 2, alongside VF.

**Virtual Function**: A Standard sub-function (§5.3, Correcting Misaligned Decisions) that is "wholly dependent upon those other functions" (page 50). It is fulfilled by execution of controls in other domains. A virtual function must be represented in the sub-function enum for completeness; the constraint `sub_function != 'dsc_corr_misaligned' OR derived_from_assignment_id IS NOT NULL` enforces this at model and DB level.

**Boolean AND composition**: All sub-functions in an AND-coupled group must perform for the group to function. Applies to Detection (strict), Detection-Response coupling, VMC Identification-Correction, DSC §5.1 sub-functions. Specific AND operator (worst-link vs. multiplicative) is a PR κ plan-gate decision.

**Boolean OR composition**: Any sub-function in an OR-coupled group sufficing means the group functions. Applies to LEC Prevention, VMC Variance Prevention.

**weak AND**: A deliberate Standard softening of strict AND (§3.3). Deficiency in one sub-function diminishes but does not eliminate group effectiveness. Applies to LEC Response trio. Correct operator candidates: weighted arithmetic mean. Hamacher t-norm (γ > 0) and weighted complement-product are **disqualified (PR κ)** — see §8.3 for reasoning. Geometric mean is NOT a valid weak-AND operator — it produces strict-AND behavior when any input is 0.

**Confirmed assignment**: A `ControlFunctionAssignment` row with `confirmed_by_user_at IS NOT NULL`, meaning a human has verified the sub-function assignment is correct. Rows backfilled by migration have `confirmed_by_user_at = NULL` and are flagged as unconfirmed.
