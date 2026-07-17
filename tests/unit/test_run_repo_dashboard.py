"""RunRepo dashboard-method tests (omicron-1 F5-F7).

Shared _make_run helper builds COMPLETED-able RiskAnalysisRun rows
with ALL required NOT-NULL fields populated (inputs_hash,
controls_snapshot, control_ids_used). Single helper used by all three
F-tasks here so the schema requirements are stated once.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from typing import Any

import pytest
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


# ---- F5: latest_aggregate_for_org ----


async def test_latest_aggregate_for_org_returns_most_recent_completed(
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

    got = await repo.latest_aggregate_for_org(organization.id)
    assert got is not None
    assert got.name == "newer"


async def test_latest_aggregate_for_org_skips_single(
    db_session: AsyncSession,
    organization: Organization,
    seed_scenario_factory: Any,
) -> None:
    repo = RunRepo(db_session)
    # FK constraint on scenario_id requires a real scenario row.
    scenario = await seed_scenario_factory(name="single_run_scenario")
    db_session.add(
        _make_run(
            org_id=organization.id,
            run_type=RunType.SINGLE,
            status=RunStatus.COMPLETED,
            name="single",
            scenario_id=scenario.id,
        )
    )
    await db_session.flush()
    assert await repo.latest_aggregate_for_org(organization.id) is None


@pytest.mark.parametrize(
    "status",
    [
        RunStatus.QUEUED,
        RunStatus.RUNNING,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    ],
)
async def test_latest_aggregate_for_org_skips_non_completed(
    db_session: AsyncSession,
    organization: Organization,
    status: RunStatus,
) -> None:
    repo = RunRepo(db_session)
    s1, s2 = uuid.uuid4(), uuid.uuid4()
    db_session.add(
        _make_run(
            org_id=organization.id,
            run_type=RunType.AGGREGATE,
            status=status,
            name=f"agg_{status.value}",
            aggregate_scenario_ids=[str(s1), str(s2)],
        )
    )
    await db_session.flush()
    assert await repo.latest_aggregate_for_org(organization.id) is None


async def test_latest_aggregate_for_org_returns_none_when_empty(
    db_session: AsyncSession,
    organization: Organization,
) -> None:
    repo = RunRepo(db_session)
    assert await repo.latest_aggregate_for_org(organization.id) is None


async def test_latest_aggregate_for_org_org_scoped(
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
            name="other_org_agg",
            aggregate_scenario_ids=[str(s1), str(s2)],
        )
    )
    await db_session.flush()
    assert await repo.latest_aggregate_for_org(organization.id) is None


# ---- F6: list_recent_for_org ----


async def test_list_recent_for_org_returns_last_n_by_created_at(
    db_session: AsyncSession,
    organization: Organization,
    seed_scenario_factory: Any,
) -> None:
    repo = RunRepo(db_session)
    sc = await seed_scenario_factory(name="F6-recent")
    runs = [
        _make_run(
            org_id=organization.id,
            run_type=RunType.SINGLE,
            status=RunStatus.COMPLETED,
            name=f"run_{i}",
            scenario_id=sc.id,
            created_at=dt.datetime(2026, 5, 1 + i, tzinfo=dt.UTC),
        )
        for i in range(15)
    ]
    db_session.add_all(runs)
    await db_session.flush()

    got = await repo.list_recent_for_org(organization.id, limit=10)
    assert len(got) == 10
    assert got[0].name == "run_14"  # newest
    assert got[9].name == "run_5"


async def test_list_recent_for_org_includes_all_five_statuses(
    db_session: AsyncSession,
    organization: Organization,
    seed_scenario_factory: Any,
) -> None:
    repo = RunRepo(db_session)
    sc = await seed_scenario_factory(name="F6-allstatuses")
    statuses = [
        RunStatus.QUEUED,
        RunStatus.RUNNING,
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    ]
    for i, st in enumerate(statuses):
        db_session.add(
            _make_run(
                org_id=organization.id,
                run_type=RunType.SINGLE,
                status=st,
                name=f"run_{st.value}",
                scenario_id=sc.id,
                created_at=dt.datetime(2026, 5, 1 + i, tzinfo=dt.UTC),
            )
        )
    await db_session.flush()

    got = await repo.list_recent_for_org(organization.id, limit=10)
    assert {r.status for r in got} == set(statuses)


async def test_list_recent_for_org_org_scoped(
    db_session: AsyncSession,
    organization: Organization,
    seed_organization_factory: Any,
    seed_scenario_factory: Any,
) -> None:
    from tests.integration._dashboard_fixtures import _make_scenario

    other_org = await seed_organization_factory(name="OtherInc")
    other_scenario = _make_scenario(org_id=other_org.id, name="other-scenario")
    db_session.add(other_scenario)
    own_scenario = await seed_scenario_factory(name="own-scenario")
    repo = RunRepo(db_session)
    db_session.add(
        _make_run(
            org_id=other_org.id,
            run_type=RunType.SINGLE,
            status=RunStatus.COMPLETED,
            name="other_org_run",
            scenario_id=other_scenario.id,
        )
    )
    db_session.add(
        _make_run(
            org_id=organization.id,
            run_type=RunType.SINGLE,
            status=RunStatus.COMPLETED,
            name="our_run",
            scenario_id=own_scenario.id,
        )
    )
    await db_session.flush()

    got = await repo.list_recent_for_org(organization.id, limit=10)
    assert {r.name for r in got} == {"our_run"}


# ---- F7: latest_single_per_scenario_for_org ----


async def test_latest_single_per_scenario_one_per_scenario(
    db_session: AsyncSession,
    organization: Organization,
    seed_scenario_factory: Any,
) -> None:
    repo = RunRepo(db_session)
    sc1 = await seed_scenario_factory(name="F7-s1")
    sc2 = await seed_scenario_factory(name="F7-s2")
    db_session.add_all(
        [
            _make_run(
                org_id=organization.id,
                run_type=RunType.SINGLE,
                status=RunStatus.COMPLETED,
                name="s1_old",
                scenario_id=sc1.id,
                created_at=dt.datetime(2026, 5, 1, tzinfo=dt.UTC),
            ),
            _make_run(
                org_id=organization.id,
                run_type=RunType.SINGLE,
                status=RunStatus.COMPLETED,
                name="s1_new",
                scenario_id=sc1.id,
                created_at=dt.datetime(2026, 5, 5, tzinfo=dt.UTC),
            ),
            _make_run(
                org_id=organization.id,
                run_type=RunType.SINGLE,
                status=RunStatus.COMPLETED,
                name="s2_only",
                scenario_id=sc2.id,
                created_at=dt.datetime(2026, 5, 3, tzinfo=dt.UTC),
            ),
        ]
    )
    await db_session.flush()

    got = await repo.latest_single_per_scenario_for_org(organization.id)
    assert set(got.keys()) == {sc1.id, sc2.id}
    assert got[sc1.id].name == "s1_new"
    assert got[sc2.id].name == "s2_only"


async def test_latest_single_per_scenario_skips_aggregate(
    db_session: AsyncSession,
    organization: Organization,
    seed_scenario_factory: Any,
) -> None:
    repo = RunRepo(db_session)
    sc1 = await seed_scenario_factory(name="F7-skip-agg-s1")
    sc2 = await seed_scenario_factory(name="F7-skip-agg-s2")
    db_session.add_all(
        [
            _make_run(
                org_id=organization.id,
                run_type=RunType.AGGREGATE,
                status=RunStatus.COMPLETED,
                name="agg",
                aggregate_scenario_ids=[str(sc1.id), str(sc2.id)],
            ),
            _make_run(
                org_id=organization.id,
                run_type=RunType.SINGLE,
                status=RunStatus.COMPLETED,
                name="single",
                scenario_id=sc1.id,
            ),
        ]
    )
    await db_session.flush()

    got = await repo.latest_single_per_scenario_for_org(organization.id)
    assert set(got.keys()) == {sc1.id}
    assert got[sc1.id].name == "single"


async def test_latest_single_per_scenario_skips_non_completed(
    db_session: AsyncSession,
    organization: Organization,
    seed_scenario_factory: Any,
) -> None:
    repo = RunRepo(db_session)
    sc = await seed_scenario_factory(name="F7-noncompleted")
    db_session.add(
        _make_run(
            org_id=organization.id,
            run_type=RunType.SINGLE,
            status=RunStatus.RUNNING,
            name="running",
            scenario_id=sc.id,
        )
    )
    await db_session.flush()
    assert await repo.latest_single_per_scenario_for_org(organization.id) == {}


async def test_latest_single_per_scenario_org_scoped(
    db_session: AsyncSession,
    organization: Organization,
    seed_organization_factory: Any,
) -> None:
    from tests.integration._dashboard_fixtures import _make_scenario

    other_org = await seed_organization_factory(name="OtherInc-F7")
    other_sc = _make_scenario(org_id=other_org.id, name="other-scenario-F7")
    db_session.add(other_sc)
    await db_session.flush()
    repo = RunRepo(db_session)
    db_session.add(
        _make_run(
            org_id=other_org.id,
            run_type=RunType.SINGLE,
            status=RunStatus.COMPLETED,
            name="other",
            scenario_id=other_sc.id,
        )
    )
    await db_session.flush()
    assert await repo.latest_single_per_scenario_for_org(organization.id) == {}
