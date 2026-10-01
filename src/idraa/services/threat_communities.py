"""Threat Agent Library service (spec 2026-09-30-threat-agent-library section 4-5)."""  # ASCII: ruff RUF002

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable
from typing import Any

from sqlalchemy import Select, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.errors import ConflictError, ValidationError
from idraa.models.threat_community import ThreatCommunity

# Shared slug-shape guard: defence in depth at every route boundary that accepts a
# threat-community slug (path param or query param) -- a lookup miss is also 404, but a
# regex mismatch short-circuits before a DB round trip. Hoisted here from
# routes/library.py and routes/threat_communities.py (both previously held identical
# copies) so there is exactly one pattern to keep in sync with the seed slugs.
SLUG_RE = re.compile(r"^[a-z0-9_]{1,64}$")

# Legacy threat_actor_type enum value -> canonical slug. Used by import paths for pre-P1
# files (the migration carries its own frozen copy).
ENUM_TO_COMMUNITY_SLUG: dict[str, str] = {
    "cybercriminals": "cybercriminals",
    "nation_state": "nation_state",
    "hacktivists": "hacktivists",
    "competitors": "competitors",
    "insider_accidental": "insider_accidental",
    "insider_malicious": "privileged_insider",  # placeholder so the review banner fires; NO numeric effect
}
LEGACY_SPLIT_SOURCE_VALUE = "insider_malicious"
_ORIGIN_ORDER = {"internal": 0, "external": 1}
_ORIGIN_LABELS = {"internal": "Internal", "external": "External"}


def legacy_slug_for(value: str | None) -> tuple[str | None, str]:
    """Legacy enum value -> (slug, provenance). Unknown values raise KeyError."""
    if value is None or value == "":
        return None, "unassigned"
    slug = ENUM_TO_COMMUNITY_SLUG[value]
    return slug, ("migrated_split_default" if value == LEGACY_SPLIT_SOURCE_VALUE else "migrated")


class ThreatCommunityNotInReview(ConflictError):  # noqa: N818 -- name fixed across the plan; errors.py:204 precedent
    """Confirm refused: the scenario is not in a review state (409)."""


class ThreatCommunityService:
    """Canonical (source='seed') communities only in P1. ``organization_id`` is REQUIRED now so
    P2's org-authored union is a service change, not a signature change, and no call site can
    ever run unscoped."""

    def __init__(self, db: AsyncSession, *, organization_id: uuid.UUID) -> None:
        self._db = db
        self._organization_id = organization_id

    # strict mypy: typed Select (`from sqlalchemy import Select`)
    def _latest_seed(self) -> Select[tuple[ThreatCommunity]]:
        latest = (
            select(ThreatCommunity.slug, func.max(ThreatCommunity.version).label("v"))
            .where(ThreatCommunity.source == "seed")
            .group_by(ThreatCommunity.slug)
            .subquery()
        )
        return (
            select(ThreatCommunity)
            .join(
                latest,
                and_(ThreatCommunity.slug == latest.c.slug, ThreatCommunity.version == latest.c.v),
            )
            .where(ThreatCommunity.source == "seed")
        )

    async def list_published(self) -> list[ThreatCommunity]:
        rows = (await self._db.execute(self._latest_seed())).scalars().all()
        return sorted(rows, key=lambda r: (_ORIGIN_ORDER.get(r.origin, 9), r.name))

    async def get_by_slug(self, slug: str) -> ThreatCommunity | None:
        return (
            (await self._db.execute(self._latest_seed().where(ThreatCommunity.slug == slug)))
            .scalars()
            .first()
        )

    async def resolve(self, slug: str) -> ThreatCommunity:
        row = await self.get_by_slug(slug)
        if row is None:
            raise ValidationError(f"unknown threat community {slug!r}")
        return row

    async def resolve_slug(self, slug: str) -> tuple[uuid.UUID, int]:
        row = await self.resolve(slug)
        return row.id, row.version

    async def grouped_choices(self) -> list[tuple[str, list[tuple[str, str]]]]:
        rows = await self.list_published()
        return [
            (_ORIGIN_LABELS[k], choices_from_rows(r for r in rows if r.origin == k))
            for k in ("internal", "external")
        ]


def choices_from_rows(rows: Iterable[Any]) -> list[tuple[str, str]]:
    return [(r.slug, r.name) for r in rows]
