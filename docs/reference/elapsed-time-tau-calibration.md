---
title: "Elapsed-time τ calibration methodology"
date: 2026-05-16
issue: 131
applies_to: "fair_cam.calibration.elapsed_time_taus.TAU_BY_SUB_FUNCTION"
status: stable
---

# Elapsed-time τ calibration methodology

## Purpose

`fair_cam.calibration.elapsed_time_taus.TAU_BY_SUB_FUNCTION` provides
the per-sub-function exponential-decay constant τ used in PR μ.1's
elapsed-time normalization: `opeff = exp(-elapsed_time / τ_sf)`.

The FAIR-CAM Standard V1.0 (§2.3) deliberately leaves time-unit
normalization to implementations. The audit at
`docs/reference/fair-cam-standard-alignment.md` §8.3 enumerates
`exp(-λ × elapsed_time)` as one example option; the §8 preamble
explicitly states no recommendation is made.

**These values are v3 implementation choices, not Standard-mandated.**

## Calibration principle

### Strict primary-cite-or-drop rule (issue #131)

Every τ value in the canonical `TAU_BY_SUB_FUNCTION` table must trace to
a **primary source** with one of:

- Page number + figure number + table number, for paginated sources.
- Commit hash + accessed date + permalink, for non-paginated primary
  sources.

Heuristic values, "v3 default" placeholders, and informally-justified
defaults are NOT permitted in the canonical τ table. A sub-function that
cannot satisfy the primary-cite gate is **reclassified to
`UnitType.PROBABILITY`** — the analyst enters a bounded `[0, 1]`
effectiveness directly, and a future per-org override layer can restore
time-axis calibration when organization-specific data materialises.

### Calibration philosophy — two admissible anchors

The decay model is `opeff(t) = exp(-t/τ)`. For each canonical-τ
sub-function, **the τ value must be derivable from a primary-source
statistic that matches the philosophy plugged into the derivation
formula**. Two philosophies are admissible; mixing them silently is the
foot-gun the methodology-reviewer persona (per project convention) now catches:

1. **Median half-life — `τ = median / ln(2)`.** Anchored on a published
   *median* of the operational time. Guarantees
   `opeff(observed_median) = 0.5`. The median anchor is
   **distribution-agnostic**: it consumes only a single point (the
   50%-survival line), so it survives non-exponentiality (Weibull,
   log-normal, or any non-constant-hazard family — the τ value still
   recovers the published median by construction). Plugging a *mean*
   into this formula while claiming median-half-life semantics is
   INCORRECT for any skewed distribution. Used by
   `VMC_CORR_IMPLEMENTATION` (DBIR 2024 Fig 19 — CISA KEV
   survival-analysis median).

2. **Exponential mean lifetime — `τ = mean`.** Anchored on a published
   *mean*. Under the exponential-distribution assumption (constant
   hazard rate `λ = 1/τ`):
   - `mean = τ`
   - `median = τ × ln(2) ≈ 0.693 × τ`
   - `opeff(τ) = exp(-1) ≈ 0.368`
   - `opeff(τ × ln(2)) = 0.5`

   This is the only anchoring approach consistent with the original PR
   μ.1 implementation when the published statistic is a mean and no
   defensible median is available. Used by `LEC_DET_MONITORING` and
   `LEC_RESP_EVENT_TERMINATION` (IBM CODB 2024 MTTI / MTTC **means**).

### Methodology-reviewer mean-vs-median trap

The methodology-reviewer "Statistical-method correctness" gate
(per project convention) prevents mixing the two philosophies. Specifically: plugging
a *mean* into `τ = X / ln(2)` is INCORRECT for any skewed distribution.
That formula only yields `opeff(X) = 0.5` when X is a median.

