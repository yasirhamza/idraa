"""SQLAlchemy declarative base and async session factory."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from idraa.config import get_settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def strict_json_dumps(obj: Any) -> str:
    """#327 — engine ``json_serializer`` rejecting non-finite floats.

    The default ``json.dumps`` emits the non-standard tokens ``Infinity`` /
    ``NaN`` for non-finite floats; once stored they corrupt the durable
    ``risk_analysis_run.simulation_results`` / ``run_samples.arrays`` blobs
    and break strict consumers (``JSON.parse("Infinity")`` throws in the
    browser; CSV/JSON export re-serializes the corruption — the #306→#307
    failure-mode class).

    ``allow_nan=False`` makes ANY non-finite-to-DB write raise ``ValueError``
    at flush/commit — caught by ``execute_run``'s exception path (run flips
    to FAILED, a clean terminal state) instead of silently storing
    corruption. Upstream guards (``validate_fair_distributions`` finite
    checks, the engine-output finite guard from #324) remain the first line;
    this is the cross-cutting backstop.

    Test fixtures (tests/conftest.py, tests/services/conftest.py, plus the
    file-local engines that write JSON columns) wire the same serializer so
    the suite exercises production write semantics.

    Read side is DELIBERATELY left permissive (no ``json_deserializer`` /
    ``parse_constant`` guard): rows written BEFORE #327 could in principle
    carry the non-standard ``Infinity`` token (the pyfair-era engine had no
    output finite-guard), and a strict deserializer would brick reads of
    those historical runs wholesale. Accepted posture: writes fail loudly
    from now on; legacy reads pass through (``json.loads`` yields
    ``float('inf')``), and a #346-style audit/repair sweep is the remedy if
    a corrupt historical row is ever observed.
    """
    return json.dumps(obj, allow_nan=False)


def reset_for_tests() -> None:
    """Drop cached engine + sessionmaker. Tests only — do not call from prod code.

    Exists so tests can flip DATABASE_URL and get a fresh engine on the next
    ``get_engine()`` call without tests having to reach into this module's
    private globals. If the singleton implementation changes (e.g. to
    ``@lru_cache`` or a class-based registry), update this function and the
    fixtures keep working.
    """
    global _engine, _sessionmaker
    _engine = None
    _sessionmaker = None


def _install_sqlite_pragmas(engine: AsyncEngine) -> None:
    """Attach a per-connection PRAGMA setter to a SQLite async engine.

    SQLite ignores all FK actions (``ON DELETE CASCADE`` / ``SET NULL``)
    unless ``PRAGMA foreign_keys=ON`` is issued on EVERY connection — it is
    not a database-level setting, it is per-connection. The run-lifecycle /
    retention cascades (#297) depend on this being on. WAL + ``busy_timeout``
    are the #294 perf-amplifier PRAGMAs; ``synchronous=FULL`` is the
    durability decision (see inline comment below — supersedes #294's
    NORMAL).

    For an async engine the ``"connect"`` event must be registered against
    ``engine.sync_engine`` (the underlying greenlet-backed engine), not the
    async wrapper. Postgres is unaffected because the caller guards on the
    SQLite dialect.
    """

    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragmas(dbapi_conn: object, _record: object) -> None:
        cur = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        # Durability decision (whole-project eval, supersedes #294's NORMAL):
        # FULL fsyncs the WAL on every commit, so committed transactions
        # survive an unplanned shutdown (Fly host crash / kernel panic) —
        # NORMAL could silently lose the most recent commits (DB stays
        # uncorrupted either way under WAL). Cost: one fsync per commit;
        # immaterial at this app's write throughput (form saves + run
        # completions, single team). The #294 perf work was read-path, not
        # commit throughput. Disaster recovery for VOLUME loss is Fly's
        # daily volume snapshots (RPO ≤ 24h); if a tighter RPO is ever
        # needed, litestream WAL-shipping is the upgrade path.
        cur.execute("PRAGMA synchronous=FULL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()


def get_engine() -> AsyncEngine:
    """Return the process-wide async engine, creating it on first call."""
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            future=True,
            json_serializer=strict_json_dumps,  # #327 non-finite write guard
        )
        if _engine.dialect.name == "sqlite":
            _install_sqlite_pragmas(_engine)
    return _engine


def _get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _sessionmaker


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    """Async context manager yielding a session.

    Auto-commits on success, rolls back on error.
    """
    sm = _get_sessionmaker()
    async with sm() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
