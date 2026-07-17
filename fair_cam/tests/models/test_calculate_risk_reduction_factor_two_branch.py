"""calculate_risk_reduction_factor uses two-branch math (Arch-B3, PR μ.1)."""

import math
from uuid import uuid4

import pytest

from fair_cam.calibration.elapsed_time_taus import get_canonical_tau
from fair_cam.models.control import (
    Control,
    ControlType,
    CostModel,
    FairCamControlFunctionAssignment,
)
from fair_cam.models.sub_function import FairCamSubFunction


def _ctrl(assignments) -> Control:
    return Control(
        control_id=str(uuid4()),
        name="C",
        description="",
        control_type=ControlType.TECHNICAL,
        assignments=assignments,
        cost_model=CostModel(annual_cost=10_000.0),
    )


def test_elapsed_time_assignment_uses_exponential_not_half_default() -> None:
    """A 0-day elapsed time should give opeff=1.0 * c * r = 0.64, not 0.5.

    Issue #131 recalibration: switched from VMC_ID_CONTROL_MONITORING
    (reclassified to PROBABILITY) to VMC_CORR_IMPLEMENTATION (KEPT
    ELAPSED_TIME, τ=79.3) — both are VMC and the test's intent is to
    exercise the ELAPSED_TIME branch math at t=0, which is τ-independent.
    """
    sf = FairCamSubFunction.VMC_CORR_IMPLEMENTATION
    asg = FairCamControlFunctionAssignment(
        sub_function=sf,
        capability_value=0.0,
        coverage=0.8,
        reliability=0.8,
        degradation_rate=0.0,
    )
    c = _ctrl([asg])
    # or_compose([0.64]) == 0.64 (single element)
    assert c.calculate_risk_reduction_factor() == pytest.approx(0.64, abs=1e-3)


def test_currency_assignment_excluded_from_opeff() -> None:
    """LEC_RESP_LOSS_REDUCTION is CURRENCY — has no opeff, excluded from squash."""
    sf = FairCamSubFunction.LEC_RESP_LOSS_REDUCTION
    asg = FairCamControlFunctionAssignment(
        sub_function=sf,
        capability_value=250_000.0,
        coverage=0.9,
        reliability=0.95,
        degradation_rate=0.0,
    )
    other_asg = FairCamControlFunctionAssignment(
        sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
        capability_value=0.85,
        coverage=0.9,
        reliability=0.9,
        degradation_rate=0.0,
    )
    c = _ctrl([asg, other_asg])
    # Squash should only include LEC_PREV_RESISTANCE's opeff.
    # asn_eff = 0.85*0.9*0.9 = 0.6885; or_compose([0.6885]) = 0.6885
    assert c.calculate_risk_reduction_factor() == pytest.approx(0.6885, abs=1e-3)


def test_elapsed_time_at_median_yields_half_opeff_times_cov_rel() -> None:
    """t = τ·ln(2) → opeff = 0.5; asn_eff = 0.5 * cov * rel.

    Issue #131: switched dropped VMC_ID_CONTROL_MONITORING → kept
    VMC_CORR_IMPLEMENTATION. τ value is read dynamically via
    get_canonical_tau, so the test is τ-version-agnostic.
    """
    sf = FairCamSubFunction.VMC_CORR_IMPLEMENTATION
    tau = get_canonical_tau(sf)
    asg = FairCamControlFunctionAssignment(
        sub_function=sf,
        capability_value=tau * math.log(2),
        coverage=0.8,
        reliability=0.8,
        degradation_rate=0.0,
    )
    c = _ctrl([asg])
    # 0.5 * 0.8 * 0.8 = 0.32; or_compose([0.32]) == 0.32
    assert c.calculate_risk_reduction_factor() == pytest.approx(0.32, abs=1e-3)


def test_null_capability_value_elapsed_time_falls_back_to_median() -> None:
    """NULL → algebraic identity 0.5 (issue #131 Arch3-N1); asn_eff = 0.5*cov*rel.

    Issue #131: switched dropped VMC_ID_CONTROL_MONITORING → kept
    VMC_CORR_IMPLEMENTATION. The NULL fallback is τ-independent
    (0.5*coverage*reliability via compute_assignment_part's 0.5 NULL-anchor;
    the standalone _null_safe_default helper was folded into it by Slice 2 #439).
    """
    sf = FairCamSubFunction.VMC_CORR_IMPLEMENTATION
    asg = FairCamControlFunctionAssignment(
        sub_function=sf,
        capability_value=None,
        coverage=0.8,
        reliability=0.8,
        degradation_rate=0.0,
    )
    c = _ctrl([asg])
    assert c.calculate_risk_reduction_factor() == pytest.approx(0.32, abs=1e-3)


def test_no_assignments_returns_zero() -> None:
    """Empty assignments list → 0.0 (no opeffs to or_compose).

    Control requires at least one assignment at construction time (spec §4.3).
    Mutate post-construction to exercise the loop-zero path.
    """
    sf = FairCamSubFunction.LEC_PREV_RESISTANCE
    asg = FairCamControlFunctionAssignment(
        sub_function=sf,
        capability_value=0.7,
        coverage=0.8,
        reliability=0.8,
        degradation_rate=0.0,
    )
    c = _ctrl([asg])
    c.assignments = []  # mutate post-construction — same pattern as test_layer3 suite
    assert c.calculate_risk_reduction_factor() == 0.0
