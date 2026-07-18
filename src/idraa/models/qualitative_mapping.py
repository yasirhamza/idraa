"""Qualitative register conversion — mapping band layer (epic #34 P1b).

Two band kinds — ``frequency`` (events/year) and ``magnitude`` (USD) — deliberately
NOT named TEF/PL in the schema, so a future ERM widening (#39) can bind additional
targets without a schema rewrite. ``kind`` and ``label`` are plain ``String``
columns, app-enforced (no DB ``CHECK`` — mirrors ``scenario_library.py``'s
``source``/``loss_tier`` columns: value sets are validated by the Pydantic seed
model and service-layer validators, never a DB constraint, avoiding the #303
CHECK-widening foot-gun).

``QualitativeMappingBand`` — canonical layer, org-less, seeded from code via a
migration (mirrors ``ScenarioLibraryEntry``'s seed/immutability discipline; the
single-column UUID PK via ``IdMixin`` is simpler than that model's composite PK
since there is no version-as-snapshot-history requirement here — ``version`` is
a plain bump counter). Pinning tests assert every value in
``data/seed_qualitative_bands.json`` traces to spec §2.2.

``QualitativeMappingOrgBand`` — per-org override layer (mirrors
``ScenarioLibraryOverride``): an org row either overrides a canonical
``(kind, label)`` or adds a new label. Soft-delete via ``deleted_at`` (not a hard
delete) so audit history survives; the partial unique index below allows
delete-then-recreate of the same label to succeed (Arch-I3).

Spec: docs/superpowers/specs/2026-07-18-qualitative-register-converter-design.md §2.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import Uuid as UuidType
from sqlalchemy.orm import Mapped, mapped_column

from idraa.db import Base
from idraa.models.mixins import IdMixin, OrgMixin, TimestampMixin


class QualitativeMappingBand(IdMixin, TimestampMixin, Base):
    """Canonical band — org-less, seeded from code, immutable via the UI.

    ``UniqueConstraint(kind, label, version)`` keeps the (kind, label) namespace
    versionable in place (a future re-derivation inserts version+1 rather than
    mutating a cited row), matching the "canonical layer immutable in code with
    pinning tests" doctrine.
    """

    __tablename__ = "qualitative_mapping_bands"

    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    mode: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    derivation: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(
        Integer, default=1, server_default=text("1"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("kind", "label", "version", name="uq_qual_band_kind_label_version"),
    )


class QualitativeMappingOrgBand(IdMixin, TimestampMixin, OrgMixin, Base):
    """Per-org override layer for a canonical (kind, label) band.

    ``reason`` is NOT NULL — audit-grade, mirrors ``ScenarioLibraryOverride``.
    ``created_by`` is nullable + ON DELETE SET NULL — the row outlives its
    creator user (audit durability). ``deleted_at`` is a soft-delete tombstone;
    the partial unique index (active rows only) lets a label be deleted and
    re-created without colliding with its own tombstone.
    """

    __tablename__ = "qualitative_mapping_org_bands"

    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    mode: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(
        Integer, default=1, server_default=text("1"), nullable=False
    )
    # Optimistic-lock primitive — server-bumped on every mutation. Distinct
    # from the descriptive `version` bump, mirrors ScenarioLibraryOverride.
    row_version: Mapped[int] = mapped_column(
        Integer, default=1, server_default=text("1"), nullable=False
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UuidType(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        # Partial unique index (active rows only) — NOT a plain UniqueConstraint —
        # so delete-then-recreate of the same (org, kind, label) label succeeds
        # (plan-gate Arch-I3). Mirrors fx_rate.py's ux_fx_rate_active_per_code.
        Index(
            "ux_qual_org_band_org_kind_label",
            "organization_id",
            "kind",
            "label",
            unique=True,
            sqlite_where=text("deleted_at IS NULL"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )
