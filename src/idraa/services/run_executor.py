"""Background-task body for risk-analysis run execution.

This module's main entry point ``execute_run`` lands in Task F6. This
file scaffolds the module and lands the v3-to-fair_cam Control adapter
helper because F4's tests need it.
"""

from __future__ import annotations

import asyncio
import gc
import logging
import math
import uuid
from collections.abc import Callable
from typing import Any

import numpy as np
from fair_cam.models.composition_topology import GROUP_NODE_MAPPING, KAPPA_META_RELIABILITY
from fair_cam.models.control import Control as FairCamControl
from fair_cam.models.control import ControlDomain as FairCamControlDomain
from fair_cam.models.control import ControlType as FairCamControlType
from fair_cam.models.control import CostModel as FairCamCostModel
from fair_cam.models.control import FairCamControlFunctionAssignment
from fair_cam.models.sub_function import FairCamSubFunction
from fair_cam.risk_engine.control_aware import (
    AggregateEnhancedRisk,
)
from fair_cam.risk_engine.fair_core import (
    DistributionType,
    FAIRDistribution,
    FAIRParameters,
)
from fair_cam.risk_engine.group_composition import ComposedParts, build_composition_provenance
from fair_cam.risk_engine.group_composition_batched import (
    finalize_reduce_batched,
    stack_node_weight_arrays,
)
from fair_cam.risk_engine.native_control_aware import NativeControlAwareRiskCalculator
from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.config import get_settings
from idraa.db import _get_sessionmaker
from idraa.models._types import now_utc
from idraa.models.control import Control
from idraa.models.enums import (
    SUB_FUNCTION_UNITS,
    ControlDomain,
    ScenarioEffect,
    subfunction_to_domain,
)
from idraa.models.organization import Organization
from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType
from idraa.models.run_samples import RunSamples
from idraa.models.scenario import Scenario
from idraa.repositories.control_repo import ControlRepo
from idraa.repositories.scenario_repo import ScenarioRepo
from idraa.schemas.control import ControlFunctionAssignmentDTO
from idraa.schemas.run_snapshot import (
    ControlFunctionAssignmentSnapshotDTO,
    ControlSnapshotV2,
    ControlSnapshotV3,
)
from idraa.services.aggregate_run_view_model import displayed_control_order
from idraa.services.audit import AuditWriter
from idraa.services.fx_rates import FxRateService
from idraa.services.run_reaper import register_active_run, unregister_active_run
from idraa.services.sample_codec import encode_sample_arrays_streaming
from idraa.services.simulation_payload import (
    SIMULATION_RESULTS_SCHEMA_VERSION,
    split_simulation_payload,
)
from idraa.services.weight_robustness import (
    FINALIZE_TO_COMPOSE_COST_RATIO,
    WEIGHT_ROBUSTNESS_KEYS,
    WEIGHT_ROBUSTNESS_SPAWN_DOMAIN,
    EnsembleDraw,
    run_weight_ensemble,
    sample_ensemble_draw,
)

logger = logging.getLogger(__name__)


# v3 ControlDomain (StrEnum) → fair_cam ControlDomain (Enum).
# Values differ: v3 uses "variance_management"/"decision_support"; fair_cam
# uses "variance"/"decision". An explicit mapping avoids string-equality bugs.
_DOMAIN_MAP: dict[ControlDomain, FairCamControlDomain] = {
    ControlDomain.LOSS_EVENT: FairCamControlDomain.LOSS_EVENT,
    ControlDomain.VARIANCE_MANAGEMENT: FairCamControlDomain.VARIANCE_MANAGEMENT,
    ControlDomain.DECISION_SUPPORT: FairCamControlDomain.DECISION_SUPPORT,
}

_RESPONSE_TIME_SECONDS: dict[ControlDomain, float] = {
    ControlDomain.LOSS_EVENT: 60.0,
    ControlDomain.VARIANCE_MANAGEMENT: 60.0,
    ControlDomain.DECISION_SUPPORT: 3600.0,
}


def _response_time_default(domain: ControlDomain) -> float:
    """Heuristic default response time per FAIR-CAM control domain."""
    return _RESPONSE_TIME_SECONDS.get(domain, 300.0)


# Degenerate distribution used when the analyst leaves Secondary Loss blank.
# A point-mass at zero produces zero secondary-loss contribution to LM
# without breaking FAIRParameters' non-optional `secondary_loss` contract.
_ZERO_SECONDARY_LOSS = FAIRDistribution(
    distribution_type=DistributionType.UNIFORM,
    parameters={"low": 0.0, "high": 0.0},
)


def _dict_to_fair_distribution(payload: dict[str, Any]) -> FAIRDistribution:
    """Build a FAIRDistribution from the JSON dict the wizard writes.

    Expected shape: {"distribution": "pert" | "uniform" | ..., "low": float,
    "mode": float, "high": float}. Lowercase distribution kind matches
    DistributionType.value. Unknown kinds raise ValueError, caught by the
    executor's existing error path and written to RiskAnalysisRun.error_message.
    """
    kind_raw = payload.get("distribution", "pert")
    try:
        kind = DistributionType(str(kind_raw).lower())
    except ValueError as exc:
        raise ValueError(f"Unsupported distribution kind: {kind_raw!r}") from exc

    if kind in (DistributionType.PERT, DistributionType.TRIANGULAR):
        params = {
            "low": float(payload["low"]),
            "mode": float(payload["mode"]),
            "high": float(payload["high"]),
        }
    elif kind == DistributionType.UNIFORM:
        params = {"low": float(payload["low"]), "high": float(payload["high"])}
    elif kind == DistributionType.LOGNORMAL:
        # Epic B (#326): native log-space params; FAIREngine samples
        # rng.lognormal(mean, sigma). Finite/positive-sigma is enforced
        # upstream by services/fair_cam_validation._validate_finite at
        # store time; this adapter coerces types only.
        params = {"mean": float(payload["mean"]), "sigma": float(payload["sigma"])}
    else:
        raise ValueError(
            f"Wizard does not produce {kind.value} distributions; got payload={payload!r}"
        )
    return FAIRDistribution(distribution_type=kind, parameters=params)


def _scenario_to_fair_parameters(scenario: Scenario) -> FAIRParameters:
    """Build FAIRParameters from the scenario's stored distributions.

    Replaces the deceased calibrate_scenario() as the v3 -> fair_cam parameter
    bridge. Reads scenario.{threat_event_frequency, vulnerability, primary_loss,
    secondary_loss} JSON columns directly -- no industry/IRIS/overlay calibration.

    secondary_loss=None on the scenario row -> degenerate point-mass-at-zero
    distribution (FAIRParameters.secondary_loss is non-optional).
    """
    sl_payload = scenario.secondary_loss
    secondary = _dict_to_fair_distribution(sl_payload) if sl_payload else _ZERO_SECONDARY_LOSS
    return FAIRParameters(
        threat_event_frequency=_dict_to_fair_distribution(scenario.threat_event_frequency),
        vulnerability=_dict_to_fair_distribution(scenario.vulnerability),
        primary_loss=_dict_to_fair_distribution(scenario.primary_loss),
        secondary_loss=secondary,
    )


def _scenario_availability_self_detects(scenario: Scenario) -> bool:
    """True iff the scenario's dominant Effect is AVAILABILITY — the only effect
    whose loss event self-manifests, satisfying the Detection->Response AND
    precondition intrinsically (FAIR-CAM §3.3.2 p.19). NULL/C/I -> False
    (detection-gated, §3.3 p.18). Structural boolean, not a calibrated weight."""
    return scenario.effect == ScenarioEffect.AVAILABILITY


def _v3_to_fair_cam_control(v3_ctrl: Control) -> FairCamControl:
    """Map a v3 Control + its assignments to a fair_cam.FairCamControl.

    Iterates ALL assignments (not just assignments[0]) — fixes the κ latent
    silent-data-loss bug. Per project-wide policy
    (feedback_data_contract_enforcement.md).

    Raises ValueError on empty assignments (defense in depth — ControlForm
    enforces min_length=1).

    ELAPSED_TIME / CURRENCY unit assignments pass through unchanged. fair_cam's
    compose_group_effectiveness (κ T9) excludes them from operand lists at
    composition time, so unit-type filtering happens downstream.

    Defense-in-depth bounds checks on coverage/reliability are inherited from
    the prior branch-(d) builder (DTO guard bypass safety per spec §8.3).
    """
    if not v3_ctrl.assignments:
        raise ValueError(
            f"Control {v3_ctrl.id} has no assignments; ControlForm enforces "
            f"min_length=1 — defense in depth"
        )
    fc_assignments: list[FairCamControlFunctionAssignment] = []
    for a in v3_ctrl.assignments:
        # Issue #209: a cleared (NULL) capability_value is NOT rejected here.
        # fair_cam handles NULL via its documented opeff(median)=0.5 midpoint
        # anchor — composition.compute_assignment_part returns 0.5 * coverage
        # for a NULL capability_value (issue #131 Arch3-N1; the reliability
        # factor is applied by the caller since Slice 2 #439 folded the
        # standalone _null_safe_default helper into compute_assignment_part) —
        # and the clear-capability UI modal promises that graceful fallback. We pass capability_value (possibly None) straight
        # through to fair_cam; the snapshot breakdown records capability_was_null
        # for the affected assignment (effectiveness.py). The prior hard-reject
        # gate (T11 PR κ / paranoid-review fix S3) was stale — it blocked the
        # path #131 fixed at the fair_cam layer.
        #
        # Defense-in-depth bounds — match existing branch-(d)
        if not (0.0 <= a.coverage <= 1.0):
            raise ValueError(
                f"Control {v3_ctrl.id} assignment.coverage={a.coverage} "
                f"out of [0, 1] bounds — DTO guard bypass detected"
            )
        if not (0.0 <= a.reliability <= 1.0):
            raise ValueError(
                f"Control {v3_ctrl.id} assignment.reliability={a.reliability} "
                f"out of [0, 1] bounds — DTO guard bypass detected"
            )
        # v3 enum and fair_cam enum have parallel values — explicit conversion
        fc_sub_function = FairCamSubFunction(a.sub_function.value)
        fc_assignments.append(
            FairCamControlFunctionAssignment(
                sub_function=fc_sub_function,
                capability_value=a.capability_value,
                coverage=a.coverage,
                reliability=a.reliability,
                degradation_rate=0.0,  # PR μ supplies normative defaults
            )
        )
    # Bridge v3's ``annual_cost: Decimal`` (NOT NULL, default 0) to
    # fair_cam's float-based CostModel. fair_cam's API is unchanged
    # (CLAUDE.md: fair_cam is source of truth for FAIR math); we coerce
    # at the v3 boundary.
    annual_cost = float(v3_ctrl.annual_cost)
    # Issue #90: pick a representative domain from assignments[0] instead of
    # reading the deprecated v3 Control.domain column. fair_cam's
    # FairCamControl.domain remains a single-domain field (its math now
    # iterates assignments internally post-issue-90 in Task 4). The
    # empty-assignments guard above ensures assignments[0] is safe to
    # dereference — DO NOT remove that guard (plan-gate fix CR-I1).
    asn0 = v3_ctrl.assignments[0]  # adapter-iter: ok — issue #90 rep-domain scalar
    representative_domain = subfunction_to_domain(asn0.sub_function)
    fair_cam_domain = _DOMAIN_MAP[representative_domain]
    return FairCamControl(
        control_id=str(v3_ctrl.id),
        name=v3_ctrl.name,
        # CRITICAL: domain via _DOMAIN_MAP — v3's ControlDomain != fair_cam's FairCamControlDomain
        domain=fair_cam_domain,
        control_type=FairCamControlType(v3_ctrl.type.value),
        assignments=fc_assignments,
        cost_model=FairCamCostModel(annual_cost=annual_cost),
        response_time_seconds=_response_time_default(representative_domain),
        recovery_time_hours=1.0,
    )


def _snapshot_control_v2(c: Control) -> ControlSnapshotV2:
    """Capture a Control's run-time state as a ControlSnapshotV2 Pydantic model.

    Returns a ControlSnapshotV2 instance (not a dict). Call sites that need a
    JSON-serialisable dict for DB storage must call .model_dump(mode="json").

    DEPRECATED FOR NEW WRITES (issue #131 T6.5): new runs persist V3 snapshots
    via ``_snapshot_control_v3`` so per-assignment ``unit_type`` is captured at
    write time. ``_snapshot_control_v2`` is retained as a reference/legacy
    constructor — historical V2 records remain immutable audit records and the
    discriminated union routes them to ``ControlSnapshotV2`` on read.

    Always captures actual assignment values regardless of unit type. The
    OQ7 safe-default logic applies only to the fair_cam bridge in
    _v3_to_fair_cam_control. Snapshot data is never defaulted. (OQ7)

    Forensic-attribution invariant (paranoid-review fix S1): measured_by and
    derived_from_assignment_id MUST be preserved. (spec §5.3, audit §9.5)
    """
    return ControlSnapshotV2(
        snapshot_version=2,
        control_id=str(c.id),
        name=c.name,
        # Issue #90: emit the derived domain SET (sorted list[str]) instead
        # of the deprecated singular Control.domain column value.
        domains=sorted(d.value for d in c.domains),
        type=c.type.value,
        assignments=[
            ControlFunctionAssignmentDTO(
                sub_function=a.sub_function,
                capability_value=a.capability_value,
                coverage=a.coverage,
                reliability=a.reliability,
                confirmed_by_user_at=a.confirmed_by_user_at,
                measured_at=a.measured_at,
                measured_by=a.measured_by,
                derived_from_assignment_id=a.derived_from_assignment_id,
            )
            for a in c.assignments
        ],
    )


def _snapshot_control_v3(c: Control) -> ControlSnapshotV3:
    """Capture a Control's run-time state as a ControlSnapshotV3 Pydantic model.

    Written by new runs from issue #131 T6.5 onward. Returns a
    ``ControlSnapshotV3`` instance (not a dict). Call sites that need a
    JSON-serialisable dict for DB storage must call ``.model_dump(mode='json')``.

    Captures per-assignment ``unit_type`` from ``SUB_FUNCTION_UNITS`` at WRITE
    TIME. Future SUB_FUNCTION_UNITS mutations do NOT mutate the persisted
    ``unit_type`` — the snapshot is byte-immutable per the audit-record policy.
    This is the central V3 contract: a V3 snapshot's ``capability_value``
    interpretation is locked to the ``unit_type`` carried alongside it.

    Always captures actual assignment values regardless of unit type. The
    OQ7 safe-default logic applies only to the fair_cam bridge in
    _v3_to_fair_cam_control. Snapshot data is never defaulted. (OQ7)

    Forensic-attribution invariant (M-B1; mirrors V2's paranoid-review fix
    S1+S2): ``confirmed_by_user_at``, ``measured_at``, ``measured_by``, and
    ``derived_from_assignment_id`` MUST be preserved per spec §5.3 and
    audit §9.5 so the snapshot is re-derivable under historical operator
    and derivation provenance, not just the historical unit-type
    interpretation.

    Issue #131 T6.5; Arch-B3 / Sec-B3 plan-gate.
    """
    return ControlSnapshotV3(
        snapshot_version=3,
        control_id=str(c.id),
        name=c.name,
        # Issue #90: emit the derived domain SET (sorted list[str]).
        domains=sorted(d.value for d in c.domains),
        type=c.type.value,
        assignments=[
            ControlFunctionAssignmentSnapshotDTO(
                sub_function=a.sub_function,
                capability_value=a.capability_value,
                coverage=a.coverage,
                reliability=a.reliability,
                # Issue #131 T6.5: capture unit_type at write time so re-runs
                # of THIS V3 snapshot ignore future SUB_FUNCTION_UNITS mutations.
                unit_type=SUB_FUNCTION_UNITS[a.sub_function],
                # M-B1: forensic-attribution invariants (spec §5.3, audit §9.5).
                confirmed_by_user_at=a.confirmed_by_user_at,
                measured_at=a.measured_at,
                measured_by=a.measured_by,
                derived_from_assignment_id=a.derived_from_assignment_id,
            )
            for a in c.assignments
        ],
    )