The 2026-05-16 re-derivation under issue #131 corrected two τ values
(`LEC_DET_MONITORING` 280d → 194d and `LEC_RESP_EVENT_TERMINATION`
92d → 64d) that had silently treated IBM CODB MTTI / MTTC **means** as
if they were medians during the 2026-05-15 Phase-0 consolidation.
(A third KEPT τ, `VMC_CORR_IMPLEMENTATION` 43d → 79.3d, was re-derived
in the same 2026-05-16 pass for an unrelated reason — rejecting a
false "30-day patch SLA" target basis in favor of the actual CISA-KEV
survival-curve median; see design doc §3.) The mean-as-median error
propagated through six plan-gate rounds with four SWE-grounded
reviewers each (architect / code-reviewer / security-auditor /
spec-compliance) undetected before a methodology-reviewer self-check
at T0f.5 flagged it. The precedent is now anchored in project convention
("Methodology reviewer persona"); full side-by-side hand-math for each
τ correction lives in the design doc at
`docs/plans/2026-05-15-issue-131-tau-calibration-design.md` §3.

## Per-sub-function calibration

The post-#131 canonical table contains **3 entries**. All other
sub-functions previously in this table reclassified to
`UnitType.PROBABILITY` — see "Reclassified sub-functions" below.

### LEC Detection / Response

| Sub-function | Citation | Anchor statistic (mean / median) | τ (days) |
|---|---|---|---|
| `LEC_DET_MONITORING` | IBM Cost of Data Breach 2024 p10 Fig 4 — MTTI "global avg" (label: *Mean time to identify*) | **mean** = 194d | **194** (exponential mean lifetime, `τ = mean`) |
| `LEC_RESP_EVENT_TERMINATION` | IBM Cost of Data Breach 2024 p10 Fig 4 — MTTC "global avg" (label: *Mean time to contain*) | **mean** = 64d | **64** (exponential mean lifetime, `τ = mean`) |

### VMC Correction

| Sub-function | Citation | Anchor statistic (mean / median) | τ (days) |
|---|---|---|---|
| `VMC_CORR_IMPLEMENTATION` | Verizon DBIR 2024 p21 Fig 19 — CISA KEV survival-analysis median read at the survival = 0.5 line | **median** = 55d | **79.3** (median half-life, `τ = median / ln(2)`) |

The underlying distribution for `VMC_CORR_IMPLEMENTATION` is empirically
not exponential — per-point exponential fits across the five published
points on DBIR Fig 19 diverge (τ estimates 79d–185d, indicating
decreasing hazard / Weibull-shape < 1 or log-normal). The median-anchor
choice survives this non-exponentiality because it uses only the single
50%-line point; see the design doc §3 "Distribution assumptions and
σ-borrowing rejection rationale" for the per-point fit table.

## Reclassified sub-functions

Per FAIR-CAM Standard §2.3 (calibration is implementation-defined) and
the audit §8 preamble ("no recommendation is made here"), v3 reserves a
strict primary-cite gate for canonical τ values. The reclassifications
below are RiskFlow's **evidentiary choices, NOT Standard-mandated** — the
Standard does not preclude calibration if data were available.

For the evidentially-deferred entries, this is a **v3 input-shape
decision**, NOT a redefinition of the Standard's natural-unit framing.
The Standard's elapsed-time framing for these sub-functions is preserved;
v3 simply accepts bounded `[0, 1]` effectiveness inputs where defensible
time medians are unavailable. The reclassification can REVERSE when new
research lands (IRIS 2024 full report becoming accessible, future
industry surveys publishing the relevant operational-time metric, or a
per-org override layer adopting org-specific dwell-time fits).

### A. Standard-defined virtual sub-functions (FAIR-CAM Standard §5.3)

Per Standard §5.3, the following sub-functions are virtual — "no
distinct controls serve this function." Reclassified to
`UnitType.PROBABILITY` for schema consistency; the schema-level
validator at `src/riskflow/schemas/control.py:80`
(`reject_virtual_unless_derived`) continues to reject direct writes to
these sub-functions.

| Sub-function | Standard reference |
|---|---|
| `DSC_CORR_MISALIGNED` | §5.3 — "no distinct controls serve this function" |

*Total: 1 virtual sub-function per Standard §5.3.*

### B. Evidentially-deferred sub-functions (v3 strict-cite gate failure)

The #131 research pass searched the following primary sources for
defensible medians (full extractions at
`docs/reference/calibration-sources/`):

