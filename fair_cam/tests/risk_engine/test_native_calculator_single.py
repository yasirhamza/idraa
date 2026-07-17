# fair_cam/tests/risk_engine/test_native_calculator_single.py
import numpy as np

from fair_cam.models.risk_enhanced import ControlEnhancedRisk, FAIRRisk
from fair_cam.risk_engine.fair_core import DistributionType, FAIRDistribution, FAIRParameters
from fair_cam.risk_engine.native_control_aware import NativeControlAwareRiskCalculator


def _params():
    return FAIRParameters(
        threat_event_frequency=FAIRDistribution(
            DistributionType.PERT, {"low": 1, "mode": 2, "high": 4}
        ),
        vulnerability=FAIRDistribution(DistributionType.UNIFORM, {"low": 0.3, "high": 0.3}),
        primary_loss=FAIRDistribution(
            DistributionType.PERT, {"low": 1000, "mode": 5000, "high": 20000}
        ),
        secondary_loss=FAIRDistribution(DistributionType.UNIFORM, {"low": 0.0, "high": 0.0}),
    )


def test_returns_control_enhanced_risk_with_sample_arrays():
    calc = NativeControlAwareRiskCalculator(controls=[], n_simulations=10_000, random_seed=1)
    out = calc.calculate_control_enhanced_risk(
        risk_params=_params(), active_control_ids=[], scenario_name="S1"
    )
    assert isinstance(out, ControlEnhancedRisk)
    assert isinstance(out.base_risk, FAIRRisk)
    assert out.base_risk.simulation_results.shape == (10_000,)
    assert out.scenario_name == "S1"


def test_no_controls_residual_equals_base_within_noise():
    calc = NativeControlAwareRiskCalculator(controls=[], n_simulations=50_000, random_seed=2)
    out = calc.calculate_control_enhanced_risk(
        risk_params=_params(), active_control_ids=[], scenario_name="S1"
    )
    assert np.isclose(out.base_risk.mean, out.residual_risk.mean, rtol=0.01)


def test_analytic_ale_anchor_no_controls():
    calc = NativeControlAwareRiskCalculator(controls=[], n_simulations=200_000, random_seed=3)
    out = calc.calculate_control_enhanced_risk(
        risk_params=_params(), active_control_ids=[], scenario_name="S1"
    )
    e_tef = (1 + 4 * 2 + 4) / 6
    e_pl = (1000 + 4 * 5000 + 20000) / 6
    analytic = e_tef * 0.3 * e_pl
    assert np.isclose(out.base_risk.mean, analytic, rtol=0.02)
