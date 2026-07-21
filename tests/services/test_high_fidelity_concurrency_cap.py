"""Issue #508 (PR2 final-gate Sec-I): create_and_dispatch caps the number of
concurrent in-flight (RUNNING + QUEUED) high-fidelity runs, so raising the MC
cap to 1M (each such run ~700 MB RSS) can't OOM the VM via unbounded concurrent
dispatch.

Fixtures mirror the disk-guard test (db_session / seed_scenario_with_controls /
seed_user / seed_organization). High-N dispatches QUEUE a background task rather
than executing inline, so seeding "in-flight" runs is just repeated
create_and_dispatch calls (with distinct random_seeds to avoid inputs_hash
collision). The default cap is 2.
"""

from __future__ import annotations

import shutil

import pytest
from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.errors import RunValidationError
from idraa.models.organization import Organization
from idraa.models.risk_analysis_run import RunStatus
from idraa.models.scenario import Scenario
from idraa.models.user import User
from idraa.services.runs import RunService

_HIGH_N = 300_000  # >= default high_fidelity_iterations_threshold (250k)


@pytest.fixture(autouse=True)
def _isolate_active_run_registry():
    """R-2: snapshot/clear the module-global active-run set around every test so
    a leaked registration can never bleed into another test's cap count."""
    from idraa.services import run_reaper

    saved = set(run_reaper._ACTIVE_RUNS)
    run_reaper._ACTIVE_RUNS.clear()
    try:
        yield
    finally:
        run_reaper._ACTIVE_RUNS.clear()
        run_reaper._ACTIVE_RUNS.update(saved)


def _ample_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(10**12, 1, 10**12))


async def _dispatch(service, org, scenario, user, *, mc: int, seed: int):
    return await service.create_and_dispatch(
        organization_id=org.id,
        scenario_ids=[scenario.id],
        mc_iterations_override=mc,
        random_seed=seed,
        created_by=user.id,
        background_tasks=BackgroundTasks(),
    )


