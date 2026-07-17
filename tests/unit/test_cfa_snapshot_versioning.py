"""Versioned snapshot contract tests (spec §7.3, M3, M-NEW2).

Covers:
  - V1 round-trip via ControlSnapshot discriminated union
  - V2 round-trip via ControlSnapshot discriminated union
  - Keyless legacy dict → routed to V1 by the Discriminator callable's
    missing-key default (M3); the V1 model accepts the dict because
    snapshot_version has a default of 1.
  - Mixed V1/V2/keyless list deserialization
  - RunDetailDTO parses controls_snapshot with both V1 and V2 dicts (M-NEW2)
  - V2 dict raises when parsed directly as V1 (wrong shape)

Test count: 6 tests.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from idraa.models.enums import FairCamSubFunction
from idraa.schemas.run_snapshot import (
    ControlSnapshot,
    ControlSnapshotV1,
    ControlSnapshotV2,
)

# Fixture helpers


_V1_DICT: dict[str, Any] = {
    "snapshot_version": 1,
    "control_id": str(uuid.uuid4()),
    "name": "Legacy Firewall",
    "control_strength": 0.7,
    "control_reliability": 0.9,
    "control_coverage": 0.8,
    "domain": "loss_event",
    "function": "PREVENTIVE",
    "type": "technical",
}

_V1_KEYLESS_DICT: dict[str, Any] = {
    # No 'snapshot_version' key — simulates pre-PR-iota snapshot (M3)
    "control_id": str(uuid.uuid4()),
    "name": "Old Control No Version",
    "control_strength": 0.6,
    "control_reliability": 0.85,
    "control_coverage": 0.75,
    "domain": "loss_event",
    "function": "DETECTIVE",
    "type": "technical",
}

_V2_DICT: dict[str, Any] = {
    "snapshot_version": 2,
    "control_id": str(uuid.uuid4()),
    "name": "New CFA Control",
    "domains": ["loss_event"],
    "type": "technical",
    "assignments": [
        {
            "sub_function": FairCamSubFunction.LEC_PREV_RESISTANCE.value,
            "capability_value": 0.85,
            "coverage": 0.9,
            "reliability": 0.95,
        }
    ],
}

_ta: TypeAdapter[ControlSnapshot] = TypeAdapter(ControlSnapshot)


# Tests


def test_snapshot_v1_roundtrip() -> None:
    """V1 dict with snapshot_version=1 deserializes to ControlSnapshotV1."""
    result = _ta.validate_python(_V1_DICT)
    assert isinstance(result, ControlSnapshotV1)
    assert result.snapshot_version == 1
    assert result.control_strength == pytest.approx(0.7)


def test_snapshot_v2_roundtrip() -> None:
    """V2 dict with snapshot_version=2 deserializes to ControlSnapshotV2."""
    result = _ta.validate_python(_V2_DICT)
    assert isinstance(result, ControlSnapshotV2)
    assert result.snapshot_version == 2
    assert len(result.assignments) == 1
    assert result.assignments[0].sub_function == FairCamSubFunction.LEC_PREV_RESISTANCE


def test_snapshot_legacy_no_version_key_routes_to_v1() -> None:
    """Keyless dict (no snapshot_version key) → Discriminator callable
    defaults to 1 (M3 missing-key routing) → ControlSnapshotV1.
    """
    result = _ta.validate_python(_V1_KEYLESS_DICT)
    assert isinstance(result, ControlSnapshotV1)
    assert result.snapshot_version == 1
    assert result.name == "Old Control No Version"


def test_snapshot_version_discriminator_mixed_list() -> None:
    """Mixed list [V1, V2, keyless] all deserialize to correct types."""
    ta_list: TypeAdapter[list[ControlSnapshot]] = TypeAdapter(list[ControlSnapshot])
    results = ta_list.validate_python([_V1_DICT, _V2_DICT, _V1_KEYLESS_DICT])
    assert isinstance(results[0], ControlSnapshotV1)
    assert isinstance(results[1], ControlSnapshotV2)
    assert isinstance(results[2], ControlSnapshotV1)  # M3 keyless → V1


def test_run_detail_dto_parses_v1_and_v2_snapshot_in_controls_list() -> None:
    """RunDetailDTO.controls_snapshot accepts mixed V1/V2 list (M-NEW2, spec §7.3)."""
    from idraa.schemas.run import RunDetailDTO

    now = datetime.datetime.now(datetime.UTC)
    dto = RunDetailDTO.model_validate(
        {
            "id": uuid.uuid4(),
            "scenario_id": uuid.uuid4(),
            "status": "COMPLETED",
            "run_type": "monte_carlo",
            "mc_iterations": 1000,
            "inputs_hash": "abc123",
            "controls_snapshot": [_V1_DICT, _V2_DICT, _V1_KEYLESS_DICT],
            "simulation_results": None,
            "error_message": None,
            "started_at": now,
            "completed_at": now,
            "created_at": now,
            "created_by": None,
        }
    )
    assert isinstance(dto.controls_snapshot[0], ControlSnapshotV1)
    assert isinstance(dto.controls_snapshot[1], ControlSnapshotV2)
    assert isinstance(dto.controls_snapshot[2], ControlSnapshotV1)  # M3 keyless


def test_v2_dict_raises_when_parsed_directly_as_v1() -> None:
    """V2 dict (has assignments, no flat triple) raises if forced into ControlSnapshotV1."""
    with pytest.raises(ValidationError):
        ControlSnapshotV1.model_validate(_V2_DICT)
