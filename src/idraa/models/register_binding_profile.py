"""RegisterBindingProfile — saved column-map + value-binding profile for the
register-import flow (epic #34 P1c Task 2).

An admin who repeatedly imports registers of the same shape (e.g. a monthly
export from an ERM tool) can save the column map + value bindings chosen
during a staged import as a named, per-org profile, then re-apply it on a
future upload instead of re-mapping every column and re-binding every
distinct value by hand. Mirrors the canonical/override layering doctrine's
"per-org CRUD-able" half — this table has no canonical counterpart (there is
nothing to seed; profiles only ever come from an admin's own prior import).

``mapping_versions_snapshot`` freezes the ``(kind, label) -> version`` map
(Task 3's ``QualitativeMappingService.mapping_versions()`` shape:
``{"canonical": {...}, "org": {...}}``) at save time. ``apply_profile``
(Task 3) diffs this snapshot against the CURRENT ``mapping_versions()`` to
surface drift warnings — e.g. "the 'high' frequency band changed since this
profile was saved" — without blocking the apply (unbindable/changed values
are simply left unbound for the admin to re-bind).

``column_map`` / ``value_bindings`` are JSON blobs shaped exactly like the
staged ``CSVImportPreview.state_json`` keys they came from (Task 3's
``set_column_map`` / ``set_value_bindings`` payloads) — no separate DTO
exists to keep them in sync with (Arch-N3 plan-gate amendment): this table
has no Pydantic DTO pair in P1c (forms are route-level, not schema-backed),
so the project's ORM<->DTO field-sync contract test does not apply here; the
schema-snapshot test (``tests/contracts/test_schema_snapshots.py``) is the
sole structural guard.

``created_by`` is nullable + ON DELETE SET NULL — mirrors
``ScenarioLibraryOverride``/``QualitativeMappingOrgBand``: the profile
outlives its creator user.

Spec: docs/superpowers/specs/2026-07-18-qualitative-register-converter-design.md §5.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint
from sqlalchemy import Uuid as UuidType
from sqlalchemy.orm import Mapped, mapped_column

from idraa.db import Base
from idraa.models.mixins import IdMixin, OrgMixin, TimestampMixin


class RegisterBindingProfile(IdMixin, TimestampMixin, OrgMixin, Base):
    """One saved (column_map, value_bindings) profile per (org, name).

    ``UniqueConstraint(organization_id, name)`` — profile names are a
    per-org namespace; two orgs may each have a "Quarterly export" profile.
    """

    __tablename__ = "register_binding_profiles"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    column_map: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    value_bindings: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    mapping_versions_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UuidType(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_register_profile_org_name"),
    )
