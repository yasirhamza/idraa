---
title: "Control Function-Decomposition Rubric"
date: 2026-06-30
issue: 437
applies_to: "data/seed_control_library_entries.json, control_library_entry_assignments"
status: methodology-gated
---

# Control Function-Decomposition Rubric

**Issue:** #437
**Spec:** `docs/superpowers/specs/2026-06-30-library-function-completeness-design.md`
**Methodology gate:** required before any library entry is curated (§5 pipeline, step 1).

This document governs the systematic audit of all 61 control-library entries. It
defines how to decompose a product into its FAIR-CAM sub-functions, classify each
behavior into the correct channel, populate effectiveness values with cited evidence,
and handle genuinely-meta products without grafting a fake scoring channel.

---

## 1. Channel decision tree

For each distinct **behavior** a product performs, locate it in the table below and
assign the matching sub-function group. Classification is **per behavior**, not per
product — a single product (e.g., CSPM) commonly spans multiple rows.

| Behavior | FAIR-CAM function | Channel | Scoring |
|---|---|---|---|
| Hardens the **asset** — reduces its susceptibility or contact with threats | LEC Prevention (Avoidance / Resistance / Deterrence) | **direct** → routes to TEF or Vulnerability | **Scores standalone** (OR group; any one member contributes) |
| Detects events — surfaces evidence of anomalous or illicit activity | LEC Detection (Visibility / Monitoring / Recognition) | LEC channel — **does NOT score standalone** | Scores only via det+resp AND-pair: **all 3 detection members** (strict AND) **+ ≥1 of {lec_resp_resilience, lec_resp_event_termination}** |
| Contains / limits events — terminates activity or restores operations | LEC Response (EventTermination / Resilience) | LEC channel — **does NOT score standalone** | Scores only via det+resp AND-pair (all 3 det strict AND + ≥1 resp opeff member); **≥3 resp is structurally unsatisfiable** — only 2 non-currency opeff members exist |
| Directly reduces realized losses (currency) | LEC Response (LossReduction) | LEC channel — **`lec_resp_loss_reduction` only** | **Scores standalone** (the single CURRENCY exception; see §5) |
| Monitors / corrects **another control's** operational health or drift | VMC Identification / Correction | **meta** (needs coupling math #439) | Scores only via fully-staffed Identification+Correction AND-pair (≥2 id + ≥2 corr members; partial 1+1 = $0) |
| Reduces frequency or probability of changes that introduce control variance | VMC Variance Prevention | direct → routes to Vulnerability (Vuln×0.3 proxy) | **Scores standalone** (OR group; PERCENT_REDUCTION unit) |
| Improves decisions / prioritization / situational awareness | DSC Prevention / Identification+Correction | **meta** | DSC_PREVENTION **scores (~$1k) when ALL 9 members are staffed** (strict AND; partial = $0). Curation policy: label-only (full-9 staffing is rare; magnitude is small — do not author DSC chasing value). DSC id+corr pair is **unreachable**: `dsc_corr_misaligned` is virtual (no control may claim it). |

**Key principle — scoring ≠ channel (B1).** Channel is where the effect routes
(direct/meta). Whether an entry produces `v(S) > 0` is a separate, topology-derived
predicate (see §3). Do not conflate the two.

**Entry-level scoring (NEW-B2).** The scoring predicate is **entry-level and
engine-based**, not per-sub-function. An entry may score because: (a) it has at
least one `scores_standalone` member (§3), OR (b) it has a *fully-staffed*
AND-pair — either `lec_det + lec_resp` (**all 3 detection members**, strict AND,
**+ ≥1 of {`lec_resp_resilience`, `lec_resp_event_termination`}**; note: ≥3 resp
is structurally unsatisfiable — only 2 non-currency response opeff members exist)
or `vmc_id + vmc_corr` (≥2 id + ≥2 corr, identification-correction pair),
OR (c) **all 9 DSC_PREVENTION members staffed** (v(S) ≈ $1k at 0.8-uniform
basis — small but non-zero; partial = $0 strict AND).
A *partial* pair (1 id + 1 corr) composes to $0 (verified empirically against
the engine: 1+1 = $0; 2+2 ≈ $36k at the 0.8-uniform-input basis).

