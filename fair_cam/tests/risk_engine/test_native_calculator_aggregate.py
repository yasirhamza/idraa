"""AGGREGATE-path tests for NativeControlAwareRiskCalculator (Task 6).

Covers the portfolio rollup (elementwise accumulator sum of per-scenario risk
arrays), the issue-#89 control-coupling validations (registry membership;
per-scenario-id <-> key match; per-scenario controls subset-of-universe;
universe == union of per-scenario controls), independent per-scenario RNG
streams (Arch-B1: cross-scenario corr ~ 0, NOT comonotone), and the N>=3
iteration contract (Spec-I2).

NB: AggregateEnhancedRisk's real home is control_aware.py:179 (NOT
models.risk_enhanced) -- imported from there.
"""

import numpy as np
import pytest

from fair_cam.risk_engine.control_aware import AggregateEnhancedRisk
from fair_cam.risk_engine.fair_core import (
    DistributionType,
    FAIRDistribution,
    FAIRParameters,
)
from fair_cam.risk_engine.native_control_aware import NativeControlAwareRiskCalculator
from fair_cam.tests.risk_engine._helpers import make_control


@pytest.fixture
def a_registered_control():
    """A single Control built via the shared factory (registered into the calc)."""
    return make_control(
        control_id="c1",
        assignments=[("lec_prev_resistance", "probability", 0.7)],
    )


def _p(mode_pl):
    return FAIRParameters(
        threat_event_frequency=FAIRDistribution(
            DistributionType.UNIFORM, {"low": 1.0, "high": 1.0}
        ),
        vulnerability=FAIRDistribution(DistributionType.UNIFORM, {"low": 1.0, "high": 1.0}),
        primary_loss=FAIRDistribution(DistributionType.UNIFORM, {"low": mode_pl, "high": mode_pl}),
        secondary_loss=FAIRDistribution(DistributionType.UNIFORM, {"low": 0.0, "high": 0.0}),
    )


def _pert(mode_pl):  # non-degenerate for the independence test
    return FAIRParameters(
        threat_event_frequency=FAIRDistribution(
            DistributionType.PERT, {"low": 1, "mode": 2, "high": 5}
        ),
        vulnerability=FAIRDistribution(DistributionType.UNIFORM, {"low": 0.4, "high": 0.4}),
        primary_loss=FAIRDistribution(
            DistributionType.PERT, {"low": mode_pl, "mode": mode_pl * 2, "high": mode_pl * 5}
        ),
        secondary_loss=FAIRDistribution(DistributionType.UNIFORM, {"low": 0.0, "high": 0.0}),
    )


def test_aggregate_rollup_is_elementwise_sum():
    calc = NativeControlAwareRiskCalculator(controls=[], n_simulations=5_000, random_seed=1)
    out = calc.calculate_aggregate_enhanced_risk(
        per_scenario_risk_params=[("s1", "S1", _p(100.0)), ("s2", "S2", _p(250.0))],
        active_control_ids=[],
    )
    assert isinstance(out, AggregateEnhancedRisk)
    assert out.n_scenarios == 2
    agg = out.aggregate_without_controls.simulation_results
    assert np.allclose(agg, 350.0)
    summed = sum(ps.base_risk.simulation_results for ps in out.per_scenario)
    assert np.allclose(agg, summed)


def test_aggregate_preserves_all_n_scenarios():  # iteration contract, N>=3 (Spec-I2)
    calc = NativeControlAwareRiskCalculator(controls=[], n_simulations=2_000, random_seed=1)
    items = [
        ("s1", "S1", _p(100.0)),
        ("s2", "S2", _p(200.0)),
        ("s3", "S3", _p(300.0)),
        ("s4", "S4", _p(400.0)),
    ]
    out = calc.calculate_aggregate_enhanced_risk(
        per_scenario_risk_params=items, active_control_ids=[]
    )
    assert out.n_scenarios == 4
    assert [ps.scenario_id for ps in out.per_scenario] == ["s1", "s2", "s3", "s4"]


def test_scenarios_use_independent_rng_streams():  # Arch-B1
    calc = NativeControlAwareRiskCalculator(controls=[], n_simulations=50_000, random_seed=7)
    out = calc.calculate_aggregate_enhanced_risk(
        per_scenario_risk_params=[("s1", "S1", _pert(1000.0)), ("s2", "S2", _pert(1000.0))],
        active_control_ids=[],
    )
    a = out.per_scenario[0].base_risk.simulation_results
    b = out.per_scenario[1].base_risk.simulation_results
    # Independent streams -> near-zero correlation (NOT comonotone ~1.0).
    corr = float(np.corrcoef(a, b)[0, 1])
    print(f"cross-scenario correlation = {corr}")
    assert abs(corr) < 0.05


def test_aggregate_requires_two_scenarios():
    calc = NativeControlAwareRiskCalculator(controls=[], n_simulations=100, random_seed=1)
    with pytest.raises(ValueError):
        calc.calculate_aggregate_enhanced_risk(
            per_scenario_risk_params=[("s1", "S1", _p(100.0))], active_control_ids=[]
        )


def test_aggregate_rejects_unknown_per_scenario_control_universe():
    # #89 subset coupling: per-scenario control outside the universe must raise.
    calc = NativeControlAwareRiskCalculator(controls=[], n_simulations=100, random_seed=1)
    with pytest.raises(ValueError):
        calc.calculate_aggregate_enhanced_risk(
            per_scenario_risk_params=[("s1", "S1", _p(100.0)), ("s2", "S2", _p(200.0))],
            active_control_ids=[],
            per_scenario_active_control_ids={
                "s1": [],
                "s2": ["unknown-ctrl"],
            },  # outside the universe
        )


def test_aggregate_rejects_universe_control_applied_to_no_scenario(a_registered_control):
    # #89 union-equality (4th check): a universe control applied to NO scenario
    # must raise (active_control_ids != union of per-scenario).
    cid = a_registered_control.control_id
    calc = NativeControlAwareRiskCalculator(
        controls=[a_registered_control], n_simulations=100, random_seed=1
    )
    with pytest.raises(ValueError, match="union"):
        calc.calculate_aggregate_enhanced_risk(
            per_scenario_risk_params=[("s1", "S1", _p(100.0)), ("s2", "S2", _p(200.0))],
            active_control_ids=[cid],  # declared in the universe
            per_scenario_active_control_ids={"s1": [], "s2": []},  # but applied nowhere
        )
