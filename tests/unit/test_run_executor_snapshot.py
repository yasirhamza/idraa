"""Issue #90 Task 1: _snapshot_control_v2 emits `domains: list[str]` (sorted).

Pre-issue-90 the snapshot writer set `domain=c.domain.value` (single
string). After the column drop in Task 2 that read crashes with
AttributeError on every new run. Task 1 changes the write path to emit
`domains: list[str]` (sorted alphabetically) derived from assignments via
subfunction_to_domain, ahead of the column drop, so intermediate commits
remain runnable.

The ControlSnapshotV2 Pydantic schema is updated in lockstep.
"""

from __future__ import annotations

import uuid

from idraa.models.control import Control
from idraa.models.control_function_assignment import ControlFunctionAssignment
from idraa.models.enums import ControlType, FairCamSubFunction
from idraa.schemas.run_snapshot import ControlSnapshotV2
from idraa.services.run_executor import _snapshot_control_v2


def _make_multi_domain_control() -> Control:
    """Build an in-memory Control with one LEC + one DSC assignment.

    Issue #90 Task 2 dropped Control.domain — the control's domains are now
    derived from its assignments via the Control.domains property.
    """
    org_id = uuid.uuid4()
    ctrl = Control(
        organization_id=org_id,
        name="multi-domain-snapshot",
        type=ControlType.TECHNICAL,
    )
    ctrl.assignments = [
        ControlFunctionAssignment(
            organization_id=org_id,
            control_id=uuid.uuid4(),
            sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
            capability_value=0.85,
            coverage=0.9,
            reliability=0.95,
        ),
        ControlFunctionAssignment(
            organization_id=org_id,
            control_id=uuid.uuid4(),
            sub_function=FairCamSubFunction.DSC_PREV_DEFINED_EXPECTATIONS,
            capability_value=0.7,
            coverage=0.8,
            reliability=0.9,
        ),
    ]
    return ctrl


def test_snapshot_v2_emits_domains_list_sorted() -> None:
    """Snapshot writer emits `domains: list[str]` sorted alphabetically."""
    snap = _snapshot_control_v2(_make_multi_domain_control())
    assert isinstance(snap, ControlSnapshotV2)
    # alphabetic sort of {"decision_support", "loss_event"}
    assert snap.domains == ["decision_support", "loss_event"], (
        f"snapshot must emit `domains: list[str]` sorted — got {snap.domains!r}"
    )


def test_snapshot_v2_no_longer_emits_singular_domain_field() -> None:
    """Regression guard: the singular `domain` field is gone from ControlSnapshotV2.

    Post-issue-90 the schema field is `domains: list[str]`. Reading
    `domain` (the legacy attribute name) on a v2 snapshot should raise
    AttributeError, not return a quietly-stale string.
    """
    snap = _snapshot_control_v2(_make_multi_domain_control())
    # The legacy field name must not exist on the v2 model.
    assert not hasattr(snap, "domain"), (
        "ControlSnapshotV2 must no longer carry the singular `domain` field"
    )


def test_snapshot_v2_single_domain_emits_single_element_list() -> None:
    """A single-assignment control emits a one-element domains list."""
    org_id = uuid.uuid4()
    ctrl = Control(
        organization_id=org_id,
        name="single-domain",
        type=ControlType.TECHNICAL,
    )
    ctrl.assignments = [
        ControlFunctionAssignment(
            organization_id=org_id,
            control_id=uuid.uuid4(),
            sub_function=FairCamSubFunction.VMC_PREV_REDUCE_VARIANCE_PROB,
            capability_value=0.7,
            coverage=0.8,
            reliability=0.85,
        ),
    ]
    snap = _snapshot_control_v2(ctrl)
    assert snap.domains == ["variance_management"]
