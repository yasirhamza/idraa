"""_snapshot_control_v2 captures per-assignment shape with snapshot_version=2,
preserving measured_by + derived_from_assignment_id (spec §5.3, audit §9.5).

T13 paranoid-review fixes S1+S2:
  S1 — function MUST emit a ControlSnapshotV2 Pydantic model (not a hand-rolled dict)
  S2 — measured_by and derived_from_assignment_id MUST be preserved
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest

from idraa.models.control import Control as V3Control
from idraa.models.control_function_assignment import ControlFunctionAssignment as V3Assignment
from idraa.models.enums import ControlType, FairCamSubFunction
from idraa.schemas.run_snapshot import ControlSnapshotV2
from idraa.services.run_executor import _snapshot_control_v2


def _ctrl(assignments: list[V3Assignment]) -> V3Control:
    """Build an in-memory V3Control with the given assignments (no DB required)."""
    ctrl = V3Control(
        organization_id=uuid.uuid4(),
        name="test-control",
        type=ControlType.TECHNICAL,
    )
    ctrl.assignments = assignments
    return ctrl


def _assignment(
    sub_function: FairCamSubFunction,
    capability_value: float,
    coverage: float = 0.88,
    reliability: float = 0.92,
    measured_by: uuid.UUID | None = None,
    measured_at: datetime | None = None,
    derived_from_assignment_id: uuid.UUID | None = None,
) -> V3Assignment:
    """Build an in-memory ControlFunctionAssignment (no DB required)."""
    a = V3Assignment(
        organization_id=uuid.uuid4(),
        control_id=uuid.uuid4(),
        sub_function=sub_function,
        capability_value=capability_value,
        coverage=coverage,
        reliability=reliability,
    )
    a.measured_by = measured_by
    a.measured_at = measured_at
    a.derived_from_assignment_id = derived_from_assignment_id
    return a


# ---------------------------------------------------------------------------
# S1: function returns ControlSnapshotV2 Pydantic model
# ---------------------------------------------------------------------------


def test_snapshot_returns_control_snapshot_v2_pydantic_model() -> None:
    """Paranoid-review S1: snapshot MUST emit a ControlSnapshotV2 Pydantic model."""
    a = _assignment(FairCamSubFunction.LEC_PREV_RESISTANCE, 0.85)
    c = _ctrl([a])
    snap = _snapshot_control_v2(c)
    assert isinstance(snap, ControlSnapshotV2)
    assert snap.snapshot_version == 2


def test_snapshot_no_longer_returns_dict() -> None:
    """Return type is ControlSnapshotV2, not dict (paranoid-review S1 regression guard)."""
    a = _assignment(FairCamSubFunction.LEC_PREV_RESISTANCE, 0.85)
    c = _ctrl([a])
    snap = _snapshot_control_v2(c)
    assert type(snap).__name__ == "ControlSnapshotV2", (
        "_snapshot_control_v2 must return ControlSnapshotV2, not dict. "
        "Call .model_dump(mode='json') at the call site for DB storage."
    )


# ---------------------------------------------------------------------------
# Per-assignment shape
# ---------------------------------------------------------------------------


def test_snapshot_captures_per_assignment_shape() -> None:
    a1 = _assignment(FairCamSubFunction.LEC_PREV_RESISTANCE, 0.85)
    a2 = _assignment(FairCamSubFunction.LEC_DET_VISIBILITY, 0.75)
    c = _ctrl([a1, a2])
    snap = _snapshot_control_v2(c)

    assert len(snap.assignments) == 2
    sub_funcs = {a.sub_function for a in snap.assignments}
    assert sub_funcs == {
        FairCamSubFunction.LEC_PREV_RESISTANCE,
        FairCamSubFunction.LEC_DET_VISIBILITY,
    }


def test_snapshot_captures_capability_coverage_reliability() -> None:
    a = _assignment(FairCamSubFunction.LEC_PREV_RESISTANCE, 0.85, coverage=0.88, reliability=0.92)
    c = _ctrl([a])
    snap = _snapshot_control_v2(c)

    dto = snap.assignments[0]
    assert dto.capability_value == pytest.approx(0.85)
    assert dto.coverage == pytest.approx(0.88)
    assert dto.reliability == pytest.approx(0.92)


# ---------------------------------------------------------------------------
# S2: forensic-attribution fields preserved
# ---------------------------------------------------------------------------


def test_snapshot_preserves_measured_by_field() -> None:
    """Forensic attribution (paranoid-review S1): measured_by MUST be preserved."""
    user_id = uuid.uuid4()
    a = _assignment(
        FairCamSubFunction.LEC_PREV_RESISTANCE,
        0.85,
        measured_by=user_id,
        measured_at=datetime(2026, 5, 2),
    )
    c = _ctrl([a])
    snap = _snapshot_control_v2(c)
    assert snap.assignments[0].measured_by == user_id
    assert snap.assignments[0].measured_at == datetime(2026, 5, 2)


def test_snapshot_preserves_derived_from_assignment_id_field() -> None:
    """derived_from_assignment_id reserved for computed-virtual assignments (paranoid-review S2)."""
    parent_id = uuid.uuid4()
    a = _assignment(
        FairCamSubFunction.DSC_CORR_MISALIGNED,
        0.5,
        derived_from_assignment_id=parent_id,
    )
    c = _ctrl([a])
    snap = _snapshot_control_v2(c)
    assert snap.assignments[0].derived_from_assignment_id == parent_id


# ---------------------------------------------------------------------------
# V1 flat-triple keys absent from V2 snapshot
# ---------------------------------------------------------------------------


def test_snapshot_no_longer_has_flat_triple_keys() -> None:
    """V2 shape must NOT carry V1 flat-triple keys (spec §8.4)."""
    a = _assignment(FairCamSubFunction.LEC_PREV_RESISTANCE, 0.85)
    c = _ctrl([a])
    snap = _snapshot_control_v2(c)
    snap_dict = snap.model_dump()
    assert "control_strength" not in snap_dict
    assert "control_reliability" not in snap_dict
    assert "control_coverage" not in snap_dict


# ---------------------------------------------------------------------------
# DB serialisation round-trip
# ---------------------------------------------------------------------------


def test_snapshot_serialises_to_json_dict_for_db_storage() -> None:
    """model_dump(mode='json') produces a dict storable in JSON DB column."""
    user_id = uuid.uuid4()
    a = _assignment(
        FairCamSubFunction.LEC_PREV_RESISTANCE,
        0.85,
        measured_by=user_id,
        measured_at=datetime(2026, 5, 2),
    )
    c = _ctrl([a])
    snap = _snapshot_control_v2(c)
    d = snap.model_dump(mode="json")

    assert isinstance(d, dict)
    assert d["snapshot_version"] == 2
    assert "assignments" in d
    assert d["assignments"][0]["measured_by"] == str(user_id)
