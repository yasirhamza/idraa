"""Data-access layer for QualitativeMappingBand + QualitativeMappingOrgBand.

Mirrors ``ScenarioLibraryRepo``'s discipline (Spec §7.1 precedent): thin
query methods, no business logic, org-scoping baked into the WHERE clause
wherever a lookup is meant to be IDOR-safe by construction.

Spec: docs/superpowers/specs/2026-07-18-qualitative-register-converter-design.md §2.
"""

from __future__ import annotations

import uuid

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.qualitative_mapping import QualitativeMappingBand, QualitativeMappingOrgBand


class QualitativeMappingRepo:
    """Data-access layer for canonical bands and per-org band overrides."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_canonical(self) -> list[QualitativeMappingBand]:
        """Return the latest-version canonical band row per (kind, label).

        Mirrors ``ScenarioLibraryRepo.list_published``'s window-function-free
        MAX(version)-subquery-joined-back pattern (SQLite compatible). Today
        every seeded row is ``version=1`` so this degenerates to "all rows",
        but the canonical layer is versionable in place (a future
        re-derivation inserts ``version+1`` under the same (kind, label)
        rather than mutating a cited row — see the model docstring), so the
        latest-per-label filter is load-bearing going forward, not
        speculative.
        """
        latest_subq = (
            select(
                QualitativeMappingBand.kind.label("kind"),
                QualitativeMappingBand.label.label("label"),
                func.max(QualitativeMappingBand.version).label("max_version"),
            )
            .group_by(QualitativeMappingBand.kind, QualitativeMappingBand.label)
            .subquery()
        )
        stmt = select(QualitativeMappingBand).join(
            latest_subq,
            and_(
                QualitativeMappingBand.kind == latest_subq.c.kind,
                QualitativeMappingBand.label == latest_subq.c.label,
                QualitativeMappingBand.version == latest_subq.c.max_version,
            ),
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return list(rows)

    async def list_org_bands(
        self,
        organization_id: uuid.UUID,
    ) -> list[QualitativeMappingOrgBand]:
        """Active (non-tombstoned) org band overrides for this org.

        Filters ``deleted_at IS NULL`` — mirrors ``ScenarioLibraryRepo
        .get_override``'s F9 tombstone-invisibility discipline. Soft-deleted
        rows are preserved for audit but never surface here.
        """
        stmt = select(QualitativeMappingOrgBand).where(
            and_(
                QualitativeMappingOrgBand.organization_id == organization_id,
                QualitativeMappingOrgBand.deleted_at.is_(None),
            )
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return list(rows)

    async def get_org_band(
        self,
        organization_id: uuid.UUID,
        band_id: uuid.UUID,
        *,
        for_update: bool = False,
    ) -> QualitativeMappingOrgBand | None:
        """Single active org band by id, scoped to ``organization_id``.

        ``organization_id`` sits IN THE WHERE clause (mirrors
        ``ScenarioLibraryRepo.get_override``) so this is the repo-level IDOR
        closure for both ``update_org_band`` and ``delete_org_band``: a
        cross-org ``band_id`` is indistinguishable from a missing one — the
        query returns ``None`` either way, never leaking which case it was
        (same existence-hiding posture ``IDORError``'s own docstring
        describes, just enforced one layer lower, in SQL rather than
        app-level post-fetch comparison).

        Also filters ``deleted_at IS NULL`` — a tombstoned band is not a
        valid update/delete target (mirrors ``list_org_bands``).

        ``for_update=True`` acquires a row-level lock (mirrors
        ``ScenarioLibraryRepo.get_by_id_version``) so the optimistic-lock
        check in ``update_org_band``/``delete_org_band`` is race-free; a
        no-op on SQLite, serializes concurrent writers on Postgres.
        """
        stmt = select(QualitativeMappingOrgBand).where(
            and_(
                QualitativeMappingOrgBand.id == band_id,
                QualitativeMappingOrgBand.organization_id == organization_id,
                QualitativeMappingOrgBand.deleted_at.is_(None),
            )
        )
        if for_update:
            stmt = stmt.with_for_update()
        return (await self.session.execute(stmt)).scalar_one_or_none()
