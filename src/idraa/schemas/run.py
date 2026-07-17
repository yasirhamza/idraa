"""Pydantic DTOs for run trigger / status / detail / history.

M-NEW2 (spec §7.3): the legacy ControlSnapshotV1DTO class was removed in PR iota's
hygiene PR; live snapshot classes are ControlSnapshotV1 / ControlSnapshotV2 in
schemas/run_snapshot.py. RunDetailDTO.controls_snapshot uses the ControlSnapshot
discriminated union, which handles both V1 (legacy flat-triple) and V2
(per-assignment) snapshot dicts.
"""

from __future__ import annotations

import datetime
import uuid

from pydantic import BaseModel, Field

from idraa.schemas.run_snapshot import ControlSnapshot


class RunTriggerForm(BaseModel):
    """Form payload for POST /scenarios/{id}/run."""

    mc_iterations: int = Field(ge=100, le=1_000_000)
    control_ids: list[uuid.UUID] = Field(default_factory=list)


class RunStatusDTO(BaseModel):
    """Compact representation for HTMX status fragment."""

    id: uuid.UUID
    status: str
    started_at: datetime.datetime | None
    completed_at: datetime.datetime | None
    mc_iterations: int
    error_message: str | None


class RunDetailDTO(BaseModel):
    """Full run detail. Used by GET /runs/{id} when status=COMPLETED.

    controls_snapshot is a discriminated union that handles both V1 (legacy
    flat-triple, snapshot_version=1 or missing key) and V2 (per-assignment,
    snapshot_version=2) snapshot dicts. (M-NEW2, spec §7.3)
    """

    id: uuid.UUID
    scenario_id: uuid.UUID
    status: str
    run_type: str
    mc_iterations: int
    inputs_hash: str
    controls_snapshot: list[ControlSnapshot]
    simulation_results: dict[str, object] | None
    error_message: str | None
    started_at: datetime.datetime | None
    completed_at: datetime.datetime | None
    created_at: datetime.datetime
    created_by: uuid.UUID | None
