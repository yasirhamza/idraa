"""Perf-regression guard: the hot read paths must NEVER touch run_samples.

The whole point of #294 was that the OLD code loaded + json-parsed every run's
~38MB ``simulation_results`` blob on the dashboard / reports-list / run-history
list paths (measured 3.67s). Those heavy per-iteration arrays now live in a
separate 1:1 ``run_samples`` table (#297), and the list paths read summary-only.

A future eager-load (``joinedload`` / ``selectinload`` of a ``samples``
relationship, or a naive join) would silently reintroduce the regression. This
guard attaches a ``before_cursor_execute`` listener to the session's underlying
sync engine, captures every SQL statement emitted while each REAL hot-path
service/repo method runs, and asserts none reference ``run_samples``.

Hot paths covered (the actual callables behind each page):
  - Dashboard   (GET /)            -> services.dashboard.build_dashboard
                                       (latest_aggregate_for_org +
                                        list_recent_for_org +
                                        latest_single_per_scenario_for_org)
  - Reports list (GET /reports)    -> RunRepo.list_aggregate_for_org
  - Run history (GET .../runs)     -> RunService.list_history
                                       (list_for_scenario + count_for_scenario)

A completed run WITH a ``run_samples`` row is seeded first, so an accidental
eager-load WOULD show up in the captured SQL (verified by temporarily adding a
``selectinload(RiskAnalysisRun.samples)`` to a hot query — the guard failed,
then reverted).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.organization import Organization
from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus
from idraa.models.run_samples import RunSamples
from idraa.repositories.run_repo import RunRepo
from idraa.services.dashboard import build_dashboard
from idraa.services.runs import RunService


def _capture(bind: Any) -> list[str]:
    """Attach a SQL-capture listener to ``bind``; return the growing hit list.

    ``bind`` is the synchronous Engine underlying the async session
    (``AsyncSession.bind`` is an ``AsyncEngine``; its ``.sync_engine`` is what
    SQLAlchemy's ``before_cursor_execute`` event fires on).
    """
    hits: list[str] = []

    @event.listens_for(bind, "before_cursor_execute")
    def _cap(conn: Any, cursor: Any, statement: str, *a: Any) -> None:
        if "run_samples" in statement.lower():
            hits.append(statement)

    return hits


@pytest.mark.asyncio
async def test_hot_paths_do_not_query_run_samples(
    db_session: AsyncSession,
    seed_organization: Organization,
    seed_scenario_with_controls: Any,
    seed_run_factory: Callable[..., Any],
) -> None:
    # Seed a COMPLETED SINGLE run that HAS a run_samples row, so an accidental
    # eager-load of the samples relationship WOULD emit a run_samples query.
    run: RiskAnalysisRun = await seed_run_factory(
        status=RunStatus.COMPLETED,
        simulation_results={"base_risk": {"ale_mean": 1.0}},
    )
    db_session.add(
        RunSamples(
            run_id=run.id,
            organization_id=seed_organization.id,
            arrays={"base_risk": [1, 2, 3]},
        )
    )
    await db_session.commit()

    # Attach the capture listener to the sync engine behind the async session.
    bind = db_session.bind
    assert bind is not None, "db_session has no bind"
    sync_engine = bind.sync_engine
    hits = _capture(sync_engine)

    org_id: uuid.UUID = seed_organization.id
    scenario_id: uuid.UUID = seed_scenario_with_controls.id

    # --- Dashboard hot path (GET /) ---
    await build_dashboard(db_session, seed_organization)

    # --- Reports-list hot path (GET /reports) ---
    await RunRepo(db_session).list_aggregate_for_org(org_id, limit=50)

    # --- Run-history hot path (GET /scenarios/{id}/runs) ---
    await RunService(db_session).list_history(
        organization_id=org_id,
        scenario_id=scenario_id,
        page=1,
        page_size=20,
    )

    assert hits == [], f"hot path queried run_samples: {hits}"


@pytest.mark.asyncio
async def test_guard_detects_a_run_samples_query(
    db_session: AsyncSession,
    seed_organization: Organization,
    seed_run_factory: Callable[..., Any],
) -> None:
    """Meta-test: the capture mechanism actually fires on a run_samples query.

    Guards against the perf guard silently passing because the listener never
    sees any SQL (e.g. a future async/sync-engine accessor rename). If THIS
    test stops detecting an explicit run_samples read, the guard above is
    no longer load-bearing.
    """
    run: RiskAnalysisRun = await seed_run_factory(
        status=RunStatus.COMPLETED,
        simulation_results={"base_risk": {"ale_mean": 1.0}},
    )
    db_session.add(
        RunSamples(
            run_id=run.id,
            organization_id=seed_organization.id,
            arrays={"base_risk": [1, 2, 3]},
        )
    )
    await db_session.commit()

    assert db_session.bind is not None, "db_session has no bind"
    sync_engine = db_session.bind.sync_engine
    hits = _capture(sync_engine)

    # An explicit samples read (the load_samples cold path) MUST be captured.
    loaded = await RunService(db_session).load_samples(org_id=seed_organization.id, run_id=run.id)
    assert loaded is not None
    assert hits, "capture listener failed to observe an explicit run_samples query"