---

## 2. FairCamSubFunction catalog

Generated from `fair_cam.models.composition_topology` (GROUP_MEMBERSHIP,
GROUP_TYPE, GROUP_NODE_MAPPING, SUB_FUNCTION_UNITS). Columns:

- **Group** — topology group key.
- **Group type** — OR (any member contributes) / AND (all members required) / weak_and.
- **Targets** — FAIR-CAM nodes the group routes to when active.
- **Sub-function** — `FairCamSubFunction` enum value.
- **UnitType** — the unit of the `capability` effectiveness field.
- **Scores standalone** — whether this sub-function's group contributes `v(S) > 0` with
  no other group needed (`scores_standalone` catalog predicate; see §3).

### 2.1 LEC Prevention — direct channel

Group: `lec_prevention` | Type: **OR** | Targets: `threat_event_frequency`, `vulnerability`

| Sub-function | UnitType | Scores standalone |
|---|---|---|
| `lec_prev_resistance` | PROBABILITY | **Yes** |
| `lec_prev_avoidance` | PROBABILITY | **Yes** |
| `lec_prev_deterrence` | PROBABILITY | **Yes** |

Avoidance removes contact (→ TEF). Resistance reduces the probability that
contact results in a loss event (→ Vulnerability). Deterrence reduces the
probability that a threat agent acts harmfully after contact (→ TEF branch).

### 2.2 LEC Detection — gates Response

Group: `lec_detection` | Type: **AND** (internal composition AND: all 3 members required for the group to activate; distinct from the cross-group AND-pair scoring mechanism where detection and response groups must both be staffed) | Targets: *(none — detection gates response via pair)*

| Sub-function | UnitType | Scores standalone |
|---|---|---|
| `lec_det_recognition` | PROBABILITY | **No** |
| `lec_det_monitoring` | ELAPSED_TIME | **No** |
| `lec_det_visibility` | PROBABILITY | **No** |

