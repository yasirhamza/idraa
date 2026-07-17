"""Overlay repository — DB lookup methods for overlay master-data.

Read paths used by the route layer + wizard helpers: ``get_active``,
``list_active``, ``get_for_org``. The historical revision-pinned fetch
methods (``fetch_revision_dto`` / ``fetch_revision_with_provenance``)
were dropped in PR pi F12 — the calibration runtime that consumed them
was excised, and no remaining code path resolves an
``(overlay_definition_id, version)`` pair to a fair_cam DTO.

The ``OverlayDefinitionRevision`` table is still written by
``OverlayService.create / update`` for audit-trail reproducibility, but
nothing reads it back through this repo today. If a future caller needs
revision lookup, restore a single typed method (mirror the prior
``_fetch_revision_or_raise`` shape) rather than re-introducing two
parallel fetch paths.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.overlay import OverlayDefinition


class OverlayRepo:
    """Read/lookup methods for overlay master-data.

    Mutations (create / update-with-revision-bump) are in the service
    layer where audit logging happens in the same DB session as the
    business write — keeping writes out of the repo follows the
    existing services/repositories split in this codebase.
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_active(self, *, organization_id: uuid.UUID, tag: str) -> OverlayDefinition | None:
        """Return the active overlay row for (org, tag), or None if none.

        Filters on ``is_active=True`` so soft-deleted overlays don't
        leak through to consumers.
        """
        stmt = select(OverlayDefinition).where(
            OverlayDefinition.organization_id == organization_id,
            OverlayDefinition.tag == tag,
            OverlayDefinition.is_active.is_(True),
        )
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none()

    async def list_active(self, *, organization_id: uuid.UUID) -> list[OverlayDefinition]:
        """All active overlays for an org, ordered by tag for stable UI rendering."""
        stmt = (
            select(OverlayDefinition)
            .where(
                OverlayDefinition.organization_id == organization_id,
                OverlayDefinition.is_active.is_(True),
            )
            .order_by(OverlayDefinition.tag)
        )
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def get_for_org(
        self, *, overlay_id: uuid.UUID, organization_id: uuid.UUID
    ) -> OverlayDefinition | None:
        """Return the overlay row only if it belongs to the given org.

        Returns ``None`` on org mismatch — the route layer treats that
        as a 404 (not a 403) so we don't leak existence of overlays
        owned by other orgs (B9/B10 fix). DO NOT change this to raise
        a permission error: a 403 would tell an attacker the row exists
        but they can't see it; a 404 keeps the existence oracle closed.
        """
        stmt = select(OverlayDefinition).where(
            OverlayDefinition.id == overlay_id,
            OverlayDefinition.organization_id == organization_id,
        )
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none()
