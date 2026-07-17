"""Cost-summary aggregation in the run_executor result builders.

Stacked on the cost-model collapse (PR #63) and issue #66's Decimal
migration: fair_cam now populates `ControlAdjustment.control_cost` from
v3's `Control.annual_cost` (Decimal, coerced to float at the bridge —
fair_cam's `CostModel.annual_cost` remains a float DTO field).
This module covers the next layer — the run_executor's result builders
read those per-control costs and emit a `cost_summary` block on the
serialised payload.

Three contracts under test:
  1. The serialised ControlAdjustment dict carries control_cost +
     risk_reduction_value (regression — they were dropped pre-fix,
     blocking aggregate rendering).
  2. The SINGLE-run payload's cost_summary aggregates per-control
     control_cost into total_annual_cost; net_benefit = ALE-reduction -
     cost; aggregate_roi = ALE-reduction / cost (None when cost = 0).
  3. The AGGREGATE-run payload dedup's controls across scenarios —
     a control mitigating two scenarios in the same run is counted ONCE
     in the aggregate cost (org pays for it once per year).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from idraa.services.run_executor import (
    _build_aggregate_results_payload,
    _build_cost_summary,
    _build_results_payload,
    _control_adjustment_to_dict,
    _v3_to_fair_cam_control,
)

# ---------------------------------------------------------------------------
# Helpers — minimal namespace mocks (no fair_cam class dependency)
# ---------------------------------------------------------------------------


def _adj(*, control_id: str, cost: float, reduction: float) -> SimpleNamespace:
    return SimpleNamespace(
        control_id=control_id,
        control_name=f"Control {control_id}",
        threat_event_frequency_multiplier=1.0,
        vulnerability_multiplier=1.0,
        primary_loss_multiplier=1.0,
        secondary_loss_multiplier=1.0,
        control_effectiveness=0.5,  # Arch-I4: field is control_effectiveness, not effectiveness
        control_cost=cost,
        risk_reduction_value=reduction,
        loss_reduction_per_event=0.0,
    )


def _fair_risk(*, ale: float) -> SimpleNamespace:
    return SimpleNamespace(
        loss_event_frequency=0.0,
        loss_magnitude=0.0,
        annualized_loss_expectancy=ale,
        mean=0.0,
        median=0.0,
        mode=0.0,
        std_deviation=0.0,
        var_95=0.0,
        var_99=0.0,
        simulation_results=None,
        n_simulations=10000,
    )


def _enhanced(
    *,
    base_ale: float,
    residual_ale: float,
    adjustments: list[SimpleNamespace],
) -> SimpleNamespace:
    return SimpleNamespace(
        base_risk=_fair_risk(ale=base_ale),
        residual_risk=_fair_risk(ale=residual_ale),
        control_adjustments=adjustments,
        confidence_intervals=SimpleNamespace(
            confidence_level=0.95,
            lower_bound=0.0,
            upper_bound=0.0,
            standard_error=0.0,
            sample_size=10000,
        ),
    )


# ---------------------------------------------------------------------------
# Contract 1 — adjustment serialiser carries cost
# ---------------------------------------------------------------------------


def test_adjustment_dict_includes_control_cost_and_risk_reduction() -> None:
    out = _control_adjustment_to_dict(_adj(control_id="c-1", cost=12000.0, reduction=45000.0))
    assert out["control_cost"] == 12000.0
    assert out["risk_reduction_value"] == 45000.0
    # Existing fields remain.
    assert out["effectiveness"] == 0.5
    assert out["control_id"] == "c-1"


def test_adjustment_dict_falls_back_to_zero_when_fields_missing() -> None:
    """Older fair_cam objects / mocks without the new fields → 0.0,
    not KeyError. Defensive."""
    bare = SimpleNamespace(
        control_id="c-2",
        control_name="C2",
        threat_event_frequency_multiplier=1.0,
        vulnerability_multiplier=1.0,
        primary_loss_multiplier=1.0,
        secondary_loss_multiplier=1.0,
        control_effectiveness=0.5,  # Arch-I4: field is control_effectiveness
        # control_cost, risk_reduction_value, and loss_reduction_per_event deliberately absent
    )
    out = _control_adjustment_to_dict(bare)
    assert out["control_cost"] == 0.0
    assert out["risk_reduction_value"] == 0.0


# ---------------------------------------------------------------------------
# Contract 2 — SINGLE cost_summary
# ---------------------------------------------------------------------------


def test_single_cost_summary_aggregates_per_control() -> None:
    enhanced = _enhanced(
        base_ale=500_000.0,
        residual_ale=200_000.0,
        adjustments=[
            _adj(control_id="c-1", cost=12_000.0, reduction=180_000.0),
            _adj(control_id="c-2", cost=8_000.0, reduction=120_000.0),
        ],
    )
    summary = _build_cost_summary(enhanced)
    assert summary["total_annual_cost"] == 20_000.0
    assert summary["total_risk_reduction"] == 300_000.0  # base - residual
    assert summary["net_benefit"] == 280_000.0  # reduction - cost
    assert summary["aggregate_roi"] == 15.0  # 300_000 / 20_000


def test_single_cost_summary_roi_is_none_when_cost_is_zero() -> None:
    """No cost → ROI is `None` (renders as ``—``), not `inf`."""
    enhanced = _enhanced(
        base_ale=100_000.0,
        residual_ale=80_000.0,
        adjustments=[_adj(control_id="c-1", cost=0.0, reduction=20_000.0)],
    )
    summary = _build_cost_summary(enhanced)
    assert summary["total_annual_cost"] == 0.0
    assert summary["aggregate_roi"] is None
    # Net benefit still meaningful (= reduction).
    assert summary["net_benefit"] == 20_000.0


def test_single_payload_includes_cost_summary_block() -> None:
    """End-to-end: _build_results_payload includes the cost_summary
    block, so consumers (template, exec PDF) can read it without
    re-aggregating."""
    enhanced = _enhanced(
        base_ale=100_000.0,
        residual_ale=50_000.0,
        adjustments=[_adj(control_id="c-1", cost=5_000.0, reduction=50_000.0)],
    )
    payload = _build_results_payload(enhanced)
    assert "cost_summary" in payload
    assert payload["cost_summary"]["total_annual_cost"] == 5_000.0
    assert payload["cost_summary"]["aggregate_roi"] == 10.0


# ---------------------------------------------------------------------------
# Contract 3 — AGGREGATE dedup's shared controls
# ---------------------------------------------------------------------------


def _aggregate_with(
    per_scenario_adjustments: list[list[SimpleNamespace]],
    *,
    base_ale_per: float = 100_000.0,
    residual_ale_per: float = 60_000.0,
) -> SimpleNamespace:
    """Build a minimal aggregate.per_scenario shape."""
    per_scenario = []
    for i, adjs in enumerate(per_scenario_adjustments):
        ps = _enhanced(base_ale=base_ale_per, residual_ale=residual_ale_per, adjustments=adjs)
        ps.scenario_id = f"s-{i}"
        ps.scenario_name = f"Scenario {i}"
        per_scenario.append(ps)

    n_scen = len(per_scenario)
    return SimpleNamespace(
        per_scenario=per_scenario,
        # Aggregate ALE = simple sum here; the real engine produces a meta
        # rollup but the cost-summary code only reads ALE values, not the
        # samples. Same shape suffices.
        aggregate_with_controls=_fair_risk(ale=residual_ale_per * n_scen),
        aggregate_without_controls=_fair_risk(ale=base_ale_per * n_scen),
        confidence_intervals=SimpleNamespace(
            confidence_level=0.95,
            lower_bound=0.0,
            upper_bound=0.0,
            standard_error=0.0,
            sample_size=10000,
        ),
        control_value_dollars=0.0,
        control_value_percent=0.0,
        n_scenarios=n_scen,
        n_simulations=10000,
    )


def test_aggregate_cost_summary_dedups_shared_controls() -> None:
    """A control mitigating two scenarios in the same run pays for itself
    ONCE, not twice. Dedup by control_id."""
    shared = _adj(control_id="mfa", cost=12_000.0, reduction=50_000.0)
    unique_to_s1 = _adj(control_id="edr", cost=20_000.0, reduction=80_000.0)
    aggregate = _aggregate_with(
        [
            [shared, unique_to_s1],  # s-0: shared + edr
            [shared],  # s-1: shared only
        ],
    )
    payload = _build_aggregate_results_payload(aggregate)
    cost = payload["cost_summary"]
    # mfa ($12K) + edr ($20K) = $32K — NOT $44K (which would be 2*mfa + edr)
    assert cost["total_annual_cost"] == 32_000.0
    assert cost["n_unique_controls"] == 2


def test_aggregate_cost_summary_roi_uses_aggregate_ale_delta() -> None:
    """Aggregate ROI = aggregate_without_controls.ALE - aggregate_with_controls.ALE
    divided by dedup'd cost. Not a per-scenario ROI sum (which would
    double-count savings)."""
    aggregate = _aggregate_with(
        [
            [_adj(control_id="c-1", cost=10_000.0, reduction=80_000.0)],
            [_adj(control_id="c-1", cost=10_000.0, reduction=80_000.0)],  # SAME control
        ],
        base_ale_per=200_000.0,
        residual_ale_per=120_000.0,
    )
    cost = _build_aggregate_results_payload(aggregate)["cost_summary"]
    # aggregate without_controls = 400K; with_controls = 240K; reduction = 160K.
    # Cost = $10K (one unique control). ROI = 160K / 10K = 16x.
    assert cost["total_annual_cost"] == 10_000.0
    assert cost["total_risk_reduction"] == 160_000.0
    assert cost["aggregate_roi"] == 16.0


# ---------------------------------------------------------------------------
# Contract 4 — Internal arithmetic consistency of the four cost cards
# (Issue #70 regression guard.)
# ---------------------------------------------------------------------------
#
# The run-detail template renders four cards from a single ``cost_summary``
# dict: total_annual_cost, total_risk_reduction, net_benefit, aggregate_roi.
# A historical AGGREGATE run (id 43984f26, since deleted) displayed values
# where the four cards did not reconcile arithmetically. The run had been
# persisted before the current single-dict-literal construction landed; the
# JSON was frozen and could not be repaired.
#
# These tests guarantee the invariants always hold, for both the SINGLE and
# AGGREGATE builders, regardless of inputs. They guard against a future
# refactor accidentally reading ``total_annual_cost`` from one source and the
# net_benefit / aggregate_roi denominators from another.


_SINGLE_CONSISTENCY_INPUTS = [
    pytest.param(
        500_000.0,
        200_000.0,
        [("c-1", 12_000.0, 180_000.0), ("c-2", 8_000.0, 120_000.0)],
        id="two-controls-positive-roi",
    ),
    pytest.param(
        1_000_000.0, 100_000.0, [("c-1", 50_000.0, 900_000.0)], id="single-control-large-roi"
    ),
    pytest.param(100_000.0, 80_000.0, [("c-1", 0.0, 20_000.0)], id="zero-cost-roi-none"),
    pytest.param(
        500_000.0,
        490_000.0,
        [("c-1", 50_000.0, 10_000.0)],
        id="under-water-net-benefit-negative",
    ),
]


@pytest.mark.parametrize("base_ale,residual_ale,adjustments", _SINGLE_CONSISTENCY_INPUTS)
def test_single_cost_summary_internal_consistency(
    base_ale: float,
    residual_ale: float,
    adjustments: list[tuple[str, float, float]],
) -> None:
    """net_benefit and aggregate_roi must reconcile with the displayed
    total_risk_reduction and total_annual_cost for any inputs."""
    enhanced = _enhanced(
        base_ale=base_ale,
        residual_ale=residual_ale,
        adjustments=[_adj(control_id=cid, cost=c, reduction=r) for cid, c, r in adjustments],
    )
    cost = _build_cost_summary(enhanced)
    assert cost["net_benefit"] == cost["total_risk_reduction"] - cost["total_annual_cost"]
    if cost["total_annual_cost"] > 0:
        assert cost["aggregate_roi"] == cost["total_risk_reduction"] / cost["total_annual_cost"]
    else:
        assert cost["aggregate_roi"] is None


_AGGREGATE_CONSISTENCY_INPUTS = [
    pytest.param(
        [
            [("c-1", 10_000.0, 80_000.0)],
            [("c-1", 10_000.0, 80_000.0)],
        ],
        200_000.0,
        120_000.0,
        id="shared-control-dedup",
    ),
    pytest.param(
        [
            [("mfa", 12_000.0, 50_000.0), ("edr", 20_000.0, 80_000.0)],
            [("mfa", 12_000.0, 50_000.0), ("siem", 15_000.0, 60_000.0)],
            [("edr", 20_000.0, 80_000.0)],
        ],
        300_000.0,
        180_000.0,
        id="three-scenarios-overlapping-controls",
    ),
    pytest.param(
        [
            [("c-1", 0.0, 50_000.0)],
            [("c-1", 0.0, 50_000.0)],
        ],
        100_000.0,
        60_000.0,
        id="zero-cost-aggregate-roi-none",
    ),
]


@pytest.mark.parametrize(
    "per_scenario_adjustments,base_ale_per,residual_ale_per", _AGGREGATE_CONSISTENCY_INPUTS
)
def test_aggregate_cost_summary_internal_consistency(
    per_scenario_adjustments: list[list[tuple[str, float, float]]],
    base_ale_per: float,
    residual_ale_per: float,
) -> None:
    """AGGREGATE-path twin of the SINGLE invariant test."""
    aggregate = _aggregate_with(
        [
            [_adj(control_id=cid, cost=c, reduction=r) for cid, c, r in adjs]
            for adjs in per_scenario_adjustments
        ],
        base_ale_per=base_ale_per,
        residual_ale_per=residual_ale_per,
    )
    cost = _build_aggregate_results_payload(aggregate)["cost_summary"]
    assert cost["net_benefit"] == cost["total_risk_reduction"] - cost["total_annual_cost"]
    if cost["total_annual_cost"] > 0:
        assert cost["aggregate_roi"] == cost["total_risk_reduction"] / cost["total_annual_cost"]
    else:
        assert cost["aggregate_roi"] is None


# ---------------------------------------------------------------------------
# Issue #66 — Decimal → float coercion at the fair_cam boundary
# ---------------------------------------------------------------------------


def test_v3_to_fair_cam_control_coerces_decimal_to_float() -> None:
    """Issue #66: the fair_cam boundary accepts Decimal and coerces to float."""
    from decimal import Decimal
    from uuid import uuid4

    from idraa.models.enums import ControlType, FairCamSubFunction

    v3_ctrl = SimpleNamespace(
        id=uuid4(),
        name="Decimal Cost Test",
        type=ControlType.ADMINISTRATIVE,
        annual_cost=Decimal("12000.50"),
        assignments=[
            SimpleNamespace(
                sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
                capability_value=0.8,
                coverage=1.0,
                reliability=1.0,
            )
        ],
    )
    fc = _v3_to_fair_cam_control(v3_ctrl)
    assert fc.cost_model.annual_cost == 12000.50
    assert isinstance(fc.cost_model.annual_cost, float)