@pytest.mark.asyncio
async def test_high_n_dispatch_rejected_at_cap(
    db_session: AsyncSession,
    seed_scenario_with_controls: Scenario,
    seed_user: User,
    seed_organization: Organization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the cap (2) already met by in-flight high-N runs, a 3rd is rejected."""
    _ample_disk(monkeypatch)
    service = RunService(db_session)
    # Two high-N runs QUEUE (BackgroundTasks is not executed in-test).
    r1 = await _dispatch(
        service, seed_organization, seed_scenario_with_controls, seed_user, mc=_HIGH_N, seed=1
    )
    r2 = await _dispatch(
        service, seed_organization, seed_scenario_with_controls, seed_user, mc=_HIGH_N, seed=2
    )
    assert r1.status == RunStatus.QUEUED and r2.status == RunStatus.QUEUED
    with pytest.raises(RunValidationError, match="capacity is busy"):
        await _dispatch(
            service, seed_organization, seed_scenario_with_controls, seed_user, mc=_HIGH_N, seed=3
        )


@pytest.mark.asyncio
async def test_low_n_run_never_capped(
    db_session: AsyncSession,
    seed_scenario_with_controls: Scenario,
    seed_user: User,
    seed_organization: Organization,
    monkeypatch: pytest.MonkeyPatch,
    wire_executor_to_test_db: None,
) -> None:
    """A sub-threshold run is dispatched even when the high-N cap is saturated."""
    _ample_disk(monkeypatch)
    service = RunService(db_session)
    await _dispatch(
        service, seed_organization, seed_scenario_with_controls, seed_user, mc=_HIGH_N, seed=1
    )
    await _dispatch(
        service, seed_organization, seed_scenario_with_controls, seed_user, mc=_HIGH_N, seed=2
    )
    # A low-N run (< 1000 → sync inline) is NOT counted against the high-N cap.
    low = await _dispatch(
        service, seed_organization, seed_scenario_with_controls, seed_user, mc=200, seed=3
    )
    await db_session.refresh(low)
    assert low.status == RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_terminal_high_n_runs_do_not_count(
    db_session: AsyncSession,
    seed_scenario_with_controls: Scenario,
    seed_user: User,
    seed_organization: Organization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Completed/failed high-N runs are not in-flight, so they free capacity."""
    _ample_disk(monkeypatch)
    service = RunService(db_session)
    r1 = await _dispatch(
        service, seed_organization, seed_scenario_with_controls, seed_user, mc=_HIGH_N, seed=1
    )
    r2 = await _dispatch(
        service, seed_organization, seed_scenario_with_controls, seed_user, mc=_HIGH_N, seed=2
    )
    # Flip both to terminal — they no longer occupy high-N capacity.
    r1.status = RunStatus.COMPLETED
    r2.status = RunStatus.FAILED
    await db_session.flush()
    r3 = await _dispatch(
        service, seed_organization, seed_scenario_with_controls, seed_user, mc=_HIGH_N, seed=3
    )
    assert r3.status == RunStatus.QUEUED  # capacity freed → accepted


# --- 2026-07-21 security sweep hardenings ---------------------------------

_SUB_N = 200_000  # < 250k per-run threshold, but 2 × 200k = 400k >= threshold


async def _dispatch_agg(service, org, scenario_ids, user, *, mc: int, seed: int):
    return await service.create_and_dispatch(
        organization_id=org.id,
        scenario_ids=scenario_ids,
        mc_iterations_override=mc,
        random_seed=seed,
        created_by=user.id,
        background_tasks=BackgroundTasks(),
    )


@pytest.mark.asyncio
async def test_cancelled_but_still_computing_run_counts_against_cap(
    db_session: AsyncSession,
    seed_scenario_with_controls: Scenario,
    seed_user: User,
    seed_organization: Organization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """riskflow#566: cancel() is accounting-only — the compute (and its ~700 MB)
    runs to completion. A run present in the in-process active-run registry must
    still count against the cap even after its DB status flips to CANCELLED, so a
    cancel-then-redispatch loop cannot free a memory slot early. Once the compute
    actually ends (unregistered), the slot frees."""
    from idraa.services.run_reaper import register_active_run, unregister_active_run

    _ample_disk(monkeypatch)
    service = RunService(db_session)
    r1 = await _dispatch(
        service, seed_organization, seed_scenario_with_controls, seed_user, mc=_HIGH_N, seed=1
    )
    r2 = await _dispatch(
        service, seed_organization, seed_scenario_with_controls, seed_user, mc=_HIGH_N, seed=2
    )
    assert r1.status == RunStatus.QUEUED and r2.status == RunStatus.QUEUED
    # Cancel r2 in the DB but keep its executor task "alive" in the registry.
    r2.status = RunStatus.CANCELLED
    await db_session.flush()
    register_active_run(r2.id)
    try:
        # r1 (QUEUED) + r2 (CANCELLED-but-active) = 2 = cap → 3rd rejected.
        with pytest.raises(RunValidationError, match="capacity is busy"):
            await _dispatch(
                service,
                seed_organization,
                seed_scenario_with_controls,
                seed_user,
                mc=_HIGH_N,
                seed=3,
            )
        # Compute finishes → registry releases → slot frees → 3rd accepted.
        unregister_active_run(r2.id)
        r3 = await _dispatch(
            service, seed_organization, seed_scenario_with_controls, seed_user, mc=_HIGH_N, seed=3
        )
        assert r3.status == RunStatus.QUEUED
    finally:
        unregister_active_run(r2.id)  # never leak the module-global set


@pytest.mark.asyncio
async def test_aggregate_below_per_run_threshold_still_capped_by_total_work(
    db_session: AsyncSession,
    seed_scenario_factory,
    seed_user: User,
    seed_organization: Organization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """riskflow#565 L5: an AGGREGATE holds every scenario's arrays at once, so its
    peak is N×M. Two aggregates each at mc=200k (individually BELOW the 250k
    per-run threshold, so the old M-only cap ignored them) but 2×200k=400k work
    saturate the cap; a 3rd is rejected."""
    _ample_disk(monkeypatch)
    service = RunService(db_session)
    s1 = await seed_scenario_factory(name="agg-cap-1")
    s2 = await seed_scenario_factory(name="agg-cap-2")
    ids = [s1.id, s2.id]
    a1 = await _dispatch_agg(service, seed_organization, ids, seed_user, mc=_SUB_N, seed=1)
    a2 = await _dispatch_agg(service, seed_organization, ids, seed_user, mc=_SUB_N, seed=2)
    assert a1.status == RunStatus.QUEUED and a2.status == RunStatus.QUEUED
    with pytest.raises(RunValidationError, match="capacity is busy"):
        await _dispatch_agg(service, seed_organization, ids, seed_user, mc=_SUB_N, seed=3)
    # Control: a SINGLE run at the same sub-threshold M (N=1 → 200k < 250k) is
    # NOT high-fidelity, so it dispatches even with the aggregate cap saturated.
    single = await _dispatch_agg(service, seed_organization, [s1.id], seed_user, mc=_SUB_N, seed=4)
    assert single.status == RunStatus.QUEUED


@pytest.mark.asyncio
async def test_force_delete_refused_while_run_is_live(
    db_session: AsyncSession,
    seed_scenario_with_controls: Scenario,
    seed_user: User,
    seed_organization: Organization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """riskflow#566 sibling (R-1): force-delete must NOT remove a run whose
    executor task is live — the compute keeps its ~700MB and a gone row is
    invisible to the cap. A RUNNING row absent from the registry (true orphan)
    stays force-deletable."""
    from idraa.errors import RunBusyError
    from idraa.services.run_reaper import register_active_run, unregister_active_run

    _ample_disk(monkeypatch)
    service = RunService(db_session)
    r = await _dispatch(
        service, seed_organization, seed_scenario_with_controls, seed_user, mc=_HIGH_N, seed=1
    )
    r.status = RunStatus.RUNNING
    await db_session.flush()
    register_active_run(r.id)
    try:
        with pytest.raises(RunBusyError, match="still computing"):
            await service.delete_run(
                r.id, org_id=seed_organization.id, user_id=seed_user.id, force=True
            )
    finally:
        unregister_active_run(r.id)
    # true orphan (RUNNING in DB, not in registry) → force-delete succeeds
    await service.delete_run(r.id, org_id=seed_organization.id, user_id=seed_user.id, force=True)
    from idraa.models.risk_analysis_run import RiskAnalysisRun

    db_session.expire_all()
    assert await db_session.get(RiskAnalysisRun, r.id) is None
