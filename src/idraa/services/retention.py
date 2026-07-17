"""Age-based run retention sweep (#297).

Two independent, off-by-default phases bound run-data growth:

1. **Sample purge** — delete the heavy ``run_samples`` row from old terminal
   runs while keeping the run + its summary (``simulation_results``). Cheap
   storage reclaim that keeps the run discoverable in lists/dashboards.
2. **Auto-delete** — fully delete very old terminal runs (cascade drops their
   ``run_samples``). The harsher phase; only runs when ``retention_run_delete_days``
   is set, and must use a strictly larger window than purge (config-validated).

Eligibility NEVER includes RUNNING / QUEUED rows — that would race the
run-state durability gap (#211), where an OOM-killed worker leaves a row stuck
at RUNNING. Only COMPLETED / FAILED / CANCELLED rows are touched, anchored on
``COALESCE(completed_at, created_at)`` so a terminal row without a recorded
completion time still ages from creation.

Row-by-row delete + per-row audit is intentional: a bulk ``DELETE ... WHERE``
cannot emit the required per-run audit log entry. Single-admin scale makes the
per-row loop acceptable; ``retention_sweep_batch_limit`` caps each phase.

This module is the PURE sweep function. The trigger (when/how often it runs)
is a SEPARATE task — nothing here wires it into the app lifespan.
"""

from __future__ import annotations

import datetime
import logging
import uuid
from typing import cast

from sqlalchemy import CursorResult, exists, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import Executable

from idraa.config import Settings
from idraa.db import get_session
from idraa.models._types import now_utc
from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus
from idraa.models.run_samples import RunSamples
from idraa.models.system_state import SystemState
from idraa.services.audit import AuditWriter
from idraa.services.run_reaper import active_run_ids

logger = logging.getLogger(__name__)

# Terminal statuses safe to retain-sweep. RUNNING / QUEUED are NEVER eligible
# (respects the #211 durability gap — a stuck RUNNING row may still be live).
# Stale runs (is_stale=True) are COMPLETED and thus already covered here —
# no separate path is required.
_RETAINABLE = (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED)

# COALESCE(completed_at, created_at): terminal rows age from completion when
# recorded, else from creation.
_AGE_ANCHOR = func.coalesce(RiskAnalysisRun.completed_at, RiskAnalysisRun.created_at)


def _age_days(anchor: datetime.datetime, now: datetime.datetime) -> int:
    """Whole-day age of ``anchor`` relative to ``now`` (floor, never negative)."""
    delta = now - anchor
    return max(0, delta.days)


async def _reclaimable_free_bytes(db: AsyncSession) -> int:
    """Bytes currently reclaimable by VACUUM: ``freelist_count * page_size``.

    SQLite only — returns 0 on any other dialect (VACUUM is a no-op there, so
    the caller's ``free_bytes >= threshold`` gate short-circuits to skip).
    These are cheap header/pragma reads; they autobegin a transaction on the
    session, which ``_run_vacuum`` clears with an explicit ``rollback()``
    before acquiring its AUTOCOMMIT connection.
    """
    if (db.bind.dialect.name if db.bind is not None else "sqlite") != "sqlite":
        return 0
    freelist = int((await db.execute(text("PRAGMA freelist_count"))).scalar_one())
    page_size = int((await db.execute(text("PRAGMA page_size"))).scalar_one())
    return freelist * page_size


async def _run_vacuum(db: AsyncSession) -> None:
    """VACUUM on an AUTOCOMMIT connection (VACUUM cannot run inside a txn).

    SQLite only — Postgres autovacuums, so this is a no-op there. Caller
    (``sweep_retention``) gates on no-active-runs + a minimum reclaimable-free-
    bytes threshold before invoking this.

    ``AsyncSession`` autobegins a transaction the moment a connection is
    associated with it — by the time a plain ``await db.connection()`` call
    returns, that connection already has a begun transaction, and SQLAlchemy
    then raises ``InvalidRequestError`` on any attempt to change
    ``isolation_level`` after the fact ("connection has already initialized a
    ... Transaction() ... may not be altered unless rollback() or commit() is
    called first"). Passing ``execution_options`` directly to
    ``db.connection()`` sets AUTOCOMMIT at acquisition time, before the
    transaction is associated, avoiding that error entirely.

    The explicit ``rollback()`` first guarantees a clean transaction boundary
    (ARCH-I2): sweep_retention's phase-2 auto-delete does a ``select(...)``
    that autobegins a transaction, and when there are zero delete candidates
    the ``if deleted:`` commit is skipped, leaving that transaction OPEN.
    Acquiring the AUTOCOMMIT connection would then raise InvalidRequestError.
    The freelist read in ``_reclaimable_free_bytes`` similarly leaves an open
    transaction. Rolling back first closes any such dangling transaction
    (no-op when none is open) so the AUTOCOMMIT acquisition always succeeds.
    """
    if (db.bind.dialect.name if db.bind is not None else "sqlite") != "sqlite":
        return
    # ARCH-I2: guarantee a clean txn boundary before AUTOCOMMIT acquisition.
    await db.rollback()
    conn = await db.connection(execution_options={"isolation_level": "AUTOCOMMIT"})
    await conn.exec_driver_sql("VACUUM")
    # db.py runs every connection in WAL mode (perf-amplifier PRAGMA). Under
    # WAL, VACUUM's rewritten pages land in the -wal file first — the main
    # .db file is NOT truncated on disk until the next checkpoint, which may
    # not happen for a while (default wal_autocheckpoint=1000 pages). Since
    # the entire point of this sweep is to reclaim disk RIGHT NOW (the prod
    # incident this closes was a volume filling up), force a TRUNCATE
    # checkpoint immediately so the freed space is actually returned to the
    # filesystem instead of sitting logically-free-but-physically-unclaimed.
    # A no-op if the connection isn't in WAL mode.
    await conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")