def _build_scenario_inputs_snapshot(scenarios: list[Scenario]) -> dict[str, Any]:
    """T2 (#351): capture scenario FAIR distribution inputs at execution time.

    Called BEFORE the engine call so the snapshot reflects what was actually
    executed. Serializes TEF/Vuln/PL/SL dicts directly from the loaded ORM
    rows — these are the as-executed FAIR distribution parameters.

    Shape::

        {
          "scenarios": [
            {
              "scenario_id": str,
              "scenario_name": str,
              "threat_event_frequency": dict,
              "vulnerability": dict,
              "primary_loss": dict,
              "secondary_loss": dict | None,
              "effect": str | None,
            }, ...
          ]
        }
    """
    return {
        "scenarios": [
            {
                "scenario_id": str(sc.id),
                "scenario_name": sc.name,
                "threat_event_frequency": sc.threat_event_frequency,
                "vulnerability": sc.vulnerability,
                "primary_loss": sc.primary_loss,
                "secondary_loss": sc.secondary_loss,
                "effect": sc.effect.value if sc.effect else None,
            }
            for sc in scenarios
        ]
    }


async def _build_presentation_fx_snapshot(
    session: AsyncSession, org_id: uuid.UUID
) -> dict[str, Any] | None:
    """P3: capture the org's reporting-currency FX rate at run calculation time.

    Returns None for USD-reporting runs (no conversion needed) and when no
    active rate exists (the run will fall back to USD identity at render time).
    Stored as a pure local; written inside the guarded UPDATE…WHERE status=RUNNING
    so it never marks the ORM dirty and cannot trigger an unguarded autoflush.

    Shape: {code, usd_rate (str), as_of_date (str), source}
    """
    org = await session.get(Organization, org_id)  # executor doesn't have org in scope
    code = (org.preferred_currency if org else "USD") or "USD"
    if code == "USD":
        return None
    rate = await FxRateService(session).active_rate(org_id, code)
    if rate is None:
        return None
    return {
        "code": rate.code,
        "usd_rate": str(rate.usd_rate),
        "as_of_date": str(rate.as_of_date),
        "source": rate.source,
    }


def _build_results_payload(enhanced: Any) -> dict[str, Any]:
    return {
        "base_risk": _fair_risk_to_dict(enhanced.base_risk),
        "residual_risk": _fair_risk_to_dict(enhanced.residual_risk),
        "control_adjustments": [
            _control_adjustment_to_dict(adj) for adj in enhanced.control_adjustments
        ],
        "cost_summary": _build_cost_summary(enhanced),
        # Issue #202: empirical central-95% percentile band (p2.5/p97.5) over the
        # residual MC samples — replaces the retired input-derived heuristic.
        "confidence_intervals": _build_loss_percentile_band(enhanced.residual_risk),
        "loss_exceedance_curve": _build_loss_exceedance_curve(enhanced.residual_risk),
        "exceedance_probability_curve": _build_exceedance_probability_curve(enhanced.residual_risk),
        # #130 Task 8 / D6 / spec §9: code-constant composition provenance for the
        # FAIR-grounding UX. EXPLICIT allowlist key (it is NOT read off `enhanced`
        # — it is sourced solely from the static topology tables, so it is
        # safe-render-invariant). Pre-#130 consumers use
        # `.get("composition_provenance", [])`. LEC + VMC only (DSC §5.1 pending,
        # N-M8). `build_composition_provenance` runs ONCE per payload here — never
        # inside the pyfair sampling loop (perf guard, see fair_cam test
        # `test_compose_groups_called_once_per_scenario`).
        "composition_provenance": build_composition_provenance(),
    }


def _build_aggregate_results_payload(aggregate: AggregateEnhancedRisk) -> dict[str, Any]:
    """Serialize AggregateEnhancedRisk -> simulation_results JSON.

    Topology mirrors the SINGLE payload at the per-scenario level (each
    per_scenario entry is a full SINGLE-shape payload), then adds the
    aggregate FairMetaModel rollups.
    """
    # Dedup'd cost across scenarios. The same control may appear as a
    # mitigating control on multiple scenarios in the run; we count its
    # annual_cost ONCE in the aggregate (the org pays for the control
    # once per year regardless of how many scenarios it touches).
    unique_costs_by_control: dict[str, float] = {}
    for ps in aggregate.per_scenario:
        for adj in getattr(ps, "control_adjustments", []) or []:
            cid = str(adj.control_id) if hasattr(adj, "control_id") else None
            if cid is None:
                continue
            unique_costs_by_control[cid] = float(getattr(adj, "control_cost", 0.0) or 0.0)
    aggregate_total_cost = sum(unique_costs_by_control.values())
    aggregate_risk_reduction = float(
        getattr(aggregate.aggregate_without_controls, "annualized_loss_expectancy", 0.0) or 0.0
    ) - float(getattr(aggregate.aggregate_with_controls, "annualized_loss_expectancy", 0.0) or 0.0)
    aggregate_roi: float | None = (
        aggregate_risk_reduction / aggregate_total_cost if aggregate_total_cost > 0 else None
    )
    aggregate_cost_summary = {
        "total_annual_cost": aggregate_total_cost,
        "total_risk_reduction": aggregate_risk_reduction,
        "net_benefit": aggregate_risk_reduction - aggregate_total_cost,
        "aggregate_roi": aggregate_roi,
        "n_unique_controls": len(unique_costs_by_control),
    }

    return {
        "per_scenario": [
            {
                "scenario_id": ps.scenario_id,
                "scenario_name": ps.scenario_name,
                **_build_results_payload(ps),
            }
            for ps in aggregate.per_scenario
        ],
        "cost_summary": aggregate_cost_summary,
        # Both curves share a single union log-grid so the dual-LEC chart can
        # render them on identical x-axes — at a loss above its own sample max
        # a curve correctly drops to probability=0; at a loss below its own
        # sample min it correctly reads near probability=1.
        **_build_aggregate_lec_pair(aggregate),
        "dual_epc": _build_aggregate_epc_pair(aggregate),
        "control_value": {
            "dollars": aggregate.control_value_dollars,
            "percent": aggregate.control_value_percent,
        },
        # Issue #202: empirical central-95% percentile band (p2.5/p97.5) over the
        # aggregate-with-controls metamodel 'Risk' samples — the per-iteration
        # portfolio TOTAL annualized loss (elementwise sum of per-scenario Risk
        # arrays). Replaces the retired heuristic, which reused the SINGLE
        # base-vs-residual SE band on the aggregate side with a documented-broken
        # n_simulations (per-scenario count, not N*). sample_size now reads the
        # true n_simulations off the aggregate FAIRRisk.
        "confidence_intervals": _build_loss_percentile_band(aggregate.aggregate_with_controls),
        "n_scenarios": aggregate.n_scenarios,
        "n_simulations": aggregate.n_simulations,
        # #130 Task 8: same code-constant provenance at the aggregate top level
        # (each per_scenario entry already carries it via _build_results_payload).
        "composition_provenance": build_composition_provenance(),
    }


# B-Sec-B1: per-scenario control cap — above this a scenario skips attribution.
MAX_ATTRIBUTION_CONTROLS = 64
# B-Sec-I1(round2): GLOBAL per-run v(S)-evaluation budget across ALL scenarios.
# compose_groups is pure Python and holds the GIL, so even off-thread it saturates
# the single vCPU; this caps total wall-clock regardless of scenario count (the
# "hundreds of scenarios" envelope). ~2M closed-form evals ≈ tens of seconds
# worst-case on the 1-vCPU VM (~19µs/eval measured). A per-draw ensemble
# composition-cache HIT (finalize_composition alone, on a cached ComposedParts
# for a representative 5-10-control subset) measures ~6.6µs (timeit, 20k iters,
# Task 8 Step 3b) — cheaper than a full ~19µs eval (as expected, since it skips
# re-composing the meta side) but NOT free; see the _comp_cache note below and
# issue #432.
MAX_ATTRIBUTION_TOTAL_EVALS = 2_000_000


def _scenario_eval_cost(
    n: int, exact_max_n: int = 12, sample_permutations: int | None = None
) -> int:
    """Estimated v(S) evaluations for a scenario with n controls (matches the
    service's branch: 2^n exact, else ~m*n sampled).

    ``sample_permutations`` overrides the Maleki count for the sampled branch
    (the ENSEMBLE per-draw path uses a reduced count) so the eval-budget /
    K-degrade accounting reflects the cheaper per-draw cost. None => full Maleki.

    Omits the service's constant +1 v(empty) probe and the n=1 short-circuit —
    O(1) noise vs the 2M budget.
    """
    if n <= exact_max_n:
        return int(2**n)
    if sample_permutations is not None:
        return int(sample_permutations) * n
    from idraa.services.shapley import maleki_sample_count

    return int(maleki_sample_count(0.02, 0.05)) * n


def _make_subset_value_fn(
    registry: Any,
    comp_cache: dict[frozenset[str], ComposedParts],
    base_cache: dict[str, tuple[float, float, float, float, float]],
    node_mapping: Any,
    kappa: float,
    per_scenario_availability: dict[str, bool] | None,
    statistic: str = "typical",
) -> Callable[[frozenset[str], str, FAIRParameters], float]:
    """Single source of truth for v(S): the closed-form modeled reduction for a
    control subset, shared by the Shapley pass and the leave-one-out pass so the
    two can never diverge (the same no-re-derivation rule that binds the engine).

    Returns ``value_fn(subset, sid, rp) -> float``. Semantics are exactly the
    former ``_compute_shapley_by_scenario`` inner closure: per-scenario base ALE
    memoised in ``base_cache``; κ- and weight-invariant ``precompose_parts``
    memoised in ``comp_cache`` keyed by ``subset`` alone (#432); per-call
    ``finalize_composition(parts, kappa)`` (cheap RELATIVE to a full
    compose_groups, ~6.6µs/hit — #432 must price cache hits at this, not zero);
    availability gating per scenario (§3.3.2 p.19).
    """
    from fair_cam.risk_engine.control_attribution import (
        reduction_from_composition,
        scenario_base_ale,
    )
    from fair_cam.risk_engine.group_composition import finalize_composition, precompose_parts

    def value_fn(subset: frozenset[str], sid: str, rp: FAIRParameters) -> float:
        avail = (
            per_scenario_availability.get(sid, False)
            if per_scenario_availability is not None
            else False
        )
        base = base_cache.get(sid)
        if base is None:  # weight-invariant base ALE; once per scenario within this call/draw
            base = scenario_base_ale(rp, statistic)
            base_cache[sid] = base
        # #432: keyed by SUBSET alone — ComposedParts is a pure function of the
        # control subset's authored assignments (kappa- and weight-invariant;
        # scenario effect enters later at the multiplier mapping, which is NOT
        # cached), so identical subsets dedupe ACROSS scenarios in a run.
        ckey = subset
        parts = comp_cache.get(ckey)
        if parts is None:  # κ- AND weight-invariant precomposition, cached across draws
            ctrls = [registry.get_control(cid) for cid in subset]
            if any(c is None for c in ctrls):  # universe-validated; defensive (B-Arch-N1)
                raise ValueError("unknown control id in attribution subset")
            parts = precompose_parts(ctrls)
            comp_cache[ckey] = parts
        comp = finalize_composition(parts, kappa=kappa)
        return float(
            reduction_from_composition(base, comp, node_mapping, availability_self_detection=avail)
        )

    return value_fn