Detection contributes to loss magnitude only through the fully-staffed
`lec_detection_response_pair` (AND-pair targeting `primary_loss`,
`secondary_loss`). A detection-only entry routes to the non-scoring residual
(→ #439).

### 2.3 LEC Response — gates on Detection

Group: `lec_response` | Type: **weak_and** | Targets: `secondary_loss`, `primary_loss`

| Sub-function | UnitType | Scores standalone |
|---|---|---|
| `lec_resp_resilience` | PROBABILITY | **No** |
| `lec_resp_loss_reduction` | **CURRENCY** | **Yes** *(CURRENCY exception — see §5)* |
| `lec_resp_event_termination` | ELAPSED_TIME | **No** |

`lec_resp_loss_reduction` is the sole CURRENCY sub-function. It scores
standalone as a direct dollar subtractor. Its capability value **must be
cited** — no expert-estimate permitted (see §5.3).

`lec_resp_resilience` and `lec_resp_event_termination` do not score without
a paired Detection group (fully-staffed pair group: `lec_detection_response_pair`,
AND, targets `primary_loss` / `secondary_loss`).

### 2.4 VMC Variance Prevention — direct channel

Group: `vmc_variance_prevention` | Type: **OR** | Targets: `vulnerability`

| Sub-function | UnitType | Scores standalone |
|---|---|---|
| `vmc_prev_reduce_change_freq` | PERCENT_REDUCTION | **Yes** |
| `vmc_prev_reduce_variance_prob` | PERCENT_REDUCTION | **Yes** |

These score via a `Vuln×0.3` proxy (current engine). Assignment is valid only
where the product genuinely reduces the *frequency* or *probability* of changes
that degrade controls — not as a score-rescue for a meta control (I5 invariant;
see §6.4).

### 2.5 VMC Identification — meta, no standalone score

Group: `vmc_identification` | Type: **AND** | Targets: *(none — contributes only via pair)*

| Sub-function | UnitType | Scores standalone |
|---|---|---|
| `vmc_id_control_monitoring` | PROBABILITY | **No** |
| `vmc_id_threat_intelligence` | PROBABILITY | **No** |

### 2.6 VMC Correction — meta, no standalone score

Group: `vmc_correction` | Type: **AND** | Targets: *(none — contributes only via pair)*

| Sub-function | UnitType | Scores standalone |
|---|---|---|
| `vmc_corr_implementation` | ELAPSED_TIME | **No** |
| `vmc_corr_treatment_selection` | PROBABILITY | **No** |

The VMC Identification+Correction AND-pair group (`vmc_identification_correction_pair`,
targets: `vulnerability`) scores when **fully staffed** (≥2 Identification + ≥2
Correction members). A partial pair (1+1) composes to $0 (empirically verified).

### 2.7 DSC Prevention — scores when ALL 9 members staffed (curation-policy label-only)

Group: `dsc_prevention` | Type: **AND** | Targets: `secondary_loss`, `primary_loss`

| Sub-function | UnitType | Scores standalone |
|---|---|---|
| `dsc_prev_defined_expectations` | PROBABILITY | **No** (individual member; all 9 required for group) |
| `dsc_prev_incentives` | PROBABILITY | **No** (individual member; all 9 required for group) |
| `dsc_prev_sa_data_asset` | PROBABILITY | **No** (individual member; all 9 required for group) |
| `dsc_prev_sa_data_threat` | PROBABILITY | **No** (individual member; all 9 required for group) |
| `dsc_prev_sa_analysis` | PROBABILITY | **No** (individual member; all 9 required for group) |
| `dsc_prev_sa_reporting` | PROBABILITY | **No** (individual member; all 9 required for group) |
| `dsc_prev_sa_data_controls` | PROBABILITY | **No** (individual member; all 9 required for group) |
| `dsc_prev_ensure_capability` | PROBABILITY | **No** (individual member; all 9 required for group) |
| `dsc_prev_communication` | PROBABILITY | **No** (individual member; all 9 required for group) |

DSC_PREVENTION is an AND group targeting `secondary_loss` / `primary_loss` via
magnitude weights (secondary_loss 0.5, primary_loss 0.2). When **all 9 members
are fully staffed**, the engine scores `v(S) ≈ $967` at the 0.8-uniform-input
basis — small, but non-zero. Partial staffing (any member absent) collapses
to $0 (strict AND).

**Curation policy — label-only in practice:** full 9-member staffing is rare in
single-product deployments and the magnitude is small (~$1k). Do not author DSC
assignments chasing v(S) — classify where the behavior is genuinely attested and
let the engine handle the math. The per-sub-function `scores_standalone` predicate
(§2.9) correctly returns **No** for individual DSC members; the all-9 group scoring
path is handled by the engine-based `entry_scores` predicate (Task 4).

### 2.8 DSC Identification+Correction pair — unreachable in authoring

Group: `dsc_identification_correction_pair` | Type: **AND** |
Targets: `secondary_loss`, `primary_loss`

| Sub-function | UnitType | Scores standalone |
|---|---|---|
| `dsc_id_misaligned` | PROBABILITY | **No** |
| `dsc_corr_misaligned` | PROBABILITY | **No** |

This pair maps to magnitude weights and would score if fully staffed. However,
it is **unreachable in authoring**: `dsc_corr_misaligned` is a virtual
sub-function — `ControlLibraryAssignmentSeed` raises a validation error if any
control attempts to claim it ("sub_function dsc_corr_misaligned is virtual; no
control may claim it"). No real library entry can score via this pair, and no
score-rescue guard is needed for it.

### 2.9 Summary: scores_standalone predicate

The `scores_standalone(sub_function)` catalog predicate (Task 4's implementable
filter, sourced from this table) is:

```
scores_standalone(sf) = sf ∈ lec_prev_* ∪ vmc_prev_* ∪ {lec_resp_loss_reduction}
```

This is **per-sub-function** — a rubric helper. The **entry-level** scoring
judgment uses the engine-based pair-aware `entry_scores(entry)` (Task 4),
which captures pair-scoring entries (fully-staffed det+resp, or fully-staffed
vmc_id+corr) and the all-9-staffed DSC_PREVENTION group — entries that have no
standalone scorer but still produce `v(S) > 0`.

---

## 3. Faithfulness rule

> **Every assignment must be a behavior the product genuinely performs, mapped
> to the correct channel. Adding a sub-function to make v(S) non-zero is
> forbidden. It turns attribution into fiction.**

Operationally:

1. Enumerate the product's behaviors **from external evidence** (vendor documentation,
   MITRE ATT&CK mitigation data, CIS efficacy data, NIST CSF mappings, IRIS/VERIS
   breach data) before opening the assignment editor.
2. Map each behavior to a sub-function using the decision tree (§1).
3. Do not consult `v(S)` until the decomposition is complete (see §6 on
   blind-to-score discipline).
4. If a proposed sub-function cannot be grounded in an attested, distinct behavior
   of this product, remove it.

**Asset-vs-control anchoring rule.** When a product acts on infrastructure
or configuration, the anchoring question is:

> *Is the thing being changed the protected asset's own susceptibility to the
> threat (→ LEC), or another security control's operational health (→ VMC)?*

Hardening the asset → direct LEC. Sustaining another control → meta VMC.
Apply this per behavior, not per product (§4 below).

---

## 4. I1 per-behavior discriminator (the load-bearing boundary)

Cloud and configuration-management products are the hardest cases because a
"misconfiguration" is ambiguous — it could be an asset vulnerability or a drifted
control setting. **Always route per behavior.** Three worked boundary examples:

### Example 1: CSPM re-privatizes a storage bucket → **LEC Avoidance**

The storage bucket is a **data asset**. A public-access setting on the bucket
is the asset's own exposure to external threat agents. Correcting it removes
contact between threat agents and the asset.

Channel: `lec_prev_avoidance` (Avoidance → reduces TEF/contact frequency).
Rationale: the bucket is the protected asset, not a security control.

### Example 2: CSPM re-enables a disabled GuardDuty detector → **VMC Correction**

GuardDuty is a **security control** (a detection control). Its disabled state
is a degraded-control condition, not an asset vulnerability. Restoring it
is correcting a variance in another control's operational health.

Channel: `vmc_corr_implementation` (Correction → elapsed time to restore the
degraded control).
Rationale: GuardDuty is the control under care, not the protected asset.

### Example 3: Patching an OS CVE → **LEC Resistance**

The operating system is the **asset** (or a component hosting assets). The CVE
represents the OS's own susceptibility to exploit. Patching reduces the probability
that a threat-agent action succeeds against it.

Channel: `lec_prev_resistance` (Resistance → reduces Vulnerability / exploit
probability).
Rationale: the OS is the asset; the CVE is its susceptibility property.

### Boundary clarification: "config-as-control"

A network security group (NSG) rule or a firewall policy can be either:
- The **asset**'s access boundary (the rule *is* the protection the asset relies
  on, not a separate control) → treat the misconfiguration as `lec_prev_avoidance`
  or `lec_prev_resistance` depending on whether it removes contact or reduces
  exploitability after contact.
