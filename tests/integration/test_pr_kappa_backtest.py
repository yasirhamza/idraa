"""PR κ backtest scenarios (spec §9).

Scenarios A, B, F live in fair_cam/tests/composition/ and risk_engine/.
This file collects engine-path scenarios C, D, E.

Non-determinism handling: these tests do NOT pin random_state. Each Monte
Carlo run reseeds independently. Assertions use analytical PERT-mean
pinning (E[X] = (low + 4*mode + high) / 6) with a ±5% tolerance band over
n=10000 iterations, which is wide enough to absorb sampling noise while
still catching drift in the underlying distribution implementation.
"""

import pytest
from fair_cam.models.control import (
    Control,
    ControlDomain,
    ControlType,
    CostModel,
    FairCamControlFunctionAssignment,
)
from fair_cam.models.sub_function import FairCamSubFunction
from fair_cam.risk_engine.fair_core import (
    DistributionType,
    FAIRDistribution,
    FAIRParameters,
)
from fair_cam.risk_engine.group_composition import build_group_effectiveness_reports
from fair_cam.risk_engine.native_control_aware import NativeControlAwareRiskCalculator


def _scenario_cd_params() -> FAIRParameters:
    """Epic #324 native re-point of the Scenario C/D RiskParameters.

    The legacy dict shape was TEF PERT{5,10,20}, vulnerability constant 0.5,
    primary PERT{500k,1M,2M}, secondary constant 500k. Mirrored here as native
    FAIRDistributions: vulnerability + secondary-loss constants become point-mass
    ``UNIFORM{v, v}``, preserving the analytic PERT-mean anchors."""
    return FAIRParameters(
        threat_event_frequency=FAIRDistribution(
            DistributionType.PERT, {"low": 5.0, "mode": 10.0, "high": 20.0}
        ),
        vulnerability=FAIRDistribution(DistributionType.UNIFORM, {"low": 0.5, "high": 0.5}),
        primary_loss=FAIRDistribution(
            DistributionType.PERT, {"low": 500_000.0, "mode": 1_000_000.0, "high": 2_000_000.0}
        ),
        secondary_loss=FAIRDistribution(
            DistributionType.UNIFORM, {"low": 500_000.0, "high": 500_000.0}
        ),
    )


def _resistance_control(
    capability: float = 0.7, coverage: float = 0.8, reliability: float = 0.8
) -> Control:
    return Control(
        control_id="C1",
        name="Test Control",
        description="",
        domain=ControlDomain.LOSS_EVENT,
        control_type=ControlType.TECHNICAL,
        cost_model=CostModel(),
        assignments=[
            FairCamControlFunctionAssignment(
                sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
                capability_value=capability,
                coverage=coverage,
                reliability=reliability,
            )
        ],
    )


def test_scenario_c_single_assignment_engine_reference():
    """Spec §9.3 Scenario C — Resistance assignment 0.7×0.8×0.8 = 0.448 OpEff;
    LEC tef_mul=0.6416, vuln_mul=0.5968.

    PERT mean formula: (low + 4*mode + high) / 6.
    TEF PERT(5, 10, 20) mean = (5 + 40 + 20)/6 = 10.833.
    Primary loss PERT(500K, 1M, 2M) mean = (500K + 4M + 2M)/6 = $1,083,333.
    E[ALE] = (10.833 × 0.6416) × (0.5 × 0.5968) × ($1,083,333 + $500,000) ≈ $3,284,000.

    Monte Carlo mean within ±5% of PERT-expected ALE over n=10000.
    (Paranoid-review N2 fix: prior version anchored to mode-based $2.872M,
    which would deterministically fail because MC mean is ~14% above mode.)
    """
    c = _resistance_control()
    calc = NativeControlAwareRiskCalculator(controls=[c], n_simulations=10_000)

    enhanced = calc.calculate_control_enhanced_risk(
        risk_params=_scenario_cd_params(),
        active_control_ids=["C1"],
        scenario_name="Scenario C",
    )

    expected_mean_ale = 3_284_000.0
    mean_ale = enhanced.residual_risk.annualized_loss_expectancy
    assert abs(mean_ale - expected_mean_ale) / expected_mean_ale < 0.05, (
        f"Scenario C mean ALE {mean_ale:,.0f} drifted >5% from PERT-expected {expected_mean_ale:,.0f}"
    )


