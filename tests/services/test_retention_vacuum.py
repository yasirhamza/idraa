"""Startup-only, AUTOCOMMIT VACUUM (Task 5, Arch-B1 / Arch-I4 / Sec-I3 +
PR1 final-review ARCH-I1 / ARCH-I2).

VACUUM reclaims disk after the retention purge sweep frees ``run_samples``
rows. It is moved OFF the per-request opportunistic path onto a startup
one-shot: ``VACUUM`` cannot run inside a transaction (SQLite raises "cannot
VACUUM from within a transaction"), and a full-file rewrite on the request
path would contend with live traffic. ``sweep_retention`` grows a
keyword-only ``vacuum: bool = False`` parameter — the request-path sweep
(``maybe_sweep_opportunistic``) never flips it; only the lifespan startup
sweep in ``app.py`` passes ``vacuum=True``.

ARCH-I1: the VACUUM decision gates on ACTUAL reclaimable free space
(``freelist_count * page_size >= retention_vacuum_min_free_bytes``), NOT on
this pass's purge count — because the opportunistic boot sweep runs FIRST and
usually purges the aged rows itself, so a purge-count gate would skip VACUUM
on exactly the boots where space was just freed.
``test_vacuum_runs_even_when_this_pass_purged_nothing`` is the regression.

ARCH-I2: ``_run_vacuum`` rolls back before acquiring its AUTOCOMMIT
connection, so a dangling open transaction (phase-2 auto-delete with zero
candidates skips its commit and leaves one open) does not raise
InvalidRequestError. ``test_vacuum_runs_with_open_txn_from_empty_autodelete``
is the regression.

``test_vacuum_actually_shrinks_the_db_file`` is the load-bearing test in this
module (Arch-B1): the plan-gate found that a monkeypatched-only suite cannot
catch a VACUUM that silently no-ops (wrong isolation level, wrong dialect
guard, running inside a still-open transaction, etc). It builds a REAL
on-disk WAL SQLite database, inserts a large ``run_samples`` blob, purges it,
sweeps with ``vacuum=True``, and asserts the file's byte size actually drops
— nothing here is monkeypatched.
"""

from __future__ import annotations

import datetime
import hashlib
import os
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from idraa.config import Settings
from idraa.db import Base, _install_sqlite_pragmas, strict_json_dumps
from idraa.models._types import now_utc
from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType
from idraa.models.run_samples import RunSamples
from idraa.services import retention
from tests.factories import create_org

pytestmark = pytest.mark.asyncio


async def _make_purgeable_run(
    db: AsyncSession, org_id: UUID, *, blob: bytes | None = None
) -> RiskAnalysisRun:
    """A COMPLETED run aged 120 days with a samples row — eligible for purge
    under the ``retention_sample_purge_days=14`` settings used in this module."""
    aged = now_utc() - datetime.timedelta(days=120)
    run = RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=org_id,
        scenario_id=None,
        mc_iterations=1000,
        inputs_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        controls_snapshot=[],
        control_ids_used=[],
        status=RunStatus.COMPLETED,
        run_type=RunType.SINGLE,
        created_at=aged,
        completed_at=aged,
    )
    db.add(run)
    await db.flush()
    db.add(
        RunSamples(
            run_id=run.id,
            organization_id=org_id,
            arrays={"residual": [1.0, 2.0, 3.0]} if blob is None else None,
            arrays_codec=blob,
        )
    )
    await db.commit()
    return run


@pytest_asyncio.fixture
async def seed_purgeable_runs(db: AsyncSession, org_id: UUID) -> RiskAnalysisRun:
    """One old COMPLETED run with a samples row for the purge phase to eat."""
    return await _make_purgeable_run(db, org_id)


async def test_vacuum_runs_when_free_space_over_threshold(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, seed_purgeable_runs: RiskAnalysisRun
) -> None:
    # min_free_bytes=0 => any freed page clears the gate.
    settings = Settings(
        retention_sample_purge_days=14,
        retention_vacuum_enabled=True,
        retention_vacuum_min_free_bytes=0,
    )
    monkeypatch.setattr(retention, "active_run_ids", lambda: set())
    calls: list[str] = []

    async def _fake_vacuum(_db: AsyncSession) -> None:
        calls.append("v")

    monkeypatch.setattr(retention, "_run_vacuum", _fake_vacuum)
    await retention.sweep_retention(db, settings, vacuum=True)
    assert calls == ["v"]


