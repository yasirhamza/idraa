"""RunRepo — CRUD + IDOR-guarded reads + paginated history for RiskAnalysisRun.

Mirrors ScenarioRepo / OverlayRepo / CalibrationOverrideRepo pattern:
session injected at __init__, query/insert primitives, no transaction
management at the repo layer (callers commit).
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.errors import RunNotFoundError
from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType


class RunRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, run: RiskAnalysisRun) -> None:
        self._session.add(run)
        await self._session.flush()

    async def get_for_org(
        self,
        organization_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> RiskAnalysisRun | None:
        """IDOR-safe lookup: returns None on miss (including cross-org)."""
        stmt = (
            select(RiskAnalysisRun)
            .where(RiskAnalysisRun.id == run_id)
            .where(RiskAnalysisRun.organization_id == organization_id)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_for_org_or_raise(
        self,
        organization_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> RiskAnalysisRun:
        run = await self.get_for_org(organization_id, run_id)
        if run is None:
            raise RunNotFoundError(f"run id={run_id} not in org={organization_id}")
        return run

    async def list_for_scenario(
        self,
        organization_id: uuid.UUID,
        scenario_id: uuid.UUID,
        *,
        limit: int,
        offset: int,
    ) -> list[RiskAnalysisRun]:
        """Paginated history. Returns SINGLE runs scoped to scenario_id AND
        AGGREGATE runs that include scenario_id in aggregate_scenario_ids.

        PR xi: extended to surface AGGREGATE-membership; load-then-filter for
        cross-DB simplicity (small phase-1 volumes; PR pi may migrate to
        in-DB JSON containment query if perf surfaces).
        """
        sid_str = str(scenario_id)
        stmt = (
            select(RiskAnalysisRun)
            .where(RiskAnalysisRun.organization_id == organization_id)
            .order_by(RiskAnalysisRun.created_at.desc())
        )
        result = await self._session.execute(stmt)
        all_runs = list(result.scalars().all())
        matching = [
            r
            for r in all_runs
            if (r.run_type == RunType.SINGLE and r.scenario_id == scenario_id)
            or (
                r.run_type == RunType.AGGREGATE
                and r.aggregate_scenario_ids
                and sid_str in r.aggregate_scenario_ids
            )
        ]
        return matching[offset : offset + limit]

    async def count_for_scenario(
        self,
        organization_id: uuid.UUID,
        scenario_id: uuid.UUID,
    ) -> int:
        """Total count for pagination — must match list_for_scenario filter shape."""
        sid_str = str(scenario_id)
        stmt = select(RiskAnalysisRun).where(RiskAnalysisRun.organization_id == organization_id)
        result = await self._session.execute(stmt)
        all_runs = list(result.scalars().all())
        return sum(
            1
            for r in all_runs
            if (r.run_type == RunType.SINGLE and r.scenario_id == scenario_id)
            or (
                r.run_type == RunType.AGGREGATE
                and r.aggregate_scenario_ids
                and sid_str in r.aggregate_scenario_ids
            )
        )

    async def list_for_org(
        self,
        organization_id: uuid.UUID,
        *,
        limit: int,
        offset: int,
    ) -> list[RiskAnalysisRun]:
        """Paginated org-wide run history (SINGLE + AGGREGATE, all statuses),
        newest first. In-DB limit/offset — no load-then-filter (org-scope is a
        plain column filter, unlike the per-scenario JSON-membership case)."""
        stmt = (
            select(RiskAnalysisRun)
            .where(RiskAnalysisRun.organization_id == organization_id)
            .order_by(RiskAnalysisRun.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_for_org(self, organization_id: uuid.UUID) -> int:
        """Total org-wide run count for pagination — matches list_for_org scope."""
        stmt = (
            select(func.count())
            .select_from(RiskAnalysisRun)
            .where(RiskAnalysisRun.organization_id == organization_id)
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    async def latest_aggregate_for_org(
        self,
        organization_id: uuid.UUID,
    ) -> RiskAnalysisRun | None:
        """Most recent COMPLETED AGGREGATE run for the org, or None.

        Reused by PR omicron-2 (Executive PDF) for the headline-run
        lookup. Skips SINGLE runs and non-COMPLETED AGGREGATE statuses.
        """
        stmt = (
            select(RiskAnalysisRun)
            .where(RiskAnalysisRun.organization_id == organization_id)
            .where(RiskAnalysisRun.run_type == RunType.AGGREGATE)
            .where(RiskAnalysisRun.status == RunStatus.COMPLETED)
            .order_by(RiskAnalysisRun.created_at.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_aggregate_for_org(
        self,
        organization_id: uuid.UUID,
        *,
        limit: int = 50,
    ) -> list[RiskAnalysisRun]:
        """All AGGREGATE runs for the org, all five statuses, by created_at desc.

        Used by the /reports index page (PR omicron-2 Q7) to surface
        every AGGREGATE run with a per-row status badge + download
        affordance. COMPLETED rows have an active button; non-COMPLETED
        rows render the badge with a disabled button.

        Phase-1 limit=50 is sufficient. Pagination deferred to a follow-up
        if a single org accumulates >50 aggregate runs in one window.
        """
        stmt = (
            select(RiskAnalysisRun)
            .where(RiskAnalysisRun.organization_id == organization_id)
            .where(RiskAnalysisRun.run_type == RunType.AGGREGATE)
            .order_by(RiskAnalysisRun.created_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_completed_for_org(
        self,
        organization_id: uuid.UUID,
        *,
        limit: int = 50,
    ) -> list[RiskAnalysisRun]:
        """All COMPLETED runs for the org (SINGLE + AGGREGATE), by created_at desc.

        T8 (#351): the /reports index page now surfaces both run types so
        operators can download PDFs for single-scenario analyses alongside
        aggregate runs. Non-COMPLETED rows (QUEUED/RUNNING/FAILED/CANCELLED)
        are excluded — a PDF is only meaningful for COMPLETED runs.

        The existing list_aggregate_for_org (aggregate-only, all statuses) is
        kept for the CSV export route which needs aggregate runs regardless of
        status.
        """
        stmt = (
            select(RiskAnalysisRun)
            .where(RiskAnalysisRun.organization_id == organization_id)
            .where(RiskAnalysisRun.status == RunStatus.COMPLETED)
            .order_by(RiskAnalysisRun.created_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_recent_for_org(
        self,
        organization_id: uuid.UUID,
        *,
        limit: int = 10,
    ) -> list[RiskAnalysisRun]:
        """Last ``limit`` runs for the org by created_at desc, all statuses."""
        stmt = (
            select(RiskAnalysisRun)
            .where(RiskAnalysisRun.organization_id == organization_id)
            .order_by(RiskAnalysisRun.created_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def latest_single_per_scenario_for_org(
        self,
        organization_id: uuid.UUID,
    ) -> dict[uuid.UUID, RiskAnalysisRun]:
        """Map ``scenario_id`` to its latest COMPLETED SINGLE run for the org.

        Used by the dashboard's top-scenarios card fallback path
        (omicron-1 Q10=D1=a) — when no AGGREGATE run exists, the per-
        scenario ranking comes from each scenario's latest SINGLE run.
        AGGREGATE runs (with ``scenario_id IS NULL``) are excluded by
        the WHERE clause.
        """
        stmt = (
            select(RiskAnalysisRun)
            .where(RiskAnalysisRun.organization_id == organization_id)
            .where(RiskAnalysisRun.run_type == RunType.SINGLE)
            .where(RiskAnalysisRun.status == RunStatus.COMPLETED)
            .where(RiskAnalysisRun.scenario_id.is_not(None))
            .order_by(RiskAnalysisRun.created_at.desc())
        )
        result = await self._session.execute(stmt)
        latest_per_scenario: dict[uuid.UUID, RiskAnalysisRun] = {}
        for run in result.scalars().all():
            if run.scenario_id is not None and run.scenario_id not in latest_per_scenario:
                latest_per_scenario[run.scenario_id] = run
        return latest_per_scenario