- A **control's** configuration setting that the CSPM product monitors for drift →
  `vmc_id_control_monitoring` / `vmc_corr_implementation`.

Route by what is being protected and what role the configuration plays in the
threat model — never default to VMC just because the word "configuration" appears.

---

## 5. Per-value citation standard

### 5.1 Three-value provenance (I2)

`ControlLibraryEntryAssignment` carries per-value provenance for each of the
three effectiveness fields:

| Field | Typical evidence basis | Common outcome |
|---|---|---|
| `capability_default` | Lab studies, MITRE ATT&CK coverage, vendor efficacy data | Often `expert-estimate` (data rare) |
| `coverage_default` | Deployment benchmarks, CIS deployment tier data | Often `expert-estimate` |
| `reliability_default` | Operational reliability data; usually thin | Usually `expert-estimate` |

One provenance field per value: `{capability,coverage,reliability}_provenance`
∈ `{cited, expert-estimate}`. Provenance is **per-value** — a cited capability
does not cover an estimated reliability.

### 5.2 Primary-cited gate

A `cited` provenance value must trace to a primary source:

- **Paginated sources** (FAIR-CAM Standard, NIST SP, CIS, academic papers,
  MITRE ATT&CK): page number + figure/table number.
- **Non-paginated primary sources** (MITRE ATT&CK technique pages, VERIS schema,
  open datasets): commit hash or versioned URL + accessed date + permalink.

