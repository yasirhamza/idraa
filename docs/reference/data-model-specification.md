# Idraa Data Model Specification

> **Status: 2026-05-09 rewrite.** The previous version of this file was a
> 1658-line aspirational spec that drifted ~60% from the actual code (PRs ι/π
> excised most of the originally-described control-effectiveness fields; many
> calculations and entity types described here were never implemented). The
> rewrite below describes only what actually exists in the SQLAlchemy models
> and `fair_cam/` dataclasses. **The ORM modules are the source of truth — if
> this doc disagrees with the code, the code wins.** Anything the previous
> spec claimed but the code never had is enumerated under
> [§ Not implemented (was aspirational)](#not-implemented-was-aspirational).
>
> **Scope banner:** this doc covers the core entities only (Control,
> ControlFunctionAssignment, Scenario, RiskAnalysisRun, ScenarioLibraryEntry,
> Organization, User). Auth/MFA (passkeys, TOTP, recovery codes), `fx_rate`,
> `wizard_draft`, `run_samples`, and other tables added since are documented
> in code, not cataloged here — see the relevant `models/*.py` module for
> those.

## Source-of-truth modules

| Layer | Module |
|---|---|
| v3 ORM (SQLAlchemy) | `src/idraa/models/` |
| v3 form schemas (Pydantic) | `src/idraa/schemas/` |
| Risk-engine dataclasses | `fair_cam/models/` |
| v3 ↔ fair_cam adapter | `src/idraa/services/run_executor.py` |
| Enum definitions | `src/idraa/models/enums.py` + `fair_cam/models/sub_function.py` |
| Migrations | `alembic/versions/` |

## Core entities

### Control (`models/control.py`)

A security control. Per FAIR-CAM, the axis along which controls reduce risk
lives on `ControlFunctionAssignment` (one Control → many assignments, each
mapping the control to a specific FAIR-CAM sub-function with capability /
coverage / reliability values). Control itself carries identity + metadata,
not effectiveness.

Persisted columns:

| Column | Type | Notes |
|---|---|---|
| `id` | UUID | PK |
| `organization_id` | UUID | FK; multi-tenant scope |
| `name`, `description` | str | required / optional |
| `domain` | _Removed in issue #90._ | No longer persisted on Control. Derived at access time as `Control.domains: frozenset[ControlDomain]` via `subfunction_to_domain()` applied to each `ControlFunctionAssignment.sub_function`. FAIR-CAM Standard §2.2 (page 5) places domain at the sub-function level; multi-domain controls are now first-class. |
| `type` | `ControlType` enum | `TECHNICAL` / `ADMINISTRATIVE` / `PHYSICAL` |
| `annual_cost` | `Numeric(18, 2)` | Annual OPEX cost as `Decimal` with cent precision. NOT NULL, defaults to `Decimal("0")`. See [§ Cost](#cost-controlannual_cost-decimal). |
| `nist_csf_functions`, `iso_27001_domains`, `compliance_mappings` | JSON | Framework mappings |
| `skill_requirements`, `technology_dependencies` | JSON | Operational metadata |
| `applicable_industries`, `applicable_org_sizes` | JSON | Scoping metadata |
| `status` | `EntityStatus` enum | `DRAFT` / `ACTIVE` / `DEPRECATED` / `DELETED` |
| `version` | str | descriptive; see `row_version` for optimistic-lock |
| `row_version` | int | optimistic-lock primitive |

Effectiveness values (`capability_value`, `coverage`, `reliability`) live on
`ControlFunctionAssignment`, NOT on Control. The previous spec described a
flat-triple `control_strength` / `control_reliability` / `control_coverage`
on Control itself; **PR ι excised those fields** in favour of per-assignment
triples. See `tests/test_anti_regression.py` for the model-level guard.

### ControlFunctionAssignment (`models/control_function_assignment.py`)

One row per (control, FAIR-CAM sub-function) pairing.

| Column | Type | Notes |
|---|---|---|
| `id`, `organization_id` | UUID | |
| `control_id` | UUID | FK to Control |
| `sub_function` | `FairCamSubFunction` enum | 26 values across LEC/VMC/DSC; see `fair_cam/models/sub_function.py` |
| `capability_value` | float \| None | unit depends on sub_function — probability [0,1] or magnitude ≥0; NULL for ELAPSED_TIME-unit sub-functions |
| `coverage` | float | [0, 1] |
| `reliability` | float | [0, 1] |
| `confirmed_by_user_at` | datetime \| None | NULL = importer-default placeholder; surfaced via `/controls/maintenance` |
| `measured_by`, `measured_at` | UUID / datetime | who confirmed and when |

The unit for each sub-function is fixed in `fair_cam.models.sub_function.SUB_FUNCTION_UNITS`. The adapter in `run_executor.py` validates that capability_value matches the unit.

### Scenario (`models/scenario.py`)

A FAIR-parameterised analytical unit. Each scenario captures threat parameters
and a PERT distribution for each of TEF / Vulnerability / Primary Loss / Secondary Loss.

| Column | Type | Notes |
|---|---|---|
| `id`, `organization_id`, `created_by` | UUID | |
| `name` | str | required |
| `description` | str \| None | |
| `scenario_type` | `ScenarioType` enum | `CUSTOM` / `LIBRARY` |
| `source` | `ScenarioSource` enum | `EXPERT_JUDGMENT` / `LIBRARY_ENTRY` / etc. |
| `threat_category` | `ThreatCategory` enum | 12 values, includes OT-specific entries |
| `threat_actor_type` | `ThreatActorType` enum \| None | 6 values |
| `attack_vector` | str \| None | curated dropdown values, but stored free-text varchar(128) |
| `asset_class` | `AssetClass` enum \| None | 7 values, includes OT_SYSTEMS / SAFETY_SYSTEMS |
| `industry`, `revenue_tier` | str | calibration anchors — pinned at create time from organization profile (issue #56 interim UX) |
| `threat_event_frequency`, `vulnerability`, `primary_loss`, `secondary_loss` | dict (JSON) | PERT shape `{"distribution": "PERT", "low": ..., "mode": ..., "high": ...}` |
| `library_entry_id`, `library_entry_version` | UUID / int | nullable; populated when scenario was cloned from a `ScenarioLibraryEntry` |
| `mitigating_controls` | many-to-many → Control | scenario's default controls |
| `status` | `EntityStatus` | |
| `row_version` | int | optimistic lock |

### RiskAnalysisRun (`models/risk_analysis_run.py`)

A simulation run. Either SINGLE (one scenario) or AGGREGATE (multiple
scenarios summed for a portfolio view).

| Column | Type | Notes |
|---|---|---|
| `id`, `organization_id` | UUID | |
| `scenario_id` | UUID \| None | populated for SINGLE runs; NULL for AGGREGATE |
| `aggregate_scenario_ids` | list[UUID] (JSON) \| None | populated for AGGREGATE runs |
| `run_type` | `RunType` enum | `SINGLE` / `AGGREGATE` |
| `name` | str | analyst-supplied or auto-generated `Run YYYY-MM-DD HH:MM` |
| `mc_iterations` | int | Monte Carlo iteration count |
| `status` | `RunStatus` enum | `QUEUED` / `RUNNING` / `COMPLETED` / `FAILED` / `CANCELLED` |
| `inputs_hash` | str | reproducibility hash over the full input pin |
| `controls_snapshot` | dict (JSON) | frozen capture of Control state at run time (versioned: `snapshot_version: 1` legacy / `2` post-PR-ι) |
| `simulation_results` | dict (JSON) | slim summary: percentiles, VaR, expected shortfall, loss-exceedance curve. Populated on COMPLETED only. The heavy per-iteration sample arrays are NOT here — they were split off to the separate `run_samples` table (`models/run_samples.py`, 1:1 by `run_id`) for warm-page perf (#294 / PR #299). Architectural rule (per project convention) still holds: full Monte Carlo output is persisted across the two tables, not just summaries |
| `started_at`, `completed_at` | datetime | |

The simulation_results dict is opaque to SQL — query/filter on financial
metrics happens client-side after the run completes. Per-column financial
metrics (ROI, NPV, payback) are NOT persisted; see [§ Cost](#cost-controlannual_cost-decimal).

### ScenarioLibraryEntry (`models/scenario_library.py`)

Canonical library scenarios. Composite PK (`id`, `version`) — publishing a
correction means inserting `(id, version+1)`; rows are never mutated.

| Column | Type | Notes |
|---|---|---|
| `id`, `version` | UUID, int | composite PK |
| `slug` | str | stable across versions |
| `status` | `library_entry_status` | `draft` / `published` / `deprecated` |
| `threat_event_type`, `threat_actor_type`, `asset_class` | enums | |
| `attack_vector`, `tags` | str / JSON | |
| `description`, `example_incidents`, `source_citations`, `canonical_fair_gap` | text / JSON | narrative + provenance |
| `applicable_industries`, `applicable_sub_sectors`, `applicable_org_sizes` | JSON | filtering metadata |
| `threat_event_frequency`, `vulnerability`, `primary_loss`, `secondary_loss` | dict (JSON) | PERT shape |
| `suggested_control_ids` | list[str] (JSON) | references to catalog control slugs (curated across all 44 seed entries = 31 base + 13 extension; populated by P2c, extended by the 2026-06-02 content extension) |
| `standards_references` | dict \| None | NIST CSF / ISO 27001 / CIS — populated as Phase 2 work lands |
| `row_version` | int | |

Per-org override layer: `ScenarioLibraryOverride` (one row per `(organization_id, library_entry_id)` pair). Versions bump in-place on edit. Surfaced via `/library/overrides`.

### Organization (`models/organization.py`)

| Column | Type | Notes |
|---|---|---|
| `id` | UUID | |
| `name` | str | |
| `industry_type` | `IndustryType` enum | drives scenario calibration anchors (interim UX patch — see issue #56) |
| `organization_size` | `OrganizationSize` enum | |
| `annual_revenue` | Decimal \| None | source for derived `revenue_tier` (no separate column today) |
| `security_maturity`, `risk_appetite` | enums | |
| `has_cyber_insurance`, `insurance_limit`, `insurance_deductible` | bool / Numeric | optional; PR #46 hotfix accepts blank optional numerics |
| `compliance_requirements`, `regulatory_environment`, `geographic_regions`, `technology_stack` | JSON | comma-separated lists captured at /organization edit |
| `currency`, `language` | str | display preferences |

### User (`models/user.py`)

| Column | Type | Notes |
|---|---|---|
| `id`, `organization_id`, `created_by` | UUID | |
| `email`, `full_name` | str | email lowercased + stripped on insert; case-insensitive lookup |
| `password_hash` | str | argon2 |
| `role` | `UserRole` enum | `ANALYST` / `REVIEWER` / `ADMIN` / `VIEWER` |

### Cost (`Control.annual_cost: Decimal`)

Annual OPEX cost as `Numeric(18, 2)` — i.e., `Decimal` with cent
precision. NOT NULL, default `Decimal("0")` (both Python-side and DB
server_default). The `_is_zero_cost` maintenance check treats
`annual_cost == 0` as "needs attention" (importer-created controls +
wizard-created controls that leave the field blank both start at 0;
admin sets a real cost or confirms 0 deliberately via the maintenance
alert UI).

CapEx / NPV / other cost components are explicitly out of scope —
when needed, they land as additional typed columns with real
calculations (not a generic JSON dict). See issue #66 and the design
doc at `docs/plans/2026-05-14-issue-66-cost-model-decimal-design.md`.

## FAIR-CAM alignment

See `docs/reference/fair-cam-standard-alignment.md` for the field-by-field
audit against FAIR-CAM Standard V1.0 (January 2025). Key alignment points:

- **Control domains** match Standard §2 (LEC / VMC / DSC). Issue #90 made
  domain a derived multi-domain set (`Control.domains: frozenset[ControlDomain]`)
  computed from each assignment's sub-function via `subfunction_to_domain()`;
  the legacy single-valued `domain` column has been dropped.
- **Sub-functions** match Standard §3-5 (26 entries).
- **Effectiveness triples** (capability_value / coverage / reliability) are
  per-assignment per Standard §2.4.2-3, not flat on Control.
- **`annual_cost`** is Standard-orthogonal — Standard does not prescribe a
  cost model; ours is a project-original financial auxiliary.

## PR changelog

### PR μ.1 — `ControlAdjustment.loss_reduction_per_event`

PR μ.1 (2026-05-15) added `loss_reduction_per_event: float = 0.0` to
the `ControlAdjustment` dataclass at `fair_cam/models/risk_enhanced.py`.
The field is the CURRENCY-branch accumulator for the FAIR-CAM
`LEC_RESP_LOSS_REDUCTION` Loss-Magnitude subtractor.

**No snapshot version bump**: the field surfaces via the per-run
output channel `_control_adjustment_to_dict` in
`src/idraa/services/run_executor.py`, NOT via the snapshot.
`_snapshot_control_v2` is unchanged. (Plan-gate Arch-B1 fix:
`_snapshot_control_v2` runs BEFORE the calculator, so the per-run
output channel is the correct surface for calculator-derived fields.)

The field flows through to the executive PDF view-model via
`services/reports.py:build_control_breakdown_rows` and is surfaced
in the `ExecutivePdfData.control_breakdown_rows` list. Renderer consumers
can check `row.loss_reduction_label is not None` to filter controls with
an active CURRENCY-branch assignment.

## Not implemented (was aspirational)

The previous spec described these. None exist today. Listed so future
readers don't assume they do.

| Aspirational item | Status |
|---|---|
| `OrganizationContext` dataclass with risk_appetite / regulatory_requirements / insurance_coverage / peer_group_metrics | Never built. Org-level data on the `Organization` model is the extent. |
| `ImpactType`, `ImplementationStatus`, `PerformanceStatus` enums | Never built. v3 uses `EntityStatus` for status; impact-types and perf-status are not modelled. |
| Per-column financial outputs on RiskAnalysisRun (`baseline_risk`, `controlled_risk`, `risk_reduction_percentage`, `var_95`, `var_99`, `expected_shortfall_*`) | Never persisted as columns. All buried inside `simulation_results` JSON. Querying requires reading the JSON in the application layer. |
| `payback_period_months`, `roi_percentage`, `net_benefit`, `net_present_value`, `internal_rate_return`, the `ControlROI` dataclass that grouped them | Declared but never written by any code path. **Removed in this PR.** |
| Sensitivity analysis output | Spec described `sensitivity_analysis` field on RiskAnalysisRun; no module computes it. |
| `ControlValidator` / `RiskAnalysisValidator` / `DistributionValidator` / `ThreatScenarioValidator` classes | Never built. Validation lives on Pydantic schemas in `src/idraa/schemas/`. |
| Control attributes: `vendor`, `implementation_time_weeks`, `effectiveness`, `threat_prevention`, `detection_capability`, `response_speed`, `threat_coverage`, `failure_frequency_per_year`, `mean_time_to_recovery_hours`, `variance_impact`, `operational_efficacy`, `dependencies`, `synergies`, `performance_metrics` | Some moved to per-assignment shape via PR ι; others never built. |
| Scenario attributes: `contact_frequency`, `confidence_level`, `likelihood_assessment`, `review_frequency_months`, `last_reviewed`, `applicable_controls` (use `mitigating_controls` instead), `business_impact_description`, `regulatory_implications`, `industry_relevance` | Not in `Scenario` ORM. |

If/when one of these is genuinely needed, it lands as code first + spec
description second. **Don't put it in this doc until the code exists.**

## Phase scope

v3 Phase 1 explicitly does NOT include: SSO/SAML, multi-tenancy, billing,
signup flows, mobile, real-time telemetry, AI-assisted features, self-service,
marketing pages, SOC 2 / compliance artifacts. (Per project convention.)
