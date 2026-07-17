"""RunSamples model — DB-level ON DELETE CASCADE behaviour (#297).

Proves the cascade fires at the DATABASE level (raw SQL DELETE on the parent),
not just via SQLAlchemy ORM unit-of-work cascade. This requires
``PRAGMA foreign_keys = ON`` (installed per-connection by the db_session
fixture) — without it SQLite silently ignores ``ON DELETE CASCADE``.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, text

from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType
from idraa.models.run_samples import RunSamples


@pytest.mark.asyncio
async def test_db_level_cascade_removes_samples(db_session, organization):
    assert int((await db_session.execute(text("PRAGMA foreign_keys"))).scalar()) == 1
    run = RiskAnalysisRun(
        organization_id=organization.id,
        run_type=RunType.SINGLE,
        status=RunStatus.COMPLETED,
        mc_iterations=1000,
        inputs_hash="0" * 64,
        simulation_results={"headline_ale": 1.0},
    )
    db_session.add(run)
    await db_session.flush()
    db_session.add(
        RunSamples(
            run_id=run.id,
            organization_id=organization.id,
            arrays={"base_risk": [1.0, 2.0]},
        )
    )
    await db_session.flush()
    # Delete via raw SQL (DB CASCADE), NOT db.delete(run) (ORM cascade) — proves
    # the DB-level ON DELETE CASCADE actually fires (requires foreign_keys=ON).
    # SQLAlchemy's Uuid type stores as 32-char hex on SQLite, so bind run.id.hex
    # (dashed str(run.id) would not match the stored column value -> no-op delete).
    await db_session.execute(
        text("DELETE FROM risk_analysis_runs WHERE id = :i"), {"i": run.id.hex}
    )
    rows = (await db_session.execute(select(RunSamples).where(RunSamples.run_id == run.id))).all()
    assert rows == []