The **derivation** (how the value follows from the source) is documented in
`{field}_citations`, e.g.:

```
capability_citations: [
  "Mechanism (expert-estimate): CSPM re-privatizes cloud storage buckets exposed
   to external access. Asset = cloud data stores; susceptibility = network
   reachability / public exposure; threat = external enumeration actors.
   Channel grounding: MITRE ATT&CK T1530 (Data from Cloud Storage) + M1037
   (Filter Network Traffic), https://attack.mitre.org/techniques/T1530/
   (accessed 2026-06-30); CIS AWS Foundations Benchmark S3 Block Public Access.
   Magnitude 0.6 is a conservative expert estimate — MITRE publishes qualitative
   mitigation coverage, not numeric population-reduction percentages; no primary
   source maps avoidance coverage to a measured [0,1] value."
]
```

The citation must state the **mechanism** (what asset, what susceptibility,
what threat) — not just the regulatory or framework tag. A citation that reads
"CIS Safeguard 4.1 applies" is not sufficient.

### 5.3 Expert-estimate flag + 0.8 ceiling

Where no real evidence exists, flag `provenance = "expert-estimate"`. Never
manufacture a citation.

The **0.8 ceiling** applies to all expert-estimate values on **bounded units**:
- `capability` for PROBABILITY and PERCENT_REDUCTION sub-functions.
- `coverage` (always bounded [0,1]).
- `reliability` (always bounded [0,1]).

The ceiling is **unit-scoped**:

| UnitType | Ceiling applies? | Rationale |
|---|---|---|
| PROBABILITY | **Yes** — cap expert-estimate capability at 0.8 | Bounded [0,1]; inflated estimates manufacture score |
| PERCENT_REDUCTION | **Yes** — cap expert-estimate capability at 0.8 | Bounded [0,1]; same inflation risk |
| ELAPSED_TIME | **Must be cited** (see §5.3.1) | Smaller time = *more* effect (lower-tail risk); citation required |
| CURRENCY | **Must be cited** (see §5.3.1) | Larger value = more effect (upper-tail risk); citation required |

#### 5.3.1 NATURAL-UNIT capability (ELAPSED_TIME + CURRENCY) — citation required, no expert-estimate

Both natural-unit capabilities manufacture a large control score at their respective
extreme tails and cannot be bounded by a ceiling:

- **CURRENCY** (`lec_resp_loss_reduction`) — upper-tail risk: a larger dollar value
  directly increases `v(S)` as an unbounded subtractor. An uncited estimate can
  manufacture a large score with no constraint.
- **ELAPSED_TIME** (`lec_det_monitoring`, `lec_resp_event_termination`,
  `vmc_corr_implementation`) — lower-tail risk: a very small elapsed-time value
  drives `opeff = exp(−t/τ) → 1`, producing a near-maximum effectiveness score.
  Verified: `lec_resp_event_termination` cap=0.01 → $42.8k vs cap=30 → $26.8k.

Therefore:

