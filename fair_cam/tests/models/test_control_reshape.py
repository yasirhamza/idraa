"""FairCamControl post-reshape -- drops flat triple, requires assignments."""

import pytest

from fair_cam.models.control import (
    Control,
    ControlDomain,
    ControlType,
    CostModel,
    FairCamControlFunctionAssignment,
)
from fair_cam.models.sub_function import FairCamSubFunction


def _assignment(
    sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE, **kwargs
) -> FairCamControlFunctionAssignment:
    defaults = {"capability_value": 0.7, "coverage": 0.8, "reliability": 0.8}
    defaults.update(kwargs)
    return FairCamControlFunctionAssignment(sub_function=sub_function, **defaults)


def test_control_requires_at_least_one_assignment():
    """Empty assignments list rejected at construction."""
    with pytest.raises(ValueError, match="at least one assignment"):
        Control(
            control_id="C1",
            name="Test",
            description="",
            domain=ControlDomain.LOSS_EVENT,
            control_type=ControlType.TECHNICAL,
            cost_model=CostModel(),
            assignments=[],
        )


def test_control_accepts_single_assignment():
    c = Control(
        control_id="C1",
        name="Test",
        description="",
        domain=ControlDomain.LOSS_EVENT,
        control_type=ControlType.TECHNICAL,
        cost_model=CostModel(),
        assignments=[_assignment()],
    )
    assert len(c.assignments) == 1


def test_control_accepts_multiple_assignments():
    c = Control(
        control_id="C1",
        name="EDR",
        description="",
        domain=ControlDomain.LOSS_EVENT,
        control_type=ControlType.TECHNICAL,
        cost_model=CostModel(),
        assignments=[
            _assignment(sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE),
            _assignment(sub_function=FairCamSubFunction.LEC_DET_VISIBILITY),
            _assignment(sub_function=FairCamSubFunction.LEC_DET_RECOGNITION),
        ],
    )
    assert len(c.assignments) == 3


def test_control_no_longer_accepts_flat_triple_kwargs():
    """Drop-test: control_strength/reliability/coverage are no longer constructor kwargs."""
    with pytest.raises(TypeError, match=r"(unexpected keyword argument|control_strength)"):
        Control(
            control_id="C1",
            name="Test",
            description="",
            domain=ControlDomain.LOSS_EVENT,
            control_type=ControlType.TECHNICAL,
            cost_model=CostModel(),
            control_strength=0.7,  # should error
            control_reliability=0.8,
            control_coverage=0.8,
            assignments=[_assignment()],
        )


def test_control_has_no_degradation_rate_field():
    """Per-Control degradation_rate dropped; per-assignment degradation_rate is the canonical home."""
    c = Control(
        control_id="C1",
        name="Test",
        description="",
        domain=ControlDomain.LOSS_EVENT,
        control_type=ControlType.TECHNICAL,
        cost_model=CostModel(),
        assignments=[_assignment()],
    )
    assert not hasattr(c, "degradation_rate")


def test_deprecated_control_function_still_accepts_values():
    """control_function is deprecated but must still be constructible and
    inspectable for backward compat with _get_function_description. A future PR
    accidentally removing the field should fail this test. (fair_cam_mappings /
    FairCamMapping / get_fair_impact_factor were removed in #177 -- zero callers,
    contradictory unlabelled weight table; register C4.)"""
    from fair_cam.models.control import ControlFunction

    c = Control(
        control_id="C1",
        name="Test",
        description="",
        domain=ControlDomain.LOSS_EVENT,
        control_type=ControlType.TECHNICAL,
        cost_model=CostModel(),
        assignments=[_assignment()],
        control_function=ControlFunction.THREAT_PREVENTION,
    )
    assert c.control_function == ControlFunction.THREAT_PREVENTION


def test_fair_impact_factor_surface_is_gone():
    """#177: the second, contradictory FAIR-axis weight table and everything that
    fed it must stay deleted (register C4; cleanup record in fair-cam-methodology.md).
    Kept separate from the control_function compat test so it survives PR mu."""
    from fair_cam.models import control as _c

    c = Control(
        control_id="C1",
        name="Test",
        description="",
        domain=ControlDomain.LOSS_EVENT,
        control_type=ControlType.TECHNICAL,
        cost_model=CostModel(),
        assignments=[_assignment()],
    )
    assert not hasattr(c, "fair_cam_mappings")
    assert not hasattr(c, "get_fair_impact_factor")
    assert not hasattr(c, "add_fair_cam_mapping")
    assert not hasattr(_c, "FairCamMapping")
    assert not hasattr(_c.ControlRegistry, "get_controls_by_fair_mapping")
    assert "fair_mappings" not in c.get_fair_cam_classification()
