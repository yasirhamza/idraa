# tests/integration/test_run_executor_adapter_lambda.py
"""PR λ adapter iteration contract test (spec item 2).

Project-wide policy per feedback_data_contract_enforcement.md:
adapter mapping list[ORM] → list[DTO] must preserve all elements.
This test would have caught the κ latent bug where _v3_to_fair_cam_control
indexed assignments[0] silently dropping multi-assignment data.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fair_cam.models.control import Control as FairCamControl

from idraa.models.control import Control
from idraa.models.control_function_assignment import ControlFunctionAssignment
from idraa.models.enums import ControlType, EntityStatus, FairCamSubFunction
from idraa.services.run_executor import _v3_to_fair_cam_control


def _make_control_with_n_assignments(n: int) -> Control:
    """Build a Control ORM instance (not persisted) with n distinct assignments."""
    org_id = uuid.uuid4()
    control = Control(
        id=uuid.uuid4(),
        organization_id=org_id,
        name="Test Control",
        type=ControlType.TECHNICAL,
        # Set explicitly: column default fires at flush; this helper builds
        # in-memory Controls that are never flushed.
        annual_cost=Decimal("0"),
        status=EntityStatus.ACTIVE,
        version="1.0",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    sub_functions = [
        FairCamSubFunction.LEC_PREV_RESISTANCE,
        FairCamSubFunction.LEC_DET_RECOGNITION,
        FairCamSubFunction.LEC_PREV_DETERRENCE,
    ][:n]
    control.assignments = [
        ControlFunctionAssignment(
            id=uuid.uuid4(),
            organization_id=org_id,
            control_id=control.id,
            sub_function=sf,
            capability_value=0.7 + 0.05 * i,
            coverage=0.8,
            reliability=0.85,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        for i, sf in enumerate(sub_functions)
    ]
    return control


def test_adapter_iterates_all_three_assignments():
    """Regression test for κ latent bug — adapter must preserve all assignments."""
    v3_ctrl = _make_control_with_n_assignments(3)

    fc_ctrl = _v3_to_fair_cam_control(v3_ctrl)

    assert isinstance(fc_ctrl, FairCamControl)
    assert len(fc_ctrl.assignments) == 3, (
        f"Adapter dropped assignments — expected 3, got {len(fc_ctrl.assignments)}. "
        f"This is the κ latent silent-data-loss bug class."
    )


def test_adapter_field_by_field_equivalence_n3():
    """Each fair_cam assignment must mirror its v3 source row."""
    v3_ctrl = _make_control_with_n_assignments(3)

    fc_ctrl = _v3_to_fair_cam_control(v3_ctrl)

    for v3_a, fc_a in zip(v3_ctrl.assignments, fc_ctrl.assignments, strict=True):
        assert fc_a.sub_function == v3_a.sub_function
        assert fc_a.capability_value == v3_a.capability_value
        assert fc_a.coverage == v3_a.coverage
        assert fc_a.reliability == v3_a.reliability


def test_adapter_raises_on_empty_assignments():
    """Empty assignments → ValueError (matches κ T11 NULL-cap-raises pattern).

    ControlForm.assignments has min_length=1 so this should never happen in
    steady state — defense in depth.
    """
    v3_ctrl = _make_control_with_n_assignments(0)

    with pytest.raises(ValueError, match="no assignments"):
        _v3_to_fair_cam_control(v3_ctrl)


def test_adapter_passes_through_elapsed_time_unit():
    """ELAPSED_TIME assignments pass through unchanged.

    fair_cam's compose_group_effectiveness (κ T9) excludes them from operand
    lists at composition time — adapter does NOT filter here.
    """
    v3_ctrl = _make_control_with_n_assignments(1)
    # Replace the single assignment with an ELAPSED_TIME one
    v3_ctrl.assignments[0].sub_function = FairCamSubFunction.LEC_DET_MONITORING
    v3_ctrl.assignments[0].capability_value = 60.0  # 60 seconds

    fc_ctrl = _v3_to_fair_cam_control(v3_ctrl)

    assert len(fc_ctrl.assignments) == 1
    assert fc_ctrl.assignments[0].sub_function == FairCamSubFunction.LEC_DET_MONITORING
    assert fc_ctrl.assignments[0].capability_value == 60.0


def test_adapter_passes_through_currency_unit():
    """CURRENCY assignments pass through unchanged (same as ELAPSED_TIME)."""
    v3_ctrl = _make_control_with_n_assignments(1)
    # Find a CURRENCY-unit sub_function from SUB_FUNCTION_UNITS
    v3_ctrl.assignments[0].sub_function = FairCamSubFunction.LEC_RESP_LOSS_REDUCTION
    v3_ctrl.assignments[0].capability_value = 50000.0  # $50K

    fc_ctrl = _v3_to_fair_cam_control(v3_ctrl)

    assert len(fc_ctrl.assignments) == 1
    assert fc_ctrl.assignments[0].capability_value == 50000.0
