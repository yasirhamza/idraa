"""Sec-L8/#84 (I-2): a codec overflow ValueError lands on the clean FAILED path.

``encode_sample_arrays_streaming`` fails closed (ValueError) when the float64
sample source overflows the float32 codec range. The encode is evaluated
BEFORE the guarded COMPLETED-flip UPDATE in ``execute_run`` — if it ran after,
the except-branch's FAILED flip (``WHERE status == RUNNING``) would see its own
uncommitted COMPLETED write inside the same transaction, match 0 rows,
misdiagnose "terminal state set by another actor", roll back, and strand the
run RUNNING until the orphan reaper. This test injects the overflow and pins
the clean path: status == FAILED, generic error message, and a
``risk_analysis_run.fail`` audit row carrying ``error_class: ValueError``.
Regression direction: under the pre-hoist ordering the run row stays RUNNING
and no fail audit row exists, so both assertions discriminate.
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
from idraa.models.run_samples import RunSamples
from idraa.services.run_executor import _RUN_FAILURE_MESSAGE, execute_run


def _raise_codec_overflow(arrays: dict[str, Any]) -> bytes:
    raise ValueError(
        "sample codec overflow: 'base_risk' produced non-finite float32 "
        "(source magnitude exceeds float32 range)"
    )


@pytest.mark.asyncio
async def test_codec_overflow_flips_run_failed_with_audit(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_user: Any,
    seed_scenario_factory: Callable[..., Awaitable[Any]],
    wire_executor_to_test_db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = await seed_scenario_factory(name="codec-overflow-fail-closed-test")

    run = RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=seed_organization.id,
        scenario_id=scenario.id,
        mc_iterations=200,
        inputs_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        controls_snapshot=[],
        control_ids_used=[],
        status=RunStatus.QUEUED,
        run_type=RunType.SINGLE,
        created_by=seed_user.id,
    )
    db_session.add(run)
    await db_session.flush()
    run_id = run.id
    await db_session.commit()

    # Patch the name execute_run actually calls (imported into run_executor's
    # module namespace at import time).
    monkeypatch.setattr(
        "idraa.services.run_executor.encode_sample_arrays_streaming",
        _raise_codec_overflow,
    )

    await execute_run(run_id)

    stmt_run = (
        select(RiskAnalysisRun)
        .where(RiskAnalysisRun.id == run_id)
        .execution_options(populate_existing=True)
    )
    refreshed = (await db_session.execute(stmt_run)).scalar_one_or_none()
    assert refreshed is not None
    assert refreshed.status == RunStatus.FAILED, (
        f"expected FAILED, got {refreshed.status!r} — a RUNNING here means the "
        f"encode ran after the COMPLETED flip and the guarded FAILED flip "
        f"matched 0 rows (the pre-hoist strand-until-reaper bug)"
    )
    assert refreshed.error_message == _RUN_FAILURE_MESSAGE

    stmt_audit = select(AuditLog).where(
        AuditLog.entity_id == run_id,
        AuditLog.action == "risk_analysis_run.fail",
    )
    audit_rows = (await db_session.execute(stmt_audit)).scalars().all()
    assert len(audit_rows) == 1
    assert audit_rows[0].changes.get("error_class") == "ValueError"

    stmt_samples = select(RunSamples).where(RunSamples.run_id == run_id)
    samples_rows = (await db_session.execute(stmt_samples)).scalars().all()
    assert samples_rows == [], "no run_samples row may land for a failed encode"
