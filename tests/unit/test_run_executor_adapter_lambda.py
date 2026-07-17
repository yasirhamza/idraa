"""Issue #90 Task 1: _v3_to_fair_cam_control derives representative domain from assignments.

Pre-issue-90 the v3-to-fair_cam adapter passed `_DOMAIN_MAP[v3_ctrl.domain]`
(the denormalized scalar column) into the fair_cam Control. Post-issue-90
the adapter reads `subfunction_to_domain(assignments[0].sub_function)` to
pick a representative domain — preserving fair_cam's single-domain field
contract while breaking the dependency on the (now-removed) column.

These tests pin:
  - The adapter derives a representative domain from assignments[0]'s
    sub_function (Task 2 dropped Control.domain entirely; the assignment
    side is now the only source).
  - The empty-assignments guard from before issue-90 is preserved.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fair_cam.models.control import Control as FairCamControl
from fair_cam.models.control import ControlDomain as FairCamControlDomain

from idraa.models.control import Control
from idraa.models.control_function_assignment import ControlFunctionAssignment
from idraa.models.enums import ControlType, EntityStatus, FairCamSubFunction
from idraa.services.run_executor import _v3_to_fair_cam_control


def _make_v3_control_with_assignment(
    *,
    sub_function: FairCamSubFunction,
) -> Control:
    """Build an in-memory Control + one assignment.

    sub_function determines the control's representative domain via
    subfunction_to_domain (issue #90 task 2 dropped the redundant
    Control.domain column — assignments are now the only source).
    """
    org_id = uuid.uuid4()
    control = Control(
        id=uuid.uuid4(),
        organization_id=org_id,
        name="Test Control",
        type=ControlType.TECHNICAL,
        annual_cost=Decimal("0"),
        status=EntityStatus.ACTIVE,
        version="1.0",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    control.assignments = [
        ControlFunctionAssignment(
            id=uuid.uuid4(),
            organization_id=org_id,
            control_id=control.id,
            sub_function=sub_function,
            capability_value=0.7,
            coverage=0.8,
            reliability=0.85,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
    ]
    return control


def test_adapter_picks_domain_from_first_assignment_not_column() -> None:
    """Issue #90: adapter reads representative domain from assignments[0].

    Task 2 dropped Control.domain — only the assignment side remains as
    the source of truth for the representative domain on the fair_cam
    Control. This test pins that the DSC sub-function flows through to
    DECISION_SUPPORT on the fair_cam side.
    """
    v3_ctrl = _make_v3_control_with_assignment(
        sub_function=FairCamSubFunction.DSC_PREV_DEFINED_EXPECTATIONS,
    )

    fc_ctrl = _v3_to_fair_cam_control(v3_ctrl)

    assert isinstance(fc_ctrl, FairCamControl)
    assert fc_ctrl.domain == FairCamControlDomain.DECISION_SUPPORT, (
        f"adapter must derive representative domain from assignments[0] — got {fc_ctrl.domain!r}"
    )


def test_adapter_picks_variance_management_from_vmc_assignment() -> None:
    """Sanity coverage for the VMC branch of the per-sub-function decoder."""
    v3_ctrl = _make_v3_control_with_assignment(
        sub_function=FairCamSubFunction.VMC_PREV_REDUCE_VARIANCE_PROB,
    )
    fc_ctrl = _v3_to_fair_cam_control(v3_ctrl)
    assert fc_ctrl.domain == FairCamControlDomain.VARIANCE_MANAGEMENT


def test_adapter_preserves_empty_assignments_guard() -> None:
    """The empty-assignments guard at run_executor.py:146 must NOT regress.

    Plan-gate fix CR-I1: even after the read path switches to
    assignments[0], the empty-assignments ValueError must keep firing
    BEFORE the assignments[0] dereference so we don't IndexError instead.
    """
    org_id = uuid.uuid4()
    v3_ctrl = Control(
        id=uuid.uuid4(),
        organization_id=org_id,
        name="No-Assignments",
        type=ControlType.TECHNICAL,
        annual_cost=Decimal("0"),
        status=EntityStatus.ACTIVE,
        version="1.0",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    v3_ctrl.assignments = []

    with pytest.raises(ValueError, match="no assignments"):
        _v3_to_fair_cam_control(v3_ctrl)
