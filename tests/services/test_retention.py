"""Age-based run retention sweep (#297).

Exercises the pure ``sweep_retention(db, settings)`` function plus
``Settings.validate_retention()``. The trigger (when/how often the sweep
runs) is a SEPARATE task and is not under test here.

These tests use the service-layer ``db`` / ``org_id`` / ``actor_id`` fixtures
(tests/services/conftest.py) and build runs + samples directly, because the
shared ``seed_run_factory`` (tests/conftest.py) does not expose ``created_at``
nor attach a RunSamples row — both of which these tests need to control.
"""

from __future__ import annotations

import datetime
import hashlib
import uuid
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.config import Settings
from idraa.errors import RetentionConfigError
from idraa.models._types import now_utc
from idraa.models.audit_log import AuditLog
from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType
from idraa.models.run_samples import RunSamples
from idraa.services.retention import sweep_retention

pytestmark = pytest.mark.asyncio


async def _make_run(
    db: AsyncSession,
    org_id: UUID,
    *,
    status: RunStatus,
    age_days: float,
    with_samples: bool,
) -> RiskAnalysisRun:
    """Build + commit a run aged ``age_days`` in the past, optionally with samples.

    Age is set on BOTH created_at and completed_at (for terminal statuses) so
    the COALESCE(completed_at, created_at) anchor resolves to ``age_days`` ago
    regardless of which column the sweep reads. The TimestampMixin init hook
    honours an explicit ``created_at`` kwarg.
    """
    aged = now_utc() - datetime.timedelta(days=age_days)
    completed = (
        aged if status in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED) else None
    )
    run = RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=org_id,
        scenario_id=None,
        mc_iterations=1000,
        inputs_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        controls_snapshot=[],
        control_ids_used=[],
        status=status,
        run_type=RunType.SINGLE,
        created_at=aged,
        completed_at=completed,
    )
    db.add(run)
    await db.flush()
    if with_samples:
        db.add(
            RunSamples(
                run_id=run.id,
                organization_id=org_id,
                arrays={"residual": [1.0, 2.0, 3.0]},
            )
        )
    await db.commit()
    await db.refresh(run)
    return run


async def _has_samples(db: AsyncSession, run_id: UUID) -> bool:
    row = await db.get(RunSamples, run_id)
    return row is not None


def _settings(**overrides: object) -> Settings:
    return Settings(environment="test", **overrides)  # type: ignore[arg-type]


async def test_purges_old_run_samples_keeps_fresh(db: AsyncSession, org_id: UUID) -> None:
    old = await _make_run(db, org_id, status=RunStatus.COMPLETED, age_days=120, with_samples=True)
    fresh = await _make_run(db, org_id, status=RunStatus.COMPLETED, age_days=1, with_samples=True)

    counts = await sweep_retention(db, _settings(retention_sample_purge_days=90))

    assert counts == {"purged": 1, "deleted": 0}
    # Old run's samples purged, run row itself kept.
    assert not await _has_samples(db, old.id)
    assert await db.get(RiskAnalysisRun, old.id) is not None
    # Fresh run untouched.
    assert await _has_samples(db, fresh.id)