- IBM Cost of Data Breach 2024 (accessed 2026-05-15)
- Verizon DBIR 2024 (accessed 2026-05-15)
- SANS 2024 D&R Survey + SANS 2023 IR Survey (accessed 2026-05-15)
- VERIS / VCDB (commit `5a64739`, accessed 2026-05-15)
- FAIR Institute IRIS 2024 (public summary pages; deep report
  inaccessible — accessed 2026-05-15)

The following sub-functions had no defensible primary citation in this
research pass. Reclassified to `UnitType.PROBABILITY` (bounded `[0, 1]`
effectiveness input; calculator routes through Layer-1 multiplicative
`compute_assignment_opeff` in the PROBABILITY branch):

| Sub-function | Searched for | Outcome |
|---|---|---|
| `LEC_RESP_RESILIENCE` | Unconditional recovery-time median (BCM literature, IBM CODB, SANS) | IBM CODB 2024 p22 Fig 24 publishes a recovery-time distribution **conditional** on the 12% of organisations fully-recovered at survey time. With 88% still recovering at the snapshot, the unconditional median is right-censored — only a lower bound can be derived. SANS sources publish no time-to-resilience-recovery median. |
| `VMC_ID_THREAT_INTELLIGENCE` | Median commercial threat-intelligence feed lag | No source publishes a canonical TI-feed-lag median. DBIR 2024 p22 Fig 20 publishes CISA-KEV-vs-non-KEV first-scan latency (5d vs 68d) — but that informs an *overlay multiplier* candidate (`cisa_kev_subscriber_overlay`), not a canonical TI-feed-lag τ. |
| `VMC_ID_CONTROL_MONITORING` | Median control-drift detection cadence | DBIR 2024 publishes only the CIS Controls *recommendation list* on p33 — no measured monitoring-cadence data. SANS sources publish qualitative monitoring guidance only, no cadence median. |
| `VMC_CORR_TREATMENT_SELECTION` | Median elapsed time from vulnerability identification to treatment-decision start | No SANS source publishes treatment-selection cadence. IBM CODB 2024 p18–19 Fig 16 / Fig 19 AI/automation-maturity-tier breakdown is the **total breach lifecycle by maturity**, NOT the "vuln-detection → remediation decision" elapsed time. Maturity-tier data informs a separate maturity-tier overlay candidate. |
| `DSC_ID_MISALIGNED` | Strategic-decision-quality time-to-detect | Governance-quality / strategic-decision-quality metric — not surveyed in any of the 5 Phase 0 primary sources. |

