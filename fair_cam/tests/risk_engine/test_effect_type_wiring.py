"""Slice 1 — availability flag reaches every fair_cam gate call site.

Covers the attribution closed form (build_control_adjustment /
reduction_from_composition) AND the native calculator methods, so control-value
(attribution matrix + Shapley/ensemble value function) reflects the availability
recovery credit, not just the MC ALE headline.
"""

from __future__ import annotations

from fair_cam.controls.effectiveness import ControlEffectivenessCalculator
from fair_cam.models.control import Control
from fair_cam.risk_engine.control_attribution import (
    build_control_adjustment,
    reduction_from_composition,
    scenario_base_ale,
    subset_reduction_closed_form,
)
from fair_cam.risk_engine.group_composition import compose_groups
from fair_cam.risk_engine.native_control_aware import NativeControlAwareRiskCalculator
from fair_cam.tests.risk_engine._helpers import make_control, make_fair_parameters


def _resp() -> Control:
    return make_control(
        control_id="resp", assignments=[("lec_resp_resilience", "probability", 0.6)]
    )


def test_reduction_from_composition_credits_availability() -> None:
    rp = make_fair_parameters(tef=10.0, vuln=0.4, primary=1_000_000, secondary=500_000)
    base = scenario_base_ale(rp)
    comp = compose_groups([_resp()])
    gated = reduction_from_composition(base, comp)  # default False -> $0 (D8)
    credited = reduction_from_composition(base, comp, availability_self_detection=True)
    assert gated == 0.0
    assert credited > 0.0


def test_subset_reduction_closed_form_credits_availability() -> None:
    rp = make_fair_parameters(tef=10.0, vuln=0.4, primary=1_000_000, secondary=500_000)
    controls = [_resp()]
    assert subset_reduction_closed_form(rp, controls) == 0.0
    assert subset_reduction_closed_form(rp, controls, availability_self_detection=True) > 0.0


def test_build_control_adjustment_credits_availability() -> None:
    calc = ControlEffectivenessCalculator()
    adj_gated = build_control_adjustment(_resp(), calc, 10.0, 0.4, 1_000_000, 500_000)
    adj_avail = build_control_adjustment(
        _resp(), calc, 10.0, 0.4, 1_000_000, 500_000, availability_self_detection=True
    )
    assert adj_gated.secondary_loss_multiplier == 1.0  # D8: no detection -> identity
    assert adj_avail.secondary_loss_multiplier < 1.0
    assert adj_avail.risk_reduction_value > adj_gated.risk_reduction_value


def test_calculate_control_enhanced_risk_availability_lowers_residual() -> None:
    rp = make_fair_parameters(tef=10.0, vuln=0.4, primary=1_000_000, secondary=500_000)
    calc = NativeControlAwareRiskCalculator(controls=[_resp()], n_simulations=4000, random_seed=7)
    gated = calc.calculate_control_enhanced_risk(rp, ["resp"])
    avail = NativeControlAwareRiskCalculator(
        controls=[_resp()], n_simulations=4000, random_seed=7
    ).calculate_control_enhanced_risk(rp, ["resp"], availability_self_detection=True)
    assert (
        avail.residual_risk.annualized_loss_expectancy
        < gated.residual_risk.annualized_loss_expectancy
    )


def test_aggregate_per_scenario_availability_map() -> None:
    rp = make_fair_parameters(tef=10.0, vuln=0.4, primary=1_000_000, secondary=500_000)
    calc = NativeControlAwareRiskCalculator(controls=[_resp()], n_simulations=4000, random_seed=7)
    agg = calc.calculate_aggregate_enhanced_risk(
        per_scenario_risk_params=[("s1", "s1", rp), ("s2", "s2", rp)],
        active_control_ids=["resp"],
        per_scenario_availability={"s1": True, "s2": False},
    )
    # s1 (availability) credits recovery; s2 (stealth) does not -> per-scenario
    # residuals differ.
    r1 = agg.per_scenario[0].residual_risk.annualized_loss_expectancy
    r2 = agg.per_scenario[1].residual_risk.annualized_loss_expectancy
    assert r1 < r2
