"""Issue #508 (A1 / pool-exhaustion, Part 2): create_and_dispatch caps the number
of concurrent in-flight STANDARD (sub-high-fidelity) runs, applied ONLY on the
BACKGROUND dispatch path (mc_iterations >= _SYNC_THRESHOLD). Mirrors
test_high_fidelity_concurrency_cap.py.

Buckets are DISJOINT by effective work (N x M) vs high_fidelity_iterations_threshold:
- standard  = N x M  <  threshold
- high-fidelity = N x M >= threshold
so a run is in exactly one bucket (no double-count).

Dispatch paths:
- background = mc_iterations >= _SYNC_THRESHOLD (1000) → QUEUED
- inline     = mc_iterations <  _SYNC_THRESHOLD        → executes synchronously

Arch-I3: a sub-1000-iter INLINE run is UNGATED. It is sub-second/few-MB and must
not 503 when the standard cap is saturated, and its dispatch must keep the
unindexed candidate count query OFF the latency-sensitive inline path.
"""

from __future__ import annotations

import shutil

import pytest
from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from idraa import config
from idraa.errors import RunValidationError
from idraa.models.organization import Organization
from idraa.models.risk_analysis_run import RunStatus
from idraa.models.scenario import Scenario
from idraa.models.user import User
from idraa.services.runs import RunService

_STD_MC = 5_000  # < 250k threshold (standard) AND >= 1000 (background) → QUEUED
_HIGH_N = 300_000  # >= default high_fidelity_iterations_threshold (250k)
_INLINE_MC = 200  # < 1000 → sync inline, UNGATED


@pytest.fixture(autouse=True)
def _isolate_active_run_registry():
    """Snapshot/clear the module-global active-run set around every test so a
    leaked registration can never bleed into another test's cap count."""
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