These sub-functions are calibratable in principle per Standard §2.3.
v3's strict primary-cite gate found no defensible 2024 primary source;
the reclassification is v3's evidentiary choice and revisitable when new
research becomes available. Per-org override (future work; see "Future
work" below) is the path to calibrated effectiveness values today. Until
the override layer ships, analysts input `[0, 1]` effectiveness via the
wizard; the calculator routes through Layer-1 multiplicative.

## Benchmark doc cross-reference

For analysts curating wizard hints or library entries, the companion
document at `docs/reference/control-class-operational-benchmarks.md`
lists typical day-counts per `(control_class × sub_function)` pair with
primary-source citations drawn from the same Phase 0 research.

The distinction matters:

- **This methodology doc** is the canonical source of truth for
  `TAU_BY_SUB_FUNCTION`. fair_cam math imports these values directly.
- **The benchmark doc** is a v3-layer reference: class-typical day-count
  hints to inform wizard input and library curation. It is **NOT
  imported by fair_cam math** — touching the benchmark doc cannot
  silently change calibration. The canonical τ table here is what
  matters for `opeff` computation.

## Authority disclaimer

Per FAIR-CAM Standard V1.0 §2.3: "The Standard deliberately does not
publish a closed-form formula composing Capability, Coverage, and
Reliability into a single Operational Effectiveness score."

Per local audit doc `fair-cam-standard-alignment.md` §8 preamble: "No
recommendation is made here."

PR μ.1 SELECTS exponential `exp(-elapsed_time / τ_sf)` as the v3
implementation choice — this is NOT a Standard-recommended shape. The
audit §8.3 enumerates this as one Option C candidate; PR μ.1 resolves
the open sub-decision by adopting it.

## Pinning

The post-#131 test landscape enforces multiple structural invariants
around the canonical τ table:

- `fair_cam/tests/calibration/test_elapsed_time_taus.py` — SHA-256
  digest pin of the literal `TAU_BY_SUB_FUNCTION` mapping AND the
  `test_all_elapsed_time_sub_functions_have_tau` completeness invariant
  asserting that the set of keys in `TAU_BY_SUB_FUNCTION` equals the set
  of `ELAPSED_TIME`-classified sub-functions in `SUB_FUNCTION_UNITS`.
  Adding an `ELAPSED_TIME` sub-function without a τ entry (or removing a
  τ entry without reclassifying its sub-function) fails the test.
- `tests/integration/test_fair_cam_v3_unit_type_parity.py` — v3 ↔
  fair_cam `SUB_FUNCTION_UNITS` lockstep invariant. v3 cannot drift away
  from fair_cam's unit-type assignments.
- `fair_cam/tests/data/test_comprehensive_controls_library.py` — SHA-256
  digest pin of the comprehensive controls library JSON shipped with
  fair_cam (introduced at T4 of issue #131).
- `fair_cam/tests/data/test_library_calibration_labels.py` — library
  source-provenance test enforcing `# calib:<entry_id>:*` markers
  threading each library entry's calibration values back to a primary
  source (introduced at T4 of issue #131).

Modifying any value in `TAU_BY_SUB_FUNCTION` requires:

1. Update the baseline digest in the pinning test.
2. Update this methodology doc (per-sub-function table) with the new
   value, anchor statistic, and primary citation.
3. Re-pin any backtest fixtures that depended on the old values, with
   side-by-side hand-math + actual code output per project convention
   ("Verification reporting").
4. PR description must call out the calibration change explicitly.

## Future work

- **Per-org τ override layer** (canonical+override pattern per project
  convention). PR μ.1's
  `get_canonical_tau()` accessor
  (`fair_cam/calibration/elapsed_time_taus.py`) is the slot-in point for
  a future wrapping accessor `get_tau(sub_function, org_id=None)`.
  Tracked by a follow-up issue (filed alongside #131 merge).
- **Per-industry τ tables** (process manufacturing, financial services,
  healthcare) — subsumed under per-org override.
- **IRIS sub-sector overlays applied to τ** — subsumed under per-org
  override.
- **Engine-path weak-AND wiring for FAIR-CAM §3.3** — separate follow-up
  (issue #130).
- **Per-sample post-MC subtractor** — architecturally cleaner than
  parameter-level subtractor; preserves triangular distribution shape.
- **Re-research dropped sub-functions** when IRIS 2024 full report
  becomes accessible OR a future industry survey publishes the relevant
  operational-time metric. Reclassification may reverse — the 5
  evidentially-deferred entries in §B above are explicitly revisitable
  on new data.

## References

- FAIR Controls Analytics Model (FAIR-CAM) Standard V1.0 — January
  2025. FAIR Institute.
- Local audit: `docs/reference/fair-cam-standard-alignment.md` §2.3,
  §3.3.3, §8, §8.3, §8.4.
- Calibration-source extractions (per-source accessed-date + permalink
  recorded in the YAML frontmatter of each file):
  - `docs/reference/calibration-sources/ibm_codb_2024.md`
  - `docs/reference/calibration-sources/dbir_2024.md`
  - `docs/reference/calibration-sources/sans_ir_2024.md`
  - `docs/reference/calibration-sources/veris_dataset.md`
  - `docs/reference/calibration-sources/iris_2024.md`
- Companion benchmark doc:
  `docs/reference/control-class-operational-benchmarks.md` (v3-layer
  class-typical hints; NOT imported by fair_cam math).
- Design doc with full side-by-side hand-math for the 2026-05-16 τ
  re-derivation: `docs/plans/2026-05-15-issue-131-tau-calibration-design.md`
  §3.