async def test_vacuum_skipped_when_runs_active(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, seed_purgeable_runs: RiskAnalysisRun
) -> None:
    settings = Settings(
        retention_sample_purge_days=14,
        retention_vacuum_enabled=True,
        retention_vacuum_min_free_bytes=0,
    )
    monkeypatch.setattr(retention, "active_run_ids", lambda: {"some-run"})
    calls: list[str] = []

    async def _fake_vacuum(_db: AsyncSession) -> None:
        calls.append("v")

    monkeypatch.setattr(retention, "_run_vacuum", _fake_vacuum)
    await retention.sweep_retention(db, settings, vacuum=True)
    assert calls == []


async def test_vacuum_skipped_when_disabled(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, seed_purgeable_runs: RiskAnalysisRun
) -> None:
    settings = Settings(
        retention_sample_purge_days=14,
        retention_vacuum_enabled=False,
        retention_vacuum_min_free_bytes=0,
    )
    monkeypatch.setattr(retention, "active_run_ids", lambda: set())
    calls: list[str] = []

    async def _fake_vacuum(_db: AsyncSession) -> None:
        calls.append("v")

    monkeypatch.setattr(retention, "_run_vacuum", _fake_vacuum)
    await retention.sweep_retention(db, settings, vacuum=True)
    assert calls == []


async def test_vacuum_skipped_below_min_free_bytes_threshold(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, seed_purgeable_runs: RiskAnalysisRun
) -> None:
    # A tiny purge frees only a few small pages, far under a 1 TB threshold.
    settings = Settings(
        retention_sample_purge_days=14,
        retention_vacuum_enabled=True,
        retention_vacuum_min_free_bytes=1_000_000_000_000,
    )
    monkeypatch.setattr(retention, "active_run_ids", lambda: set())
    calls: list[str] = []

    async def _fake_vacuum(_db: AsyncSession) -> None:
        calls.append("v")

    monkeypatch.setattr(retention, "_run_vacuum", _fake_vacuum)
    await retention.sweep_retention(db, settings, vacuum=True)
    assert calls == []


async def test_vacuum_runs_even_when_this_pass_purged_nothing(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, seed_purgeable_runs: RiskAnalysisRun
) -> None:
    """ARCH-I1 regression: mirror the lifespan order. First sweep (vacuum=False)
    purges the aged row; the SECOND sweep (vacuum=True) purges 0 rows — but the
    freelist still holds the pages the first sweep freed, so VACUUM must still
    fire. A purge-count gate would (wrongly) skip here."""
    settings = Settings(
        retention_sample_purge_days=14,
        retention_vacuum_enabled=True,
        retention_vacuum_min_free_bytes=0,
    )
    monkeypatch.setattr(retention, "active_run_ids", lambda: set())

    # Pass 1: request-path shape — purge only, never vacuum.
    first = await retention.sweep_retention(db, settings)
    assert first == {"purged": 1, "deleted": 0}

    calls: list[str] = []

    async def _fake_vacuum(_db: AsyncSession) -> None:
        calls.append("v")

    monkeypatch.setattr(retention, "_run_vacuum", _fake_vacuum)

    # Pass 2: startup vacuum sweep — nothing left to purge, but VACUUM fires.
    second = await retention.sweep_retention(db, settings, vacuum=True)
    assert second == {"purged": 0, "deleted": 0}
    assert calls == ["v"]


async def test_request_path_sweep_never_vacuums(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, seed_purgeable_runs: RiskAnalysisRun
) -> None:
    settings = Settings(
        retention_sample_purge_days=14,
        retention_vacuum_enabled=True,
        retention_vacuum_min_free_bytes=0,
    )
    monkeypatch.setattr(retention, "active_run_ids", lambda: set())
    calls: list[str] = []

    async def _fake_vacuum(_db: AsyncSession) -> None:
        calls.append("v")

    monkeypatch.setattr(retention, "_run_vacuum", _fake_vacuum)
    await retention.sweep_retention(db, settings)  # vacuum defaults False
    assert calls == []


async def test_run_vacuum_noop_on_non_sqlite_dialect() -> None:
    """``_run_vacuum`` guards on dialect — Postgres autovacuums, so it must
    return without touching the connection (or rolling anything back)."""

    class _FakeDialect:
        name = "postgresql"

    class _FakeBind:
        dialect = _FakeDialect()

    class _FakeDb:
        bind = _FakeBind()

        async def rollback(self) -> None:  # pragma: no cover - must never be called
            raise AssertionError("must not roll back on a non-sqlite dialect")

        async def connection(self) -> None:  # pragma: no cover - must never be called
            raise AssertionError("must not open a connection on a non-sqlite dialect")

    await retention._run_vacuum(_FakeDb())  # type: ignore[arg-type]


