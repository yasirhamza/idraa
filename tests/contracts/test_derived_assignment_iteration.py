# tests/contracts/test_derived_assignment_iteration.py
"""N>=3 order-preservation tests for the derived-assignment adapters (issue #268).

Every adapter that maps a control's assignments list must keep ALL of them, in order,
when one of them is a derived ``dsc_corr_misaligned`` row (CLAUDE.md "Data contract
enforcement"): a ``[0]`` / ``[-1]`` / first-match shortcut would silently drop or
mis-resolve a row. N=4 with the derived row in the MIDDLE so neither end masks a bug.

Three adapters resolve the link:
  - ``run_executor._v3_to_fair_cam_control`` (ORM rows)
  - ``run_executor._snapshot_control_v3`` (ORM rows -> V3 snapshot, writes the source slug)
  - ``verification_workbook.snapshot_to_fair_cam_controls`` (V3 snapshot -> fair_cam)
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from idraa.models.control import Control
from idraa.models.control_function_assignment import ControlFunctionAssignment
from idraa.models.enums import ControlType, EntityStatus, FairCamSubFunction
from idraa.services.run_executor import _snapshot_control_v3, _v3_to_fair_cam_control
from idraa.services.verification_workbook import snapshot_to_fair_cam_controls

_SF = FairCamSubFunction
_SRC_ID = uuid.UUID(int=22)
_DERIVED_ID = uuid.UUID(int=21)

# (sub_function, capability, coverage, reliability) in list order; the derived row sits
# at index 1 and is linked to the vmc_corr_implementation row at index 2.
_EXPECTED_ORDER = [
    _SF.LEC_PREV_RESISTANCE,
    _SF.DSC_CORR_MISALIGNED,
    _SF.VMC_CORR_IMPLEMENTATION,
    _SF.DSC_ID_MISALIGNED,
]


def _control_n4() -> Control:
    org_id = uuid.uuid4()
    ctrl_id = uuid.uuid4()
    ctrl = Control(
        id=ctrl_id,
        organization_id=org_id,
        name="N4 derived",
        type=ControlType.TECHNICAL,
        annual_cost=Decimal("0"),
        status=EntityStatus.ACTIVE,
        version="1.0",
    )

    def row(rid: uuid.UUID, sf: _SF, cap: float | None, cov: float, rel: float, link=None):
        return ControlFunctionAssignment(
            id=rid,
            organization_id=org_id,
            control_id=ctrl_id,
            sub_function=sf,
            capability_value=cap,
            coverage=cov,
            reliability=rel,
            derived_from_assignment_id=link,
        )

    ctrl.assignments = [
        row(uuid.UUID(int=20), _SF.LEC_PREV_RESISTANCE, 0.85, 0.8, 0.9),
        row(_DERIVED_ID, _SF.DSC_CORR_MISALIGNED, None, 0.0, 0.0, link=_SRC_ID),
        row(_SRC_ID, _SF.VMC_CORR_IMPLEMENTATION, 30.0, 0.7, 0.95),
        row(uuid.UUID(int=23), _SF.DSC_ID_MISALIGNED, 0.6, 0.8, 0.85),
    ]
    return ctrl


def test_run_mapper_preserves_all_four_assignments_in_order() -> None:
    fc = _v3_to_fair_cam_control(_control_n4())
    assert [a.sub_function.value for a in fc.assignments] == [sf.value for sf in _EXPECTED_ORDER]
    # The derived row (index 1) is the source's (index 2) numbers; the others pass through.
    assert (fc.assignments[1].coverage, fc.assignments[1].reliability) == (0.7, 0.95)
    assert fc.assignments[0].capability_value == 0.85
    assert fc.assignments[3].capability_value == 0.6


def test_snapshot_writer_preserves_all_four_assignments_in_order() -> None:
    snap = _snapshot_control_v3(_control_n4())
    assert [a.sub_function for a in snap.assignments] == _EXPECTED_ORDER
    assert [a.derived_from_sub_function for a in snap.assignments] == [
        None,
        _SF.VMC_CORR_IMPLEMENTATION,
        None,
        None,
    ]


def test_workbook_adapter_preserves_all_four_assignments_in_order() -> None:
    snap = _snapshot_control_v3(_control_n4()).model_dump(mode="json")
    (fc,) = snapshot_to_fair_cam_controls([snap])
    assert [a.sub_function.value for a in fc.assignments] == [sf.value for sf in _EXPECTED_ORDER]
    assert (fc.assignments[1].coverage, fc.assignments[1].reliability) == (0.7, 0.95)


def test_run_mapper_and_workbook_adapter_agree_on_every_assignment() -> None:
    """Snapshot round-trip fidelity: the workbook's reconstruction of the writer's
    snapshot equals the run mapper's output, field by field, for all four rows."""
    ctrl = _control_n4()
    via_mapper = _v3_to_fair_cam_control(ctrl).assignments
    (via_workbook,) = snapshot_to_fair_cam_controls(
        [_snapshot_control_v3(ctrl).model_dump(mode="json")]
    )
    assert len(via_mapper) == len(via_workbook.assignments) == 4
    for m, w in zip(via_mapper, via_workbook.assignments, strict=True):
        assert (m.sub_function, m.capability_value, m.coverage, m.reliability) == (
            w.sub_function,
            w.capability_value,
            w.coverage,
            w.reliability,
        )
