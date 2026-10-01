"""Threat Community — the canonical Threat Agent Library (spec 2026-09-30-threat-agent-library).

Canonical catalog (NOT org-scoped), mirroring ``ScenarioLibraryEntry``: composite PK
``(id, version)``; a row is never mutated — re-curation inserts ``(id, version+1)`` from a
SEPARATE seed file (the P1 migration inserts every row of seed_threat_communities.json as v1).
Canonical ids are deterministic ``uuid5(THREAT_COMMUNITY_NAMESPACE, slug)``.

Landmarks, never engine inputs (register A2 amendment + D10): ``tef_landmark`` is a PERT-shaped
triple whose low/high ARE the curator's 5th/95th promoted to bounds — the same convention as
every library-entry and wizard TEF (docs/reference/tef-representation.md, register B1) — stated
for ``reference_org`` across all its assets (``tef_basis``); ``tcap_landmark`` is a percentile-rank
triple (0-100) on the same convention, displayed and never converted to Vulnerability, and SQL NULL
for a non-malicious community (error is not a capability contest). Nothing under ``fair_cam`` and
nothing in ``services/run_executor.py`` reads this table (tests/integration/test_threat_community_engine_parity.py).

P2 org-authored communities live in their own org-scoped table (spec §9): ``source`` is always
``"seed"`` in P1 and the service filters on it.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Uuid as UuidType
from sqlalchemy.orm import Mapped, mapped_column

from idraa.db import Base
from idraa.models.mixins import TimestampMixin
from idraa.threat_community_provenance import PROVENANCE_VALUES, REVIEW_PROVENANCES

THREAT_COMMUNITY_NAMESPACE = uuid.UUID("6f1c4a4e-2b7d-4d2a-9c3e-8e5f0b7a1d10")

CANONICAL_THREAT_COMMUNITY_SLUGS: tuple[str, ...] = (
    "nation_state",
    "cybercriminals",
    "hacktivists",
    "competitors",
    "privileged_insider",
    "nonprivileged_insider",
    "insider_accidental",
    "third_party",
    "opportunistic_hackers",
)

# App-enforced value sets (no DB CHECK — #303 foot-gun).
THREAT_COMMUNITY_ORIGINS = ("internal", "external")
THREAT_COMMUNITY_INTENTS = ("malicious", "non_malicious")
THREAT_COMMUNITY_SOURCES = ("seed",)
LANDMARK_BASIS_CLASSES = ("cited", "derived", "convention")
THREAT_COMMUNITY_PROVENANCE_VALUES = PROVENANCE_VALUES
THREAT_COMMUNITY_REVIEW_PROVENANCES = REVIEW_PROVENANCES


def canonical_threat_community_id(slug: str) -> uuid.UUID:
    return uuid.uuid5(THREAT_COMMUNITY_NAMESPACE, slug)


class ThreatCommunity(TimestampMixin, Base):
    __tablename__ = "threat_communities"

    id: Mapped[uuid.UUID] = mapped_column(UuidType(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="seed", server_default="seed"
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    origin: Mapped[str] = mapped_column(String(16), nullable=False)
    intent: Mapped[str] = mapped_column(String(16), nullable=False)
    motive: Mapped[str] = mapped_column(Text, nullable=False)
    primary_intent: Mapped[str] = mapped_column(Text, nullable=False)
    sponsorship: Mapped[str] = mapped_column(Text, nullable=False)
    preferred_target_characteristics: Mapped[str] = mapped_column(Text, nullable=False)
    preferred_targets: Mapped[str] = mapped_column(Text, nullable=False)
    capability: Mapped[str] = mapped_column(Text, nullable=False)
    personal_risk_tolerance: Mapped[str] = mapped_column(Text, nullable=False)
    collateral_damage_concern: Mapped[str] = mapped_column(Text, nullable=False)
    threat_event_definition: Mapped[str] = mapped_column(Text, nullable=False)
    tef_basis: Mapped[str] = mapped_column(Text, nullable=False)
    reference_org: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    tef_landmark: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    # none_as_null=True: Python None -> SQL NULL (plain JSON would store the text 'null';
    # see the library_pin NULLIF workaround at repositories/scenario_repo.py:128-131).
    tcap_landmark: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    reviewed_at: Mapped[dt.date] = mapped_column(Date, nullable=False)
    published_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("id", "version", name="pk_threat_communities"),
        UniqueConstraint("slug", "version", name="uq_threat_community_slug_version"),
        Index("ix_threat_communities_slug", "slug"),
    )
