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

    async def list_org_wide_meta(
        self, organization_id: uuid.UUID, *, with_assignments: bool = True
    ) -> list[Control]:
        """Issue #299: controls whose VMC/DSC assignments every scenario in the
        org inherits at run time. Same eligibility predicate as list_for_org
        (published + operating) AND org_wide_meta — mirrors
        services.controls._org_wide_effective; test_repo_filter_matches_predicate
        locks all three to ContributesToComposition. Control.assignments is
        lazy="selectin" (models/control.py:112-116), so the rows load by default
        for the callers that filter on them (freeze, run-create refusal);
        display callers that only read id/name pass ``with_assignments=False``,
        which swaps in ``raiseload`` — the second SELECT is skipped and an
        accidental read fails loud instead of returning [] (Sec-N6 / Arch-N3).

        Session stickiness (Arch-N8; probed on SQLAlchemy 2.1.1): instances first
        loaded with ``with_assignments=False`` raise on ``.assignments`` for the
        rest of the session unless re-selected (a later plain ``select(Control)``
        repopulates the unloaded collection on the existing instance) — so call
        this AFTER any query in the same session that needs those controls' rows.
        The three display callers (Task 10) are safe: each only reads ``id`` /
        ``name``, and ``list_for_org`` / ``selectinload(...mitigating_controls)``
        run before it where their rows are needed."""
        from sqlalchemy.orm import raiseload

        from idraa.models.enums import ControlImplementationStage, EntityStatus

        stmt = (
            select(Control)
            .where(Control.organization_id == organization_id)
            .where(Control.org_wide_meta.is_(True))
            .where(Control.status == EntityStatus.ACTIVE)
            .where(Control.implementation_stage == ControlImplementationStage.ACTIVE)
            .order_by(Control.name)
        )
        if not with_assignments:
            stmt = stmt.options(raiseload(Control.assignments))
        return list((await self._session.execute(stmt)).scalars().all())

    async def count_org_wide_meta(self, organization_id: uuid.UUID) -> int:
        """Issue #299: how many effective org-wide controls the org has — one
        scalar query (no assignment load) for the run-detail legacy note."""
        from sqlalchemy import func

        from idraa.models.enums import ControlImplementationStage, EntityStatus

        stmt = (
            select(func.count())
            .select_from(Control)
            .where(Control.organization_id == organization_id)
            .where(Control.org_wide_meta.is_(True))
            .where(Control.status == EntityStatus.ACTIVE)
            .where(Control.implementation_stage == ControlImplementationStage.ACTIVE)
        )
        return int((await self._session.execute(stmt)).scalar_one())
