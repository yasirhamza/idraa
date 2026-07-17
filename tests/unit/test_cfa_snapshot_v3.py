"""ControlSnapshotV3 schema + discriminated-union round-trip tests (issue #131 T6.5).

Covers:
  - V3 round-trip via ControlSnapshot discriminated union (Test A)
  - Backward-compat: V1/V2/V3 dicts route to their respective readers (Test B, CR2-B2)
  - V3 assignment DTO preserves the new ``unit_type`` field on round-trip
  - V3 dict raises when forced into ControlSnapshotV2 (wrong shape)
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from idraa.models.enums import FairCamSubFunction, UnitType
from idraa.schemas.run_snapshot import (
    ControlFunctionAssignmentSnapshotDTO,
    ControlSnapshot,
    ControlSnapshotV1,
    ControlSnapshotV2,
    ControlSnapshotV3,
)

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

_V2_DICT: dict[str, Any] = {
    "snapshot_version": 2,
    "control_id": str(uuid.uuid4()),
    "name": "PR-iota CFA Control",
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

_V3_DICT: dict[str, Any] = {
    "snapshot_version": 3,
    "control_id": str(uuid.uuid4()),
    "name": "Post-#131 Control",
    "domains": ["loss_event"],
    "type": "technical",
    "assignments": [
        {
            "sub_function": FairCamSubFunction.LEC_PREV_RESISTANCE.value,
            "capability_value": 0.85,
            "coverage": 0.9,
            "reliability": 0.95,
            "unit_type": UnitType.PROBABILITY.value,
        }
    ],
}

_ta: TypeAdapter[ControlSnapshot] = TypeAdapter(ControlSnapshot)


# ---------------------------------------------------------------------------
# Test A — V3 round-trip via discriminated union
# ---------------------------------------------------------------------------


def test_snapshot_v3_routes_via_discriminator() -> None:
    """V3 dict with snapshot_version=3 deserialises to ControlSnapshotV3."""
    result = _ta.validate_python(_V3_DICT)
    assert isinstance(result, ControlSnapshotV3)
    assert result.snapshot_version == 3


def test_snapshot_v3_preserves_unit_type_on_round_trip() -> None:
    """Per-assignment unit_type is captured + preserved on V3 deserialisation."""
    result = _ta.validate_python(_V3_DICT)
    assert isinstance(result, ControlSnapshotV3)
    assert len(result.assignments) == 1
    a = result.assignments[0]
    assert a.sub_function == FairCamSubFunction.LEC_PREV_RESISTANCE
    assert a.unit_type == UnitType.PROBABILITY
    assert a.capability_value == pytest.approx(0.85)
    assert a.coverage == pytest.approx(0.9)
    assert a.reliability == pytest.approx(0.95)


def test_snapshot_v3_assignment_dto_has_no_input_validators() -> None:
    """ControlFunctionAssignmentSnapshotDTO is a post-validation audit record.

    Bounds checks (M1 unit-correct validation) belong on the input DTO
    (ControlFunctionAssignmentDTO), NOT on the snapshot DTO. Constructing
    the snapshot DTO with an out-of-band capability_value must succeed —
    audit records preserve whatever the writer captured, including values
    written before the bounds were tightened.
    """
    # A PROBABILITY-unit sub_function with capability_value > 1 would fail M1
    # validation on ControlFunctionAssignmentDTO. The snapshot DTO must accept it.
    dto = ControlFunctionAssignmentSnapshotDTO(
        sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
        capability_value=1.5,  # out-of-band for PROBABILITY unit
        coverage=0.9,
        reliability=0.95,
        unit_type=UnitType.PROBABILITY,
    )
    assert dto.capability_value == pytest.approx(1.5)


# ---------------------------------------------------------------------------
# Test B — backward-compat: V1/V2/V3 each route to their reader (CR2-B2)
# ---------------------------------------------------------------------------


def test_snapshot_v1_still_routes_to_v1_after_v3_added() -> None:
    """V1 dict continues to route to ControlSnapshotV1 (CR2-B2)."""
    result = _ta.validate_python(_V1_DICT)
    assert isinstance(result, ControlSnapshotV1)


def test_snapshot_v2_still_routes_to_v2_after_v3_added() -> None:
    """V2 dict continues to route to ControlSnapshotV2 (CR2-B2)."""
    result = _ta.validate_python(_V2_DICT)
    assert isinstance(result, ControlSnapshotV2)


def test_snapshot_mixed_v1_v2_v3_list_routes_correctly() -> None:
    """Mixed list [V1, V2, V3] each route to the correct reader (CR2-B2)."""
    ta_list: TypeAdapter[list[ControlSnapshot]] = TypeAdapter(list[ControlSnapshot])
    results = ta_list.validate_python([_V1_DICT, _V2_DICT, _V3_DICT])
    assert isinstance(results[0], ControlSnapshotV1)
    assert isinstance(results[1], ControlSnapshotV2)
    assert isinstance(results[2], ControlSnapshotV3)


def test_run_detail_dto_parses_v1_v2_v3_in_controls_snapshot() -> None:
    """RunDetailDTO.controls_snapshot accepts mixed V1/V2/V3 list (CR2-B2)."""
    import datetime

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
            "controls_snapshot": [_V1_DICT, _V2_DICT, _V3_DICT],
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
    assert isinstance(dto.controls_snapshot[2], ControlSnapshotV3)


# ---------------------------------------------------------------------------
# Defensive: V3 dict can't be coerced into V2 directly
# ---------------------------------------------------------------------------


def test_v3_dict_raises_when_parsed_directly_as_v2() -> None:
    """V3 dict (snapshot_version=3) raises if forced into ControlSnapshotV2.

    V2 has snapshot_version: Literal[2]; pydantic rejects the literal mismatch.
    """
    with pytest.raises(ValidationError):
        ControlSnapshotV2.model_validate(_V3_DICT)
