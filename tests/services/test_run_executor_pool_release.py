"""A1 / #508 Part 1 — the executor must hold NO pooled DB connection across any
compute-phase ``asyncio.to_thread`` (Monte-Carlo / Shapley / ensemble).

Root cause (pre-fix): ``_check_cancelled_or_continue`` (and the earlier
scenario/control loads) autobegin a transaction that is never committed, so the
pooled connection stays checked out across each subsequent to_thread. A burst of
concurrent runs therefore pins the whole 15-slot pool (size 5 + overflow 10) and
500s every other request — including login. The fix releases the connection
(``expunge_all()`` + ``close()``) before each to_thread and re-acquires it on the
next DB op, mirroring ``routes/reports.py``.

Probe design (Rel-N4): the run is seeded/observed via the ``db_session`` fixture,
which owns its OWN engine/pool (conftest.py). The executor uses
``idraa.db.get_engine()``'s pool (wired to the same SQLite FILE via
``wire_executor_to_test_db``). So ``get_engine().pool.checkedout()`` reflects ONLY
the executor's connections — the probe never self-contaminates. A ``to_thread``
spy records ``checkedout()`` at each compute call (must be 0 post-fix); a
``_check_cancelled_or_continue`` spy records ``checkedout()`` right after each bare
SELECT (the pre-release side of the delta — a connection IS held there, == 1),
proving the probe reads non-zero when appropriate.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

import idraa.services.run_executor as rex
from idraa.db import get_engine
from idraa.models.audit_log import AuditLog
from idraa.models.organization import Organization
from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType
from idraa.models.scenario import Scenario
from idraa.models.scenario_control import ScenarioControl
from idraa.models.user import User
from idraa.services.run_executor import execute_run

# mc_iterations for the background path (>= the 1000 sync threshold), per the
# design's "seed QUEUED run (mc>=1000)". Kept small so Shapley/ensemble stays fast.
_MC = 1000


# --------------------------------------------------------------------------- #
# fixtures                                                                     #
# --------------------------------------------------------------------------- #
@pytest.fixture
async def single_run(
    db_session: AsyncSession,
    seed_scenario_with_controls: Scenario,
    seed_user: User,
    seed_organization: Organization,
    wire_executor_to_test_db: None,
) -> RiskAnalysisRun:
    """A QUEUED SINGLE run with 2 mitigating controls (exercises the post-release
    Control.name/.id reads inside the weight-robustness worker thread)."""
    scenario = seed_scenario_with_controls
    run = RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=seed_organization.id,
        scenario_id=scenario.id,
        mc_iterations=_MC,
        inputs_hash="h" * 64,
        controls_snapshot=[],
        control_ids_used=[str(c.id) for c in scenario.mitigating_controls],
        status=RunStatus.QUEUED,
        run_type=RunType.SINGLE,
        created_by=seed_user.id,
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)
    return run


@pytest.fixture
async def aggregate_run(
    db_session: AsyncSession,
    seed_scenario_factory: Any,
    seed_control_factory: Any,
    seed_user: User,
    seed_organization: Organization,
    wire_executor_to_test_db: None,
) -> RiskAnalysisRun:
    """A QUEUED AGGREGATE run: 2 scenarios, 1 control each, per-scenario dict.

    Mirrors the proven ``aggregate_queued_run`` fixture in
    ``tests/unit/test_run_executor.py`` (which reaches COMPLETED)."""
    c1 = await seed_control_factory(name="pr_ctrl_x")
    c2 = await seed_control_factory(name="pr_ctrl_y")
    s1 = await seed_scenario_factory(name="pr_agg_s1")
    s2 = await seed_scenario_factory(name="pr_agg_s2")
    db_session.add(ScenarioControl(scenario_id=s1.id, control_id=c1.id))
    db_session.add(ScenarioControl(scenario_id=s2.id, control_id=c2.id))
    await db_session.commit()
    run = RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=seed_organization.id,
        scenario_id=None,
        run_type=RunType.AGGREGATE,
        mc_iterations=_MC,
        inputs_hash="h" * 64,
        controls_snapshot=[],
        control_ids_used=[str(c1.id), str(c2.id)],
        aggregate_scenario_ids=sorted([str(s1.id), str(s2.id)]),
        aggregate_control_ids_per_scenario={
            str(s1.id): [str(c1.id)],
            str(s2.id): [str(c2.id)],
        },
        status=RunStatus.QUEUED,
        created_by=seed_user.id,
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)
    return run


# --------------------------------------------------------------------------- #
# spies                                                                        #
# --------------------------------------------------------------------------- #
def _install_pool_probes(monkeypatch: pytest.MonkeyPatch) -> tuple[list[int], list[int]]:
    """Install a to_thread spy and a cancel-check spy that record the executor
    pool's checked-out count. Returns (at_to_thread, at_cancel_check) lists."""
    at_to_thread: list[int] = []
    at_cancel_check: list[int] = []

    real_to_thread = rex.asyncio.to_thread

    async def to_thread_spy(func: Any, /, *args: Any, **kwargs: Any) -> Any:
        at_to_thread.append(get_engine().pool.checkedout())
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(rex.asyncio, "to_thread", to_thread_spy)

    real_check = rex._check_cancelled_or_continue

    async def check_spy(session: AsyncSession, run_id: uuid.UUID) -> bool:
        # Record AFTER the real bare SELECT — a connection is now held (the
        # autobegun txn that the next _release_conn_for_compute frees).
        result = await real_check(session, run_id)
        at_cancel_check.append(get_engine().pool.checkedout())
        return result

    monkeypatch.setattr(rex, "_check_cancelled_or_continue", check_spy)
    return at_to_thread, at_cancel_check