@pytest_asyncio.fixture
async def _real_sqlite_engine(tmp_path: Path) -> AsyncGenerator[tuple[str, Path], None]:
    """A REAL on-disk WAL-mode SQLite engine (mirrors ``db.py``'s prod pragmas),
    NOT the in-memory ``db`` fixture — VACUUM has nothing to shrink on
    ``:memory:``, so the file-shrink assertion needs a real file on disk."""
    db_path = tmp_path / "vacuum_test.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(url, json_serializer=strict_json_dumps)
    _install_sqlite_pragmas(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield url, db_path
    await engine.dispose()


async def test_vacuum_actually_shrinks_the_db_file(
    _real_sqlite_engine: tuple[str, Path],
) -> None:
    """NON-monkeypatched (Arch-B1): a large run_samples blob is purged, the
    startup sweep runs a REAL AUTOCOMMIT VACUUM, and the on-disk file shrinks."""
    url, db_path = _real_sqlite_engine
    engine = create_async_engine(url, json_serializer=strict_json_dumps)
    _install_sqlite_pragmas(engine)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    big_blob = os.urandom(3_000_000)  # 3MB — large enough to show up in file size

    async with factory() as session:
        org = await create_org(session)
        await session.flush()
        await _make_purgeable_run(session, org.id, blob=big_blob)

        # Force a checkpoint so the blob lands in the MAIN db file (not
        # sitting only in the -wal file) before we snapshot "before" size.
        raw_conn = await session.connection()
        await raw_conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")

    size_before = os.path.getsize(db_path)
    assert size_before > len(big_blob), "fixture did not actually write the blob to disk"

    # 1 MB threshold: the ~3 MB the purge frees clears it deterministically.
    settings = Settings(
        retention_sample_purge_days=14,
        retention_vacuum_enabled=True,
        retention_vacuum_min_free_bytes=1_000_000,
    )
    async with factory() as session:
        counts = await retention.sweep_retention(session, settings, vacuum=True)

    assert counts == {"purged": 1, "deleted": 0}

    size_after = os.path.getsize(db_path)
    assert size_after < size_before, (
        f"VACUUM did not shrink the file: before={size_before} after={size_after}"
    )

    await engine.dispose()


async def test_run_vacuum_succeeds_with_open_write_txn(
    _real_sqlite_engine: tuple[str, Path],
) -> None:
    """ARCH-I2 regression (the genuine catch): an OPEN, uncommitted WRITE on the
    session holds a real SQLite write transaction, and VACUUM raises
    ``OperationalError: cannot VACUUM from within a transaction`` unless
    ``_run_vacuum`` rolls back first. Directly exercises the REAL
    (non-monkeypatched) AUTOCOMMIT path.

    NOTE: a SELECT-only open txn (e.g. phase-2 auto-delete with zero
    candidates) does NOT reproduce this — SQLite only takes a write lock on an
    actual write, so ``db.connection(execution_options=...)`` merely warns and
    VACUUM still succeeds. The uncommitted write below is what actually trips
    the failure, so this test flips red if the rollback is removed."""
    url, _db_path = _real_sqlite_engine
    engine = create_async_engine(url, json_serializer=strict_json_dumps)
    _install_sqlite_pragmas(engine)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        # An uncommitted write => a real open SQLite write transaction.
        await create_org(session)
        await session.flush()
        assert session.in_transaction()
        # Must NOT raise "cannot VACUUM from within a transaction".
        await retention._run_vacuum(session)

    await engine.dispose()


async def test_sweep_with_autodelete_enabled_and_zero_candidates_vacuums(
    _real_sqlite_engine: tuple[str, Path],
) -> None:
    """End-to-end ARCH-I2 coverage: auto-delete ENABLED but zero delete
    candidates (phase-2 select autobegins a txn, ``if deleted:`` commit
    skipped), a REAL vacuum sweep still completes and shrinks the file."""
    url, db_path = _real_sqlite_engine
    engine = create_async_engine(url, json_serializer=strict_json_dumps)
    _install_sqlite_pragmas(engine)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    big_blob = os.urandom(3_000_000)

    async with factory() as session:
        org = await create_org(session)
        await session.flush()
        # 120-day-old run: purged (>14d) but NOT auto-deleted (<365d), so
        # phase 2 finds zero delete candidates.
        await _make_purgeable_run(session, org.id, blob=big_blob)
        raw_conn = await session.connection()
        await raw_conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")

    size_before = os.path.getsize(db_path)

    settings = Settings(
        retention_sample_purge_days=14,
        retention_run_delete_days=365,  # enabled; no candidate at 120 days
        retention_vacuum_enabled=True,
        retention_vacuum_min_free_bytes=1_000_000,
    )
    async with factory() as session:
        counts = await retention.sweep_retention(session, settings, vacuum=True)

    assert counts == {"purged": 1, "deleted": 0}
    size_after = os.path.getsize(db_path)
    assert size_after < size_before, (
        f"VACUUM did not shrink the file: before={size_before} after={size_after}"
    )

    await engine.dispose()