async def sweep_retention(
    db: AsyncSession, settings: Settings, *, vacuum: bool = False
) -> dict[str, int]:
    """Age-based run retention. Eligibility excludes running/queued (respects
    #211). Row-by-row delete + per-row audit (a bulk DELETE can't emit the
    required per-run audit). Sample-purge phase first; auto-delete phase only
    if enabled. Returns {"purged": n, "deleted": m}.

    ``vacuum`` (Task 5, Arch-B1) is STARTUP-ONLY — the per-request
    opportunistic sweep (``maybe_sweep_opportunistic``) always calls this with
    the default ``vacuum=False``. Only the lifespan startup one-shot passes
    ``vacuum=True``. Even then, VACUUM only actually runs when the config is
    enabled, the file already holds at least
    ``retention_vacuum_min_free_bytes`` of reclaimable free space, and no run
    is currently live (VACUUM rewrites the whole file and would otherwise
    contend with an in-flight run's writes).

    The free-space gate (ARCH-I1) reads ACTUAL reclaimable free pages rather
    than this pass's purge count: the opportunistic boot sweep runs BEFORE the
    startup vacuum sweep and usually purges the aged rows itself, so a
    this-pass-purge-count gate would see 0 and skip VACUUM on exactly the
    boots where space was just freed. VACUUM reclaims ALL free pages whenever
    it runs, so gating on the freelist matches its semantics.
    """
    # Defense in depth: a misconfigured window must not silently mis-retain.
    settings.validate_retention()

    now = now_utc()
    limit = settings.retention_sweep_batch_limit
    audit = AuditWriter(db)

    purged = 0
    deleted = 0

    # --- Phase 1: sample purge (only if enabled) ---
    if settings.retention_sample_purge_days > 0:
        purge_n = settings.retention_sample_purge_days
        purge_cutoff = now - datetime.timedelta(days=purge_n)
        # Eligible terminal runs older than N days that STILL have a samples row.
        has_samples = exists().where(RunSamples.run_id == RiskAnalysisRun.id)
        candidates = (
            await db.execute(
                select(
                    RiskAnalysisRun.id,
                    RiskAnalysisRun.organization_id,
                    _AGE_ANCHOR,
                )
                .where(
                    RiskAnalysisRun.status.in_(_RETAINABLE),
                    purge_cutoff > _AGE_ANCHOR,
                    has_samples,
                )
                .limit(limit)
            )
        ).all()

        for run_id, org_id, anchor in candidates:
            await audit.log(
                organization_id=org_id,
                user_id=None,
                action="risk_analysis_run.purge_samples",
                entity_type="risk_analysis_run",
                entity_id=run_id,
                changes={
                    "actor": "retention-policy",
                    "policy": f"sample_purge_days={purge_n}",
                    "run_age_days": _age_days(anchor, now),
                },
            )
            samples = await db.get(RunSamples, run_id)
            if samples is not None:
                await db.delete(samples)
            purged += 1

        if purged:
            await db.commit()
            logger.info("Retention sweep purged samples from %d run(s)", purged)

    # --- Phase 2: auto-delete (only if enabled) ---
    if settings.retention_run_delete_days > 0:
        delete_m = settings.retention_run_delete_days
        delete_cutoff = now - datetime.timedelta(days=delete_m)
        candidates = (
            await db.execute(
                select(
                    RiskAnalysisRun.id,
                    RiskAnalysisRun.organization_id,
                    _AGE_ANCHOR,
                )
                .where(
                    RiskAnalysisRun.status.in_(_RETAINABLE),
                    delete_cutoff > _AGE_ANCHOR,
                )
                .limit(limit)
            )
        ).all()

        for run_id, org_id, anchor in candidates:
            await audit.log(
                organization_id=org_id,
                user_id=None,
                action="risk_analysis_run.delete",
                entity_type="risk_analysis_run",
                entity_id=run_id,
                changes={
                    "actor": "retention-policy",
                    "policy": f"run_delete_days={delete_m}",
                    "run_age_days": _age_days(anchor, now),
                },
            )
            run = await db.get(RiskAnalysisRun, run_id)
            if run is not None:
                # cascade="all, delete-orphan" removes the run_samples child.
                await db.delete(run)
            deleted += 1

        if deleted:
            await db.commit()
            logger.info("Retention sweep deleted %d old run(s)", deleted)

    if vacuum and settings.retention_vacuum_enabled and not active_run_ids():
        free_bytes = await _reclaimable_free_bytes(db)
        if free_bytes >= settings.retention_vacuum_min_free_bytes:
            await _run_vacuum(db)
            logger.info(
                "Startup VACUUM ran; reclaimed ~%d free byte(s) (this pass purged=%d deleted=%d)",
                free_bytes,
                purged,
                deleted,
            )

    return {"purged": purged, "deleted": deleted}


