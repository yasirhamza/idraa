"""Scenario library schema — canonical entry layer + per-org override layer.

ScenarioLibraryEntry: composite PK (id, version) for immutable history versioning.
Each row is an immutable snapshot; new versions are new rows under the same logical id.

ScenarioLibraryOverride (F3): per-org override layer; composite FK to entry's
(id, version); UNIQUE on (org, entry); version bumps in-place on edit.

Spec: docs/superpowers/specs/2026-04-28-phase-1.5a-scenario-library-design.md §6.1
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import Uuid as UuidType
from sqlalchemy.orm import Mapped, mapped_column

from idraa.db import Base
from idraa.models.enums import AssetClass, ThreatActorType, ThreatCategory
from idraa.models.mixins import IdMixin, OrgMixin, TimestampMixin


class ScenarioLibraryEntry(TimestampMixin, Base):
    """Immutable canonical scenario library entry.

    Composite PK (id, version) enforces that each row is a frozen snapshot.
    Publishing a corrected scenario means inserting a new row with the same
    logical id and version+1. The previous row is never mutated.

    ``status`` drives library visibility:
    - ``draft``       — in-progress; not visible to end users.
    - ``published``   — live; visible in wizard and scenario selector.
    - ``deprecated``  — superseded; hidden from new use but preserved for
                        audit trails on existing derived scenarios.
    """

    __tablename__ = "scenario_library_entries"

    id: Mapped[uuid.UUID] = mapped_column(
        UuidType(as_uuid=True),
        default=uuid.uuid4,
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        Enum(
            "draft",
            "published",
            "deprecated",
            native_enum=False,
            create_constraint=True,
            name="library_entry_status",
        ),
        default="draft",
        nullable=False,
    )

    threat_event_type: Mapped[ThreatCategory] = mapped_column(
        Enum(ThreatCategory, native_enum=False, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    threat_actor_type: Mapped[ThreatActorType] = mapped_column(
        Enum(ThreatActorType, native_enum=False, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    asset_class: Mapped[AssetClass] = mapped_column(
        Enum(AssetClass, native_enum=False, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    attack_vector: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    description: Mapped[str] = mapped_column(Text, nullable=False)
    example_incidents: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_citations: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    canonical_fair_gap: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        doc="Brief: which FAIR canonical gap this entry fills.",
    )

    applicable_industries: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    applicable_sub_sectors: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    applicable_org_sizes: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)

    threat_event_frequency: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    vulnerability: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    primary_loss: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    secondary_loss: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # Phase 1.5b: control library references (populated when F-control-library lands)
    suggested_control_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    # Phase 2: standards/compliance mapping (NIST CSF, ISO 27001, CIS Controls, etc.)
    standards_references: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # PR gamma-4 (#115): per-entry anchor declaring the (industry, revenue_tier) the
    # curator's published PL/SL values assume. Introduced nullable in PR gamma-2;
    # all 31 seed entries curated in PR gamma-3; flipped to NOT NULL here once
    # curation completed. Industry is advisory only — IRIS Table 1 is industry-
    # aggregate; only revenue_tier drives the multiplier.
    calibration_anchor: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    # P3: provenance — 'seed' (shipped in code, migration-seeded) vs 'imported'
    # (uploaded at runtime via bundle import). No DB CHECK (native_enum=False,
    # no create_constraint) — mirrors scenarios.source, avoids the #303
    # CHECK-widening foot-gun; the value set is app-enforced.
    source: Mapped[str] = mapped_column(
        Enum("seed", "imported", native_enum=False, name="library_entry_source"),
        server_default="seed",
        nullable=False,
    )

    # Epic C-i (#335 §6): epistemic tier of the entry's loss-magnitude anchor.
    # paginated/vendor -> lognormal loss node; anecdotal/none -> PERT. Mirrors
    # the `source` column above (native_enum=False, NO create_constraint — no
    # CHECK emitted; value set is app-enforced via LossTier + the seed/import
    # validators). server_default='anecdotal' backfills existing rows (all
    # current seed entries are PERT, so anecdotal is correct on upgrade).
    loss_tier: Mapped[str] = mapped_column(
        Enum(
            "paginated",
            "vendor",
            "anecdotal",
            "none",
            native_enum=False,
            name="library_entry_loss_tier",
        ),
        server_default="anecdotal",
        nullable=False,
    )

    # Milestone B (#loss-pert-overhaul): shape class of the loss magnitude.
    # capped -> PERT (bounded; the high IS the economic ceiling); catastrophic
    # -> uncapped lognormal (curated shortlist, spec 2026-07-09 §3).
    # Independent of loss_tier (citation quality). Mirrors the loss_tier
    # column style (native_enum=False, no CHECK; value set app-enforced via
    # LossShape + the seed validator).
    loss_shape: Mapped[str] = mapped_column(
        Enum(
            "capped",
            "catastrophic",
            native_enum=False,
            name="library_entry_loss_shape",
        ),
        server_default="capped",
        nullable=False,
    )

    # Epic D-i (#497 §6): per-archetype FAIR loss-form provenance. A list of
    # {form, kind ('primary'|'secondary'), magnitude_basis, citations, verified,
    # composition_role} — the authoring-time record of how primary_loss /
    # secondary_loss were composed (see docs/reference/loss-magnitude-forms.md).
    # JSON, server_default '[]' so existing rows backfill empty on upgrade
    # (recalibration populates it in D-iii). The engine never reads this — it is
    # provenance + the differentiation-guard anchor.
    loss_form_profile: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, server_default=text("'[]'"), default=list, nullable=False
    )

    # Optimistic-lock primitive — server-bumped on every mutation.
    row_version: Mapped[int] = mapped_column(
        Integer,
        default=1,
        server_default=text("1"),
        nullable=False,
    )

    def __init__(self, **kwargs: Any) -> None:
        """Pre-fill id with uuid.uuid4() if not supplied; placeholder anchor.

        ScenarioLibraryEntry deliberately does NOT inherit from IdMixin
        (composite PK clashes with IdMixin's primary_key=True on id), so
        the project's _populate_defaults_on_init event hook does NOT fire
        here. Mirroring OverlayDefinition's pattern of explicit __init__
        setdefault keeps id populated at construction time so callers
        that construct without explicit id= still get a usable id before
        flush (e.g., for logging, library_pin construction).

        PR gamma-4 (#115): calibration_anchor is NOT NULL on the column. A  # noqa: RUF003
        placeholder default ('other' / '100m_to_1b') is supplied here so
        test fixtures that don't explicitly set calibration_anchor still
        construct cleanly. Production seed data goes through raw SQL
        INSERT in the seed migration (alembic versions/c1d2e3f4a5b6),
        validated by LibraryEntrySeed Pydantic before the SQL runs — so
        the placeholder never fires on the production path.
        """
        kwargs.setdefault("id", uuid.uuid4())
        kwargs.setdefault(
            "calibration_anchor",
            {"industry": "other", "revenue_tier": "100m_to_1b"},
        )
        # D-i (#497): construct-time default so in-memory entries (pre-flush,
        # before the server_default '[]' fires) expose [] not None — export/DTO
        # reject a None loss_form_profile. Mirrors the id/calibration_anchor pattern.
        kwargs.setdefault("loss_form_profile", [])
        super().__init__(**kwargs)

    __table_args__ = (
        PrimaryKeyConstraint("id", "version", name="pk_scenario_library_entries"),
        UniqueConstraint("slug", "version", name="uq_library_entry_slug_version"),
        Index("ix_library_entry_status", "status"),
        Index("ix_library_entry_threat_actor", "threat_actor_type"),
        Index("ix_library_entry_threat_event", "threat_event_type"),
    )


class ScenarioLibraryOverride(IdMixin, TimestampMixin, OrgMixin, Base):
    """Per-org override layer for a canonical ScenarioLibraryEntry.

    One row per (organization, library entry) — UNIQUE constraint enforces
    that only one active override can exist per (org, entry) pair. Version
    bumps in-place on edit (override IS the row, not a row-set). Soft-delete
    (tombstone) is deferred to F9.

    Override field semantics: all distribution fields are nullable. The merge
    rule is "if override field is non-null, use override; else use canonical
    entry value." F8 implements ``merge_canonical_and_override``.

    Composite FK (library_entry_id, library_entry_version) →
    scenario_library_entries(id, version) ensures the override is always
    pinned to a specific published snapshot, not just any version with that
    logical id. Pin-resolution lookup is safe even after the canonical entry
    is deprecated.

    ``reason`` is NOT NULL — audit-grade, mirrors CalibrationOverride pattern.
    ``created_by`` is nullable + ON DELETE SET NULL — override outlives its
    creator user (audit durability).

    Spec: §6.1 / §12.1 (TOMBSTONE policy deferred to F9).
    """

    __tablename__ = "scenario_library_overrides"

    library_entry_id: Mapped[uuid.UUID] = mapped_column(UuidType(as_uuid=True), nullable=False)
    library_entry_version: Mapped[int] = mapped_column(Integer, nullable=False)

    threat_event_frequency: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    vulnerability: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    primary_loss: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    secondary_loss: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    reason: Mapped[str] = mapped_column(Text, nullable=False)
    methodology_change_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # version: which override revision this row represents (bumped in-place on edit).
    version: Mapped[int] = mapped_column(
        Integer, default=1, server_default=text("1"), nullable=False
    )
    # row_version: optimistic-lock primitive, server-bumped on every mutation.
    row_version: Mapped[int] = mapped_column(
        Integer, default=1, server_default=text("1"), nullable=False
    )

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UuidType(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # F9 tombstone path: deleted_at ships here so F9 doesn't need a follow-up
    # schema migration. Active rows: NULL. Tombstoned rows: timestamp.
    # get_by_org_entry filters deleted_at IS NULL; audit-grade pin lookups don't.
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "library_entry_id",
            name="uq_library_override_org_entry",
        ),
        ForeignKeyConstraint(
            ["library_entry_id", "library_entry_version"],
            ["scenario_library_entries.id", "scenario_library_entries.version"],
            name="fk_library_override_entry_version",
        ),
        # Note: OrgMixin's organization_id mapped_column uses index=True which
        # auto-generates ix_scenario_library_overrides_organization_id. We do NOT
        # add a second explicit Index here to avoid duplicate indexes on the same column.
    )