# --------------------------------------------------------------------------- #
# 1. pool-probe                                                                #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_pool_released_across_every_to_thread_single(
    db_session: AsyncSession,
    single_run: RiskAnalysisRun,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    at_to_thread, at_cancel_check = _install_pool_probes(monkeypatch)

    await execute_run(single_run.id)

    await db_session.refresh(single_run)
    assert single_run.status == RunStatus.COMPLETED, single_run.error_message
    # SINGLE fires 2 compute to_threads (enhanced + weight-robustness ensemble).
    assert len(at_to_thread) >= 2, at_to_thread
    # THE CORE ASSERTION: the pool is empty at every compute to_thread.
    assert all(c == 0 for c in at_to_thread), at_to_thread
    # Pre-release control: a connection IS held right after each cancel-check.
    assert at_cancel_check and all(c == 1 for c in at_cancel_check), at_cancel_check


@pytest.mark.asyncio
async def test_pool_released_across_every_to_thread_aggregate(
    db_session: AsyncSession,
    aggregate_run: RiskAnalysisRun,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    at_to_thread, at_cancel_check = _install_pool_probes(monkeypatch)

    await execute_run(aggregate_run.id)

    await db_session.refresh(aggregate_run)
    assert aggregate_run.status == RunStatus.COMPLETED, aggregate_run.error_message
    # AGGREGATE fires 6 compute to_threads: aggregate MC, shapley (typ+mean),
    # loo (typ+mean), weight-robustness ensemble.
    assert len(at_to_thread) >= 6, at_to_thread
    assert all(c == 0 for c in at_to_thread), at_to_thread
    assert at_cancel_check and all(c == 1 for c in at_cancel_check), at_cancel_check


# --------------------------------------------------------------------------- #
# 2. cancel-visibility during a to_thread                                      #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_cancel_committed_during_to_thread_is_seen_at_next_check(
    db_session: AsyncSession,
    single_run: RiskAnalysisRun,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A competing CANCELLED committed (on the separate db_session engine) WHILE
    the executor's session is released for the first to_thread must be visible to
    the very next ``_check_cancelled_or_continue`` — the fresh SELECT after
    release/re-acquire gets a new WAL snapshot. Proven by the executor bailing at
    the cancel-check BEFORE the second compute to_thread runs.

    (Pre-fix, the held long-open txn keeps a stale snapshot, so the cancel-check
    still reads RUNNING and the executor proceeds to the ensemble to_thread; the
    #272 guarded UPDATE then catches it at write time. This test discriminates the
    two by counting to_threads, not just the terminal status.)"""
    calls: list[str] = []

    real_to_thread = rex.asyncio.to_thread

    async def to_thread_spy(func: Any, /, *args: Any, **kwargs: Any) -> Any:
        calls.append(getattr(func, "__name__", repr(func)))
        if len(calls) == 1:
            # Commit CANCELLED on the test's separate session/engine.
            await db_session.execute(
                update(RiskAnalysisRun)
                .where(RiskAnalysisRun.id == single_run.id)
                .values(status=RunStatus.CANCELLED)
            )
            await db_session.commit()
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(rex.asyncio, "to_thread", to_thread_spy)

    await execute_run(single_run.id)

    await db_session.refresh(single_run)
    # The next cancel-check saw CANCELLED and returned early — the ensemble
    # to_thread never ran, so exactly ONE compute to_thread executed.
    assert len(calls) == 1, calls
    assert single_run.status == RunStatus.CANCELLED
    assert single_run.simulation_results is None


# --------------------------------------------------------------------------- #
# degradation injection helper (tests 3 & 4)                                   #
# --------------------------------------------------------------------------- #
def _install_shapley_skip_injection(monkeypatch: pytest.MonkeyPatch, injected_sid: str) -> None:
    """Make the executor's DIRECT shapley to_thread calls report one extra
    ``(injected_sid, "error")`` skip → one deferred ``run.shapley_skipped`` row.

    Keys on ``func`` name so ONLY the top-level ``asyncio.to_thread(
    _compute_shapley_by_scenario, ...)`` calls are touched. The per-draw ensemble
    calls run SYNCHRONOUSLY inside ``_build_weight_robustness`` (never through
    to_thread), so the ensemble is left undistorted and the run still COMPLETES.
    """
    real_to_thread = rex.asyncio.to_thread

    async def to_thread_spy(func: Any, /, *args: Any, **kwargs: Any) -> Any:
        result = await real_to_thread(func, *args, **kwargs)
        if getattr(func, "__name__", "") == "_compute_shapley_by_scenario":
            raw, skipped = result
            return raw, [*list(skipped), (injected_sid, "error")]
        return result

    monkeypatch.setattr(rex.asyncio, "to_thread", to_thread_spy)


async def _count_shapley_skipped_rows(db_session: AsyncSession, run_id: uuid.UUID) -> int:
    rs = await db_session.execute(
        select(AuditLog).where(
            AuditLog.entity_id == run_id,
            AuditLog.action == "run.shapley_skipped",
        )
    )
    return len(rs.scalars().all())


# --------------------------------------------------------------------------- #
# 3. AGG degradation-audit rides the terminal txn on COMPLETED (Rel-I1)        #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_aggregate_degradation_audit_row_present_on_completed(
    db_session: AsyncSession,
    aggregate_run: RiskAnalysisRun,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rel-I1: append-only _log_terminal_audit + terminal replay must still land
    the degradation audit row atomically with the COMPLETED flip. (This is the
    row that the release-without-Enabler-1 anti-pattern would silently drop.)"""
    injected_sid = aggregate_run.aggregate_scenario_ids[0]
    _install_shapley_skip_injection(monkeypatch, injected_sid)

    await execute_run(aggregate_run.id)

    await db_session.refresh(aggregate_run)
    assert aggregate_run.status == RunStatus.COMPLETED, aggregate_run.error_message
    assert aggregate_run.simulation_results is not None
    assert await _count_shapley_skipped_rows(db_session, aggregate_run.id) == 1


# --------------------------------------------------------------------------- #
# 4. #272 lost-race: rowcount==0 → no results, no attribution audit rows       #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_lost_race_writes_no_results_and_no_attribution_audit(
    db_session: AsyncSession,
    db_url: str,
    aggregate_run: RiskAnalysisRun,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A competing CANCELLED commits AFTER the last cancel-check but BEFORE the
    guarded COMPLETED UPDATE (injected at ``split_simulation_payload``, which runs
    in exactly that window). The guarded UPDATE matches 0 rows → rollback+return
    BEFORE the terminal replay. simulation_results stays NULL and the deferred
    degradation row is NEVER written (#272 atomicity: audit never lands without
    OUR flip)."""
    import sqlite3

    injected_sid = aggregate_run.aggregate_scenario_ids[0]
    _install_shapley_skip_injection(monkeypatch, injected_sid)

    # split_simulation_payload runs synchronously ON the event loop, so the
    # competing CANCELLED is committed via a separate synchronous sqlite3
    # connection (status stored as its enum value 'cancelled'; id as .hex).
    db_file = db_url.replace("sqlite+aiosqlite:///", "")
    real_split = rex.split_simulation_payload
    flipped = {"done": False}

    def split_spy(payload: Any, *args: Any, **kwargs: Any) -> Any:
        if not flipped["done"]:
            flipped["done"] = True
            raw = sqlite3.connect(db_file)
            try:
                raw.execute("PRAGMA busy_timeout=5000")
                raw.execute(
                    "UPDATE risk_analysis_runs SET status = 'cancelled' WHERE id = ?",
                    (aggregate_run.id.hex,),
                )
                raw.commit()
            finally:
                raw.close()
        return real_split(payload, *args, **kwargs)

    monkeypatch.setattr(rex, "split_simulation_payload", split_spy)

    await execute_run(aggregate_run.id)

    await db_session.refresh(aggregate_run)
    assert aggregate_run.status == RunStatus.CANCELLED
    assert aggregate_run.simulation_results is None
    # The deferred run.shapley_skipped row rode the (rolled-back) terminal txn —
    # it must NOT have landed.
    assert await _count_shapley_skipped_rows(db_session, aggregate_run.id) == 0


# --------------------------------------------------------------------------- #
# 5. end-to-end guard (catches DetachedInstanceError / MissingGreenlet)        #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_end_to_end_single_completes_with_robustness(
    db_session: AsyncSession,
    single_run: RiskAnalysisRun,
) -> None:
    """Real SINGLE run with releases active: a post-release relationship/expired
    read would raise DetachedInstanceError (loop) or MissingGreenlet (thread) and
    FAIL the run. Assert the positive end state, which those would break."""
    await execute_run(single_run.id)

    await db_session.refresh(single_run)
    assert single_run.status == RunStatus.COMPLETED, single_run.error_message
    assert single_run.simulation_results is not None
    assert single_run.weight_robustness is not None


@pytest.mark.asyncio
async def test_end_to_end_aggregate_completes_with_robustness(
    db_session: AsyncSession,
    aggregate_run: RiskAnalysisRun,
) -> None:
    await execute_run(aggregate_run.id)

    await db_session.refresh(aggregate_run)
    assert aggregate_run.status == RunStatus.COMPLETED, aggregate_run.error_message
    assert aggregate_run.simulation_results is not None
    assert aggregate_run.weight_robustness is not None