def _set_standard_cap(monkeypatch: pytest.MonkeyPatch, n: int) -> None:
    """Lower the standard cap on the process-wide Settings singleton so a burst
    test saturates it with a handful of QUEUED rows. monkeypatch restores the
    original value after the test."""
    settings = config.get_settings()
    monkeypatch.setattr(settings, "max_concurrent_standard_runs", n)


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
async def test_standard_burst_rejected_at_cap(
    db_session: AsyncSession,
    seed_scenario_with_controls: Scenario,
    seed_user: User,
    seed_organization: Organization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the standard cap already met by in-flight standard BACKGROUND runs,
    the next standard background run is rejected."""
    _ample_disk(monkeypatch)
    _set_standard_cap(monkeypatch, 2)
    service = RunService(db_session)
    r1 = await _dispatch(
        service, seed_organization, seed_scenario_with_controls, seed_user, mc=_STD_MC, seed=1
    )
    r2 = await _dispatch(
        service, seed_organization, seed_scenario_with_controls, seed_user, mc=_STD_MC, seed=2
    )
    assert r1.status == RunStatus.QUEUED and r2.status == RunStatus.QUEUED
    with pytest.raises(RunValidationError, match="capacity is busy"):
        await _dispatch(
            service, seed_organization, seed_scenario_with_controls, seed_user, mc=_STD_MC, seed=3
        )


@pytest.mark.asyncio
async def test_inline_run_not_gated_when_standard_cap_saturated(
    db_session: AsyncSession,
    seed_scenario_with_controls: Scenario,
    seed_user: User,
    seed_organization: Organization,
    monkeypatch: pytest.MonkeyPatch,
    wire_executor_to_test_db: None,
) -> None:
    """Arch-I3: saturate the standard cap with background runs, then a sub-1000
    iter INLINE run STILL succeeds — proof the inline path is ungated."""
    _ample_disk(monkeypatch)
    _set_standard_cap(monkeypatch, 2)
    service = RunService(db_session)
    await _dispatch(
        service, seed_organization, seed_scenario_with_controls, seed_user, mc=_STD_MC, seed=1
    )
    await _dispatch(
        service, seed_organization, seed_scenario_with_controls, seed_user, mc=_STD_MC, seed=2
    )
    # Cap is saturated: a 3rd BACKGROUND standard run is rejected...
    with pytest.raises(RunValidationError, match="capacity is busy"):
        await _dispatch(
            service, seed_organization, seed_scenario_with_controls, seed_user, mc=_STD_MC, seed=3
        )
    # ...but a sub-1000-iter INLINE run is UNGATED and completes.
    inline = await _dispatch(
        service, seed_organization, seed_scenario_with_controls, seed_user, mc=_INLINE_MC, seed=4
    )
    await db_session.refresh(inline)
    assert inline.status == RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_standard_saturation_still_allows_high_fidelity(
    db_session: AsyncSession,
    seed_scenario_with_controls: Scenario,
    seed_user: User,
    seed_organization: Organization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disjointness (one way): a saturated STANDARD cap does not block a
    high-fidelity dispatch — the high-fidelity run lands in its own bucket and is
    accepted until the SEPARATE high-fidelity cap (default 2)."""
    _ample_disk(monkeypatch)
    _set_standard_cap(monkeypatch, 2)
    service = RunService(db_session)
    r1 = await _dispatch(
        service, seed_organization, seed_scenario_with_controls, seed_user, mc=_STD_MC, seed=1
    )
    r2 = await _dispatch(
        service, seed_organization, seed_scenario_with_controls, seed_user, mc=_STD_MC, seed=2
    )
    assert r1.status == RunStatus.QUEUED and r2.status == RunStatus.QUEUED
    # Standard bucket full, high-fidelity bucket empty → high-N dispatch accepted.
    hi = await _dispatch(
        service, seed_organization, seed_scenario_with_controls, seed_user, mc=_HIGH_N, seed=3
    )
    assert hi.status == RunStatus.QUEUED


@pytest.mark.asyncio
async def test_high_fidelity_saturation_still_allows_standard(
    db_session: AsyncSession,
    seed_scenario_with_controls: Scenario,
    seed_user: User,
    seed_organization: Organization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disjointness (vice versa): a saturated HIGH-FIDELITY cap (default 2) does
    not block a standard background dispatch — standard runs are counted in the
    OTHER bucket."""
    _ample_disk(monkeypatch)
    _set_standard_cap(monkeypatch, 2)
    service = RunService(db_session)
    h1 = await _dispatch(
        service, seed_organization, seed_scenario_with_controls, seed_user, mc=_HIGH_N, seed=1
    )
    h2 = await _dispatch(
        service, seed_organization, seed_scenario_with_controls, seed_user, mc=_HIGH_N, seed=2
    )
    assert h1.status == RunStatus.QUEUED and h2.status == RunStatus.QUEUED
    # High-fidelity bucket full (a 3rd high-N would reject), but standard bucket
    # empty → standard background dispatch accepted.
    std = await _dispatch(
        service, seed_organization, seed_scenario_with_controls, seed_user, mc=_STD_MC, seed=3
    )
    assert std.status == RunStatus.QUEUED


@pytest.mark.asyncio
async def test_high_fidelity_runs_not_charged_to_standard_bucket(
    db_session: AsyncSession,
    seed_scenario_with_controls: Scenario,
    seed_user: User,
    seed_organization: Organization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Control: an in-flight high-fidelity run is NOT charged to the standard
    bucket. With standard cap = 1 and one high-N run in flight, a standard
    background run is STILL accepted (proving high-N isn't in the standard
    count); the NEXT standard run is then rejected by the standard cap of 1."""
    _ample_disk(monkeypatch)
    _set_standard_cap(monkeypatch, 1)
    service = RunService(db_session)
    hi = await _dispatch(
        service, seed_organization, seed_scenario_with_controls, seed_user, mc=_HIGH_N, seed=1
    )
    assert hi.status == RunStatus.QUEUED
    # inflight_standard == 0 (the high-N run is in the OTHER bucket) → accepted.
    std = await _dispatch(
        service, seed_organization, seed_scenario_with_controls, seed_user, mc=_STD_MC, seed=2
    )
    assert std.status == RunStatus.QUEUED
    # Now inflight_standard == 1 == cap → the next standard run is rejected.
    with pytest.raises(RunValidationError, match="capacity is busy"):
        await _dispatch(
            service, seed_organization, seed_scenario_with_controls, seed_user, mc=_STD_MC, seed=3
        )


@pytest.mark.asyncio
async def test_cancelled_but_registered_standard_run_counts_against_cap(
    db_session: AsyncSession,
    seed_scenario_with_controls: Scenario,
    seed_user: User,
    seed_organization: Organization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Registry-counting parity: a standard run whose DB status flipped to
    CANCELLED but whose executor task is still alive (in the active-run registry)
    STILL counts against the standard cap — its compute (and memory + pooled
    connection) outlives the CANCELLED status. Unregistering frees the slot."""
    from idraa.services.run_reaper import register_active_run, unregister_active_run

    _ample_disk(monkeypatch)
    _set_standard_cap(monkeypatch, 2)
    service = RunService(db_session)
    r1 = await _dispatch(
        service, seed_organization, seed_scenario_with_controls, seed_user, mc=_STD_MC, seed=1
    )
    r2 = await _dispatch(
        service, seed_organization, seed_scenario_with_controls, seed_user, mc=_STD_MC, seed=2
    )
    assert r1.status == RunStatus.QUEUED and r2.status == RunStatus.QUEUED
    # Cancel r2 in the DB but keep its executor task "alive" in the registry.
    r2.status = RunStatus.CANCELLED
    await db_session.flush()
    register_active_run(r2.id)
    try:
        # r1 (QUEUED) + r2 (CANCELLED-but-active) = 2 = cap → next standard rejected.
        with pytest.raises(RunValidationError, match="capacity is busy"):
            await _dispatch(
                service,
                seed_organization,
                seed_scenario_with_controls,
                seed_user,
                mc=_STD_MC,
                seed=3,
            )
        # Compute finishes → registry releases → slot frees → next accepted.
        unregister_active_run(r2.id)
        r3 = await _dispatch(
            service, seed_organization, seed_scenario_with_controls, seed_user, mc=_STD_MC, seed=3
        )
        assert r3.status == RunStatus.QUEUED
    finally:
        unregister_active_run(r2.id)  # never leak the module-global set
