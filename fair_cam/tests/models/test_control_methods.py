"""FairCamControl method semantics post-reshape."""

import pytest

from fair_cam.models.control import (
    Control,
    ControlDomain,
    ControlType,
    CostModel,
    FairCamControlFunctionAssignment,
)
from fair_cam.models.sub_function import FairCamSubFunction


def _control(*assignments) -> Control:
    return Control(
        control_id="C",
        name="C",
        description="",
        domain=ControlDomain.LOSS_EVENT,
        control_type=ControlType.TECHNICAL,
        cost_model=CostModel(),
        assignments=list(assignments),
    )


def test_get_current_capability_per_assignment():
    a1 = FairCamControlFunctionAssignment(
        sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
        capability_value=0.85,
        coverage=0.88,
        reliability=0.92,
    )
    a2 = FairCamControlFunctionAssignment(
        sub_function=FairCamSubFunction.LEC_DET_VISIBILITY,
        capability_value=0.75,
        coverage=0.88,
        reliability=0.92,
    )
    c = _control(a1, a2)
    # No degradation in PR kappa (default = 0.0); current capability == capability_value
    assert c.get_current_capability(FairCamSubFunction.LEC_PREV_RESISTANCE) == pytest.approx(
        0.85, abs=1e-9
    )
    assert c.get_current_capability(FairCamSubFunction.LEC_DET_VISIBILITY) == pytest.approx(
        0.75, abs=1e-9
    )


def test_get_current_capability_unknown_subfunction_raises():
    c = _control(
        FairCamControlFunctionAssignment(
            sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
            capability_value=0.85,
            coverage=0.88,
            reliability=0.92,
        )
    )
    with pytest.raises(KeyError):
        c.get_current_capability(FairCamSubFunction.LEC_DET_VISIBILITY)


def test_calculate_risk_reduction_factor_single_assignment():
    """Single assignment: OpEff = capability * coverage * reliability;
    OR-aggregation across one element collapses to the OpEff itself."""
    c = _control(
        FairCamControlFunctionAssignment(
            sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
            capability_value=0.7,
            coverage=0.8,
            reliability=0.8,
        )
    )
    # OpEff = 0.7 * 0.8 * 0.8 = 0.448
    # OR-aggregation of [0.448]: 1 - (1 - 0.448) = 0.448
    assert c.calculate_risk_reduction_factor() == pytest.approx(0.448, abs=1e-9)


def test_calculate_risk_reduction_factor_multi_assignment_or_aggregation():
    """Multi-assignment: OR-style aggregation 1 - product(1 - OpEff_i).
    Spec §5.1 squash rule."""
    a1 = FairCamControlFunctionAssignment(
        sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
        capability_value=0.7,
        coverage=0.8,
        reliability=0.8,  # OpEff = 0.448
    )
    a2 = FairCamControlFunctionAssignment(
        sub_function=FairCamSubFunction.LEC_DET_VISIBILITY,
        capability_value=1.0,
        coverage=0.5,
        reliability=1.0,  # OpEff = 0.5
    )
    c = _control(a1, a2)
    # 1 - (1 - 0.448)(1 - 0.5) = 1 - 0.552 * 0.5 = 1 - 0.276 = 0.724
    assert c.calculate_risk_reduction_factor() == pytest.approx(0.724, abs=1e-9)


def test_calculate_risk_reduction_factor_elapsed_time_uses_exponential():
    """PR μ.1 Task 6 re-pin: ELAPSED_TIME assignments use exp(-t/τ) * c * r,
    not the 0.5 safe-default.

    LEC_PREV_RESISTANCE (PROBABILITY): opeff = 0.7 * 0.8 * 0.8 = 0.448
    LEC_RESP_EVENT_TERMINATION (ELAPSED_TIME, τ=64 post-issue-#131):
      opeff = exp(-300/64) * 0.9 * 0.9 ≈ 0.007460
    or_compose([0.448, 0.007460]) = 1 - (1-0.448)(1-0.007460) ≈ 0.452118

    Issue #131 recalibration (2026-05-16): LEC_RESP_EVENT_TERMINATION τ
    moved from 92 (mean / ln(2)) → 64 (raw mean per IBM CODB 2024 p10 Fig 4
    MTTC). Hand-math recomputed via the side-by-side verification
    methodology in CLAUDE.md.
    """
    a1 = FairCamControlFunctionAssignment(
        sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
        capability_value=0.7,
        coverage=0.8,
        reliability=0.8,  # OpEff = 0.448
    )
    a2 = FairCamControlFunctionAssignment(
        sub_function=FairCamSubFunction.LEC_RESP_EVENT_TERMINATION,
        capability_value=300.0,
        coverage=0.9,
        reliability=0.9,  # ELAPSED_TIME: exp(-300/64)*0.81 ≈ 0.007460
    )
    c = _control(a1, a2)
    assert c.calculate_risk_reduction_factor() == pytest.approx(0.452118, abs=1e-5)