def _compute_shapley_by_scenario(
    calculator: Any,
    per_scenario_inputs: list[tuple[str, str, Any]],
    per_scenario_control_ids: dict[str, list[str]] | None,
    universe_control_ids: list[str],
    *,
    max_controls: int = MAX_ATTRIBUTION_CONTROLS,
    total_eval_budget: int = MAX_ATTRIBUTION_TOTAL_EVALS,
    node_mapping: Any = None,
    sample_permutations: int | None = None,
    kappa: float = KAPPA_META_RELIABILITY,
    composition_cache: dict[frozenset[str], ComposedParts] | None = None,
    per_scenario_availability: dict[str, bool] | None = None,
    statistic: str = "typical",
) -> tuple[dict[str, dict[str, float]], list[tuple[str, str]]]:
    """Per-scenario Shapley attribution. Returns (by_scenario, skipped) where
    `skipped` is [(scenario_id, reason)], reason in {"over_cap","over_budget","error"}.

    v(S) is the closed-form modeled reduction for control-subset S (fair_cam) —
    cheap arithmetic, not a Monte-Carlo run; the averaging is domain-agnostic
    (services/shapley.py). The None branch mirrors the engine's full-universe
    fallback in calculate_aggregate_enhanced_risk, so per-scenario Σφ equals the
    closed-form modeled reduction v(N) for the scenario's applied subset (≈ the MC
    headline base-residual ALE), NOT the sum of the standalone per-control cells —
    efficiency redistributes the overlap the standalone cells double-count.

    Bounds (B-Sec-B1 / B-Sec-I1-r2): a scenario over `max_controls` is skipped;
    once the cumulative estimated eval count would exceed `total_eval_budget`, all
    remaining scenarios are skipped — so total compute is bounded irrespective of
    scenario count. Budget drop-selection is order-dependent (first-come consumes
    the budget), deterministic given stable input order, and fully audited.
    compose_groups is GIL-bound, so the caller runs this via asyncio.to_thread to
    keep it off the event-loop hot path (the server stays responsive but the
    single vCPU is CPU-saturated for the duration — NOT free parallelism).
    ``kappa`` — the meta→reliability coupling strength forwarded into every
    per-draw ``finalize_composition`` (Slice 2 #439). Defaults to the canonical
    :data:`KAPPA_META_RELIABILITY`; the weight-robustness ensemble (Task 4)
    perturbs it per draw. The two canonical Shapley call sites leave the default.

    ``composition_cache`` — optional persistent ``{subset:
    ComposedParts}``. The cache now stores the κ- AND weight-invariant
    ``ComposedParts`` from ``precompose_parts`` (E_meta + the reliability-free
    LEC opeff parts + the meta diagnostics), NOT a finished ``GroupComposition``.
    Per subset, ``precompose_parts`` runs ONCE (cached across every κ and weight
    draw); each call then does ``finalize_composition(parts, kappa=kappa)`` +
    ``reduction_from_composition(base, comp, node_mapping)``.
    ``finalize_composition`` is the per-draw step and is NOT a ~free dict lookup:
    it rebuilds the LEC opeff buckets (applying ``r_eff``) and re-runs the LEC
    leaf/pair group pass, so it is CHEAP RELATIVE TO a full ``compose_groups``
    (which additionally re-composes the meta side), but has a real per-hit cost:
    ~6.6us measured at Task 8 (timeit, 20k iterations, representative
    5-10-control subset) — issue #432's future cache-credit model must price
    cache hits at this ``finalize_composition`` cost, not at zero. The
    weight-robustness ensemble passes ONE cache shared across all weight AND κ
    draws so the expensive precomposition for a subset is computed once (#419 /
    #439 perf refactor — full Shapley precision, ~K x fewer precompositions where
    K = the ensemble draw count, settings.weight_ensemble_draws).
    None -> a fresh per-call cache (still dedups repeated subsets within one pass).

    ``per_scenario_availability`` — {scenario_id: availability_self_detection};
    forwarded into the per-draw ``reduction_from_composition`` so availability
    scenarios credit raw LEC_RESPONSE (§3.3.2 p.19). Default None = all
    detection-gated.

    Per-scenario errors degrade that scenario (reason "error"), never raising
    (B-Arch-N3)."""
    from idraa.services.shapley import shapley_values

    # Key-set validation: every key in per_scenario_availability must correspond to a
    # scenario that is actually present in per_scenario_inputs. A mis-keyed entry
    # would silently fail to credit the scenario's recovery controls via the .get()
    # default=False path — catch it loudly instead (methodology carry-forward, Task 5).
    if per_scenario_availability is not None:
        _valid_sids = {sid for sid, *_ in per_scenario_inputs}
        _stray = set(per_scenario_availability) - _valid_sids
        if _stray:
            raise ValueError(
                f"per_scenario_availability contains keys not in per_scenario_inputs: "
                f"{sorted(_stray)}. A mis-keyed entry silently under-credits recovery controls."
            )

    registry = calculator.control_registry
    out: dict[str, dict[str, float]] = {}
    skipped: list[tuple[str, str]] = []
    spent = 0
    _comp_cache = composition_cache if composition_cache is not None else {}
    _base_cache: dict[str, tuple[float, float, float, float, float]] = {}
    # (The #543 ``subset_value_fn`` injection seam was removed once the ensemble
    # moved to `_compute_shapley_by_scenario_batched` — this scalar fn is now
    # exclusively the canonical/displayed single-game pass.)
    _shared_value_fn = _make_subset_value_fn(
        registry,
        _comp_cache,
        _base_cache,
        node_mapping,
        kappa,
        per_scenario_availability,
        statistic=statistic,
    )
    for scenario_id, _name, risk_params in per_scenario_inputs:
        if per_scenario_control_ids is not None:
            cids = list(per_scenario_control_ids.get(scenario_id, []))
        else:
            cids = list(universe_control_ids)  # engine applies the full universe to all scenarios
        if not cids:
            out[scenario_id] = {}
            continue
        if len(cids) > max_controls:
            skipped.append((scenario_id, "over_cap"))
            continue
        cost = _scenario_eval_cost(len(cids), sample_permutations=sample_permutations)
        if spent + cost > total_eval_budget:
            skipped.append((scenario_id, "over_budget"))
            continue
        spent += cost

        def _value_fn(
            subset: frozenset[str],
            sid: str = scenario_id,
            rp: FAIRParameters = risk_params,
        ) -> float:
            # v(S) itself lives in _make_subset_value_fn — SHARED with the
            # leave-one-out pass so the two attributions can never diverge.
            return _shared_value_fn(subset, sid, rp)

        try:
            # sample_permutations: reduced perms on the ENSEMBLE per-draw path;
            # None (the canonical/displayed pass) -> shapley_values uses full Maleki.
            out[scenario_id] = shapley_values(
                cids, _value_fn, sample_permutations=sample_permutations
            )
        except Exception:  # degrade this scenario, never fail the run (B-Arch-N3)
            logger.exception(
                "shapley failed for scenario %s; degrading to unavailable", scenario_id
            )
            skipped.append((scenario_id, "error"))
    return out, skipped


def _compute_shapley_by_scenario_batched(
    per_scenario_inputs: list[tuple[str, str, Any]],
    per_scenario_control_ids: dict[str, list[str]] | None,
    universe_control_ids: list[str],
    subset_value_fn_vec: Callable[[frozenset[str], str, FAIRParameters], np.ndarray],
    k_draws: int,
    *,
    max_controls: int = MAX_ATTRIBUTION_CONTROLS,
    total_eval_budget: int = MAX_ATTRIBUTION_TOTAL_EVALS,
    sample_permutations: int | None = None,
    per_scenario_availability: dict[str, bool] | None = None,
) -> tuple[dict[str, dict[str, np.ndarray]], list[tuple[str, str]]]:
    """K-draw vector sibling of :func:`_compute_shapley_by_scenario` for the
    weight-robustness ensemble (#419/#439 walk batching).

    The per-draw Shapley games differ ONLY in the value function's (kappa,
    node-mapping) parameters, and the coalition walk is deterministic (fixed
    Shapley seed) — so instead of re-running the combinatorics once per draw,
    walk ONCE via ``shapley_values_batched`` with a vector-valued v(S). Returns
    ``{scenario_id: {control_id: ndarray(K,)}}``; slicing ``[k]`` is
    bit-identical to a scalar `_compute_shapley_by_scenario` pass for draw k
    (pinned by tests/services/test_shapley_batched.py + the ensemble golden).

    The skip skeleton (cids selection, over_cap, cost/budget accounting, error
    degrade) MIRRORS the scalar function line-for-line — the eval-budget
    accounting stays PER-GAME (not multiplied by K) because the pinned #432
    cache-credited cost model already prices the batched value computation;
    draws share the walk, they do not repeat it. Keep the two skeletons in
    sync: any change to the scalar loop's skip logic must land here too (the
    ensemble golden gate catches divergence).
    """
    from idraa.services.shapley import shapley_values_batched

    if per_scenario_availability is not None:
        _valid_sids = {sid for sid, *_ in per_scenario_inputs}
        _stray = set(per_scenario_availability) - _valid_sids
        if _stray:
            raise ValueError(
                f"per_scenario_availability contains keys not in per_scenario_inputs: "
                f"{sorted(_stray)}. A mis-keyed entry silently under-credits recovery controls."
            )

    out: dict[str, dict[str, np.ndarray]] = {}
    skipped: list[tuple[str, str]] = []
    spent = 0
    for scenario_id, _name, risk_params in per_scenario_inputs:
        if per_scenario_control_ids is not None:
            cids = list(per_scenario_control_ids.get(scenario_id, []))
        else:
            cids = list(universe_control_ids)  # engine applies the full universe to all scenarios
        if not cids:
            out[scenario_id] = {}
            continue
        if len(cids) > max_controls:
            skipped.append((scenario_id, "over_cap"))
            continue
        cost = _scenario_eval_cost(len(cids), sample_permutations=sample_permutations)
        if spent + cost > total_eval_budget:
            skipped.append((scenario_id, "over_budget"))
            continue
        spent += cost

        def _value_fn_vec(
            subset: frozenset[str],
            sid: str = scenario_id,
            rp: FAIRParameters = risk_params,
        ) -> np.ndarray:
            return subset_value_fn_vec(subset, sid, rp)

        try:
            out[scenario_id] = shapley_values_batched(
                cids, _value_fn_vec, k_draws, sample_permutations=sample_permutations
            )
        except Exception:  # degrade this scenario, never fail the run (B-Arch-N3)
            logger.exception(
                "batched shapley failed for scenario %s; degrading to unavailable", scenario_id
            )
            skipped.append((scenario_id, "error"))
    return out, skipped


def _sanitize_shapley(
    shapley_by_scenario: dict[str, dict[str, float]],
) -> tuple[dict[str, dict[str, float]], list[str]]:
    """Drop any scenario with a non-finite Shapley value (degrade, do NOT fail the
    run — B-Arch-I4/B-Sec-I1). Returns (clean_map, dropped_scenario_ids).

    The #306->#307 precedent rejects non-finite at FAIR INPUT pre-run to prevent
    Monte-Carlo corruption. Shapley is a POST-run display derivation; a non-finite
    cell must not discard an already-valid, expensive MC run. The caller writes a
    run.non_finite_shapley audit row and persists the run COMPLETED with
    attribution omitted for dropped scenarios (they render the unavailable state)."""
    clean: dict[str, dict[str, float]] = {}
    dropped: list[str] = []
    for sid, sv in shapley_by_scenario.items():
        if all(math.isfinite(v) for v in sv.values()):
            clean[sid] = sv
        else:
            dropped.append(sid)
    return clean, dropped


def _inject_shapley(
    per_scenario_payload: list[dict[str, Any]],
    shapley_by_scenario: dict[str, dict[str, float]],
    key: str = "shapley_value",
) -> None:
    """Write shapley_value into each control_adjustment dict (v3 JSON layer — NOT
    the fair_cam ControlAdjustment dataclass). Scenarios ABSENT from the map
    (skipped/dropped/legacy) get NO key, so the view-model renders the
    'attribution unavailable' state for them rather than a misleading all-$0 row
    (B-Arch-I1)."""
    for entry in per_scenario_payload:
        sid = entry.get("scenario_id")
        if sid not in shapley_by_scenario:
            continue
        sv = shapley_by_scenario[sid]
        for adj in entry.get("control_adjustments", []) or []:
            adj[key] = float(sv.get(adj.get("control_id"), 0.0))


def _compute_loo_by_scenario(
    calculator: Any,
    per_scenario_inputs: list[tuple[str, str, Any]],
    per_scenario_control_ids: dict[str, list[str]] | None,
    universe_control_ids: list[str],
    *,
    node_mapping: dict[str, Any] | None = None,
    total_eval_budget: int = MAX_ATTRIBUTION_TOTAL_EVALS,
    kappa: float = KAPPA_META_RELIABILITY,
    composition_cache: dict[frozenset[str], ComposedParts] | None = None,
    per_scenario_availability: dict[str, bool] | None = None,
    statistic: str = "typical",
) -> tuple[dict[str, dict[str, float]], list[tuple[str, str]]]:
    """Per-scenario leave-one-out (LOO) attribution: for each control i on the
    scenario, ``LOO_i = v(N) - v(N minus {i})`` at canonical weights and canonical κ —
    the increase in modeled annual loss if control i were REMOVED from the
    current portfolio ("if removed"). Returns (by_scenario, skipped) with the
    same shapes as :func:`_compute_shapley_by_scenario`.

    Decision semantics (the map-legend distinction, 2026-07-03 methodology
    adjudication): the Shapley figure is a FAIR-SHARE allocation (efficiency:
    shares sum to v(N)); LOO is the DROP-COST counterfactual. They deliberately
    diverge on the informative cases —
    - redundant controls (OR-overlap): LOO ≈ 0 each, fair share splits evenly;
    - gating pairs (detection∧response on stealth effects): LOO of EACH member
      ≈ the full pair value (both are individually necessary);
    - meta controls: LOO > 0 through the κ reliability coupling.
    Consequently Σ LOO_i ≠ v(N) in general — LOO is NOT an allocation and is
    never totalled in the UI.

    Compute shape: n+1 subset evaluations per scenario (v(N) plus one per
    control), linear in n — so, unlike Shapley, there is NO control-count cap:
    over-cap scenarios (> MAX_ATTRIBUTION_CONTROLS) still get an "if removed"
    figure. Budget accounting: this pass runs a FRESH eval counter against the
    same ``MAX_ATTRIBUTION_TOTAL_EVALS`` constant — it does NOT share the
    Shapley pass's spent counter (n+1 charged per scenario; scenarios beyond
    the budget are skipped with reason "over_budget"). Worst-case combined
    spend is therefore budget + Σ(n_s + 1); the LOO term is linear and
    trivially bounded, so no shared counter is needed. v(S) comes from the SAME
    :func:`_make_subset_value_fn` as the Shapley pass; pass the same
    ``composition_cache`` AND the same ``node_mapping`` (both canonical call
    sites use the default None) so exact-Shapley scenarios (n ≤ 12) find every
    needed subset already precomposed and LOO is nearly free — a divergent
    node_mapping between the two passes would silently split the attributions
    the shared factory exists to keep consistent.

    Faithfulness note: a NEGATIVE value is reachable through the
    Standard-prescribed LEC_RESPONSE weak-AND (a diluting weak response member
    — removing it would lower modeled loss). Reported as-is, never clamped
    (same policy as #453's Shapley adjudication).

    Per-scenario errors degrade that scenario (reason "error"), never raising
    (B-Arch-N3).
    """
    if per_scenario_availability is not None:
        _valid_sids = {sid for sid, *_ in per_scenario_inputs}
        _stray = set(per_scenario_availability) - _valid_sids
        if _stray:
            raise ValueError(
                f"per_scenario_availability contains keys not in per_scenario_inputs: "
                f"{sorted(_stray)}. A mis-keyed entry silently under-credits recovery controls."
            )

    registry = calculator.control_registry
    out: dict[str, dict[str, float]] = {}
    skipped: list[tuple[str, str]] = []
    spent = 0
    _comp_cache = composition_cache if composition_cache is not None else {}
    _base_cache: dict[str, tuple[float, float, float, float, float]] = {}
    _value_fn = _make_subset_value_fn(
        registry,
        _comp_cache,
        _base_cache,
        node_mapping,
        kappa,
        per_scenario_availability,
        statistic=statistic,
    )
    for scenario_id, _name, risk_params in per_scenario_inputs:
        if per_scenario_control_ids is not None:
            cids = list(per_scenario_control_ids.get(scenario_id, []))
        else:
            cids = list(universe_control_ids)
        if not cids:
            out[scenario_id] = {}
            continue
        cost = len(cids) + 1  # v(N) + one leave-one-out per control — exact, linear
        if spent + cost > total_eval_budget:
            skipped.append((scenario_id, "over_budget"))
            continue
        spent += cost
        try:
            full = frozenset(cids)
            v_full = _value_fn(full, scenario_id, risk_params)
            out[scenario_id] = {
                cid: v_full - _value_fn(full - {cid}, scenario_id, risk_params) for cid in cids
            }
        except Exception:  # degrade this scenario, never fail the run (B-Arch-N3)
            logger.exception(
                "leave-one-out failed for scenario %s; degrading to unavailable", scenario_id
            )
            skipped.append((scenario_id, "error"))
    return out, skipped


def _inject_loo(
    per_scenario_payload: list[dict[str, Any]],
    loo_by_scenario: dict[str, dict[str, float]],
    key: str = "if_removed_value",
) -> None:
    """Write if_removed_value into each control_adjustment dict (v3 JSON layer,
    additive key). Scenarios ABSENT from the map (skipped/dropped) get NO key,
    so the view-model renders "—" rather than a misleading $0 (the same
    absent≠0.0 convention as _inject_shapley / B-Arch-I1)."""
    for entry in per_scenario_payload:
        sid = entry.get("scenario_id")
        if sid not in loo_by_scenario:
            continue
        lv = loo_by_scenario[sid]
        for adj in entry.get("control_adjustments", []) or []:
            adj[key] = float(lv.get(adj.get("control_id"), 0.0))


# ---------------------------------------------------------------------------
# Weight-robustness ensemble wiring (issue #419, Task 4).
# ---------------------------------------------------------------------------