def _multi_assignment_control() -> Control:
    return Control(
        control_id="C1",
        name="Multi-assignment",
        description="",
        domain=ControlDomain.LOSS_EVENT,
        control_type=ControlType.TECHNICAL,
        cost_model=CostModel(),
        assignments=[
            FairCamControlFunctionAssignment(
                sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
                capability_value=0.7,
                coverage=0.8,
                reliability=0.8,  # OpEff = 0.448
            ),
            FairCamControlFunctionAssignment(
                sub_function=FairCamSubFunction.LEC_DET_VISIBILITY,
                capability_value=1.0,
                coverage=0.5,
                reliability=1.0,  # OpEff = 0.5
            ),
        ],
    )


def test_scenario_d_multi_assignment_regression():
    """Spec §9.4 Scenario D — Resistance + Visibility on one control.

    #130 FULL MIGRATION re-pin (was the issue-#90 per-control-multiplicative
    pin). The engine now composes PER BOOLEAN GROUP via the shared
    `compose_groups` routine (engine ≡ diagnostic, D2), NOT per-assignment
    multiplicative factors. Two consequences change this scenario's ALE:

      1. Prevention is OR-composed within the LEC_PREVENTION group: only
         Resistance is present, so g_eff = opeff = 0.7×0.8×0.8 = 0.448.
         The Prevention -> {TEF, Vuln} node mapping (GROUP_NODE_MAPPING) gives
           tef_mult  = 1 - 0.448·0.8 = 0.6416
           vuln_mult = 1 - 0.448·0.9 = 0.5968
      2. Visibility is in the LEC_DETECTION (strict AND) group. Monitoring and
         Recognition are ABSENT, so the AND collapses to 0.0 — AND further,
         Detection has NO standalone node multiplier (it only GATES Response via
         the Detection->Response pair, and no Response sub-function is present).
         So Visibility now contributes NOTHING to the engine ALE.

    This is exactly what `test_scenario_d_layer2_reporting` (below) has always
    asserted for the diagnostic (Prevention 0.448, Detection 0.0); the engine
    now matches it by construction.

    PERT means: E[TEF]=10.833, E[PL]=$1,083,333, SL=$500,000.
      E[ALE] ≈ 10.833 × 0.6416 × 0.5 × 0.5968 × $1,583,333 ≈ $3,283,958.

    (Pre-#130 the now-removed per-control Visibility frequency reduction
    understated ALE at ≈ $1,083,633 — the mis-routing this migration fixes:
    a Detection-only control was wrongly credited with a frequency reduction.)

    Monte Carlo mean within ±5% of PERT-expected ALE over n=10000.
    """
    c = _multi_assignment_control()
    calc = NativeControlAwareRiskCalculator(controls=[c], n_simulations=10_000)

    enhanced = calc.calculate_control_enhanced_risk(
        risk_params=_scenario_cd_params(),
        active_control_ids=["C1"],
        scenario_name="Scenario D",
    )

    # Analytical PERT-expected under per-group composition (Prevention only;
    # Detection contributes nothing). The ±5% band absorbs MC sample variance.
    expected_mean_ale = 3_283_958.0
    mean_ale = enhanced.residual_risk.annualized_loss_expectancy
    assert abs(mean_ale - expected_mean_ale) / expected_mean_ale < 0.05, (
        f"Scenario D mean ALE {mean_ale:,.0f} drifted >5% from PERT-expected {expected_mean_ale:,.0f}"
    )


def test_scenario_d_layer2_reporting():
    """Spec §9.4 Scenario D Layer 2 reporting checks:
    - LEC Prevention OR-trio: g_eff = 0.448 (Resistance only contributes; Avoidance + Deterrence absent → s_i = 0)
    - LEC Detection AND-trio: g_eff = 0 (only Visibility contributes; Monitoring + Recognition absent → AND-product = 0)
    """
    from fair_cam.models.composition_topology import BooleanGroup

    c = _multi_assignment_control()

    reports = build_group_effectiveness_reports([c])

    assert reports[BooleanGroup.LEC_PREVENTION].group_effectiveness == pytest.approx(
        0.448, abs=1e-9
    )
    assert reports[BooleanGroup.LEC_DETECTION].group_effectiveness == 0.0