async def _seed_system_state(db: AsyncSession, org_id: uuid.UUID) -> None:
    """Idempotently ensure the org's ``system_state`` row exists.

    Without this seed the conditional-UPDATE throttle would match 0 rows on a
    fresh DB and the sweep would silently never run. Uses the dialect-native
    ``INSERT ... ON CONFLICT (organization_id) DO NOTHING`` (SQLite + Postgres),
    relying on the ``uq_system_state_org`` UNIQUE constraint so concurrent
    seeders collapse to a single row atomically.
    """
    dialect = db.bind.dialect.name if db.bind is not None else "sqlite"
    stmt: Executable
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        stmt = (
            pg_insert(SystemState)
            .values(organization_id=org_id, last_retention_sweep_at=None)
            .on_conflict_do_nothing(index_elements=["organization_id"])
        )
    else:
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        stmt = (
            sqlite_insert(SystemState)
            .values(organization_id=org_id, last_retention_sweep_at=None)
            .on_conflict_do_nothing(index_elements=["organization_id"])
        )
    await db.execute(stmt)


async def maybe_sweep_opportunistic(settings: Settings, *, org_id: uuid.UUID) -> None:
    """Opportunistic, throttled, concurrency-safe retention-sweep entry point.

    Fires from a FastAPI ``BackgroundTask`` (runs AFTER the response is sent,
    when the request-scoped DB session is already committed + closed) and from
    app startup. So it opens its OWN session via ``get_session()`` — NEVER the
    request session — and takes ``settings`` + ``org_id`` rather than a ``db``.

    Throttle: at most one sweep per ``retention_sweep_interval_hours``, made
    concurrency-safe by an ATOMIC conditional UPDATE gated on rowcount (NOT a
    read-then-write). Two concurrent dashboard loads race on the UPDATE; only
    the caller whose UPDATE wins (``rowcount == 1``) runs the sweep. First the
    row is self-seeded (idempotent ``ON CONFLICT DO NOTHING``) so the UPDATE has
    a row to match on a fresh DB.

    No-op when both retention phases are disabled.
    """
    if not (settings.retention_sample_purge_days or settings.retention_run_delete_days):
        return

    # Self-guarding: this runs from unguarded FastAPI BackgroundTasks (dashboard
    # GET, run-history GET) as well as the try/except-wrapped startup lifespan.
    # A transient DB/validation error must NOT surface as a Starlette traceback,
    # so the entire sweep body is wrapped here — all three callers inherit it.
    try:
        cutoff = now_utc() - datetime.timedelta(hours=settings.retention_sweep_interval_hours)

        async with get_session() as db:
            # Self-seed first (idempotent; relies on uq_system_state_org) so the
            # conditional UPDATE below has a row to match even on a fresh DB.
            await _seed_system_state(db, org_id)

            # Atomic throttle: claim the sweep by advancing the marker only if
            # it's NULL or older than the interval cutoff. Exactly one concurrent
            # caller gets rowcount == 1.
            result = cast(
                CursorResult[object],
                await db.execute(
                    update(SystemState)
                    .where(
                        SystemState.organization_id == org_id,
                        or_(
                            SystemState.last_retention_sweep_at.is_(None),
                            SystemState.last_retention_sweep_at < cutoff,
                        ),
                    )
                    .values(last_retention_sweep_at=now_utc())
                ),
            )
            # Commit the claim before sweeping so a concurrent caller (or a crash
            # mid-sweep) cannot re-claim. sweep_retention does its own commits and
            # is safe to run on this same already-claimed session.
            await db.commit()

            if result.rowcount == 1:
                await sweep_retention(db, settings)
    except Exception:
        logger.exception("Opportunistic retention sweep failed")
