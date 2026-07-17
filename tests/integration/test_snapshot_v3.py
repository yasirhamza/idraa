"""_snapshot_control_v3 writer captures per-assignment unit_type at write time.

Issue #131 T6.5. New writes from this PR onward emit ``ControlSnapshotV3`` which
carries ``unit_type`` for each assignment. The persisted ``unit_type`` is what
makes a V3 snapshot reproducible across future SUB_FUNCTION_UNITS mutations.

Mirrors the test topology of tests/integration/test_snapshot_v2.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest

from idraa.models.control import Control as V3Control
from idraa.models.control_function_assignment import ControlFunctionAssignment as V3Assignment
from idraa.models.enums import (
    SUB_FUNCTION_UNITS,
    ControlType,
    FairCamSubFunction,
    UnitType,
)
from idraa.routes.runs import _RECLASSIFIED_SUB_FUNCTIONS_131
from idraa.schemas.run_snapshot import ControlSnapshotV3
from idraa.services.run_executor import _snapshot_control_v3


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
    capability_value: float | None,
    coverage: float = 0.88,
    reliability: float = 0.92,
    confirmed_by_user_at: datetime | None = None,
    measured_at: datetime | None = None,
    measured_by: uuid.UUID | None = None,
    derived_from_assignment_id: uuid.UUID | None = None,
) -> V3Assignment:
    """Build an in-memory ControlFunctionAssignment (no DB required).

    Forensic-attribution kwargs default to None so the bulk of existing
    tests stay unaffected; M-B1 forensic-preservation tests opt in.
    """
    a = V3Assignment(
        organization_id=uuid.uuid4(),
        control_id=uuid.uuid4(),
        sub_function=sub_function,
        capability_value=capability_value,
        coverage=coverage,
        reliability=reliability,
    )
    a.confirmed_by_user_at = confirmed_by_user_at
    a.measured_at = measured_at
    a.measured_by = measured_by
    a.derived_from_assignment_id = derived_from_assignment_id
    return a


# ---------------------------------------------------------------------------
# Writer returns ControlSnapshotV3 (analog of V2 paranoid-review fix S1)
# ---------------------------------------------------------------------------


def test_snapshot_v3_writer_returns_control_snapshot_v3_pydantic_model() -> None:
    """``_snapshot_control_v3`` returns a ControlSnapshotV3 Pydantic model."""
    a = _assignment(FairCamSubFunction.LEC_PREV_RESISTANCE, 0.85)
    c = _ctrl([a])
    snap = _snapshot_control_v3(c)
    assert isinstance(snap, ControlSnapshotV3)
    assert snap.snapshot_version == 3


# ---------------------------------------------------------------------------
# Test C — unit_type captured at write time matches SUB_FUNCTION_UNITS
# ---------------------------------------------------------------------------


def test_snapshot_v3_unit_type_matches_sub_function_units_for_probability() -> None:
    """For a PROBABILITY-unit sub_function, the captured unit_type is PROBABILITY."""
    a = _assignment(FairCamSubFunction.LEC_PREV_RESISTANCE, 0.85)
    c = _ctrl([a])
    snap = _snapshot_control_v3(c)
    assert snap.assignments[0].unit_type == UnitType.PROBABILITY
    assert (
        snap.assignments[0].unit_type == SUB_FUNCTION_UNITS[FairCamSubFunction.LEC_PREV_RESISTANCE]
    )


def test_snapshot_v3_unit_type_matches_sub_function_units_for_elapsed_time() -> None:
    """For an ELAPSED_TIME-unit sub_function, the captured unit_type is ELAPSED_TIME."""
    a = _assignment(FairCamSubFunction.LEC_DET_MONITORING, 14.0)
    c = _ctrl([a])
    snap = _snapshot_control_v3(c)
    assert snap.assignments[0].unit_type == UnitType.ELAPSED_TIME
    assert (
        snap.assignments[0].unit_type == SUB_FUNCTION_UNITS[FairCamSubFunction.LEC_DET_MONITORING]
    )


def test_snapshot_v3_unit_type_covers_reclassified_sub_function() -> None:
    """Issue #131: LEC_RESP_RESILIENCE is post-#131 reclassified ELAPSED_TIME → PROBABILITY.

    A V3 snapshot written today captures PROBABILITY. The capability_value
    stays in [0, 1] (per the post-#131 contract). Any future re-classification
    would not mutate this snapshot's unit_type.
    """
    a = _assignment(FairCamSubFunction.LEC_RESP_RESILIENCE, 0.6)
    c = _ctrl([a])
    snap = _snapshot_control_v3(c)
    assert snap.assignments[0].unit_type == UnitType.PROBABILITY


def test_snapshot_v3_unit_type_for_each_assignment_in_multi_assignment_control() -> None:
    """A multi-assignment control captures unit_type PER assignment.

    Mixes a PROBABILITY sub-function, an ELAPSED_TIME sub-function, and a
    CURRENCY sub-function to exercise three unit_type branches in one
    snapshot. Asserts each assignment's unit_type matches SUB_FUNCTION_UNITS
    at its own sub_function key.
    """
    assignments = [
        _assignment(FairCamSubFunction.LEC_PREV_RESISTANCE, 0.85),  # PROBABILITY
        _assignment(FairCamSubFunction.LEC_DET_MONITORING, 14.0),  # ELAPSED_TIME
        _assignment(FairCamSubFunction.LEC_RESP_LOSS_REDUCTION, 50_000.0),  # CURRENCY
    ]
    c = _ctrl(assignments)
    snap = _snapshot_control_v3(c)

    assert len(snap.assignments) == 3
    for a in snap.assignments:
        assert a.unit_type == SUB_FUNCTION_UNITS[a.sub_function]


# ---------------------------------------------------------------------------
# DB-serialisation round-trip
# ---------------------------------------------------------------------------


def test_snapshot_v3_serialises_to_json_dict_for_db_storage() -> None:
    """model_dump(mode='json') emits a JSON-storable dict with unit_type."""
    a = _assignment(FairCamSubFunction.LEC_PREV_RESISTANCE, 0.85)
    c = _ctrl([a])
    snap = _snapshot_control_v3(c)
    d = snap.model_dump(mode="json")

    assert isinstance(d, dict)
    assert d["snapshot_version"] == 3
    assert "assignments" in d
    assert d["assignments"][0]["unit_type"] == "probability"


def test_snapshot_v3_preserves_null_capability_value() -> None:
    """capability_value=None (TIME/CURRENCY sentinel) is preserved into V3."""
    a = _assignment(FairCamSubFunction.LEC_DET_MONITORING, None)
    c = _ctrl([a])
    snap = _snapshot_control_v3(c)
    assert snap.assignments[0].capability_value is None
    assert snap.assignments[0].unit_type == UnitType.ELAPSED_TIME


# ---------------------------------------------------------------------------
# Iteration-preserving contract (PR ρ adapter list-count test)
# ---------------------------------------------------------------------------


def test_snapshot_v3_writer_preserves_assignment_count() -> None:
    """N-in / N-out for the assignments list (PR ρ adapter contract).

    Catches future [0] / [-1] / [first] optimisations that would silently
    drop assignments at snapshot time.
    """
    assignments = [
        _assignment(FairCamSubFunction.LEC_PREV_RESISTANCE, 0.85),
        _assignment(FairCamSubFunction.LEC_DET_VISIBILITY, 0.75),
        _assignment(FairCamSubFunction.LEC_DET_MONITORING, 14.0),
    ]
    c = _ctrl(assignments)
    snap = _snapshot_control_v3(c)
    assert len(snap.assignments) == 3
    # Sub-function set preserved.
    sub_funcs = {a.sub_function for a in snap.assignments}
    assert sub_funcs == {
        FairCamSubFunction.LEC_PREV_RESISTANCE,
        FairCamSubFunction.LEC_DET_VISIBILITY,
        FairCamSubFunction.LEC_DET_MONITORING,
    }


# ---------------------------------------------------------------------------
# M-B1: forensic-attribution preservation (mirrors V2's S1+S2 tests at
# tests/integration/test_snapshot_v2.py:118-143). Spec §5.3, audit §9.5.
# ---------------------------------------------------------------------------


def test_snapshot_v3_preserves_measured_by_field() -> None:
    """M-B1: ``measured_by`` MUST round-trip through the V3 snapshot DTO."""
    user_id = uuid.uuid4()
    a = _assignment(
        FairCamSubFunction.LEC_PREV_RESISTANCE,
        0.85,
        measured_by=user_id,
        measured_at=datetime(2026, 5, 2),
    )
    c = _ctrl([a])
    snap = _snapshot_control_v3(c)
    assert snap.assignments[0].measured_by == user_id


def test_snapshot_v3_preserves_measured_at_field() -> None:
    """M-B1: ``measured_at`` MUST round-trip through the V3 snapshot DTO."""
    measured_at = datetime(2026, 5, 2)
    a = _assignment(
        FairCamSubFunction.LEC_PREV_RESISTANCE,
        0.85,
        measured_at=measured_at,
    )
    c = _ctrl([a])
    snap = _snapshot_control_v3(c)
    assert snap.assignments[0].measured_at == measured_at


def test_snapshot_v3_preserves_confirmed_by_user_at_field() -> None:
    """M-B1: ``confirmed_by_user_at`` MUST round-trip through the V3 snapshot DTO."""
    confirmed_at = datetime(2026, 5, 3)
    a = _assignment(
        FairCamSubFunction.LEC_PREV_RESISTANCE,
        0.85,
        confirmed_by_user_at=confirmed_at,
    )
    c = _ctrl([a])
    snap = _snapshot_control_v3(c)
    assert snap.assignments[0].confirmed_by_user_at == confirmed_at


def test_snapshot_v3_preserves_derived_from_assignment_id_field() -> None:
    """M-B1: ``derived_from_assignment_id`` MUST round-trip through the V3
    snapshot DTO (reserved for computed-virtual DSC_CORR_MISALIGNED rows)."""
    parent_id = uuid.uuid4()
    a = _assignment(
        FairCamSubFunction.DSC_CORR_MISALIGNED,
        0.5,
        derived_from_assignment_id=parent_id,
    )
    c = _ctrl([a])
    snap = _snapshot_control_v3(c)
    assert snap.assignments[0].derived_from_assignment_id == parent_id


@pytest.mark.parametrize(
    "sub_function",
    sorted(_RECLASSIFIED_SUB_FUNCTIONS_131, key=lambda s: s.value),
    ids=lambda s: s.value,
)
def test_snapshot_v3_unit_type_for_post_131_reclassified_sub_functions(
    sub_function: FairCamSubFunction,
) -> None:
    """Each of the 6 post-#131 reclassified sub-functions captures PROBABILITY.

    M-I2: consumes ``_RECLASSIFIED_SUB_FUNCTIONS_131`` as the single source
    of truth so this test cannot drift from the log filter (M-I1) or the
    banner condition (M-N2). Previously parametrized 4 of 6 — missed
    ``DSC_ID_MISALIGNED`` and ``DSC_CORR_MISALIGNED``.

    These are the sub-functions where the V2-vs-V3 interpretation actually
    differs — V2 snapshots written before #131 captured an ELAPSED_TIME
    capability_value, and reading them today re-interprets the same number
    as a PROBABILITY. V3 snapshots written today freeze the interpretation
    explicitly.

    ``DSC_CORR_MISALIGNED`` is virtual per Standard §5.3 (page 50) — the
    schema-input validator at ``schemas/control.py`` rejects it without a
    ``derived_from_assignment_id``. The snapshot writer reads ORM rows
    directly (no input-DTO validation), and the V3 snapshot DTO itself is
    a post-validation audit record without validators, so direct writer
    invocation succeeds. We still populate ``derived_from_assignment_id``
    on the fixture to mirror what a real persisted row carries (the
    forensic-attribution preservation is asserted separately under M-B1).
    """
    derived_from = uuid.uuid4() if sub_function == FairCamSubFunction.DSC_CORR_MISALIGNED else None
    a = _assignment(sub_function, 0.5, derived_from_assignment_id=derived_from)
    c = _ctrl([a])
    snap = _snapshot_control_v3(c)
    assert snap.assignments[0].unit_type == UnitType.PROBABILITY
