"""Versioned control snapshot DTOs for risk_analysis_runs.snapshot JSON.

Three shapes exist in the wild:
  - V1: written before PR iota — flat triple (control_strength, control_reliability,
        control_coverage) at the Control level. Snapshot dicts may lack a
        'snapshot_version' key (written before the discriminator was introduced).
  - V2: written from PR iota until issue #131 T6.5 — per-assignment list with
        sub_function slugs.
  - V3: written from issue #131 T6.5 onward — extends V2 by capturing per-assignment
        ``unit_type`` at snapshot write time. Locks the assignment's unit
        interpretation against future SUB_FUNCTION_UNITS mutations so re-runs
        of historical V3 snapshots remain reproducible.

Reading code MUST detect the version and route to the correct decoder.
The ControlSnapshot discriminated union handles this automatically.

Historical runs retain their V1 / V2 snapshots as immutable audit records.
V1 snapshots are NEVER rewritten to V2 or V3. V2 snapshots are NEVER rewritten
to V3 — V2 reads under post-#131 SUB_FUNCTION_UNITS surface a banner + a
server-side ``snapshot_v2_read`` structured log entry instead. (spec §7.3)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Discriminator, Tag

from idraa.models.enums import FairCamSubFunction, UnitType
from idraa.schemas.control import ControlFunctionAssignmentDTO


class ControlSnapshotV1(BaseModel):
    """Historical snapshot shape written by _snapshot_control() before PR iota.

    Preserved for reading historical run records. Never written by new code
    after PR iota ships. The flat triple reflects Phase 1.2 schema.

    M3 missing-key handling: the Discriminator callable below defaults to 1
    when the dict has no 'snapshot_version' key, routing keyless legacy dicts
    to this model. The Literal[1] field has a default of 1, so Pydantic
    accepts the dict whether the key is present or absent. No model_validator
    pre-processor is required. (spec §7.3, M3)
    """

    snapshot_version: Literal[1] = 1
    control_id: str
    name: str
    control_strength: float
    control_reliability: float
    control_coverage: float
    domain: str
    function: str
    type: str


class ControlSnapshotV2(BaseModel):
    """Snapshot shape written by _snapshot_control_v2() from PR iota onward.

    Captures the full per-assignment effectiveness list per spec §5.4.
    Historical runs with snapshot_version=1 are immutable audit records and
    must be decoded via ControlSnapshotV1, not this model. (spec §7.3)

    Issue #90: ``domain: str`` renamed to ``domains: list[str]`` (sorted
    ascending). FAIR-CAM §2.2 places domain at the sub-function level, so a
    control's domain is the SET spanned by its assignments — a multi-domain
    control was previously truncated to a single (first-recognized) value.
    In-place schema evolution: pre-issue-90 snapshots retain their legacy
    ``domain: str`` shape (immutable audit records, never rewritten).
    Templates branch on the presence of ``domains`` vs ``domain``.
    """

    snapshot_version: Literal[2] = 2
    control_id: str
    name: str
    domains: list[str]
    type: str
    assignments: list[ControlFunctionAssignmentDTO]


class ControlFunctionAssignmentSnapshotDTO(BaseModel):
    """Per-assignment snapshot record (issue #131 / T6.5).

    Captures the assignment's effectiveness inputs PLUS the ``unit_type``
    in effect at snapshot write time. This is what makes V3 snapshots
    reproducible across future SUB_FUNCTION_UNITS table mutations: re-runs
    interpret ``capability_value`` under the ``unit_type`` that was active
    when the snapshot was written, not under the current SUB_FUNCTION_UNITS
    mapping.

    Distinct from ``ControlFunctionAssignmentDTO`` (a form input with M1
    unit-correct bound validators) — this DTO is a post-validation audit
    record. Bound checks belong on input DTOs, not on the audit trail.
    (issue #131 T6.5; Arch-B3 / Sec-B3 plan-gate)

    Forensic-attribution fields (spec §5.3, audit §9.5; PR kappa T13
    paranoid-review S1+S2): ``confirmed_by_user_at``, ``measured_at``,
    ``measured_by``, ``derived_from_assignment_id`` MUST be preserved on
    the audit record so the snapshot can be re-derived under historical
    assumptions for BOTH the unit-type interpretation (the V3 contract)
    AND the operator/derivation provenance (the V2 contract carried
    forward). Adding fields to V3 closes M-B1 from the methodology
    reviewer's 4-reviewer plan-gate pass.
    """

    sub_function: FairCamSubFunction
    capability_value: float | None
    coverage: float
    reliability: float
    unit_type: UnitType
    # Forensic-attribution invariants — preserved across V2 → V3 (M-B1).
    confirmed_by_user_at: datetime | None = None
    measured_at: datetime | None = None
    measured_by: uuid.UUID | None = None
    derived_from_assignment_id: uuid.UUID | None = None


class ControlSnapshotV3(BaseModel):
    """Snapshot shape written from issue #131 T6.5 onward.

    Adds per-assignment ``unit_type`` to ControlSnapshotV2's shape. Existing
    V2 fields preserved 1:1; only the assignment DTO is upgraded. The
    discriminator returns 3 for ``snapshot_version=3``.

    V2 → V3 migration is read-only: V2 snapshots are immutable audit records
    and are NEVER rewritten to V3. The runtime banner + structured
    ``snapshot_v2_read`` log entry document the post-#131 re-interpretation
    of V2 reads. (issue #131 T6.5; Arch-B3 / Sec-B3 plan-gate)
    """

    snapshot_version: Literal[3] = 3
    control_id: str
    name: str
    domains: list[str]
    type: str
    assignments: list[ControlFunctionAssignmentSnapshotDTO]


def _snapshot_version_discriminator(data: Any) -> int:
    """Discriminator callable for the ControlSnapshot union.

    Returns the snapshot_version integer. Defaults to 1 for legacy snapshots
    missing the key — this is the M3 missing-key handling path. The Tag
    values on the union members must match these returns (1 -> V1, 2 -> V2,
    3 -> V3). (spec §7.3, issue #131 T6.5)
    """
    if isinstance(data, dict):
        return int(data.get("snapshot_version", 1))
    return int(getattr(data, "snapshot_version", 1))


# Discriminated union for version-aware deserialization (Pydantic v2
# callable-discriminator form). The Discriminator callable runs first and
# returns the integer Tag value; Pydantic then dispatches to the member
# whose Tag matches.  Tag(1) -> ControlSnapshotV1, Tag(2) -> ControlSnapshotV2,
# Tag(3) -> ControlSnapshotV3 (issue #131 T6.5).
# Keyless legacy dicts route to V1 because the callable defaults to 1 when
# ``snapshot_version`` is absent (M3).
# Usage: parse a raw dict with TypeAdapter(ControlSnapshot).validate_python(d)
# or embed in RunDetailDTO.controls_snapshot: list[ControlSnapshot].
ControlSnapshot = Annotated[
    Annotated[ControlSnapshotV1, Tag(1)]
    | Annotated[ControlSnapshotV2, Tag(2)]
    | Annotated[ControlSnapshotV3, Tag(3)],
    Discriminator(_snapshot_version_discriminator),
]
