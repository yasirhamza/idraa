"""idraa#131 — except-branch rollback-first in execute_run.

Bug class (deferred from PR #99's final review): an exception raised BETWEEN
the COMPLETED flip UPDATE and ``session.commit()`` (the complete-audit flush,
or the commit itself) left the guarded FAILED flip running inside the SAME
uncommitted transaction — it saw its own COMPLETED write, matched 0 rows,
logged the misdiagnosis "terminal state set by another actor", and stranded
the run RUNNING until the orphan reaper.

Fix: ``await session.rollback()`` as the first statement of the except
branch, so the guarded FAILED UPDATE sees the COMMITTED state (RUNNING).
The rollback also discards any flushed attribution audit rows
(run.shapley_skipped / run.non_finite_shapley / run.loo_skipped /
run.non_finite_loo) — those are captured in memory as they are first logged
and RE-LOGGED after a matched FAILED flip, so a failed run keeps its
attribution-degradation audit trail.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.audit_log import AuditLog
from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus
from idraa.services.audit import AuditWriter
from idraa.services.run_executor import execute_run

# Reuse the canonical executable-run fixture (its dependency fixtures live in
# the shared conftest; the fixture itself is file-local to the executor tests).
from tests.unit.test_run_executor import queued_run  # noqa: F401


def _fail_on_complete_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the complete-audit flush raise — the exact between-flip-and-commit
    window the bug lives in."""
    real_log = AuditWriter.log

    async def _failing_log(self: AuditWriter, **kwargs: Any) -> Any:
        if kwargs.get("action") == "risk_analysis_run.complete":
            raise RuntimeError("simulated audit-flush failure between flip and commit")
        return await real_log(self, **kwargs)

    monkeypatch.setattr(AuditWriter, "log", _failing_log)


@pytest.mark.asyncio
async def test_late_exception_flips_failed_not_stranded(
    db_session: AsyncSession,
    queued_run: RiskAnalysisRun,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The core #131 pin: a post-flip pre-commit exception ends in FAILED.

    Pre-fix this asserts RUNNING (the stranded misdiagnosis); the run only
    healed via the orphan reaper minutes later.
    """
    _fail_on_complete_audit(monkeypatch)

    await execute_run(queued_run.id)

    await db_session.refresh(queued_run)
    assert queued_run.status == RunStatus.FAILED
    assert queued_run.error_message
    assert queued_run.completed_at is not None

    rows = (
        (
            await db_session.execute(
                select(AuditLog).where(AuditLog.action == "risk_analysis_run.fail")
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1, "exactly one fail audit row must land with the flip"


# Sibling contracts pinned elsewhere:
# - The STRONG rowcount==0 preserve pin (flushed attribution rows must NOT
#   survive a lost race) is tests/integration/test_aggregate_shapley_persist.py
#   ::test_aggregate_cancel_during_fail_window_rolls_back_shapley_audit —
#   it forces shapley_skipped rows into the pending transaction, interleaves
#   an external CANCELLED at a lock-free point, and asserts zero rows land.
# - The re-log pin (attribution rows DO survive a matched FAILED flip, exact
#   multiplicity) is test_attribution_rows_survive_failed_flip in the same
#   file.
# (Interleaving an external commit AFTER the COMPLETED flip, as an earlier
# draft of this file did, is mechanically impossible on SQLite: the
# executor's uncommitted flip holds the writer, so the competing commit
# times out. On Postgres it would block rather than time out — the
# impossibility argument is engine-specific.)
