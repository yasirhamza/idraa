"""ControlRepo — IDOR-safe lookup for Control rows.

Mirrors ScenarioRepo / OverlayRepo / CalibrationOverrideRepo pattern:
session injected at __init__, query primitives, no transaction
management at the repo layer (callers commit).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.control import Control


class ControlRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def fetch_by_ids_for_org(
        self,
        organization_id: uuid.UUID,
        control_ids: list[uuid.UUID],
    ) -> list[Control]:
        """Fetch Controls scoped to org; rejects cross-org IDs silently."""
        if not control_ids:
            return []
        stmt = (
            select(Control)
            .where(Control.organization_id == organization_id)
            .where(Control.id.in_(control_ids))
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_for_org(
        self,
        organization_id: uuid.UUID,
    ) -> list[Control]:
        """List controls eligible to attach to scenarios, ordered by name.

        Eligible = published (EntityStatus.ACTIVE) AND operating
        (implementation_stage ACTIVE). Non-active-stage controls are hidden
        from every scenario-side picker / review surface (issue #395): you
        can only attach a control that actually composes. The controls
        LIBRARY list uses services.controls.list_controls (not this method),
        so planned / in-project controls remain visible there for management.
        """
        from idraa.models.enums import ControlImplementationStage, EntityStatus

        # The stage predicate below MIRRORS
        # ControlImplementationStage.contributes_to_composition (== ACTIVE).
        # It is a raw column comparison because a Python @property cannot be
        # called inside a SQL .where(); test_repo_filter_matches_predicate
        # locks the two together.
        stmt = (
            select(Control)
            .where(Control.organization_id == organization_id)
            .where(Control.status == EntityStatus.ACTIVE)
            .where(Control.implementation_stage == ControlImplementationStage.ACTIVE)
            .order_by(Control.name)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