def _aggregate_clean(by_scenario: dict[str, dict[str, float]]) -> dict[str, float]:
    """Sum per-control Shapley across scenarios over the SANITIZED scenario set.

    Arch-B2: applies the SAME ``_sanitize_shapley`` the display path uses, so the
    canonical reference ranking and every ensemble draw rank the IDENTICAL displayed
    scenario set (a non-finite scenario dropped from display is also dropped here).
    Returns {control_id: summed reduction-$}.
    """
    clean, _dropped = _sanitize_shapley(by_scenario)
    agg: dict[str, float] = {}
    for sv in clean.values():
        for cid, val in sv.items():
            agg[cid] = agg.get(cid, 0.0) + val
    return agg


def _build_weight_robustness(
    *,
    calculator: Any,
    controls: list[Control],
    per_scenario_inputs: list[tuple[str, str, FAIRParameters]],
    per_scenario_dict: dict[str, list[str]] | None,
    canonical_values: dict[str, float],
    canonical_values_typical: dict[str, float] | None = None,
    persisted: dict[str, Any] | None,
    random_seed: int | None,
    compute_rank_stability: bool,
    per_scenario_availability: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """Run the weight-uncertainty ensemble and return the persisted dict.

    Shared by the SINGLE and AGGREGATE branches (Arch-I1). Runs SYNCHRONOUSLY —
    the caller wraps the WHOLE call in ONE ``asyncio.to_thread`` (Arch-N5), and the
    per-draw value fn calls ``_compute_shapley_by_scenario`` directly (already on the
    worker thread — no nested per-draw to_thread).

    Reproducibility (Sec-Repro-1 / Repro-I1):
      * the band (incl. seed) is read back from ``persisted["band"]`` when present
        (a re-run) instead of live Settings — so ranges reproduce even if canonical
        weights or settings drift;
      * the ensemble RNG is spawned from ``SeedSequence(seed, spawn_key=(DOMAIN,))``
        — a DISTINCT namespace from the main MC's ``.spawn(n)`` scenario streams.

    Args:
        calculator: the LIVE NativeControlAwareRiskCalculator (reused — no second
            construction, memory envelope unchanged).
        controls: v3 Control rows (id universe + display names).
        per_scenario_inputs: [(scenario_id, name, FAIRParameters)] (one element for
            SINGLE; N for AGGREGATE).
        per_scenario_dict: per-scenario active control-id subsets, or None (legacy /
            SINGLE full-universe fallback).
        canonical_values: the REUSED canonical _aggregate_clean(clean_shapley) for
            AGGREGATE (do NOT re-run a second canonical pass — Arch-B2); for SINGLE
            the one canonical pass result.
        persisted: existing ``run.weight_robustness`` (re-run band read-back) or None.
        random_seed: the run's random_seed (None handled explicitly).
        compute_rank_stability: False for SINGLE (ranges-only — Meth-B6).
        per_scenario_availability: {scenario_id: availability_self_detection};
            forwarded into every per-draw Shapley pass so ensemble ranges credit
            availability recovery (§3.3.2 p.19). None = detection-gated.

    Returns:
        ``run_weight_ensemble(...)`` output augmented with ``band`` and
        ``canonical_value`` keys.
    """
    settings = get_settings()
    universe = [str(c.id) for c in controls]  # full control-id universe (Arch-N-Universe1)
    control_names = {str(c.id): c.name for c in controls}

    # Band: read back from the persisted snapshot on re-run; else derive from live
    # settings + the run's seed (explicit null handling -> 0).
    if persisted is not None and persisted.get("band") is not None:
        band: dict[str, Any] = persisted["band"]
    else:
        root = random_seed if random_seed is not None else 0
        band = {
            "logit_sigma": settings.weight_band_logit_sigma,
            "distribution": "logit_normal",
            "seed": root,
            "draws": settings.weight_ensemble_draws,
            # Pin eval_budget + min_draws so the K-degrade branch and the
            # band-endpoint fallback (sigma + budget gate) reproduce under
            # live-Settings drift (Sec-I2). Callers read these back from the
            # stored band on re-run and pass them to run_weight_ensemble.
            "eval_budget": settings.weight_ensemble_eval_budget,
            "min_draws": settings.weight_ensemble_min_draws,
            "shapley_permutations": settings.weight_ensemble_shapley_permutations,
            # #432 item 1: pin the cache-credited cost model + its finalize/compose
            # ratio at band creation so K-degrade reproduces on re-run even if the
            # module constant is re-measured later (same Sec-I2 discipline as
            # eval_budget/min_draws). Legacy bands lack these keys -> linear model.
            "cost_model": "cache_credited",
            "finalize_cost_ratio": FINALIZE_TO_COMPOSE_COST_RATIO,
        }

    # Per-draw Shapley permutation count (reproducibility-pinned, Sec-I2). Default
    # None => FULL Maleki precision (no sample compromise — the 2026-06-30 efficiency
    # refactor makes the ensemble fast at full precision via the composition cache
    # below, not by cutting permutations). The reduced-perm knob
    # (Settings.weight_ensemble_shapley_permutations) remains available as an opt-in
    # emergency throttle but is OFF by default. None on legacy bands -> full Maleki.
    _shapley_perms: int | None = band.get("shapley_permutations")

    # Persistent κ- AND weight-INVARIANT precomposition cache shared across ALL
    # ensemble draws (#419 / #439 perf refactor): precompose_parts(subset) takes
    # neither weights nor κ, so it is computed once per subset and reused every
    # draw — leaving only the per-draw finalize_composition(parts, kappa) (LEC
    # bucket rebuild + LEC group pass) plus reduction_from_composition (1-E*w).
    # finalize_composition is CHEAP RELATIVE TO a full compose_groups but is NOT a
    # ~free dict lookup: ~6.6us/hit measured at Task 8 (timeit, 20k iterations,
    # representative 5-10-control subset) — issue #432's cache-credit model must
    # price hits at this finalize cost, not at zero. ~K x fewer precompositions
    # (K = draw count) at full precision. Keyed by subset alone (#432 item 2,
    # shipped): ComposedParts is a pure function of the subset's authored
    # assignments, so identical subsets dedup across scenarios in aggregates.
    _comp_cache: dict[frozenset[str], ComposedParts] = {}

    # Arch-I10: realized per-draw eval cost — replicates the exact skip-aware
    # accumulation _compute_shapley_by_scenario uses (over_cap dropped, cumulative
    # cap at MAX_ATTRIBUTION_TOTAL_EVALS). Constant across draws (identical cap +
    # skip set every draw), so K-degrade isn't pessimistic when over_cap scenarios
    # drop out. NOT a raw sum.
    def _realized_eval_cost() -> int:
        spent = 0
        for sid, *_rest in per_scenario_inputs:
            if per_scenario_dict is not None:
                n = len(per_scenario_dict.get(sid, []))
            else:
                n = len(universe)
            if n == 0:
                continue
            if n > MAX_ATTRIBUTION_CONTROLS:  # over_cap -> 0 evals
                continue
            cost = _scenario_eval_cost(n, sample_permutations=_shapley_perms)
            if spent + cost > MAX_ATTRIBUTION_TOTAL_EVALS:  # over_budget -> skipped
                continue
            spent += cost
        return min(spent, MAX_ATTRIBUTION_TOTAL_EVALS)

    _full_eval_cost = _realized_eval_cost()
    if band.get("cost_model") == "cache_credited":
        # #432 item 1: credit the cross-draw ComposedParts cache in the budget
        # model. Draw 1 composes every distinct evaluated subset in full
        # (realized cost x (1 + r): compose + its own finalize); draws 2..K
        # only finalize (realized cost x r), r pinned in the band. ceil + the
        # max(1, ...) floor keep the charge conservative in integer evals.
        _ratio = float(band["finalize_cost_ratio"])
        first_draw_cost = max(1, math.ceil(_full_eval_cost * (1.0 + _ratio)))
        eval_cost_per_draw = max(1, math.ceil(_full_eval_cost * _ratio))
    else:
        # Legacy stored band (pre-#432): keep the linear all-draws-full-cost
        # model so the pinned degrade math reproduces byte-identically.
        first_draw_cost = None
        eval_cost_per_draw = _full_eval_cost

    # Batched per-draw value fn (#419/#439 vectorization): the coalition set is
    # IDENTICAL across all K draws (the Shapley combinatorics use a fixed seed;
    # only (kappa, node_mapping) change), so each distinct (scenario, coalition)
    # is finalized+reduced ONCE as a (K,) vector via finalize_reduce_batched
    # instead of once per (coalition x draw) — collapsing the ~16.8M scalar
    # finalize/reduce calls on a 14-control aggregate. Per draw i, the injected
    # value fn just serves vec[i]; the Shapley combinatorics (cap/budget/skip/
    # exact-vs-sampled) run UNCHANGED once per draw. The draw is the opaque
    # (node_mapping, kappa) EnsembleDraw — BOTH the perturbed weights AND the
    # perturbed meta->reliability coupling kappa flow in via the stacked arrays.
    def _per_control_values_for(draws: list[EnsembleDraw]) -> list[dict[str, float]]:
        from fair_cam.risk_engine.control_attribution import scenario_base_ale
        from fair_cam.risk_engine.group_composition import precompose_parts

        registry = calculator.control_registry
        # (kappa, node_mapping) stacked across the K draws — computed ONCE. The
        # canonical ``None`` sentinel maps to GROUP_NODE_MAPPING (mirroring
        # reduction_from_composition's ``node_mapping=None`` fallback); the real
        # executor samplers always emit a concrete mapping, so this never fires
        # on that path — it keeps the batched path faithful to the scalar one.
        kappa_arr = np.array([kappa for (_nm, kappa) in draws], dtype=float)
        node_weight_arrs = stack_node_weight_arrays(
            [nm if nm is not None else GROUP_NODE_MAPPING for (nm, _k) in draws]
        )
        # Shared per-(scenario, coalition) (K,) value cache + per-scenario base
        # ALE cache; _comp_cache (weight- AND κ-invariant parts) is reused.
        bcache: dict[tuple[str, frozenset[str]], np.ndarray] = {}
        base_cache: dict[str, tuple[float, float, float, float, float]] = {}

        def _coalition_vec(subset: frozenset[str], sid: str, rp: FAIRParameters) -> np.ndarray:
            key = (sid, subset)
            vec = bcache.get(key)
            if vec is not None:
                return vec
            parts = _comp_cache.get(subset)
            if (
                parts is None
            ):  # κ- AND weight-invariant; validate universe (as _make_subset_value_fn)
                ctrls = [registry.get_control(cid) for cid in subset]
                if any(c is None for c in ctrls):
                    raise ValueError("unknown control id in attribution subset")
                parts = precompose_parts(ctrls)
                _comp_cache[subset] = parts
            base = base_cache.get(sid)
            if base is None:  # MEAN basis (2026-07-04): scale-coherent with the MC mean headline
                base = scenario_base_ale(rp, "mean")
                base_cache[sid] = base
            avail = (
                per_scenario_availability.get(sid, False) if per_scenario_availability else False
            )
            vec = finalize_reduce_batched(
                parts, base, kappa_arr, node_weight_arrs, availability_self_detection=avail
            )
            bcache[key] = vec
            return vec

        # Walk the Shapley combinatorics ONCE with the vector value fn (#419/
        # #439 walk batching): the K per-draw games share the identical
        # deterministic coalition walk (fixed Shapley seed), so the batched
        # sibling produces {sid: {cid: (K,)}} in one pass — the same cap/budget
        # skip set as the canonical pass (Arch-I7/Sec-I4). Slicing draw i
        # through the UNCHANGED per-draw _aggregate_clean (sanitize + sum on
        # Python floats) keeps the downstream semantics byte-identical to the
        # former one-scalar-pass-per-draw loop.
        by_scn_vec, _sk = _compute_shapley_by_scenario_batched(
            per_scenario_inputs,
            per_scenario_dict,
            universe,
            _coalition_vec,
            len(draws),
            total_eval_budget=MAX_ATTRIBUTION_TOTAL_EVALS,
            sample_permutations=_shapley_perms,
            per_scenario_availability=per_scenario_availability,
        )
        results: list[dict[str, float]] = []
        for i in range(len(draws)):
            by_scn_i = {
                sid: {cid: float(vec[i]) for cid, vec in sv.items()}
                for sid, sv in by_scn_vec.items()
            }
            results.append(_aggregate_clean(by_scn_i))
        return results

    # Canonical reference ranking = the SAME control set + sort key the displayed
    # matrix uses (Arch-I8); absent-only controls (no valued cell) are excluded.
    canon_order = displayed_control_order(canonical_values, control_names)

    # Seed: DISTINCT spawn_key namespace so the ensemble stream cannot collide with
    # the main MC's .spawn(n) scenario streams off the same root (Sec-Repro-1).
    _ensemble_seed_seq = np.random.SeedSequence(
        band["seed"], spawn_key=(WEIGHT_ROBUSTNESS_SPAWN_DOMAIN,)
    )
    child = _ensemble_seed_seq.spawn(1)[0]  # adapter-iter: ok — spawn(1) returns 1 child stream
    rng = np.random.default_rng(child)
    sigma = band["logit_sigma"]

    def _sampler(r: np.random.Generator) -> EnsembleDraw:
        return sample_ensemble_draw(r, sigma=sigma)

    ensemble = run_weight_ensemble(
        per_control_value_fn=_per_control_values_for,
        control_ids=canon_order,
        rng=rng,
        draws=band["draws"],
        eval_cost_per_draw=eval_cost_per_draw,
        first_draw_cost=first_draw_cost,
        # Pass PINNED eval_budget + min_draws from the stored band so the K-degrade
        # branch and band-endpoint fallback reproduce under Settings drift (Sec-I2).
        # band.get() returns None for legacy stored bands that predate these keys,
        # and run_weight_ensemble falls back to live Settings for None -- acceptable
        # for legacy data since those rows were never pinned.
        eval_budget=band.get("eval_budget"),
        min_draws=band.get("min_draws"),
        # Pass PINNED sigma so _deterministic_envelope honors the stored band width,
        # not live Settings (closes the degraded-path reproducibility hole).
        sigma=band["logit_sigma"],
        sampler=_sampler,
        compute_rank_stability=compute_rank_stability,
    )
    # "basis" marks the statistic chain of every dollar figure in this blob.
    # Legacy blobs (no key) are typical-basis; the view-model uses this to label
    # ranges and to decide whether a paired typical point figure is available.
    result = {
        **ensemble,
        "band": band,
        "canonical_value": canonical_values,
        # Paired typical-basis canonical point per control (the side-by-side
        # secondary figure). None-safe: {} when the caller has no typical pass.
        "canonical_value_typical": canonical_values_typical or {},
        "basis": "mean",
    }
    # Writer-side contract guard (methodology review F3): the persisted key set
    # is pinned by WEIGHT_ROBUSTNESS_KEYS; enforcing it HERE means a drifting
    # writer fails loud at run time instead of relying on a test replica that
    # can silently under-cover.
    if set(result.keys()) != WEIGHT_ROBUSTNESS_KEYS:
        raise AssertionError(
            f"weight_robustness blob keys {sorted(result)} != pinned contract "
            f"{sorted(WEIGHT_ROBUSTNESS_KEYS)} — update WEIGHT_ROBUSTNESS_KEYS "
            "+ view-model/migration together"
        )
    return result


# Tail-VaR percentiles persisted per risk side (#266 D1). Expected Shortfall
# (ES/CVaR) is computed at 95/99/99.9 only — we do NOT compute ES beyond
# p99.9 because at the 10k default iteration count the p99.9 tail holds only
# ~10 samples (and proportionally fewer above it), so any deeper ES would be
# dominated by a handful of draws.
# NOTE on var_95/var_99: those two keys are already persisted by
# _fair_risk_to_dict straight off the fair_cam FAIRRisk dataclass
# (fr.var_95 / fr.var_99). _build_tail_metrics therefore returns ALL FOUR
# tail-VaR levels for its own unit-test surface and for any caller that wants
# the full sample-derived set, but _fair_risk_to_dict only merges the NEW
# keys (var_90, var_999, expected_shortfall) so it does not silently swap the
# dataclass-sourced var_95/var_99 for sample-derived percentiles.
_NEW_VAR_KEYS: tuple[str, ...] = ("var_90", "var_999")
_VAR_QUANTILES: dict[str, float] = {
    "var_90": 90.0,
    "var_95": 95.0,
    "var_99": 99.0,
    "var_999": 99.9,
}
_ES_QUANTILES: dict[str, float] = {
    "es_95": 95.0,
    "es_99": 99.0,
    "es_999": 99.9,
}

# Empirical central-95% percentile band (issue #202). The displayed "interval"
# on the residual-ALE headline is the central 95% of the modeled
# annualized-loss / Risk distribution — p2.5 to p97.5 — NOT a heuristic
# confidence interval. Fixed at 95% (analyst-chosen significance, independent
# of control count). Defining the two endpoints + the displayed integer as
# named constants here (next to _VAR_QUANTILES) means the np.percentile reads,
# the persisted ``interval_pct``, and any label can never silently diverge.
# NOTE: var_95 is a ONE-SIDED p95 tail percentile; this band is the TWO-SIDED
# central 95% (p2.5/p97.5) — they are distinct statistics and the copy must
# not imply the band endpoint equals var_95.
_BAND_LO_PCT: float = 2.5
_BAND_HI_PCT: float = 97.5
_BAND_INTERVAL_PCT: int = 95


def _es_standard_error(samples: np.ndarray, alpha: float, es: float, var: float) -> float:
    """Monte Carlo *sampling* standard error of the sample Expected Shortfall.

    v3 VIEW-MODEL DERIVATION — a convergence diagnostic on the already-simulated
    array, NOT a FAIR node and NOT epistemic uncertainty in the FAIR inputs. It
    answers "how much would ES(alpha) move under a different simulation seed?",
    so consumers can tell a converged deep-tail ES from a noisy one.

    First-order influence-function estimator of the sample upper-tail ES
    (Scaillet 2004, *Mathematical Finance* 14(1), the ES influence-function
    proposition; Manistre & Hancock 2005, *NAAJ* 9(2), the CTE-estimator
    variance equation). For tail probability ``p = 1 - alpha`` and quantile
    ``var``::

        IF(x) = (1/p)*(x - var)_+ - (es - var),      E[IF] = 0
        Var(ES_hat) ~= E[IF^2]/n = sigma^2_tail/(n p) + (1 - p)(es - var)^2/(n p)

    BOTH terms are retained: the tail-dispersion term ``sigma^2_tail/(n p)`` AND
    the ES-vs-VaR centering term ``(1 - p)(es - var)^2/(n p)``. Dropping the
    second understates the SE by ~sqrt(2) at small p (the mean/median-style
    one-term foot-gun). The quantile-estimation contribution is *separately*
    second-order for ES (the ES functional has zero derivative in the quantile
    at the true quantile), which is why VaR's own sampling error does not enter
    to first order — that is the ONLY thing "second-order" refers to here; no IF
    term is dropped. Computing ``mean(influence**2)`` over all N samples
    captures both terms directly (tail samples supply the dispersion term, the
    all-sample centering supplies the ``(es - var)`` term); ``E[IF] = 0`` makes
    the raw second moment equal to the variance to O(1/(n p)).

    VALIDATED against a nonparametric bootstrap oracle and a closed-form
    exponential anchor (``SE = (1/lambda) sqrt((2 - p)/(n p))``) in
    tests/services/test_es_standard_error.py.

    Normal-approximation caveat: the reported band assumes a symmetric Gaussian
    sampling distribution for ES_hat, valid once the tail count ``n p`` is large;
    at low N the ES estimator is right-skewed and the band is indicative. Returns
    NaN when the ``>= var`` tail has < 2 samples (SE undefined — the deep tail at
    low N); consumers render "insufficient tail samples" rather than a false 0.
    """
    p = 1.0 - alpha
    if samples[samples >= var].size < 2:
        return float("nan")
    influence = np.maximum(samples - var, 0.0) / p - (es - var)
    return float(np.sqrt(np.mean(influence**2) / samples.size))


def _build_tail_metrics(fr: Any) -> dict[str, Any]:
    """Derive tail-VaR + Expected Shortfall from already-simulated samples.

    v3 VIEW-MODEL DERIVATION (issue #266 D1): descriptive statistics on
    fair_cam's already-simulated ``simulation_results`` array — NOT a
    re-derivation of FAIR math. Maps to the FAIR Loss-Magnitude tail /
    loss-exceedance surface (the same surface ``_build_loss_exceedance_curve``
    reads), so no "not FAIR-grounded" label is needed.

    Returns::

        {
          "var_90": float, "var_95": float, "var_99": float, "var_999": float,
          "expected_shortfall": {"es_95": float, "es_99": float, "es_999": float},
        }

    VaR_q = ``np.percentile(samples, q)`` (linear interpolation).
    ES_q (a.k.a. CVaR / TVaR) = ``mean(samples[samples >= VaR_q])`` — the
    conditional mean loss in the q-tail. If the ``>= VaR_q`` slice is EMPTY
    (degenerate — numpy never produces VaR_q > max, but guarded defensively),
    ES_q falls back to ``samples.max()`` so it is never silently 0.

    Statistical-reliability caveat (persist, but read with care): at the
    10k-iteration form default the p99.9 tail contains only ~10 samples, so
    ``var_999`` / ``es_999`` are stable only near the 100k server cap. The
    fields are persisted regardless; consumers should treat the deepest tail
    as indicative until iteration count is high.

    Empty / None sample arrays return all-zero metrics (mirrors the LEC/EPC
    builders' empty-input contract).
    """
    zero_es = dict.fromkeys(_ES_QUANTILES, 0.0)
    if fr.simulation_results is None or len(fr.simulation_results) == 0:
        return {
            **dict.fromkeys(_VAR_QUANTILES, 0.0),
            "expected_shortfall": zero_es,
            "expected_shortfall_se": dict.fromkeys(_ES_QUANTILES, 0.0),
        }

    samples = np.asarray(fr.simulation_results, dtype=float)
    var = {key: float(np.percentile(samples, q)) for key, q in _VAR_QUANTILES.items()}

    sample_max = float(samples.max())
    expected_shortfall: dict[str, float] = {}
    # #266 tail-fidelity: the Monte Carlo standard error of each ES estimate, so
    # a consumer can tell a converged deep-tail ES from a noisy one (rendered as
    # a 95% MC interval, es +/- 1.96*se). Additive key — no schema bump. Stored
    # as ``float | None``: NaN (deep tail with < 2 samples, SE undefined) is
    # persisted as JSON ``null`` — the JSON serializer runs allow_nan=False, and
    # None cleanly signals "insufficient tail samples" to consumers.
    expected_shortfall_se: dict[str, float | None] = {}
    for es_key, q in _ES_QUANTILES.items():
        threshold = float(np.percentile(samples, q))
        tail = samples[samples >= threshold]
        # Empty tail can only happen if threshold > max (numpy never produces
        # this); fall back to the worst observed loss rather than 0.0.
        es_val = float(tail.mean()) if tail.size else sample_max
        expected_shortfall[es_key] = es_val
        se = _es_standard_error(samples, alpha=q / 100.0, es=es_val, var=threshold)
        expected_shortfall_se[es_key] = None if math.isnan(se) else se

    return {
        **var,
        "expected_shortfall": expected_shortfall,
        "expected_shortfall_se": expected_shortfall_se,
    }


def _build_loss_percentile_band(fr: Any) -> dict[str, Any]:
    """Empirical central-95% percentile band over the persisted MC samples.

    Issue #202. Replaces the retired heuristic "confidence interval"
    (fair_cam ``_calculate_confidence_metrics``, which derived a significance
    level from ``len(controls)`` and drew a Gaussian SE-of-the-mean band).

    v3 VIEW-MODEL DERIVATION: a descriptive statistic (``np.percentile``) on
    fair_cam's already-simulated ``simulation_results`` array — NOT a
    re-derivation of FAIR math. The array is the pyfair 'Risk' column, i.e. the
    ANNUALIZED loss / Risk distribution per iteration (LEF x LM) — the SAME
    surface the loss-exceedance curve and VaR/ES read; for the AGGREGATE side
    it is the elementwise SUM of per-scenario Risk arrays (the per-iteration
    portfolio TOTAL annualized loss, length = n_iterations). This is NOT the
    per-event Loss-Magnitude node (distinct Open FAIR node, per-event severity).

    Band = ``[p2.5, p97.5]`` = the central 95% of modeled annualized losses.
    This is a TWO-SIDED central interval; note ``var_95`` is the distinct
    ONE-SIDED p95 tail percentile — the band endpoint is NOT ``var_95``.

    Returns::

        {
          "lower_bound": float,   # p2.5 of the sample array
          "upper_bound": float,   # p97.5 of the sample array
          "interval_pct": 95,     # fixed analyst-chosen central interval
          "sample_size": int,     # n_simulations (kept for the PDF n_sims read)
        }

    Empty / None sample arrays return all-zero bounds (mirrors the LEC/EPC and
    tail-metric builders' empty-input contract). ``interval_pct`` is ALWAYS
    present so consumers can distinguish a real new-schema row from a legacy
    row (legacy rows lack ``interval_pct`` and are routed to "not available").
    """
    sample_size = int(getattr(fr, "n_simulations", 0) or 0)
    if fr.simulation_results is None or len(fr.simulation_results) == 0:
        return {
            "lower_bound": 0.0,
            "upper_bound": 0.0,
            "interval_pct": _BAND_INTERVAL_PCT,
            "sample_size": sample_size,
        }
    samples = np.asarray(fr.simulation_results, dtype=float)
    return {
        "lower_bound": float(np.percentile(samples, _BAND_LO_PCT)),
        "upper_bound": float(np.percentile(samples, _BAND_HI_PCT)),
        "interval_pct": _BAND_INTERVAL_PCT,
        "sample_size": sample_size,
    }


def _fair_risk_to_dict(fr: Any) -> dict[str, Any]:
    return {
        "annualized_loss_expectancy": fr.annualized_loss_expectancy,
        "mean": fr.mean,
        "median": fr.median,
        "std_deviation": fr.std_deviation,
        "var_95": fr.var_95,
        "var_99": fr.var_99,
        "loss_event_frequency": fr.loss_event_frequency,
        "loss_magnitude": fr.loss_magnitude,
        # Keep the raw numpy array — popped into run_samples by
        # split_simulation_payload and serialized by the binary codec at the
        # persist site. Dropping .tolist() removes the O(M·N) Python-list
        # materialization that drove the aggregate OOM. Tail metrics below still
        # read fr.simulation_results (the float64 array) directly.
        "simulation_results": (
            fr.simulation_results
            if fr.simulation_results is not None
            else np.empty(0, dtype=np.float64)
        ),
        "n_simulations": fr.n_simulations,
        # #266 D1: tail-VaR (p90/p99.9) + Expected Shortfall (ES/CVaR) + the ES
        # Monte Carlo standard error, derived here at persist time from the raw
        # sample array. Only the NEW keys (_NEW_VAR_KEYS + expected_shortfall +
        # expected_shortfall_se) are merged — var_95/var_99 above stay sourced
        # from the fair_cam dataclass. expected_shortfall_se is additive; no
        # SIMULATION_RESULTS_SCHEMA_VERSION bump (additive-key policy).
        **{
            k: v
            for k, v in _build_tail_metrics(fr).items()
            if k in _NEW_VAR_KEYS or k in ("expected_shortfall", "expected_shortfall_se")
        },
    }


def _control_adjustment_to_dict(adj: Any) -> dict[str, Any]:
    """Serialize ControlAdjustment to a JSON-safe dict.

    PR μ.1:
      * ``effectiveness`` reads from ``control_effectiveness`` (Arch-I4 fix —
        the field is named ``control_effectiveness`` on the dataclass; the
        old ``getattr(adj, "effectiveness", 0.0)`` silently returned 0.0 for
        every control because ``ControlAdjustment`` has no ``effectiveness``
        attribute).
      * Adds ``loss_reduction_per_event`` for the CURRENCY-branch subtractor.
    PR μ.1b (#129 §6):
      * Adds ``breakdown`` per-assignment list for snapshot debuggability.
    """
    return {
        "control_id": str(adj.control_id) if hasattr(adj, "control_id") else None,
        "control_name": getattr(adj, "control_name", ""),
        "tef_multiplier": getattr(adj, "threat_event_frequency_multiplier", 1.0),
        "vulnerability_multiplier": getattr(adj, "vulnerability_multiplier", 1.0),
        "primary_loss_multiplier": getattr(adj, "primary_loss_multiplier", 1.0),
        "secondary_loss_multiplier": getattr(adj, "secondary_loss_multiplier", 1.0),
        "effectiveness": float(getattr(adj, "control_effectiveness", 0.0)),  # Arch-I4 fix
        # Per-control cost + risk-reduction (introduced after the cost-model
        # collapse — fair_cam already populates these on ControlAdjustment;
        # the serialiser was dropping them, blocking aggregate rendering on
        # the run-detail page).
        "control_cost": float(getattr(adj, "control_cost", 0.0) or 0.0),
        "risk_reduction_value": float(getattr(adj, "risk_reduction_value", 0.0) or 0.0),
        "loss_reduction_per_event": float(getattr(adj, "loss_reduction_per_event", 0.0)),  # PR μ.1
        "breakdown": list(getattr(adj, "breakdown", []) or []),  # PR μ.1b #129 T2
    }


def _build_cost_summary(enhanced: Any) -> dict[str, Any]:
    """Aggregate per-control cost + risk-reduction across the run.

    Pure addition over fair_cam's per-control output. ``aggregate_roi`` is
    None (rendered as ``—``) when there's no cost; ``inf`` is misleading
    in a UI context.
    """
    adjustments = getattr(enhanced, "control_adjustments", []) or []
    total_cost = sum(float(getattr(a, "control_cost", 0.0) or 0.0) for a in adjustments)
    base_ale = float(getattr(enhanced.base_risk, "annualized_loss_expectancy", 0.0) or 0.0)
    residual_ale = float(getattr(enhanced.residual_risk, "annualized_loss_expectancy", 0.0) or 0.0)
    total_risk_reduction = base_ale - residual_ale
    net_benefit = total_risk_reduction - total_cost
    aggregate_roi: float | None = total_risk_reduction / total_cost if total_cost > 0 else None
    return {
        "total_annual_cost": total_cost,
        "total_risk_reduction": total_risk_reduction,
        "net_benefit": net_benefit,
        "aggregate_roi": aggregate_roi,
    }


_LEC_GRID_POINTS = 100


def _build_aggregate_lec_pair(aggregate: Any) -> dict[str, dict[str, list[dict[str, float]]]]:
    """Build with/without-controls LECs evaluated on a shared union log-grid.

    Without a shared grid, each curve is dense in its own sample range and
    absent past it — the chart layer would have to either render two
    visually-disjoint segments or re-derive the union range itself. Doing
    the alignment here keeps the storage shape the chart consumes
    homogeneous and lets pyfair-style fully-overlaid LECs render directly.
    """
    with_fr = aggregate.aggregate_with_controls
    without_fr = aggregate.aggregate_without_controls
    with_samples = (
        np.asarray(with_fr.simulation_results)
        if with_fr.simulation_results is not None
        else np.array([])
    )
    without_samples = (
        np.asarray(without_fr.simulation_results)
        if without_fr.simulation_results is not None
        else np.array([])
    )
    all_positive = np.concatenate(
        [with_samples[with_samples > 0], without_samples[without_samples > 0]]
    )
    if all_positive.size == 0:
        return {
            "aggregate_with_controls": {
                **_fair_risk_to_dict(with_fr),
                "loss_exceedance_curve": [],
            },
            "aggregate_without_controls": {
                **_fair_risk_to_dict(without_fr),
                "loss_exceedance_curve": [],
            },
        }
    union_grid = _log_grid(float(all_positive.min()), float(all_positive.max()))
    return {
        "aggregate_with_controls": {
            **_fair_risk_to_dict(with_fr),
            "loss_exceedance_curve": _build_loss_exceedance_curve(with_fr, loss_grid=union_grid),
        },
        "aggregate_without_controls": {
            **_fair_risk_to_dict(without_fr),
            "loss_exceedance_curve": _build_loss_exceedance_curve(without_fr, loss_grid=union_grid),
        },
    }


def _log_grid(low: float, high: float, n: int = _LEC_GRID_POINTS) -> np.ndarray:
    """Logarithmically-spaced loss grid clamped to ≥ $1 (log10(0) is -inf)."""
    lo = max(low, 1.0)
    hi = max(high, lo + 1.0)
    return np.logspace(np.log10(lo), np.log10(hi), n)


def _exceedance_at_grid(samples: np.ndarray, grid: np.ndarray) -> list[dict[str, float]]:
    """Mirror pyfair's ``(value < risk).mean()`` across a dense loss grid.

    Returns ``[{loss, probability}, ...]`` evaluated at each grid point so the
    chart layer can render a smooth curve without re-sampling at render time.
    """
    n = len(samples)
    if n == 0:
        return []
    return [{"loss": float(x), "probability": float((samples >= x).sum() / n)} for x in grid]


def _build_loss_exceedance_curve(
    fr: Any, *, loss_grid: np.ndarray | None = None
) -> list[dict[str, float]]:
    """Build a dense loss-exceedance curve from raw MC samples.

    Replaces the prior 20-quantile downsampling: instead of picking 20 fixed
    percentile points and reading off their loss values (which produced
    visibly faceted curves on the chart), we evaluate exceedance probability
    at 100 log-spaced loss points across the sample range. This matches
    ``pyfair/report/exceedence.py``'s sampling strategy at higher resolution.

    For AGGREGATE runs, callers should pass a shared ``loss_grid`` spanning
    the union of with/without-controls sample ranges so both curves are
    evaluated on the same x-domain — produces curves that genuinely overlay
    on shared axes (a curve at a loss value above its own sample max
    correctly reads ``probability=0``).
    """
    if fr.simulation_results is None or len(fr.simulation_results) == 0:
        return []
    samples = np.asarray(fr.simulation_results)
    if loss_grid is None:
        positive = samples[samples > 0]
        sample_min = float(positive.min()) if positive.size else 1.0
        sample_max = float(samples.max())
        loss_grid = _log_grid(sample_min, sample_max)
    return _exceedance_at_grid(samples, loss_grid)


def _build_exceedance_probability_curve(fr: Any) -> list[dict[str, float]]:
    """Build a dense Exceedance Probability Curve from raw MC samples.

    EPC is the inverse view of the LEC: x = percentile (linear 0-1),
    y = loss ($, log scale). Returns 100 points at percentiles 0.01..1.00
    (step 0.01) computed via ``numpy.percentile``. Non-positive samples
    are clamped to $1 so the chart's log y-axis can render every point
    (mirrors the LEC's log-x clamp).

    Closes the SINGLE half of issue #72; pairs with
    ``_build_aggregate_epc_pair`` for AGGREGATE runs.
    """
    if fr.simulation_results is None or len(fr.simulation_results) == 0:
        return []
    samples = np.asarray(fr.simulation_results, dtype=float)
    samples = np.maximum(samples, 1.0)
    pcts = np.arange(1, 101) / 100.0
    losses = np.percentile(samples, pcts * 100)
    return [
        {"percentile": float(p), "loss": float(loss_val)}
        for p, loss_val in zip(pcts, losses, strict=True)
    ]


def _build_aggregate_epc_pair(aggregate: Any) -> dict[str, list[dict[str, float]]]:
    """Build with/without-controls EPCs for AGGREGATE runs.

    Each curve is computed from its own samples — unlike the LEC pair
    (which shares a union log-grid for x-domain alignment), the EPC's
    x-axis is the percentile, intrinsic to each sample set. No grid
    alignment needed. Returns ``{"with_controls": [...], "without_controls":
    [...]}`` where each list is a 100-point EPC payload.
    """
    return {
        "with_controls": _build_exceedance_probability_curve(aggregate.aggregate_with_controls),
        "without_controls": _build_exceedance_probability_curve(
            aggregate.aggregate_without_controls
        ),
    }


async def _check_cancelled_or_continue(
    session: AsyncSession,
    run_id: uuid.UUID,
) -> bool:
    """Re-fetch run status via fresh SELECT (NOT session.get).

    Returns True if status is still RUNNING; False if CANCELLED, COMPLETED,
    FAILED, or row vanished.

    Uses a raw SELECT rather than ``session.get`` so that an external commit
    flipping the run to CANCELLED is visible even when the run row is already
    in this session's identity map (which .get() would return the cached copy).
    """
    stmt = select(RiskAnalysisRun.status).where(RiskAnalysisRun.id == run_id)
    result = await session.execute(stmt)
    status = result.scalar_one_or_none()
    return status == RunStatus.RUNNING


async def execute_run(run_id: uuid.UUID) -> None:
    """BackgroundTask entry point — registry bookkeeping around the body.

    #211 Phase 2: the run id is held in the in-memory active-run registry
    for the task's whole lifetime so the periodic orphan sweep never reaps
    a row a live task still owns (age alone cannot distinguish a slow run
    from an orphan). The finally guarantees unregistration on EVERY exit
    path — success, failure, early return, even cancellation — so a stuck
    row's absence from the registry is exactly what marks it orphaned.
    """
    register_active_run(run_id)
    try:
        await _execute_run_body(run_id)
    finally:
        unregister_active_run(run_id)


async def _execute_run_body(run_id: uuid.UUID) -> None:
    """BackgroundTask body. Owns its own session via _get_sessionmaker().

    Audit log entries are written in the SAME transaction as their state
    change (Phase 1.3 AuditWriter pattern); each commit covers one
    state-flip-plus-its-audit pair.

    NOTE: _get_sessionmaker() is the private module-level singleton from
    idraa.db — it is the only way to acquire an HTTP-request-independent
    session in v3's current architecture. This is intentional for background
    tasks that run outside of a request context.

    Session sharing with test db_session: tests use a per-test SQLite
    file (db_url fixture) and the client fixture sets DATABASE_URL via
    monkeypatch. execute_run tests that call this directly must ensure
    the executor's _get_sessionmaker() points to the same DB file — see
    conftest.py engine setup for how this is arranged.
    """
    sessionmaker = _get_sessionmaker()
    async with sessionmaker() as session:
        run = await session.get(RiskAnalysisRun, run_id)
        if run is None or run.status != RunStatus.QUEUED:
            return

        # Phase 1: snapshot controls + flip RUNNING + audit (one transaction)
        control_ids = [uuid.UUID(s) for s in run.control_ids_used]
        controls = await ControlRepo(session).fetch_by_ids_for_org(
            run.organization_id,
            control_ids,
        )
        # Issue #131 T6.5: new writes persist V3 (per-assignment unit_type
        # captured at write time). V2 stays read-only via the discriminated
        # union; V2 reads surface the post-#131 re-interpretation banner +
        # ``snapshot_v2_read`` structured log on the run-detail route.
        run.controls_snapshot = [_snapshot_control_v3(c).model_dump(mode="json") for c in controls]
        run.status = RunStatus.RUNNING
        run.started_at = now_utc()
        await AuditWriter(session).log(
            organization_id=run.organization_id,
            user_id=run.created_by,
            action="risk_analysis_run.start",
            entity_type="risk_analysis_run",
            entity_id=run.id,
            changes={"status": [RunStatus.QUEUED.value, RunStatus.RUNNING.value]},
        )
        await session.commit()

        try:
            if not await _check_cancelled_or_continue(session, run_id):
                return

            # Declare before discriminator branch so mypy sees a single binding per var.
            # Native cut: the engine consumes FAIRParameters directly (no lossy
            # FAIRParameters->RiskParameters->pyfair bridge).
            fair_params: FAIRParameters | None = None
            per_scenario_inputs: list[tuple[str, str, FAIRParameters]] | None = None
            scenarios: list[Scenario]
            # T4M-5-I1 (#351): pre-declare here (alongside fair_params/per_scenario_inputs)
            # so the FAILED except handler can reference it directly without locals().get().
            # Semantics unchanged: exceptions before the assignment point legitimately
            # produce None (scenario-load errors in the discriminator branch, etc.).
            _snapshot_local: dict[str, Any] | None = None
            # P3 (currency): pre-declare alongside _snapshot_local for the same reason —
            # FAILED handler can reference it without locals().get().
            _fx_snapshot_local: dict[str, Any] | None = None
            # #419 (weight-robustness): pre-declare the LOCAL var (Arch-N2). NEVER
            # assign run.weight_robustness before the guarded COMPLETED UPDATE — that
            # would mark the ORM dirty and let _check_cancelled_or_continue's SELECT
            # autoflush an unguarded write (same hazard as scenario_inputs_snapshot).
            # Persisted ONLY inside the COMPLETED .values(...); NULL on FAILED.
            _robustness_local: dict[str, Any] | None = None

            # ---- discriminator branch: SINGLE vs AGGREGATE ----
            if run.run_type == RunType.SINGLE:
                # Issue #265: org-scope the fetch (mirror the AGGREGATE path,
                # which uses fetch_by_ids_for_org). The old session.get was
                # PK-only and would load a cross-org scenario by raw id.
                # get_for_org_or_raise raises ScenarioNotFoundError on a miss,
                # which the terminalization handler below flips to FAILED.
                if run.scenario_id is None:
                    raise RuntimeError(f"SINGLE run {run_id} has no scenario_id set")
                scenario = await ScenarioRepo(session).get_for_org_or_raise(
                    run.organization_id,
                    run.scenario_id,
                )
                fair_params = _scenario_to_fair_parameters(scenario)
                scenarios = [scenario]
            else:
                # AGGREGATE: load N scenarios, calibrate each with per-scenario cancel-checks
                if not run.aggregate_scenario_ids:
                    raise RuntimeError(f"AGGREGATE run {run_id} has no aggregate_scenario_ids set")
                agg_scenario_ids = [uuid.UUID(s) for s in run.aggregate_scenario_ids]
                scenarios = await ScenarioRepo(session).fetch_by_ids_for_org(
                    run.organization_id,
                    agg_scenario_ids,
                )
                if len(scenarios) < 2:
                    raise RuntimeError(
                        f"AGGREGATE run {run_id} requires >=2 scenarios; "
                        f"got {len(scenarios)} (some may be missing or cross-org)"
                    )
                # Seed reproducibility: pin scenario iteration order to the
                # run's frozen aggregate_scenario_ids list so the spawn index
                # assigned to each scenario (SeedSequence.spawn() increments per
                # call, in iteration order) is deterministic across re-runs.
                # fetch_by_ids_for_org has no ORDER BY, so without this the
                # spawn-index <-> scenario_id mapping would be non-deterministic.
                order = {sid: i for i, sid in enumerate(run.aggregate_scenario_ids)}
                scenarios = sorted(scenarios, key=lambda s: order[str(s.id)])
                per_scenario_inputs = []
                for _scenario in scenarios:
                    if not await _check_cancelled_or_continue(session, run_id):
                        return
                    _fair_params = _scenario_to_fair_parameters(_scenario)
                    per_scenario_inputs.append((str(_scenario.id), _scenario.name, _fair_params))

            if not await _check_cancelled_or_continue(session, run_id):
                return

            # ---- T2 (#351): capture scenario_inputs_snapshot BEFORE the engine call ----
            # PA2-Arch-I1: must be written BEFORE calculate_*_risk so that any COMPLETED
            # or FAILED run carries the as-executed FAIR distribution parameters.
            # Scenarios can be edited between queue and execution; the executor's
            # live load here is the as-executed truth (NOT run-create snapshot).
            #
            # T2.a FIX (#351): store in a LOCAL VARIABLE, not as an ORM attribute.
            # Assigning run.scenario_inputs_snapshot here marks `run` dirty in the
            # executor's session.  When _check_cancelled_or_continue fires at line ~1008
            # (after the engine call), its session.execute(SELECT …) triggers SQLAlchemy
            # autoflush, which emits an unguarded UPDATE risk_analysis_run SET
            # scenario_inputs_snapshot=… — without the WHERE status='running' guard.
            # That unguarded write opens a SQLite write-lock BEFORE the test's competing
            # db_session.cancel() can acquire it, producing "database is locked" on the
            # cancel's AuditWriter.flush().  The snapshot reaches the DB only inside the
            # two guarded UPDATE…WHERE status='running' calls below (COMPLETED and FAILED
            # paths), so the cancel-guard invariant is fully preserved.
            _snapshot_local = _build_scenario_inputs_snapshot(scenarios)
            # P3 (currency): capture reporting-currency FX rate before engine call.
            # Pure local — written inside the guarded UPDATE…WHERE status=RUNNING
            # only, so it never marks the ORM dirty.
            _fx_snapshot_local = await _build_presentation_fx_snapshot(session, run.organization_id)

            # ---- HOISTED: build fc_controls (both paths use the same controls list) ----
            # Issue #209: a cleared (NULL) capability_value no longer raises here —
            # _v3_to_fair_cam_control passes it through to fair_cam's documented
            # midpoint fallback. The old run.null_capability audit-write gate is
            # therefore removed. Any ValueError that still escapes the adapter
            # (e.g. coverage/reliability bounds, empty assignments) propagates to
            # execute_run's outer except, which flips the run to FAILED.
            fc_controls = [_v3_to_fair_cam_control(v3_ctrl) for v3_ctrl in controls]

            # ---- engine call branches by run_type ----
            # Native cut: NativeControlAwareRiskCalculator samples FAIRParameters
            # through the native FAIREngine and returns the SAME
            # ControlEnhancedRisk/AggregateEnhancedRisk dataclasses as the retired
            # pyfair path, so the payload builders below are untouched.
            # Seed reproducibility: the run's persisted random_seed roots the
            # calculator's SeedSequence so a re-run with identical inputs
            # reproduces the exact loss distributions. The per-scenario child
            # spawn keys are captured below and persisted as derived_seed_keys.
            calculator = NativeControlAwareRiskCalculator(
                controls=fc_controls,
                n_simulations=run.mc_iterations,
                random_seed=run.random_seed,
            )
            if run.run_type == RunType.SINGLE:
                # fair_params is guaranteed non-None by the SINGLE discriminator branch above.
                if fair_params is None:  # pragma: no cover
                    raise RuntimeError("SINGLE branch must set fair_params")
                # fmt: off
                _single_scenario_name = scenarios[0].name  # adapter-iter: ok — SINGLE branch; scenarios has length 1 by RunType.SINGLE discriminator
                # fmt: on
                enhanced = await asyncio.to_thread(
                    calculator.calculate_control_enhanced_risk,
                    risk_params=fair_params,  # FAIRParameters directly — no bridge
                    active_control_ids=[str(c.id) for c in controls],
                    scenario_name=_single_scenario_name,
                    availability_self_detection=_scenario_availability_self_detects(scenario),
                )
            else:
                # per_scenario_inputs is guaranteed non-None by the AGGREGATE discriminator branch.
                if per_scenario_inputs is None:  # pragma: no cover
                    raise RuntimeError("AGGREGATE branch must set per_scenario_inputs")
                # Issue #89: per-scenario control coupling. NULL column => legacy
                # AGGREGATE row (pre-issue-89) => None passed to fair_cam =>
                # back-compat path (unified active_control_ids applied to all
                # scenarios). New runs always populate the column.
                per_scenario_dict = run.aggregate_control_ids_per_scenario
                if per_scenario_dict is not None:
                    # M5 (plan-gate): fail-loud race check. scenarios'
                    # mitigating_controls may have changed since run-create.
                    # If any per-scenario cid is missing from the loaded universe,
                    # the run cannot proceed soundly — fail with audit row.
                    loaded_universe = {str(c.id) for c in controls}
                    for _sid, _sublist in per_scenario_dict.items():
                        _stale = set(_sublist) - loaded_universe
                        if _stale:
                            await AuditWriter(session).log(
                                organization_id=run.organization_id,
                                user_id=run.created_by,
                                action="run.stale_per_scenario_control_ids",
                                entity_type="risk_analysis_run",
                                entity_id=run.id,
                                changes={
                                    "scenario_id": [None, _sid],
                                    "stale_ids": [None, sorted(_stale)],
                                },
                            )
                            await session.commit()
                            raise RuntimeError(
                                f"per-scenario control ids {sorted(_stale)} for "
                                f"scenario {_sid} are not in the loaded snapshot "
                                f"universe — race against scenario edit?"
                            )
                per_scenario_availability = {
                    str(sc.id): _scenario_availability_self_detects(sc) for sc in scenarios
                }
                aggregate = await asyncio.to_thread(
                    calculator.calculate_aggregate_enhanced_risk,
                    per_scenario_risk_params=per_scenario_inputs,
                    active_control_ids=[str(c.id) for c in controls],
                    per_scenario_active_control_ids=per_scenario_dict,
                    per_scenario_availability=per_scenario_availability,
                )

            if not await _check_cancelled_or_continue(session, run_id):
                return

            # ---- build the results payload (NOT yet persisted onto the row) ----
            # Issue #272 TOCTOU: do NOT assign run.simulation_results on the
            # in-memory ORM object here. The session is expire_on_commit=False,
            # so a competing RunService.cancel committing CANCELLED in the
            # window between the :903 cancel-check and this flip leaves our
            # in-memory `run.status` stale at RUNNING. The terminal flip below
            # is therefore an atomic guarded UPDATE...WHERE status='running';
            # we only persist simulation_results inside that same UPDATE (so a
            # lost-race row is never mutated with a full loss distribution).
            completed_at = now_utc()
            # Seed reproducibility: capture the per-scenario child spawn indices
            # into a LOCAL here, BEFORE the `del enhanced` / `del aggregate`
            # below — the heavy result objects are freed ~125 lines before the
            # RunSamples(...) construction, so the keys must be materialized now.
            # spawn_key[0] is the SeedSequence.spawn() index (0-based, assigned
            # in iteration order). Keyed by scenario_id (str).
            derived_seed_keys: dict[str, int] = {}
            if run.run_type == RunType.SINGLE:
                # The SINGLE path leaves ControlEnhancedRisk.scenario_id None,
                # so key off the RUN's scenario_id. Single spawns exactly one
                # child -> spawn index 0. spawn_key is a tuple; [0] is the spawn
                # index, NOT a list-element drop.
                if enhanced.spawn_key is None:
                    raise AssertionError("SINGLE run result missing spawn_key")
                _single_spawn_idx = enhanced.spawn_key[0]  # adapter-iter: ok — spawn index
                derived_seed_keys = {str(run.scenario_id): _single_spawn_idx}
                results_payload = _build_results_payload(enhanced)
                # UAT 2026-05-21 issue #211/#212: free the fair_cam result object
                # (carries the full per-iter sample numpy arrays) BEFORE the
                # commit phase, which itself allocates more for SQLite write
                # serialization. The payload dict is already built above —
                # `enhanced` is no longer needed. (The ensemble below needs only
                # `calculator`, so `del enhanced` stays here — Arch-I-Single-Del1.)
                del enhanced

                # #419 weight-robustness (SINGLE — RANGES ONLY, Arch-B3/Meth-B6):
                # SINGLE has no Shapley pass / clean_shapley, so derive the canonical
                # via ONE _compute_shapley_by_scenario over a one-element
                # per_scenario_inputs (full-universe fallback, per_scenario_dict=None),
                # then run the ensemble with compute_rank_stability=False — the
                # displayed SINGLE order is effectiveness-sorted, NOT the Shapley basis
                # the ensemble ranks, so only the basis-agnostic dollar ranges +
                # headline are faithful (stability/indistinguishable verdicts deferred).
                if fair_params is None:  # pragma: no cover — guaranteed by SINGLE branch
                    raise RuntimeError("SINGLE weight-robustness: fair_params must be set")
                _single_per_scenario_inputs: list[tuple[str, str, FAIRParameters]] = [
                    (str(run.scenario_id), _single_scenario_name, fair_params)
                ]

                _single_availability = {
                    str(run.scenario_id): _scenario_availability_self_detects(scenario)
                }

                def _single_weight_robustness() -> tuple[
                    dict[str, Any], dict[str, dict[str, float]], dict[str, list[Any]]
                ]:
                    # Shared cache: the canonical Shapley pass precomposes every
                    # subset (n ≤ 12), so the leave-one-out pass after it is ~free.
                    _canon_comp_cache: dict[frozenset[str], ComposedParts] = {}
                    _by, _sk = _compute_shapley_by_scenario(
                        calculator,
                        _single_per_scenario_inputs,
                        None,
                        [str(c.id) for c in controls],
                        node_mapping=None,
                        total_eval_budget=MAX_ATTRIBUTION_TOTAL_EVALS,
                        per_scenario_availability=_single_availability,
                        composition_cache=_canon_comp_cache,
                    )
                    _sh_clean, _sh_dropped = _sanitize_shapley(_by)
                    # MEAN-basis canonical (side-by-side, 2026-07-04): the ensemble
                    # below runs on the mean chain, so its canonical reference must
                    # be mean-basis too. Shares _canon_comp_cache (statistic-
                    # invariant composition).
                    _by_mean, _sk_mean = _compute_shapley_by_scenario(
                        calculator,
                        _single_per_scenario_inputs,
                        None,
                        [str(c.id) for c in controls],
                        node_mapping=None,
                        total_eval_budget=MAX_ATTRIBUTION_TOTAL_EVALS,
                        per_scenario_availability=_single_availability,
                        composition_cache=_canon_comp_cache,
                        statistic="mean",
                    )
                    _sh_mean_clean, _sh_mean_dropped = _sanitize_shapley(_by_mean)
                    _canon_vals = _aggregate_clean(_by_mean)
                    # "If removed" (leave-one-out) for the SINGLE payload's flat
                    # control_adjustments — drop-cost counterfactual per control.
                    _loo_by, _loo_sk = _compute_loo_by_scenario(
                        calculator,
                        _single_per_scenario_inputs,
                        None,
                        [str(c.id) for c in controls],
                        per_scenario_availability=_single_availability,
                        composition_cache=_canon_comp_cache,
                    )
                    _loo_clean, _loo_dropped = _sanitize_shapley(_loo_by)
                    if _loo_sk or _loo_dropped:
                        logger.warning(
                            "leave-one-out degraded (single): skipped=%s dropped=%s",
                            _loo_sk,
                            _loo_dropped,
                        )
                    _loo_map = _loo_clean.get(str(run.scenario_id), {})
                    # MEAN-basis LOO for the paired "If removed" figure.
                    _loo_mean_by, _loo_mean_sk = _compute_loo_by_scenario(
                        calculator,
                        _single_per_scenario_inputs,
                        None,
                        [str(c.id) for c in controls],
                        per_scenario_availability=_single_availability,
                        composition_cache=_canon_comp_cache,
                        statistic="mean",
                    )
                    _loo_mean_clean, _loo_mean_dropped = _sanitize_shapley(_loo_mean_by)
                    if _loo_mean_sk or _loo_mean_dropped:
                        logger.warning(
                            "mean-basis leave-one-out degraded (single): skipped=%s dropped=%s",
                            _loo_mean_sk,
                            _loo_mean_dropped,
                        )
                    _loo_mean_map = _loo_mean_clean.get(str(run.scenario_id), {})
                    # LOO-Meth-2: surface degradations to the coroutine so they get
                    # audit rows in the terminal transaction (a thread can't await
                    # AuditWriter). Shapley's were previously unaudited on SINGLE
                    # (only AGGREGATE audited) — same fix, same parity.
                    _degradations: dict[str, list[Any]] = {
                        "shapley_skipped": list(_sk),
                        "shapley_dropped": list(_sh_dropped),
                        "loo_skipped": list(_loo_sk),
                        "loo_dropped": list(_loo_dropped),
                        # Basis-specific numeric drops beyond the typical pass's
                        # (structural skips are shared — see the AGGREGATE note).
                        "shapley_dropped_mean_only": sorted(
                            set(_sh_mean_dropped) - set(_sh_dropped)
                        ),
                        "loo_dropped_mean_only": sorted(set(_loo_mean_dropped) - set(_loo_dropped)),
                    }
                    return (
                        _build_weight_robustness(
                            calculator=calculator,
                            controls=controls,
                            per_scenario_inputs=_single_per_scenario_inputs,
                            per_scenario_dict=None,
                            canonical_values=_canon_vals,
                            canonical_values_typical=_aggregate_clean(_by),
                            persisted=run.weight_robustness,
                            random_seed=run.random_seed,
                            compute_rank_stability=False,
                            per_scenario_availability=_single_availability,
                        ),
                        {"typical": _loo_map, "mean": _loo_mean_map},
                        _degradations,
                    )

                # Arch-N5: the WHOLE ensemble (canonical pass + K draws) runs under
                # ONE outer to_thread (GIL-bound, responsiveness-only).
                _robustness_local, _single_loo, _single_degradations = await asyncio.to_thread(
                    _single_weight_robustness
                )
                # LOO-Meth-2: audit attribution degradations (first-class audit rule).
                # Same no-separate-commit pattern as the AGGREGATE block — rides the
                # terminal guarded-UPDATE transaction below.
                if any(_single_degradations.values()):
                    _attr_writer = AuditWriter(session)
                    for _action_key, _action in (
                        ("shapley_skipped", "run.shapley_skipped"),
                        ("loo_skipped", "run.loo_skipped"),
                    ):
                        for _sid, _reason in _single_degradations[_action_key]:
                            await _attr_writer.log(
                                organization_id=run.organization_id,
                                user_id=run.created_by,
                                action=_action,
                                entity_type="risk_analysis_run",
                                entity_id=run.id,
                                changes={"scenario_id": [None, _sid], "reason": [None, _reason]},
                            )
                    for _action_key, _action, _basis in (
                        ("shapley_dropped", "run.non_finite_shapley", None),
                        ("loo_dropped", "run.non_finite_loo", None),
                        ("shapley_dropped_mean_only", "run.non_finite_shapley", "mean"),
                        ("loo_dropped_mean_only", "run.non_finite_loo", "mean"),
                    ):
                        for _sid in _single_degradations.get(_action_key, []):
                            _changes: dict[str, Any] = {"scenario_id": [None, _sid]}
                            if _basis is not None:
                                _changes["basis"] = [None, _basis]
                            await _attr_writer.log(
                                organization_id=run.organization_id,
                                user_id=run.created_by,
                                action=_action,
                                entity_type="risk_analysis_run",
                                entity_id=run.id,
                                changes=_changes,
                            )
                # Inject "if removed" into the SINGLE payload's flat adjustments
                # (absent map -> no key -> "—", the absent≠0.0 convention).
                for _basis_key, _adj_key in (
                    ("typical", "if_removed_value"),
                    ("mean", "if_removed_value_mean"),
                ):
                    _basis_map = _single_loo.get(_basis_key) or {}
                    if _basis_map:
                        for _adj in results_payload.get("control_adjustments", []) or []:
                            _adj[_adj_key] = float(_basis_map.get(_adj.get("control_id"), 0.0))
                # MOVED here (was before the ensemble): SINGLE needs the engine
                # objects for the ensemble, so free them only now (Arch-I-Single-Del1).
                del calculator, fc_controls
            else:
                # Each per-scenario ControlEnhancedRisk carries its scenario_id
                # (set on the aggregate path) and its spawn_key. Capture the
                # mapping BEFORE `del aggregate` frees the heavy arrays.
                # spawn_key is a tuple; [0] is the spawn index, NOT a list drop.
                for ps in aggregate.per_scenario:
                    if ps.scenario_id is None or ps.spawn_key is None:
                        raise AssertionError(
                            "aggregate per-scenario result missing scenario_id/spawn_key"
                        )
                    spawn_idx = ps.spawn_key[0]  # adapter-iter: ok — spawn index
                    derived_seed_keys[ps.scenario_id] = spawn_idx
                results_payload = _build_aggregate_results_payload(aggregate)
                del aggregate
                gc.collect()  # free heavy per-scenario sample arrays BEFORE the Shapley window

                # Shapley needs only calculator + inputs + the validated universe — NOT aggregate.
                # compose_groups is GIL-bound, so to_thread keeps it OFF the event-loop hot path
                # (server stays responsive; the single vCPU is CPU-saturated for the duration —
                # this is not free parallelism). Total compute is bounded by the eval budget.
                if (
                    per_scenario_inputs is None
                ):  # pragma: no cover — guaranteed by the AGGREGATE branch above
                    raise RuntimeError("AGGREGATE branch: per_scenario_inputs must be set")
                # One composition cache shared between the canonical Shapley pass
                # and the leave-one-out pass: exact Shapley (n ≤ 12) evaluates
                # every leave-one-out subset anyway, so LOO afterwards is ~free.
                _canon_comp_cache: dict[frozenset[str], ComposedParts] = {}
                shapley_raw, shapley_skipped = await asyncio.to_thread(
                    _compute_shapley_by_scenario,
                    calculator,
                    per_scenario_inputs,
                    per_scenario_dict,
                    [
                        str(c.id) for c in controls
                    ],  # validated universe (matches active_control_ids)
                    per_scenario_availability=per_scenario_availability,
                    composition_cache=_canon_comp_cache,
                )
                clean_shapley, shapley_dropped = _sanitize_shapley(shapley_raw)
                _inject_shapley(results_payload["per_scenario"], clean_shapley)

                # MEAN-basis canonical Shapley (2026-07-04 side-by-side): same
                # machinery, same subsets, statistic="mean" — shares
                # _canon_comp_cache (composition is statistic-invariant), so this
                # pass costs only the reduction arithmetic. Skip sets are
                # structurally identical to the typical pass (cost/caps depend
                # only on control counts); basis-specific non-finite drops are
                # audited below via the set difference.
                shapley_mean_raw, shapley_mean_skipped = await asyncio.to_thread(
                    _compute_shapley_by_scenario,
                    calculator,
                    per_scenario_inputs,
                    per_scenario_dict,
                    [str(c.id) for c in controls],
                    per_scenario_availability=per_scenario_availability,
                    composition_cache=_canon_comp_cache,
                    statistic="mean",
                )
                clean_shapley_mean, shapley_mean_dropped = _sanitize_shapley(shapley_mean_raw)
                _inject_shapley(
                    results_payload["per_scenario"], clean_shapley_mean, key="shapley_value_mean"
                )

                # "If removed" (leave-one-out): drop-cost counterfactual per control
                # — linear in n, so it also covers over_cap scenarios Shapley skips.
                loo_raw, loo_skipped = await asyncio.to_thread(
                    _compute_loo_by_scenario,
                    calculator,
                    per_scenario_inputs,
                    per_scenario_dict,
                    [str(c.id) for c in controls],
                    per_scenario_availability=per_scenario_availability,
                    composition_cache=_canon_comp_cache,
                )
                clean_loo, loo_dropped = _sanitize_shapley(loo_raw)  # same shape/finite rule
                if loo_skipped or loo_dropped:
                    logger.warning(
                        "leave-one-out degraded: skipped=%s dropped=%s", loo_skipped, loo_dropped
                    )
                _inject_loo(results_payload["per_scenario"], clean_loo)

                # MEAN-basis LOO (side-by-side "If removed"): shared cache, mean chain.
                loo_mean_raw, loo_mean_skipped = await asyncio.to_thread(
                    _compute_loo_by_scenario,
                    calculator,
                    per_scenario_inputs,
                    per_scenario_dict,
                    [str(c.id) for c in controls],
                    per_scenario_availability=per_scenario_availability,
                    composition_cache=_canon_comp_cache,
                    statistic="mean",
                )
                clean_loo_mean, loo_mean_dropped = _sanitize_shapley(loo_mean_raw)
                if loo_mean_skipped or loo_mean_dropped:
                    logger.warning(
                        "mean-basis leave-one-out degraded: skipped=%s dropped=%s",
                        loo_mean_skipped,
                        loo_mean_dropped,
                    )
                _inject_loo(
                    results_payload["per_scenario"], clean_loo_mean, key="if_removed_value_mean"
                )
                del _canon_comp_cache

                # Audit degraded scenarios. NO separate commit (B-Sec-N1): these log
                # calls ride the terminal guarded-UPDATE transaction below so audit +
                # status + results land atomically (the #272 design intent).
                _shapley_writer = AuditWriter(session)
                for _sid, _reason in shapley_skipped:  # {"over_cap","over_budget","error"}
                    await _shapley_writer.log(
                        organization_id=run.organization_id,
                        user_id=run.created_by,
                        action="run.shapley_skipped",
                        entity_type="risk_analysis_run",
                        entity_id=run.id,
                        changes={"scenario_id": [None, _sid], "reason": [None, _reason]},
                    )
                for _sid in shapley_dropped:  # non-finite (post-compute)
                    await _shapley_writer.log(
                        organization_id=run.organization_id,
                        user_id=run.created_by,
                        action="run.non_finite_shapley",
                        entity_type="risk_analysis_run",
                        entity_id=run.id,
                        changes={"scenario_id": [None, _sid]},
                    )
                # LOO-Meth-2: leave-one-out degradations get the same first-class
                # audit as Shapley's — a "—" cell (or a partial aggregate sum) must
                # have a durable record of why, not just a log line.
                for _sid, _reason in loo_skipped:  # {"over_budget","error"}
                    await _shapley_writer.log(
                        organization_id=run.organization_id,
                        user_id=run.created_by,
                        action="run.loo_skipped",
                        entity_type="risk_analysis_run",
                        entity_id=run.id,
                        changes={"scenario_id": [None, _sid], "reason": [None, _reason]},
                    )
                for _sid in loo_dropped:  # non-finite (post-compute)
                    await _shapley_writer.log(
                        organization_id=run.organization_id,
                        user_id=run.created_by,
                        action="run.non_finite_loo",
                        entity_type="risk_analysis_run",
                        entity_id=run.id,
                        changes={"scenario_id": [None, _sid]},
                    )
                # Mean-basis passes share the typical passes' structural skip sets
                # (cost/caps depend only on control counts), so only basis-specific
                # NEW degradations get extra rows — tagged with the basis so a
                # mean-only numeric drop is distinguishable in the audit trail.
                _typ_sh_deg = {_sid for _sid, _r in shapley_skipped} | set(shapley_dropped)
                _typ_loo_deg = {_sid for _sid, _r in loo_skipped} | set(loo_dropped)
                _mean_only_sh = (
                    {_sid for _sid, _r in shapley_mean_skipped} | set(shapley_mean_dropped)
                ) - _typ_sh_deg
                _mean_only_loo = (
                    {_sid for _sid, _r in loo_mean_skipped} | set(loo_mean_dropped)
                ) - _typ_loo_deg
                for _action, _sids in (
                    ("run.non_finite_shapley", _mean_only_sh),
                    ("run.non_finite_loo", _mean_only_loo),
                ):
                    for _sid in sorted(_sids):
                        await _shapley_writer.log(
                            organization_id=run.organization_id,
                            user_id=run.created_by,
                            action=_action,
                            entity_type="risk_analysis_run",
                            entity_id=run.id,
                            changes={"scenario_id": [None, _sid], "basis": [None, "mean"]},
                        )

                # #419 weight-robustness (AGGREGATE — Arch-I2): run the ensemble AFTER
                # _inject_shapley (so the sanitized canonical clean_shapley exists) and
                # BEFORE `del calculator, fc_controls`, reusing the LIVE calculator (no
                # second construction; memory envelope unchanged). Reuse clean_shapley
                # for the canonical values — do NOT re-run a second canonical pass
                # (Arch-B2). Whole ensemble under ONE outer to_thread (Arch-N5).
                # Canonical reference for the ensemble is the MEAN basis — the
                # per-draw ensemble values are mean-basis, so ranks/stability must
                # compare like against like (typical canonical stays in
                # shapley_value for the matrix's paired display).
                _canon_vals_agg = _aggregate_clean(clean_shapley_mean)
                _robustness_local = await asyncio.to_thread(
                    _build_weight_robustness,
                    calculator=calculator,
                    controls=controls,
                    per_scenario_inputs=per_scenario_inputs,
                    per_scenario_dict=per_scenario_dict,
                    canonical_values=_canon_vals_agg,
                    canonical_values_typical=_aggregate_clean(clean_shapley),
                    persisted=run.weight_robustness,
                    random_seed=run.random_seed,
                    compute_rank_stability=True,
                    per_scenario_availability=per_scenario_availability,
                )

                del calculator, fc_controls  # now safe — Shapley + ensemble done
                del per_scenario_inputs  # AGGREGATE always binds it
                if not await _check_cancelled_or_continue(session, run_id):
                    return

            # Cyclic GC pass: numpy arrays held inside fair_cam dataclasses can
            # form reference cycles via back-references; explicit gc.collect()
            # drops them before the SQLite commit allocates serialization
            # buffers. Marginal on small runs; meaningful on the 100k-iter VM
            # OOM path (issue #211 root cause).
            gc.collect()

            # Split the heavy per-iteration sample arrays out of the payload
            # (#297/#294). copy=False is the no-copy/move mode: it mutates
            # results_payload in place, popping the arrays into sample_arrays, so
            # we never hold two copies of the (up to 100k-iter AGGREGATE) heavy
            # arrays near the 2GB VM ceiling. The stripped summary stays on the
            # run; the arrays go to run_samples. Capture run identity into locals
            # BEFORE the guarded UPDATE — the in-memory `run` ORM object may be
            # stale/expired after the write, so do not read off it below.
            run_id_local = run.id
            org_id_local = run.organization_id
            created_by_local = run.created_by
            # T2 (#351): _snapshot_local was already built as a pure local above
            # (BEFORE the engine call, after the 2nd cancel-check) — no ORM read needed.
            summary_payload, sample_arrays = split_simulation_payload(results_payload, copy=False)
            del results_payload
            # Schema-version stamp — written here (not in split) so the
            # split/merge helpers stay pure and the run_samples backfill
            # round-trip stays lossless. Bump policy: see simulation_payload.py.
            summary_payload["schema_version"] = SIMULATION_RESULTS_SCHEMA_VERSION

            # Atomic guarded COMPLETED flip (issue #272). The WHERE status=RUNNING
            # clause re-validates the row's CURRENT persisted status at write
            # time, so a CANCELLED committed by another session in the window is
            # never overwritten. rowcount==0 => a terminal state was already set
            # by another actor; skip the flip, the results persist, and the audit
            # row entirely.
            result: CursorResult[Any] = await session.execute(  # type: ignore[assignment]
                update(RiskAnalysisRun)
                .where(
                    RiskAnalysisRun.id == run_id,
                    RiskAnalysisRun.status == RunStatus.RUNNING,
                )
                .values(
                    status=RunStatus.COMPLETED,
                    completed_at=completed_at,
                    simulation_results=summary_payload,
                    # T2 (#351): persist scenario_inputs_snapshot in the same atomic
                    # UPDATE so a COMPLETED run always carries the snapshot.
                    scenario_inputs_snapshot=_snapshot_local,
                    # P3 (currency): pin presentation FX snapshot in the same atomic UPDATE.
                    presentation_fx_snapshot=_fx_snapshot_local,
                    # #419: persist the weight-robustness ensemble in the SAME atomic
                    # UPDATE (LOCAL var — Arch-I-Persist-Update1). NULL on FAILED/lost-race
                    # (column default); never written via run.* assignment.
                    weight_robustness=_robustness_local,
                )
            )
            if result.rowcount == 0:
                logger.info(
                    "Run %s complete-flip skipped: no longer running "
                    "(terminal state set by another actor)",
                    run_id,
                )
                # A terminal state was set by another actor — do NOT insert a
                # run_samples row (it would orphan against a row whose status we
                # refused to flip). Roll back the pending transaction: this
                # discards the flushed attribution audit rows (run.shapley_skipped /
                # run.non_finite_shapley / run.loo_skipped / run.non_finite_loo,
                # written above on BOTH the AGGREGATE and SINGLE paths) as
                # well as the no-match UPDATE — both must be discarded atomically
                # so audit rows never land without the matching COMPLETED flip.
                await session.rollback()
                return
            # The flip matched our RUNNING row: persist the heavy arrays in the
            # 1:1 run_samples table (skip when empty — a degenerate run may carry
            # no sample arrays). This must happen on the matched branch only.
            if sample_arrays:
                session.add(
                    RunSamples(
                        run_id=run_id_local,
                        organization_id=org_id_local,
                        # Writer invariant: arrays and arrays_codec are NEVER both
                        # populated on a new row. arrays=None here; the binary
                        # codec (services/sample_codec.py) is the sole writer of
                        # the heavy per-iteration arrays going forward. Legacy
                        # rows with arrays populated / arrays_codec NULL are
                        # read-only history, not re-written here.
                        arrays=None,
                        arrays_codec=encode_sample_arrays_streaming(sample_arrays),
                        derived_seed_keys=derived_seed_keys,
                    )
                )
            await AuditWriter(session).log(
                organization_id=org_id_local,
                user_id=created_by_local,
                action="risk_analysis_run.complete",
                entity_type="risk_analysis_run",
                entity_id=run_id_local,
                changes={"status": [RunStatus.RUNNING.value, RunStatus.COMPLETED.value]},
            )
            await session.commit()

        except Exception as exc:
            # Atomic guarded FAILED flip (issue #272). Symmetric with the
            # COMPLETED branch: if a CANCELLED (or COMPLETED) was committed by
            # another session before this exception fired, the guarded UPDATE
            # matches no row and the terminal state is preserved. error_message
            # is written in the same UPDATE so it never lands on a row whose
            # status the UPDATE refused to change.
            # T2 (#351): also persist scenario_inputs_snapshot on FAILED runs
            # (PA2-Arch-I1: "any COMPLETED or FAILED run carries it"). _snapshot_local
            # is pre-declared above (T4M-5-I1 fix); reference directly — no locals().get().
            # Exceptions before the assignment point (scenario-load errors, etc.) produce
            # the pre-declared None, which is the correct "not yet captured" sentinel.
            _fail_snapshot = _snapshot_local
            result = await session.execute(  # type: ignore[assignment]
                update(RiskAnalysisRun)
                .where(
                    RiskAnalysisRun.id == run_id,
                    RiskAnalysisRun.status == RunStatus.RUNNING,
                )
                .values(
                    status=RunStatus.FAILED,
                    error_message=f"{type(exc).__name__}: {exc}",
                    completed_at=now_utc(),
                    scenario_inputs_snapshot=_fail_snapshot,
                    # P3 (currency): pin presentation FX snapshot in the same atomic UPDATE.
                    presentation_fx_snapshot=_fx_snapshot_local,
                )
            )
            if result.rowcount == 0:
                logger.info(
                    "Run %s fail-flip skipped: no longer running "
                    "(terminal state set by another actor)",
                    run_id,
                )
                # A terminal state was set by another actor — roll back the
                # pending transaction: this discards any flushed attribution
                # audit rows (run.shapley_skipped / run.non_finite_shapley /
                # run.loo_skipped / run.non_finite_loo, written above on both
                # the AGGREGATE and SINGLE paths) as well as the no-match UPDATE
                # — both must be discarded atomically so audit rows never land
                # without the matching terminal flip. Pre-attribution-flush
                # exceptions have no flushed rows; rollback is a no-op there.
                await session.rollback()
                logger.exception("Run %s raised after terminalization", run_id)
                return
            await AuditWriter(session).log(
                organization_id=run.organization_id,
                user_id=run.created_by,
                action="risk_analysis_run.fail",
                entity_type="risk_analysis_run",
                entity_id=run.id,
                changes={
                    "status": [RunStatus.RUNNING.value, RunStatus.FAILED.value],
                    "error_class": type(exc).__name__,
                },
            )
            await session.commit()
            logger.exception("Run %s failed", run_id)
