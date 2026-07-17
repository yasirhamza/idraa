"""Executor cancel-vs-complete / cancel-vs-fail TOCTOU (issue #272).

The executor loads its run row with ``expire_on_commit=False`` (the
background-task session pattern). The final cancel-check at
run_executor.py:903 does a fresh SELECT and returns True (still RUNNING).
In the window between that check and the terminal flip, ``RunService.cancel``
(running in the HTTP request's session) can commit CANCELLED. The executor
then writes its terminal status onto its stale in-memory ORM object and
commits — silently overwriting the committed CANCELLED.

Two races:
  1. complete path (run_executor.py:937-947) — overwrites CANCELLED with COMPLETED
  2. fail path    (run_executor.py:949-964) — overwrites CANCELLED with FAILED

Test seam: we wrap ``_check_cancelled_or_continue``. Its final invocation
(run_executor.py:903) is the last cancel-check before the terminal flip and
runs OUTSIDE any open executor write transaction (the prior commit at :781
released the write lock; the intervening reads are SELECTs). On that call the
wrapper commits the competing CANCELLED via a SEPARATE session against the
SAME SQLite file (the db_session fixture) — commit-and-release before
returning True, reproducing the classic TOCTOU (the executor's own SELECT
preceded the external commit, so it proceeds to flip on a stale RUNNING
in-memory object). For the fail-path test the wrapper ALSO raises after
committing the cancel, driving the except handler.

Both tests assert RED against unguarded main (status==COMPLETED / FAILED) and
GREEN after the guarded UPDATE...WHERE status='running' fix (status==CANCELLED).
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.audit_log import AuditLog
from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType
from idraa.services.run_executor import execute_run
from idraa.services.runs import RunService


async def _seed_queued_single_run(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    scenario_id: uuid.UUID,
    control_ids: list[uuid.UUID],
    created_by: uuid.UUID,
) -> RiskAnalysisRun:
    run = RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=org_id,
        scenario_id=scenario_id,
        mc_iterations=200,
        inputs_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        controls_snapshot=[],
        control_ids_used=[str(c) for c in control_ids],
        status=RunStatus.QUEUED,
        run_type=RunType.SINGLE,
        created_by=created_by,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


@pytest.mark.asyncio
async def test_cancel_during_complete_window_not_overwritten(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_user: Any,
    seed_scenario_factory: Callable[..., Awaitable[Any]],
    seed_control_factory: Callable[..., Awaitable[Any]],
    wire_executor_to_test_db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A CANCELLED committed in the gc-window must survive the COMPLETED flip."""
    scenario = await seed_scenario_factory(name="toctou-complete")
    ctrl = await seed_control_factory(name="toctou-ctrl-complete")
    # ScenarioControl link so the executor loads the control universe.
    from idraa.models.scenario_control import ScenarioControl

    db_session.add(ScenarioControl(scenario_id=scenario.id, control_id=ctrl.id))
    await db_session.commit()

    run = await _seed_queued_single_run(
        db_session,
        org_id=seed_organization.id,
        scenario_id=scenario.id,
        control_ids=[ctrl.id],
        created_by=seed_user.id,
    )
    run_id = run.id
    org_id = seed_organization.id
    user_id = seed_user.id

    # Seam: wrap _check_cancelled_or_continue. The final cancel-check (:903) is
    # the last one before the COMPLETED flip — for a SINGLE run the checks fire
    # at :784, :822, :903 (3rd call). On that call we commit the competing
    # CANCELLED via the SEPARATE db_session (same SQLite file, no open executor
    # write txn at this point) then return True, reproducing the classic
    # TOCTOU: the executor's own SELECT preceded the cancel commit, so it
    # proceeds to flip on a stale (RUNNING) in-memory object.
    import idraa.services.run_executor as rex

    real_check = rex._check_cancelled_or_continue
    state = {"calls": 0}

    async def _check_then_cancel(session: AsyncSession, rid: uuid.UUID) -> bool:
        state["calls"] += 1
        result = await real_check(session, rid)
        if state["calls"] == 3 and result:
            await RunService(db_session).cancel(
                organization_id=org_id,
                run_id=rid,
                cancelled_by=user_id,
            )
            return True
        return result

    monkeypatch.setattr(rex, "_check_cancelled_or_continue", _check_then_cancel)

    await execute_run(run_id)

    refreshed = (
        await db_session.execute(
            select(RiskAnalysisRun)
            .where(RiskAnalysisRun.id == run_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    # GREEN after fix: CANCELLED survives. RED on unguarded main: COMPLETED.
    assert refreshed.status == RunStatus.CANCELLED, (
        f"cancel was overwritten by the complete flip: status={refreshed.status}"
    )
    # No spurious complete audit row should claim running->completed.
    complete_audit = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.entity_id == run_id,
                    AuditLog.action == "risk_analysis_run.complete",
                )
            )
        )
        .scalars()
        .all()
    )
    assert complete_audit == []


@pytest.mark.asyncio
async def test_cancel_during_fail_window_not_overwritten(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_user: Any,
    seed_scenario_factory: Callable[..., Awaitable[Any]],
    seed_control_factory: Callable[..., Awaitable[Any]],
    wire_executor_to_test_db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A CANCELLED committed before an exception fires must survive the FAILED flip."""
    scenario = await seed_scenario_factory(name="toctou-fail")
    ctrl = await seed_control_factory(name="toctou-ctrl-fail")
    from idraa.models.scenario_control import ScenarioControl

    db_session.add(ScenarioControl(scenario_id=scenario.id, control_id=ctrl.id))
    await db_session.commit()

    run = await _seed_queued_single_run(
        db_session,
        org_id=seed_organization.id,
        scenario_id=scenario.id,
        control_ids=[ctrl.id],
        created_by=seed_user.id,
    )
    run_id = run.id
    org_id = seed_organization.id
    user_id = seed_user.id

    import idraa.services.run_executor as rex

    real_check = rex._check_cancelled_or_continue
    state = {"calls": 0}

    async def _check_then_cancel_then_raise(session: AsyncSession, rid: uuid.UUID) -> bool:
        state["calls"] += 1
        result = await real_check(session, rid)
        if state["calls"] == 3 and result:
            # Commit the competing CANCELLED, then raise inside the try block
            # so the except handler runs while the row is already CANCELLED.
            await RunService(db_session).cancel(
                organization_id=org_id,
                run_id=rid,
                cancelled_by=user_id,
            )
            raise RuntimeError("simulated mid-run engine error after cancel landed")
        return result

    monkeypatch.setattr(rex, "_check_cancelled_or_continue", _check_then_cancel_then_raise)

    await execute_run(run_id)

    refreshed = (
        await db_session.execute(
            select(RiskAnalysisRun)
            .where(RiskAnalysisRun.id == run_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    # GREEN after fix: CANCELLED survives. RED on unguarded main: FAILED.
    assert refreshed.status == RunStatus.CANCELLED, (
        f"cancel was overwritten by the fail flip: status={refreshed.status}"
    )
    fail_audit = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.entity_id == run_id,
                    AuditLog.action == "risk_analysis_run.fail",
                )
            )
        )
        .scalars()
        .all()
    )
    assert fail_audit == []
