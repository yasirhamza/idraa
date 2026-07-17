"""RunRepo reports-method tests (omicron-2 F4).

Mirrors tests/unit/test_run_repo_dashboard.py shape (PR omicron-1 F5-F7).
The shared _make_run helper builds RiskAnalysisRun rows with all
required NOT-NULL fields populated.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.organization import Organization
from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType
from idraa.repositories.run_repo import RunRepo


def _make_run(
    *,
    org_id: uuid.UUID,
    run_type: RunType,
    status: RunStatus,
    name: str | None = None,
    scenario_id: uuid.UUID | None = None,
    aggregate_scenario_ids: list[str] | None = None,
    created_at: dt.datetime | None = None,
    simulation_results: dict[str, Any] | None = None,
) -> RiskAnalysisRun:
    return RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=org_id,
        name=name,
        run_type=run_type,
        status=status,
        scenario_id=scenario_id,
        aggregate_scenario_ids=aggregate_scenario_ids,
        mc_iterations=1000,
        inputs_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        controls_snapshot=[],
        control_ids_used=[],
        created_at=created_at or dt.datetime.now(dt.UTC),
        simulation_results=simulation_results,
    )


async def test_list_aggregate_for_org_empty_returns_empty_list(
    db_session: AsyncSession, organization: Organization
) -> None:
    repo = RunRepo(db_session)
    assert await repo.list_aggregate_for_org(organization.id) == []


async def test_list_aggregate_for_org_returns_all_five_statuses(
    db_session: AsyncSession, organization: Organization
) -> None:
    """Q7=A: index page shows all statuses; status badge carries meaning."""
    repo = RunRepo(db_session)
    s1, s2 = uuid.uuid4(), uuid.uuid4()
    statuses = [
        RunStatus.QUEUED,
        RunStatus.RUNNING,
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    ]
    for status in statuses:
        db_session.add(
            _make_run(
                org_id=organization.id,
                run_type=RunType.AGGREGATE,
                status=status,
                aggregate_scenario_ids=[str(s1), str(s2)],
                name=f"agg_{status.value}",
            )
        )
    await db_session.flush()

    rows = await repo.list_aggregate_for_org(organization.id)
    got_statuses = {r.status for r in rows}
    assert got_statuses == set(statuses)
    assert len(rows) == 5


async def test_list_aggregate_for_org_excludes_single(
    db_session: AsyncSession,
    organization: Organization,
    seed_scenario_factory: Any,
) -> None:
    repo = RunRepo(db_session)
    s1, s2 = uuid.uuid4(), uuid.uuid4()
    # FK constraint on scenario_id requires a real scenario row.
    scenario = await seed_scenario_factory(name="excludes_single_scenario")
    db_session.add(
        _make_run(
            org_id=organization.id,
            run_type=RunType.SINGLE,
            status=RunStatus.COMPLETED,
            scenario_id=scenario.id,
            name="single",
        )
    )
    db_session.add(
        _make_run(
            org_id=organization.id,
            run_type=RunType.AGGREGATE,
            status=RunStatus.COMPLETED,
            aggregate_scenario_ids=[str(s1), str(s2)],
            name="agg",
        )
    )
    await db_session.flush()

    rows = await repo.list_aggregate_for_org(organization.id)
    assert len(rows) == 1
    assert rows[0].name == "agg"


async def test_list_aggregate_for_org_orders_created_at_desc(
    db_session: AsyncSession, organization: Organization
) -> None:
    repo = RunRepo(db_session)
    s1, s2 = uuid.uuid4(), uuid.uuid4()
    older = _make_run(
        org_id=organization.id,
        run_type=RunType.AGGREGATE,
        status=RunStatus.COMPLETED,
        name="older",
        aggregate_scenario_ids=[str(s1), str(s2)],
        created_at=dt.datetime(2026, 5, 1, tzinfo=dt.UTC),
    )
    newer = _make_run(
        org_id=organization.id,
        run_type=RunType.AGGREGATE,
        status=RunStatus.COMPLETED,
        name="newer",
        aggregate_scenario_ids=[str(s1), str(s2)],
        created_at=dt.datetime(2026, 5, 5, tzinfo=dt.UTC),
    )
    db_session.add_all([older, newer])
    await db_session.flush()

    rows = await repo.list_aggregate_for_org(organization.id)
    assert [r.name for r in rows] == ["newer", "older"]


async def test_list_aggregate_for_org_respects_limit(
    db_session: AsyncSession, organization: Organization
) -> None:
    repo = RunRepo(db_session)
    s1, s2 = uuid.uuid4(), uuid.uuid4()
    for i in range(7):
        db_session.add(
            _make_run(
                org_id=organization.id,
                run_type=RunType.AGGREGATE,
                status=RunStatus.COMPLETED,
                aggregate_scenario_ids=[str(s1), str(s2)],
                name=f"agg_{i}",
                created_at=dt.datetime(2026, 5, 1, 12, i, tzinfo=dt.UTC),
            )
        )
    await db_session.flush()

    rows = await repo.list_aggregate_for_org(organization.id, limit=3)
    assert len(rows) == 3
    # Newest 3 by created_at desc -> agg_6, agg_5, agg_4
    assert [r.name for r in rows] == ["agg_6", "agg_5", "agg_4"]


async def test_list_aggregate_for_org_idor(
    db_session: AsyncSession,
    organization: Organization,
    seed_organization_factory: Any,
) -> None:
    other_org = await seed_organization_factory(name="OtherInc")
    repo = RunRepo(db_session)
    s1, s2 = uuid.uuid4(), uuid.uuid4()
    db_session.add(
        _make_run(
            org_id=other_org.id,
            run_type=RunType.AGGREGATE,
            status=RunStatus.COMPLETED,
            name="canary",
            aggregate_scenario_ids=[str(s1), str(s2)],
        )
    )
    await db_session.flush()

    rows = await repo.list_aggregate_for_org(organization.id)
    assert rows == []
