"""#299: attribution (Shapley + leave-one-out + weight ensemble) runs one run at a time.

Attribution memory does not scale with iterations, and the admission gate budgets only
iterations x scenarios, so N concurrent attributions multiplied the per-run ceiling by
N. `_attribution_slot` (an `asyncio.Lock` per event loop, see its block comment in
services/run_executor.py) makes the ceiling per-process. These tests pin that two
concurrent runs never have attribution calls in flight together, that a run's
canonical phase is not interleaved with another's, that the slot is released when an
attribution raises, and that a queued run holds no pooled DB connection while it waits.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import idraa.services.run_executor as rex
from idraa.db import get_engine
from idraa.models.organization import Organization
from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType
from idraa.models.scenario import Scenario
from idraa.models.scenario_control import ScenarioControl
from idraa.models.user import User

_MC = 1000  # background-path iterations, small enough to keep attribution quick
_HOLD_SECONDS = 0.05  # widens the overlap window each wrapped call


class _Probe:
    """Records, from the worker threads, which run's attribution call is in flight."""

    def __init__(self) -> None:
        self._mutex = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.events: list[tuple[int, str]] = []  # (owner = id(calculator), call name)

    def wrap(self, name: str, real: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            calculator = args[0] if args else kwargs["calculator"]
            with self._mutex:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
                self.events.append((id(calculator), name))
            try:
                time.sleep(_HOLD_SECONDS)
                return real(*args, **kwargs)
            finally:
                with self._mutex:
                    self.active -= 1

        return wrapper

    def owners(self, *names: str) -> list[int]:
        return [owner for owner, name in self.events if name in names]


def _install(monkeypatch: pytest.MonkeyPatch) -> _Probe:
    probe = _Probe()
    for attr, name in (
        ("_compute_shapley_by_scenario", "shapley"),
        ("_compute_loo_by_scenario", "loo"),
        ("_build_weight_robustness", "ensemble"),
    ):
        monkeypatch.setattr(rex, attr, probe.wrap(name, getattr(rex, attr)))
    return probe


def _grouped(owners: list[int]) -> list[int]:
    """Collapse consecutive duplicates: [A, A, B, B, A] -> [A, B, A]."""
    return [o for i, o in enumerate(owners) if i == 0 or o != owners[i - 1]]


async def _aggregate_run(
    db: AsyncSession,
    seed_scenario_factory: Any,
    seed_control_factory: Any,
    user: User,
    org: Organization,
    tag: str,
) -> RiskAnalysisRun:
    c1 = await seed_control_factory(name=f"slot_{tag}_x")
    c2 = await seed_control_factory(name=f"slot_{tag}_y")
    s1 = await seed_scenario_factory(name=f"slot_{tag}_s1")
    s2 = await seed_scenario_factory(name=f"slot_{tag}_s2")
    db.add_all(
        [
            ScenarioControl(scenario_id=s1.id, control_id=c1.id),
            ScenarioControl(scenario_id=s2.id, control_id=c2.id),
        ]
    )
    await db.commit()
    run = RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=org.id,
        scenario_id=None,
        run_type=RunType.AGGREGATE,
        mc_iterations=_MC,
        inputs_hash="h" * 64,
        controls_snapshot=[],
        control_ids_used=[str(c1.id), str(c2.id)],
        aggregate_scenario_ids=sorted([str(s1.id), str(s2.id)]),
        aggregate_control_ids_per_scenario={str(s1.id): [str(c1.id)], str(s2.id): [str(c2.id)]},
        status=RunStatus.QUEUED,
        created_by=user.id,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def _single_run(
    db: AsyncSession, scenario: Scenario, user: User, org: Organization
) -> RiskAnalysisRun:
    run = RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=org.id,
        scenario_id=scenario.id,
        mc_iterations=_MC,
        inputs_hash="h" * 64,
        controls_snapshot=[],
        control_ids_used=[str(c.id) for c in scenario.mitigating_controls],
        status=RunStatus.QUEUED,
        run_type=RunType.SINGLE,
        created_by=user.id,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def _status(db: AsyncSession, run: RiskAnalysisRun) -> RunStatus:
    await db.refresh(run)
    return run.status


@pytest.mark.asyncio
async def test_concurrent_aggregate_runs_never_overlap_attribution(
    db_session: AsyncSession,
    seed_scenario_factory: Any,
    seed_control_factory: Any,
    seed_user: User,
    seed_organization: Organization,
    wire_executor_to_test_db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runs = [
        await _aggregate_run(
            db_session, seed_scenario_factory, seed_control_factory, seed_user, seed_organization, t
        )
        for t in ("a", "b")
    ]
    probe = _install(monkeypatch)
    await asyncio.gather(*(rex.execute_run(r.id) for r in runs))

    assert [await _status(db_session, r) for r in runs] == [RunStatus.COMPLETED] * 2
    assert probe.max_active == 1, "two runs had attribution calls in flight together"
    # canonical phase (Shapley + LOO) is one contiguous stretch per run, never interleaved
    assert len(set(probe.owners("shapley", "loo"))) == 2
    assert len(_grouped(probe.owners("shapley", "loo"))) == 2
    assert len(_grouped(probe.owners("ensemble"))) == 2


@pytest.mark.asyncio
async def test_concurrent_single_runs_never_overlap_attribution(
    db_session: AsyncSession,
    seed_scenario_with_controls: Scenario,
    seed_user: User,
    seed_organization: Organization,
    wire_executor_to_test_db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runs = [
        await _single_run(db_session, seed_scenario_with_controls, seed_user, seed_organization)
        for _ in range(2)
    ]
    probe = _install(monkeypatch)
    await asyncio.gather(*(rex.execute_run(r.id) for r in runs))

    assert [await _status(db_session, r) for r in runs] == [RunStatus.COMPLETED] * 2
    assert probe.max_active == 1
    # all of a SINGLE run's attribution is one slot: its five calls are contiguous
    assert len(set(probe.owners("shapley", "loo", "ensemble"))) == 2
    assert len(_grouped(probe.owners("shapley", "loo", "ensemble"))) == 2


@pytest.mark.asyncio
async def test_slot_is_released_when_an_attribution_raises(
    db_session: AsyncSession,
    seed_scenario_factory: Any,
    seed_control_factory: Any,
    seed_user: User,
    seed_organization: Organization,
    wire_executor_to_test_db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing attribution must not wedge the queue: run A raises inside the slot,
    run B (queued behind it) still completes and the lock ends up free."""
    runs = [
        await _aggregate_run(
            db_session, seed_scenario_factory, seed_control_factory, seed_user, seed_organization, t
        )
        for t in ("fail", "ok")
    ]
    real = rex._compute_shapley_by_scenario
    first_call: list[int] = []

    def flaky(calculator: Any, *args: Any, **kwargs: Any) -> Any:
        # Fail only the first attribution call that reaches the slot, whichever run it is.
        if not first_call:
            first_call.append(id(calculator))
            raise RuntimeError("injected attribution failure")
        return real(calculator, *args, **kwargs)

    monkeypatch.setattr(rex, "_compute_shapley_by_scenario", flaky)
    await asyncio.gather(*(rex.execute_run(r.id) for r in runs))

    statuses = sorted([(await _status(db_session, r)).value for r in runs])
    assert statuses == [RunStatus.COMPLETED.value, RunStatus.FAILED.value]
    assert not rex._attribution_lock_for_current_loop().locked()


@pytest.mark.asyncio
async def test_queued_run_holds_no_pooled_connection_while_it_waits(
    db_session: AsyncSession,
    seed_scenario_factory: Any,
    seed_control_factory: Any,
    seed_user: User,
    seed_organization: Organization,
    wire_executor_to_test_db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The slot is entered after `_release_conn_for_compute`, so a run parked on it holds
    ZERO pooled connections (A1 / #508: a parked run that kept its connection would
    exhaust the 15-slot pool and could starve the holder's next DB op). Orchestrated:
    run A is held inside the slot, run B is started and observed parked on the lock,
    and only then is the pool sampled."""
    runs = [
        await _aggregate_run(
            db_session, seed_scenario_factory, seed_control_factory, seed_user, seed_organization, t
        )
        for t in ("p", "q")
    ]
    loop = asyncio.get_running_loop()
    a_in_slot = asyncio.Event()
    release_a = threading.Event()
    real = rex._compute_shapley_by_scenario

    def holding(*args: Any, **kwargs: Any) -> Any:
        loop.call_soon_threadsafe(a_in_slot.set)
        release_a.wait(timeout=60)  # A keeps the slot until the test has looked
        return real(*args, **kwargs)

    monkeypatch.setattr(rex, "_compute_shapley_by_scenario", holding)
    lock = rex._attribution_lock_for_current_loop()
    task_a = asyncio.create_task(rex.execute_run(runs[0].id))
    try:
        await asyncio.wait_for(a_in_slot.wait(), timeout=60)
        task_b = asyncio.create_task(rex.execute_run(runs[1].id))

        async def b_is_parked() -> None:
            while not lock._waiters:  # a coroutine is queued on the lock
                await asyncio.sleep(0.01)

        await asyncio.wait_for(b_is_parked(), timeout=60)
        assert lock.locked()
        pool: Any = get_engine().pool  # QueuePool.checkedout() is not on the base type
        assert pool.checkedout() == 0  # A computes, B waits: no connection held
    finally:
        release_a.set()
    await asyncio.gather(task_a, task_b)

    assert [await _status(db_session, r) for r in runs] == [RunStatus.COMPLETED] * 2


@pytest.mark.asyncio
async def test_slot_serializes_coroutines_and_is_per_event_loop() -> None:
    order: list[str] = []

    async def holder(tag: str) -> None:
        async with rex._attribution_slot():
            order.append(f"{tag}+")
            await asyncio.sleep(0.02)  # yields; the other coroutine must stay parked
            order.append(f"{tag}-")

    await asyncio.gather(holder("a"), holder("b"))
    assert order in (["a+", "a-", "b+", "b-"], ["b+", "b-", "a+", "a-"])
    mine = rex._attribution_lock_for_current_loop()
    assert not mine.locked()

    # a lock that was contended on THIS loop is never handed to another loop
    async def other_loops_lock() -> asyncio.Lock:
        return rex._attribution_lock_for_current_loop()

    theirs = await asyncio.to_thread(asyncio.run, other_loops_lock())
    assert theirs is not mine
