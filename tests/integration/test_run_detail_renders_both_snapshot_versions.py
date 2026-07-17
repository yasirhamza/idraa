"""Render tests for runs/detail.html v1/v2 discriminated snapshot rendering.

Spec §B-NEW2, B4.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest


def _v2_snapshot_dict(name: str = "PR-iota Firewall") -> dict[str, Any]:
    return {
        "snapshot_version": 2,
        "name": name,
        "control_id": str(uuid.uuid4()),
        "domains": ["loss_event"],
        "type": "technical",
        "assignments": [
            {
                "sub_function": "lec_prev_resistance",
                "capability_value": 0.85,
                "coverage": 0.9,
                "reliability": 0.8,
                "confirmed_by_user_at": datetime.now(UTC).isoformat(),
                "derived_from_assignment_id": None,
                "measured_at": None,
                "measured_by": None,
            }
        ],
    }


def _v2_snapshot_null_capability() -> dict[str, Any]:
    return {
        "snapshot_version": 2,
        "name": "Monitoring Control (null cap)",
        "control_id": str(uuid.uuid4()),
        "domains": ["loss_event"],
        "type": "technical",
        "assignments": [
            {
                "sub_function": "lec_det_monitoring",
                "capability_value": None,
                "coverage": 0.8,
                "reliability": 0.75,
                "confirmed_by_user_at": None,
                "derived_from_assignment_id": None,
                "measured_at": None,
                "measured_by": None,
            }
        ],
    }


def test_v2_snapshot_shape_parses_via_pydantic() -> None:
    """V2 snapshot dict passes through ControlSnapshotV2 model correctly."""
    from idraa.schemas.run_snapshot import ControlSnapshotV2

    snap = _v2_snapshot_dict()
    parsed = ControlSnapshotV2.model_validate(snap)
    assert parsed.snapshot_version == 2
    assert len(parsed.assignments) == 1
    assert parsed.assignments[0].capability_value == pytest.approx(0.85)


def test_v2_snapshot_with_null_capability_parses() -> None:
    """NULL capability_value in v2 snapshot is handled by ControlSnapshotV2 parse."""
    from idraa.schemas.run_snapshot import ControlSnapshotV2

    snap = _v2_snapshot_null_capability()
    parsed = ControlSnapshotV2.model_validate(snap)
    assert parsed.snapshot_version == 2
    assert parsed.assignments[0].capability_value is None
    assert parsed.assignments[0].sub_function.value == "lec_det_monitoring"


def _v2_snapshot_empty_assignments() -> dict[str, Any]:
    """V2 snapshot factory with empty assignments list (degenerate case)."""
    return {
        "snapshot_version": 2,
        "name": "Stripped Control",
        "control_id": str(uuid.uuid4()),
        "domains": ["loss_event"],
        "type": "technical",
        "assignments": [],
    }


def test_v2_snapshot_with_empty_assignments_parses() -> None:
    """V2 snapshot with assignments=[] parses without error.

    Currently un-tested empty-state path; surfaced during paranoid review.
    Empty list is a valid Pydantic shape — no min_length on the
    ControlSnapshotV2.assignments field. (F21 hygiene)
    """
    from idraa.schemas.run_snapshot import ControlSnapshotV2

    snap = _v2_snapshot_empty_assignments()
    parsed = ControlSnapshotV2.model_validate(snap)
    assert parsed.snapshot_version == 2
    assert parsed.assignments == []