async def test_purge_writes_audit_before_delete(db: AsyncSession, org_id: UUID) -> None:
    old = await _make_run(db, org_id, status=RunStatus.COMPLETED, age_days=200, with_samples=True)

    await sweep_retention(db, _settings(retention_sample_purge_days=90))

    rows = (
        (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.action == "risk_analysis_run.purge_samples",
                    AuditLog.entity_id == old.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    audit = rows[0]
    assert audit.user_id is None
    assert audit.changes["actor"] == "retention-policy"
    assert audit.changes["policy"] == "sample_purge_days=90"
    assert audit.changes["run_age_days"] == 200


async def test_never_touches_running_run(db: AsyncSession, org_id: UUID) -> None:
    running = await _make_run(db, org_id, status=RunStatus.RUNNING, age_days=999, with_samples=True)
    queued = await _make_run(db, org_id, status=RunStatus.QUEUED, age_days=999, with_samples=True)

    counts = await sweep_retention(
        db,
        _settings(retention_sample_purge_days=30, retention_run_delete_days=60),
    )

    assert counts == {"purged": 0, "deleted": 0}
    assert await _has_samples(db, running.id)
    assert await db.get(RiskAnalysisRun, running.id) is not None
    assert await _has_samples(db, queued.id)
    assert await db.get(RiskAnalysisRun, queued.id) is not None


async def test_auto_delete_removes_run_and_audits(db: AsyncSession, org_id: UUID) -> None:
    old = await _make_run(db, org_id, status=RunStatus.FAILED, age_days=400, with_samples=True)

    counts = await sweep_retention(
        db,
        _settings(retention_sample_purge_days=90, retention_run_delete_days=365),
    )

    assert counts["deleted"] == 1
    # Run + cascaded samples gone.
    assert await db.get(RiskAnalysisRun, old.id) is None
    assert not await _has_samples(db, old.id)

    rows = (
        (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.action == "risk_analysis_run.delete",
                    AuditLog.entity_id == old.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    audit = rows[0]
    assert audit.user_id is None
    assert audit.changes["actor"] == "retention-policy"
    assert audit.changes["policy"] == "run_delete_days=365"
    assert audit.changes["run_age_days"] == 400


async def test_disabled_by_default_no_op(db: AsyncSession, org_id: UUID) -> None:
    old = await _make_run(db, org_id, status=RunStatus.COMPLETED, age_days=9999, with_samples=True)

    counts = await sweep_retention(db, _settings())

    assert counts == {"purged": 0, "deleted": 0}
    assert await _has_samples(db, old.id)
    assert await db.get(RiskAnalysisRun, old.id) is not None


async def test_batch_limit_caps_purge(db: AsyncSession, org_id: UUID) -> None:
    for _ in range(3):
        await _make_run(db, org_id, status=RunStatus.COMPLETED, age_days=120, with_samples=True)

    counts = await sweep_retention(
        db,
        _settings(retention_sample_purge_days=90, retention_sweep_batch_limit=2),
    )

    assert counts == {"purged": 2, "deleted": 0}


def test_validate_retention_rejects_delete_le_purge() -> None:
    with pytest.raises(RetentionConfigError):
        Settings(
            environment="test",
            retention_sample_purge_days=90,
            retention_run_delete_days=30,
        ).validate_retention()


def test_validate_retention_allows_delete_gt_purge() -> None:
    # No raise.
    Settings(
        environment="test",
        retention_sample_purge_days=30,
        retention_run_delete_days=90,
    ).validate_retention()


def test_validate_retention_allows_either_disabled() -> None:
    # delete enabled, purge disabled — independent toggles, valid.
    Settings(environment="test", retention_run_delete_days=30).validate_retention()
    # purge enabled, delete disabled — valid.
    Settings(environment="test", retention_sample_purge_days=30).validate_retention()


async def test_stale_completed_run_still_purged(db: AsyncSession, org_id: UUID) -> None:
    """Stale runs (is_stale=True) stay COMPLETED, so they are swept by the existing
    COMPLETED path — no separate retainable bucket is required.  Verifies that
    is_stale=True does not block sample-purge. (#437 T8 — is_stale flag model)"""
    old_stale = await _make_run(
        db, org_id, status=RunStatus.COMPLETED, age_days=120, with_samples=True
    )
    # Mark it stale directly (mirrors what flag_runs_stale_for_control does).
    old_stale.is_stale = True
    await db.commit()

    counts = await sweep_retention(db, _settings(retention_sample_purge_days=90))

    assert counts["purged"] >= 1
    # Stale COMPLETED run: samples purged, run row kept.
    assert not await _has_samples(db, old_stale.id)
    assert await db.get(RiskAnalysisRun, old_stale.id) is not None