> **Any natural-unit capability (ELAPSED_TIME or CURRENCY) MUST carry
> `provenance = "cited"`. An expert-estimate is not permitted for these fields.**

The citation must document the natural-unit efficacy basis and the derivation
from the source to the default value (e.g., a breach-cost reduction study for
CURRENCY; an empirical mean-time-to-contain study for ELAPSED_TIME, with the
derivation from the reported statistic to the capability value).

Note: `coverage` and `reliability` (both bounded [0,1]) still gate the effective
contribution — realized effect = `capability × coverage × reliability`. Even a
cited capability at the extreme tail is modulated by these multipliers. The
citation-required rule remains essential because an uncited capability is
unanchored regardless of coverage/reliability bounds.

### 5.4 Non-identifiability disclaimer (I3)

Both cited and expert-estimate defaults inherit the single-org non-identifiability
posture (`docs/reviews/2026-06-25-faircam-control-roi-identifiability.md`).

> A `cited` effectiveness value is cited to an *external/population* efficacy
> study (MITRE coverage, a CIS deployment study), not to this organization's
> realized risk reduction. Defaults are **point-anchored-with-rationale**, not
> validated measurements for any specific deployment.

The weight-robustness ensemble (#419) perturbs the FAIR-CAM `node_mapping`
weights, not these effectiveness inputs. Widening to effectiveness-input ranges
is out of scope here (noted for #439).

---

## 6. I4 hardening — assign-to-score guards

The epic's "no more $0" framing creates optimization pressure toward grafting a
scoring sub-function onto a genuinely-meta product. Five guards + one symmetric
guard prevent that:

### 6.1 Blind-to-score decomposition

Authors propose the full behavior decomposition **before** inspecting `v(S)`.
Behaviors are enumerated from vendor documentation, MITRE ATT&CK mitigations,
NIST CSF mappings, and control-efficacy evidence — not reverse-engineered from
the score. An agent should decompose and propose assignments first; a separate
pass computes `v(S)` for the methodology reviewer to audit.

### 6.2 Mechanism-citation requirement

Every scoring-channel assignment (i.e., a sub-function in `scores_standalone`)
must carry a citation that states the **mechanism**:

> What asset? What susceptibility / contact vector? What threat class?

Example (CSPM `lec_prev_avoidance`):
```
"CSPM re-privatizes cloud storage buckets and object-level ACLs that are publicly
 accessible. The asset class is cloud data stores (S3/GCS/Azure Blob). The attack
 vector removed is unauthenticated object read/enumeration by external threat
 agents. Source: CIS Benchmark for AWS §2.1.5 (S3 Block Public Access) +
 MITRE ATT&CK Cloud Matrix T1530 (Data from Cloud Storage Object), accessed
 2026-06-30, permalink: https://attack.mitre.org/techniques/T1530/"
```

A citation that cites only a compliance tag (CIS Safeguard 4.1) or a framework
subcategory (PR.IP-1) without specifying the mechanism is **not sufficient** for
a scoring-channel assignment.

### 6.3 Non-scoring residual terminal bucket (blessed $0 outcome)

If, after completing the blind-to-score decomposition, an entry has:
- No `lec_prev_*` assignments (no direct asset hardening).
- No `vmc_prev_*` assignments (no direct variance prevention).
- No `lec_resp_loss_reduction` assignment.
- No fully-staffed detection+response pair (all 3 det members + ≥1 resp opeff member).
- No fully-staffed identification+correction pair (≥2 id + ≥2 corr members).
- No all-9-staffed DSC_PREVENTION group (rare; v(S) ≈ $1k when fully staffed).

...then the entry is **genuinely meta** and routes to the non-scoring residual
bucket. The authoritative residual predicate is the engine-based `entry_scores`
(Task 4); this prose list is illustrative. The correct outcome is:

> "This product's value is genuinely meta after enrichment. It scores $0 under
> the current engine. Route to #439 (coupling math) for future scoring."

This is a first-class outcome — not a failure state. **Never graft a scoring
sub-function onto a genuinely-meta control to rescue it from $0.** The residual
list is the input scope for the #439 coupling-math epic.

### 6.4 I5 invariant — no vmc_prev_* or vmc_id+corr score-rescue

The two VMC scoring paths (variance-prevention OR group, and the
identification+correction fully-staffed pair) may only be assigned where the
product **genuinely** performs those behaviors:

- `vmc_prev_*` (OR, PERCENT_REDUCTION): only where the product demonstrably
  reduces the *frequency* or *probability* of changes that degrade controls
  — not as a score-rescue for a monitoring-only meta control.
- `vmc_id + vmc_corr` pair: only where the product genuinely monitors AND
  remediates other controls' drift (fully-staffed). A product that only monitors
  (no auto-remediation) gets `vmc_id_*` only, which is a partial pair ($0).
- **all-9 DSC_PREVENTION:** DSC_PREVENTION scores ~$1k when all 9 members are
  staffed. Do not author all 9 DSC members for a product that genuinely exhibits
  only a subset of DSC behaviors, merely to extract a small v(S). The same
  faithfulness rule (§3) and blind-to-score discipline (§6.1) govern DSC
  assignments.

All must pass the faithfulness rule (§3) and the blind-to-score discipline (§6.1).

### 6.5 Score-delta-targeted review

Every entry that moves from $0 to > $0 after re-curation receives a
**methodology-reviewer audit** of the newly-added scoring assignment, regardless
of batch size. The reviewer confirms the behavior is attested, the channel is
correct per the decision tree (§1) and the I1 discriminator (§4), and the
citation states the mechanism (§6.2).

### 6.6 NEW-N1 symmetric guard — residual bucket gets the same audit depth

> **A residual label sets the expected *outcome* of the review, not the depth
> of the review itself.**

Entries routed to the non-scoring residual bucket (§6.3) receive the **same
blind-to-score completeness audit** as under-authored entries. The reviewer
still enumerates all attested behaviors from external evidence and maps each
to a sub-function. Only after the full decomposition is the "all meta" finding
confirmed.

Motivating case: **deception technology** (honeypots, decoys) looks detection-only
today. A thorough behavioral analysis may reveal that well-configured deception
also performs genuine avoidance (contact with real assets is diverted to decoys,
reducing real-asset contact frequency → `lec_prev_avoidance`) and deterrence
(awareness of deception infrastructure deters opportunistic threat agents →
`lec_prev_deterrence`). A shallow "all detection, route to residual" label
without the completeness audit would miss these.

**The residual report is authoritative only post-full-rollout** — it can only be
trusted once every entry has completed the blind-to-score audit, including
residual-labeled ones.

---

## 7. Worked example: Cloud Security Posture Management (CSPM)

**Entry:** `cloud-security-posture-management`
**Current assignments:** `vmc_id_control_monitoring` only → `v(S) = $0`
**Audit finding:** the control-monitoring assignment is correct but incomplete;
CSPM's primary value (direct asset hardening) is not assigned.

### 7.1 Behavior decomposition (blind-to-score)

Enumerated from CSPM vendor documentation, CIS Benchmark AWS/Azure/GCP
sections, MITRE ATT&CK Cloud Matrix, and NIST SP 800-53 CA/CM control families:

1. **Detects and auto-remediates publicly-accessible cloud storage** (S3 Block
   Public Access, Azure Storage Public Access, GCS bucket IAM) — removes
   unauthenticated contact between external threat agents and cloud data stores.
   → Behavior: asset hardening (removes contact). Channel: **LEC Avoidance**.

2. **Enforces encryption, least-privilege IAM, and security-group hardening** —
   reduces the probability that a threat agent who reaches the asset can exploit
   it (CVE exploitation, credential abuse).
   → Behavior: asset hardening (reduces exploitability). Channel: **LEC Resistance**.

3. **Continuously monitors configuration drift of security controls** (e.g., S3
   bucket policies, GuardDuty enablement state, logging configurations) — identifies
   variance in another control's operational health.
   → Behavior: monitors other controls' health. Channel: **VMC Identification**.

4. **Triggers auto-remediation workflows for security control drift** (re-enables
   a disabled detector, restores a control policy to baseline).
   → Behavior: corrects drifted controls. Channel: **VMC Correction**.

5. **Provides a continuous posture report feeding analyst decisions** (compliance
   dashboard, risk prioritization feeds, executive posture score).
   → Behavior: improves decision quality. Channel: **DSC** (curation-policy label-only for this entry — CSPM assigns only 1 of 9 DSC_PREVENTION members; partial = $0).

### 7.2 Channel mapping

| Behavior | Sub-function | Group | Scores standalone |
|---|---|---|---|
| Removes public asset exposure (avoidance-dominant) | `lec_prev_avoidance` | lec_prevention (OR, direct) | **Yes** |
| Enforces encryption / hardening / patching | `lec_prev_resistance` | lec_prevention (OR, direct) | **Yes** |
| Monitors other controls' configuration drift | `vmc_id_control_monitoring` | vmc_identification (AND, meta) | No |
| Auto-remediates detected control drift | `vmc_corr_implementation` | vmc_correction (AND, meta) | No |
| Posture reporting → analyst situational awareness | `dsc_prev_sa_analysis` | dsc_prevention (AND, curation-policy label-only for this entry) | No (partial DSC: 1 of 9 members; $0) |

### 7.3 Why avoidance is dominant

Most CSPM findings are **exposure / reachability issues** — public cloud buckets,
open security groups, exposed RDP/SSH, unrestrained egress — not exploitability
issues. Avoidance (removing contact) is the primary CSPM value; resistance (reducing
exploit probability after contact) is secondary. The avoidance assignment therefore
carries the higher capability value.

### 7.4 Why the VMC pair does not score for CSPM

CSPM at the standard single-product deployment provides **1 Identification +
1 Correction** member. The VMC pair requires ≥2 id + ≥2 corr members (both groups
fully staffed) to score via the pair channel. A 1+1 partial pair composes to $0
(verified empirically). **CSPM scores via `lec_prev_avoidance` and
`lec_prev_resistance` only** — not via the VMC pair.

### 7.5 I1 discriminator application

- Re-privatizing a data bucket: the bucket is the **asset** → LEC Avoidance (not VMC).
- Re-enabling GuardDuty: GuardDuty is a **control** → VMC Correction (not LEC).
- Enforcing S3 server-side encryption: the data in the bucket is the **asset** → LEC
  Resistance (reduces exploitability of stored data if an attacker reaches the bucket).

CSPM spans both LEC and VMC precisely because it acts on both assets and other
controls. The I1 discriminator resolves each behavior individually.

### 7.6 Post-curation score

With `lec_prev_avoidance` and `lec_prev_resistance` added, the entry score becomes
`v(S) > $0`. This is a genuine correction — not a grafted score — because both
behaviors are directly attested in CSPM vendor documentation and CIS benchmark
remediation guidance.

---

## 8. Cross-references

| Section | Referenced in |
|---|---|
| §1 (channel decision tree) | Task 2 (schema), Task 4 (triage), Task 5 (pilot curation) |
| §2 (sub-function catalog) | Task 4 (`scores_standalone` predicate + `entry_scores`) |
| §3 (faithfulness rule) | All curation tasks; methodology reviewer gate |
| §4 (I1 discriminator) | Task 5 (pilot: CSPM, config-management entries), Task 7 (residual report) |
| §5 (citation standard) | Task 3 (seed-schema bump + validator), Task 5 (pilot curation), Task 6 (pinning tests) |
| §6 (I4 hardening) | Task 5 (pilot), Task 7 (residual report generator) |
| §7 (CSPM worked example) | Task 5 pilot batch (CSPM is entry 1 of the pilot) |
