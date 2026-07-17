"""MITRE ATT&CK technique catalog + scenario mappings (issue #475).

Canonical catalog (NOT org-scoped): ``AttackTactic`` / ``AttackTechnique``,
seeded from ``data/seed_attack_catalog.json`` (MITRE ATT&CK Enterprise + ICS,
techniques only — sub-techniques deferred, ``parent_technique_id`` ships NULL).

Curated canonical layer: ``ScenarioLibraryEntryAttackMapping`` — cited,
provenance-labeled technique claims per library-entry snapshot (composite FK,
mirroring ``ScenarioLibraryOverride``).

Org layer: ``ScenarioAttackMapping`` — a scenario's technique tags
(``source='library'`` inherited at clone / ``source='user'`` authored).

Deliberate deviation from the FrameworkControl precedent: catalog version is
NOT part of the technique unique key. ATT&CK technique IDs are stable across
releases; version refreshes ship as recuration migrations that update rows in
place and flag ``deprecated`` — mapping FKs never churn.

Techniques are taxonomy metadata only: they never enter fair_cam and never
influence FAIR math.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Uuid as UuidType
from sqlalchemy.orm import Mapped, mapped_column, relationship

from idraa.db import Base
from idraa.models.mixins import IdMixin, OrgMixin, TimestampMixin

# App-enforced value sets (no DB CHECK — #303 foot-gun). Seed schemas and the
# mapping service validate against these.
ATTACK_DOMAINS = ("enterprise", "ics", "atlas")
# #482: canonical display labels + order, single-sourced here (previously
# duplicated in services/attack_coverage.py + routes/scenario_form_helpers.py).
# "atlas" = MITRE ATLAS (AI/ML threat landscape); schema-ready — catalog data
# seeding is gated separately on AI-scenario growth (see #482).
DOMAIN_LABELS = {"enterprise": "Enterprise", "ics": "ICS", "atlas": "ATLAS"}
DOMAIN_ORDER = {d: i for i, d in enumerate(ATTACK_DOMAINS)}
MAPPING_PROVENANCE_VALUES = ("cited", "expert-estimate")
SCENARIO_MAPPING_SOURCES = ("library", "user")


class AttackTactic(IdMixin, Base):
    __tablename__ = "attack_tactics"
    __table_args__ = (
        UniqueConstraint("domain", "tactic_id", name="uq_attack_tactic_id"),
        UniqueConstraint("domain", "shortname", name="uq_attack_tactic_shortname"),
    )

    domain: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    tactic_id: Mapped[str] = mapped_column(String(16), nullable=False)
    shortname: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False)
    url: Mapped[str] = mapped_column(String(256), nullable=False)


class AttackTechnique(IdMixin, Base):
    __tablename__ = "attack_techniques"
    __table_args__ = (UniqueConstraint("domain", "technique_id", name="uq_attack_technique_id"),)

    domain: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    technique_id: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Tactic shortnames — a technique can span multiple tactics (e.g. Valid
    # Accounts appears in 4). Values reference attack_tactics.shortname within
    # the same domain (integrity enforced by seed schema + pinning tests, not FK).
    tactics: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    # Sub-technique readiness only — NULL for every PR-1 row.
    parent_technique_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # SC-N5: mirrors the repo's existing Boolean server_default idiom
    # (risk_analysis_run.py's ``is_stale``) — a string "0"/"1" literal is
    # dialect-portable (SQLite + Postgres), unlike text("0") for booleans.
    deprecated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    catalog_version: Mapped[str] = mapped_column(String(16), nullable=False)
    url: Mapped[str] = mapped_column(String(256), nullable=False)
    citation: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class ScenarioLibraryEntryAttackMapping(IdMixin, Base):
    """Curated technique claim for a library-entry snapshot (canonical, cited)."""

    __tablename__ = "library_entry_attack_mappings"
    __table_args__ = (
        UniqueConstraint(
            "library_entry_id",
            "library_entry_version",
            "technique_id",
            name="uq_library_entry_attack_mapping",
        ),
        ForeignKeyConstraint(
            ["library_entry_id", "library_entry_version"],
            ["scenario_library_entries.id", "scenario_library_entries.version"],
            name="fk_leam_entry_version",
        ),
        Index("ix_leam_entry", "library_entry_id", "library_entry_version"),
    )

    library_entry_id: Mapped[uuid.UUID] = mapped_column(UuidType(as_uuid=True), nullable=False)
    library_entry_version: Mapped[int] = mapped_column(Integer, nullable=False)
    technique_id: Mapped[uuid.UUID] = mapped_column(
        UuidType(as_uuid=True),
        ForeignKey("attack_techniques.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    provenance: Mapped[str] = mapped_column(String(32), nullable=False)
    citations: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    technique: Mapped[AttackTechnique] = relationship(lazy="joined")


class ScenarioAttackMapping(IdMixin, TimestampMixin, OrgMixin, Base):
    """An org scenario's technique tag (library-inherited or user-authored)."""

    __tablename__ = "scenario_attack_mappings"
    __table_args__ = (
        UniqueConstraint("scenario_id", "technique_id", name="uq_scenario_attack_mapping"),
    )

    scenario_id: Mapped[uuid.UUID] = mapped_column(
        UuidType(as_uuid=True),
        ForeignKey("scenarios.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    technique_id: Mapped[uuid.UUID] = mapped_column(
        UuidType(as_uuid=True),
        ForeignKey("attack_techniques.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="user")
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)

    technique: Mapped[AttackTechnique] = relationship(lazy="joined")
