"""Startup orphaned-run reaper (issue #211).

A SIGKILL/OOM on the single Fly worker leaves the in-flight
``risk_analysis_runs`` row stuck at ``status='running'`` forever — there is
no in-process exception handler that can catch SIGKILL. The reaper runs on
app startup (FastAPI lifespan) and flips any RUNNING (and stale QUEUED) row
older than a threshold to FAILED with an audit row, on the single-process
invariant: a process boot means any pre-boot RUNNING/QUEUED worker is dead.

These tests cover:
  - a stale RUNNING row is reaped -> FAILED + audit row
  - a stale QUEUED row is reaped -> FAILED + audit row (prev='queued')
  - a fresh (within-threshold) QUEUED row is NOT reaped
  - a terminal row (COMPLETED/CANCELLED/FAILED) is never touched
  - cross-org isolation is NOT a thing — reaper sweeps process-wide, but each
    audit row carries the run's own organization_id
  - N>=3 mixed-status rows produce exactly N audit rows with correct per-row
    prev-status (Data-contract iteration test)
  - the FastAPI lifespan actually runs the reaper (real OOM->restart wiring)
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.config import get_settings
from idraa.models._types import now_utc
from idraa.models.audit_log import AuditLog
from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType
from idraa.services.run_reaper import reap_orphaned_runs


async def _seed_run(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    scenario_id: uuid.UUID | None,
    created_by: uuid.UUID,
    status: RunStatus,
    age_seconds: float,
) -> RiskAnalysisRun:
    """Seed a RiskAnalysisRun with created_at/started_at set ``age_seconds`` ago.

    started_at anchors RUNNING-row staleness; created_at anchors QUEUED-row
    staleness. We set both to the same past timestamp so the row's age is
    unambiguous regardless of which anchor the reaper uses.
    """
    past = now_utc() - datetime.timedelta(seconds=age_seconds)
    run = RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=org_id,
        scenario_id=scenario_id,
        mc_iterations=200,
        inputs_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        controls_snapshot=[],
        control_ids_used=[],
        status=status,
        run_type=RunType.SINGLE,
        created_by=created_by,
        started_at=past if status == RunStatus.RUNNING else None,
    )
    db.add(run)
    await db.flush()
    # TimestampMixin sets created_at on construction; override to the past.
    run.created_at = past
    await db.flush()
    return run


@pytest.mark.asyncio
async def test_reaper_flips_stale_running_to_failed_with_audit(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_user: Any,
    seed_scenario_factory: Callable[..., Awaitable[Any]],
) -> None:
    scenario = await seed_scenario_factory(name="reaper-running")
    run = await _seed_run(
        db_session,
        org_id=seed_organization.id,
        scenario_id=scenario.id,
        created_by=seed_user.id,
        status=RunStatus.RUNNING,
        age_seconds=3600,  # 1h old, well past the 300s default
    )
    run_id = run.id
    await db_session.commit()

    reaped = await reap_orphaned_runs(db_session, get_settings())
    assert reaped == 1

    refreshed = (
        await db_session.execute(
            select(RiskAnalysisRun)
            .where(RiskAnalysisRun.id == run_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert refreshed.status == RunStatus.FAILED
    assert refreshed.error_message == "orphaned (worker terminated)"
    assert refreshed.completed_at is not None
    # No valid simulation_results — same shape as the exception-path FAILED row.
    assert refreshed.simulation_results is None

    audit = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.entity_id == run_id,
                    AuditLog.action == "risk_analysis_run.reap",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(audit) == 1
    assert audit[0].changes["status"] == ["running", "failed"]
    assert audit[0].organization_id == seed_organization.id


@pytest.mark.asyncio
async def test_reaper_flips_stale_queued_to_failed_prev_queued(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_user: Any,
    seed_scenario_factory: Callable[..., Awaitable[Any]],
) -> None:
    scenario = await seed_scenario_factory(name="reaper-queued")
    run = await _seed_run(
        db_session,
        org_id=seed_organization.id,
        scenario_id=scenario.id,
        created_by=seed_user.id,
        status=RunStatus.QUEUED,
        age_seconds=3600,
    )
    run_id = run.id
    await db_session.commit()

    reaped = await reap_orphaned_runs(db_session, get_settings())
    assert reaped == 1

    refreshed = (
        await db_session.execute(
            select(RiskAnalysisRun)
            .where(RiskAnalysisRun.id == run_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert refreshed.status == RunStatus.FAILED

    audit = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.entity_id == run_id,
                    AuditLog.action == "risk_analysis_run.reap",
                )
            )
        )
        .scalars()
        .one()
    )
    # prev-status must be the row's actual prior status, not a hardcoded 'running'.
    assert audit.changes["status"] == ["queued", "failed"]


@pytest.mark.asyncio
async def test_reaper_does_not_touch_fresh_queued(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_user: Any,
    seed_scenario_factory: Callable[..., Awaitable[Any]],
) -> None:
    """A QUEUED run created seconds before boot (within the threshold) is a
    legitimately-pending run, NOT an orphan — it must survive the sweep."""
    scenario = await seed_scenario_factory(name="reaper-fresh-queued")
    run = await _seed_run(
        db_session,
        org_id=seed_organization.id,
        scenario_id=scenario.id,
        created_by=seed_user.id,
        status=RunStatus.QUEUED,
        age_seconds=5,  # well within the 300s default threshold
    )
    run_id = run.id
    await db_session.commit()

    reaped = await reap_orphaned_runs(db_session, get_settings())
    assert reaped == 0

    refreshed = (
        await db_session.execute(
            select(RiskAnalysisRun)
            .where(RiskAnalysisRun.id == run_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert refreshed.status == RunStatus.QUEUED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "terminal_status",
    [RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED],
)
async def test_reaper_never_touches_terminal_rows(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_user: Any,
    seed_scenario_factory: Callable[..., Awaitable[Any]],
    terminal_status: RunStatus,
) -> None:
    scenario = await seed_scenario_factory(name=f"reaper-terminal-{terminal_status.value}")
    run = await _seed_run(
        db_session,
        org_id=seed_organization.id,
        scenario_id=scenario.id,
        created_by=seed_user.id,
        status=terminal_status,
        age_seconds=3600,
    )
    run_id = run.id
    await db_session.commit()

    reaped = await reap_orphaned_runs(db_session, get_settings())
    assert reaped == 0

    refreshed = (
        await db_session.execute(
            select(RiskAnalysisRun)
            .where(RiskAnalysisRun.id == run_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert refreshed.status == terminal_status

    audit = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.entity_id == run_id,
                    AuditLog.action == "risk_analysis_run.reap",
                )
            )
        )
        .scalars()
        .all()
    )
    assert audit == []


@pytest.mark.asyncio
async def test_reaper_iteration_contract_mixed_statuses(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_user: Any,
    seed_scenario_factory: Callable[..., Awaitable[Any]],
) -> None:
    """Data-contract iteration test: N>=3 stale rows (mix of RUNNING + QUEUED)
    produce exactly N reap audit rows, each with the correct per-row prev-status.
    Catches a future '[0]'/'first-row' optimization or single-aggregate-audit-row
    shortcut."""
    scenario = await seed_scenario_factory(name="reaper-mixed")
    statuses = [RunStatus.RUNNING, RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.QUEUED]
    prev_by_id: dict[uuid.UUID, str] = {}
    for i, st in enumerate(statuses):
        run = await _seed_run(
            db_session,
            org_id=seed_organization.id,
            scenario_id=scenario.id,
            created_by=seed_user.id,
            status=st,
            age_seconds=3600 + i,
        )
        prev_by_id[run.id] = st.value
    await db_session.commit()

    reaped = await reap_orphaned_runs(db_session, get_settings())
    assert reaped == len(statuses)

    for run_id, prev in prev_by_id.items():
        refreshed = (
            await db_session.execute(
                select(RiskAnalysisRun)
                .where(RiskAnalysisRun.id == run_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        assert refreshed.status == RunStatus.FAILED

        audit = (
            (
                await db_session.execute(
                    select(AuditLog).where(
                        AuditLog.entity_id == run_id,
                        AuditLog.action == "risk_analysis_run.reap",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(audit) == 1, f"expected exactly one reap audit row for {run_id}"
        assert audit[0].changes["status"] == [prev, "failed"]


@pytest.mark.asyncio
async def test_lifespan_runs_reaper(
    db_url: str,
    db_session: AsyncSession,
    seed_organization: Any,
    seed_user: Any,
    seed_scenario_factory: Callable[..., Awaitable[Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The FastAPI lifespan startup must run the reaper against the live DB.

    This is the one test proving the real OOM->restart wiring: a stale RUNNING
    row seeded before app startup must be FAILED after the lifespan startup
    runs — and must still be RUNNING before startup (proving the lifespan, not
    a fixture side-effect, did the flip).

    httpx ASGITransport does NOT run Starlette lifespan events. We drive the
    real lifespan directly via ``app.router.lifespan_context(app)`` (the same
    async context manager LifespanManager wraps) — no extra dev dependency.
    """
    from idraa import config, db
    from idraa.app import create_app

    scenario = await seed_scenario_factory(name="reaper-lifespan")
    run = await _seed_run(
        db_session,
        org_id=seed_organization.id,
        scenario_id=scenario.id,
        created_by=seed_user.id,
        status=RunStatus.RUNNING,
        age_seconds=3600,
    )
    run_id = run.id
    await db_session.commit()

    # Wire the app's own (singleton) sessionmaker to the per-test DB file.
    monkeypatch.setenv("DATABASE_URL", db_url)
    config.reset_for_tests()
    db.reset_for_tests()
    try:
        app = create_app()

        # Before startup: still RUNNING (no fixture side-effect flipped it).
        before = (
            await db_session.execute(
                select(RiskAnalysisRun.status)
                .where(RiskAnalysisRun.id == run_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        assert before == RunStatus.RUNNING

        # Run real lifespan startup -> reaper sweeps -> shutdown.
        async with app.router.lifespan_context(app):
            pass

        after = (
            await db_session.execute(
                select(RiskAnalysisRun.status)
                .where(RiskAnalysisRun.id == run_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        assert after == RunStatus.FAILED
    finally:
        config.reset_for_tests()
        db.reset_for_tests()


# ---------------------------------------------------------------------------
# Issue #211 Phase 2 — periodic sweep + in-memory active-run registry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reaper_skips_registered_active_run(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_user: Any,
    seed_scenario_factory: Callable[..., Awaitable[Any]],
) -> None:
    """A stale RUNNING row whose id is in the active-run registry is OWNED by
    a live in-process task — the sweep must never flip it, regardless of age
    (this is what makes the PERIODIC sweep safe where age-only reaping would
    false-kill a legitimately slow run)."""
    from idraa.services.run_reaper import (
        active_run_ids,
        register_active_run,
        unregister_active_run,
    )

    scenario = await seed_scenario_factory(name="reaper-active-registry")
    run = await _seed_run(
        db_session,
        org_id=seed_organization.id,
        scenario_id=scenario.id,
        created_by=seed_user.id,
        status=RunStatus.RUNNING,
        age_seconds=3600,
    )
    await db_session.commit()

    register_active_run(run.id)
    try:
        assert run.id in active_run_ids()
        reaped = await reap_orphaned_runs(db_session, get_settings())
        assert reaped == 0
        await db_session.refresh(run)
        assert run.status == RunStatus.RUNNING
    finally:
        unregister_active_run(run.id)

    # Once unregistered (task gone), the same stale row IS orphaned.
    reaped = await reap_orphaned_runs(db_session, get_settings())
    assert reaped == 1
    await db_session.refresh(run)
    assert run.status == RunStatus.FAILED


@pytest.mark.asyncio
async def test_unregister_is_idempotent() -> None:
    from idraa.services.run_reaper import unregister_active_run

    unregister_active_run(uuid.uuid4())  # never registered — must not raise


@pytest.mark.asyncio
async def test_reap_once_uses_own_session(
    db_url: str,
    db_session: AsyncSession,
    seed_organization: Any,
    seed_user: Any,
    seed_scenario_factory: Callable[..., Awaitable[Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """reap_once (the periodic-loop body) acquires its own session via
    idraa.db (no request context) and sweeps — same wiring as the
    lifespan test."""
    from idraa import config, db
    from idraa.services.run_reaper import reap_once

    scenario = await seed_scenario_factory(name="reaper-periodic-once")
    run = await _seed_run(
        db_session,
        org_id=seed_organization.id,
        scenario_id=scenario.id,
        created_by=seed_user.id,
        status=RunStatus.RUNNING,
        age_seconds=3600,
    )
    run_id = run.id
    await db_session.commit()

    monkeypatch.setenv("DATABASE_URL", db_url)
    config.reset_for_tests()
    db.reset_for_tests()
    try:
        reaped = await reap_once(config.get_settings())
        assert reaped == 1
    finally:
        config.reset_for_tests()
        db.reset_for_tests()

    after = (
        await db_session.execute(
            select(RiskAnalysisRun.status)
            .where(RiskAnalysisRun.id == run_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert after == RunStatus.FAILED


@pytest.mark.asyncio
async def test_periodic_reaper_loop_cancels_cleanly() -> None:
    """The lifespan cancels the loop task on shutdown — cancellation during
    the sleep must exit the task without leaking the swallow-Exception
    clause (CancelledError is BaseException; the loop re-raises it)."""
    import asyncio

    from idraa.config import get_settings
    from idraa.services.run_reaper import periodic_reaper_loop

    task = asyncio.create_task(periodic_reaper_loop(get_settings()))
    await asyncio.sleep(0)  # let the loop enter its first sleep
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()


@pytest.mark.asyncio
async def test_periodic_reaper_loop_disabled_at_zero_interval() -> None:
    """interval=0 returns immediately (belt-and-suspenders with the lifespan
    guard that never spawns the task)."""
    from idraa.config import get_settings
    from idraa.services.run_reaper import periodic_reaper_loop

    settings = get_settings().model_copy(update={"run_reaper_interval_seconds": 0})
    await asyncio.wait_for(periodic_reaper_loop(settings), timeout=1.0)
