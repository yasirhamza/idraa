"""Startup orphaned-run reaper (issue #211).

A SIGKILL / OOM on the single Fly worker leaves the in-flight
``risk_analysis_runs`` row stuck at ``status='running'`` forever — there is
no way to catch SIGKILL in-process, so the executor's exception handler never
runs and the row is never flipped to FAILED. Polling lies (forever 'running')
and any "latest successful run for org" query is poisoned by the zombie.

This module ships issue #211 Phase-1 (Option 2: startup reconciliation). It is
invoked from the FastAPI lifespan on every app boot.

Single-process orphan invariant
--------------------------------
v3 dispatches runs via in-process ``BackgroundTasks`` (``routes/runs.py``).
In-process BackgroundTasks die with the process — they do NOT survive a
restart. On a single Fly machine the only worker IS this process, so any
RUNNING or QUEUED row that predates THIS boot is necessarily orphaned: the
worker that owned it just (re)started and lost its in-memory task. That makes
QUEUED-at-boot safe to reap, not just RUNNING.

The ``run_orphan_threshold_seconds`` window (default 300s) is an operational
watchdog timeout, not a calibration anchor. It only guards clock skew and
runs that flipped RUNNING / were enqueued seconds before boot — e.g. a run
legitimately QUEUED-but-not-yet-picked-up at the instant of a routine
restart, which the threshold keeps from being killed.

Phase 2 (issue #211): periodic sweep + active-run registry
-----------------------------------------------------------
The boot sweep only fires on restart — a run orphaned WITHOUT a process
death (background task lost to a pathological bug, an exception path that
itself failed) previously sat 'running' until the next deploy. Phase 2 adds
a periodic in-process sweep (``periodic_reaper_loop``, spawned from the
FastAPI lifespan at ``Settings.run_reaper_interval_seconds``).

Mid-process, the boot invariant ("any pre-boot row is orphaned") does NOT
hold — an age-only periodic sweep could false-kill a legitimately slow run.
Instead of the issue's Option-1 heartbeat column (which cannot tick reliably
anyway: the Monte Carlo engine call runs synchronously ON the event loop, so
a sibling asyncio heartbeat task starves exactly when the run is busiest),
``execute_run`` registers its run id in an in-memory ACTIVE-RUN REGISTRY for
its whole lifetime. The sweep reaps only rows past the age threshold that NO
live in-process task owns — exact under the single-process invariant, with
zero schema change.

Out of scope (Phase 3+)
-----------------------
A durable external queue (Celery / arq) or multi-machine scaling breaks BOTH
the QUEUED-at-boot invariant AND the in-memory registry (another process may
own the row). That future requires a real heartbeat column — see issue #211
Option 1 for the design.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import uuid
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.config import Settings
from idraa.models._types import now_utc
from idraa.models.csv_import_preview import CSVImportPreview
from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus
from idraa.models.session import AuthSession
from idraa.services.audit import AuditWriter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Active-run registry (#211 Phase 2) — in-memory, single-process by design.
# execute_run registers on entry and unregisters in a finally; the periodic
# sweep never touches a registered row. NOT shared across processes — see the
# module docstring's Phase-3 note before scaling beyond one machine.
# ---------------------------------------------------------------------------

_ACTIVE_RUNS: set[uuid.UUID] = set()


def register_active_run(run_id: uuid.UUID) -> None:
    """Mark ``run_id`` as owned by a live in-process task (sweep-exempt)."""
    _ACTIVE_RUNS.add(run_id)


def unregister_active_run(run_id: uuid.UUID) -> None:
    """Idempotent removal — safe on early-return / failure paths."""
    _ACTIVE_RUNS.discard(run_id)


def active_run_ids() -> frozenset[uuid.UUID]:
    """Snapshot of currently-owned run ids (frozen — callers cannot mutate)."""
    return frozenset(_ACTIVE_RUNS)


_REAP_ERROR_MESSAGE = "orphaned (worker terminated)"
# Intentional, stable wording (issue #211). Differs from Option-1's heartbeat
# phrasing and from the executor's "TypeName: msg" exception format; keep it
# stable since UI / queries may key on it.

# Status values eligible for reaping. Terminal rows (COMPLETED / FAILED /
# CANCELLED) are never touched.
_ORPHANABLE = (RunStatus.RUNNING, RunStatus.QUEUED)


async def reap_orphaned_runs(session: AsyncSession, settings: Settings) -> int:
    """Flip orphaned RUNNING / stale QUEUED runs to FAILED. Returns the count.

    Selects every ``RUNNING`` / ``QUEUED`` row whose age exceeds
    ``settings.run_orphan_threshold_seconds`` (anchored on
    ``COALESCE(started_at, created_at)`` so RUNNING rows use their start time
    and QUEUED rows use their enqueue time), then flips each to FAILED via an
    atomic guarded UPDATE that re-validates the row's status at write time
    (issue #272 — never overwrite a terminal state another actor set between
    this SELECT and its UPDATE). One audit row per row actually flipped, with
    the row's real prior status. A single batch commit covers all flips +
    audits (single-admin scale; no HTTP traffic is served before lifespan
    startup completes).
    """
    cutoff = now_utc() - datetime.timedelta(seconds=settings.run_orphan_threshold_seconds)
    anchor = func.coalesce(RiskAnalysisRun.started_at, RiskAnalysisRun.created_at)

    # #211 Phase 2: rows owned by a live in-process task are sweep-exempt
    # regardless of age (a legitimately slow run must never be false-killed
    # by the periodic sweep). At boot the registry is empty (fresh process),
    # so the boot sweep behaves exactly as Phase 1 did.
    stmt = select(
        RiskAnalysisRun.id,
        RiskAnalysisRun.status,
        RiskAnalysisRun.organization_id,
        RiskAnalysisRun.created_by,
    ).where(
        RiskAnalysisRun.status.in_(_ORPHANABLE),
        anchor < cutoff,
    )
    active = active_run_ids()
    if active:
        stmt = stmt.where(RiskAnalysisRun.id.not_in(active))

    candidates = (await session.execute(stmt)).all()

    reaped = 0
    flip_time = now_utc()
    for run_id, prev_status, org_id, created_by in candidates:
        # Guarded flip: only succeeds if the row is STILL in its observed
        # orphanable status. rowcount==0 => terminalized between SELECT and
        # UPDATE (e.g. a concurrent cancel) — skip silently, no audit row.
        result: CursorResult[Any] = await session.execute(  # type: ignore[assignment]
            update(RiskAnalysisRun)
            .where(
                RiskAnalysisRun.id == run_id,
                RiskAnalysisRun.status == prev_status,
            )
            .values(
                status=RunStatus.FAILED,
                error_message=_REAP_ERROR_MESSAGE,
                completed_at=flip_time,
            )
        )
        if result.rowcount == 0:
            logger.info(
                "Reaper skipped run %s: status changed since select (terminal race)",
                run_id,
            )
            continue
        await AuditWriter(session).log(
            organization_id=org_id,
            user_id=created_by,
            action="risk_analysis_run.reap",
            entity_type="risk_analysis_run",
            entity_id=run_id,
            changes={
                "status": [prev_status.value, RunStatus.FAILED.value],
                "reason": [None, _REAP_ERROR_MESSAGE],
            },
        )
        reaped += 1

    if reaped:
        await session.commit()
        logger.warning(
            "Reaper flipped %d orphaned run(s) to FAILED (worker terminated)",
            reaped,
        )
    return reaped


async def reap_once(settings: Settings) -> int:
    """One periodic-sweep iteration: own session, one reap, count returned.

    Split from ``periodic_reaper_loop`` so tests exercise the sweep body
    without the sleep loop. Acquires the session via ``idraa.db`` (no
    request context — same pattern as the executor's sessionmaker note).
    """
    from idraa.db import get_session

    async with get_session() as db:
        return await reap_orphaned_runs(db, settings)


async def sweep_wizard_drafts(settings: Settings) -> None:
    """Drafts-surfaced spec §4: TTL-sweep idle wizard drafts on the
    reaper cadence (public name — consumed by both the boot one-shot
    and the loop, DQ-13). 0 days = disabled."""
    ttl_days = settings.wizard_draft_ttl_days
    if ttl_days <= 0:
        return
    from idraa.db import get_session
    from idraa.services.wizard_state import WizardStateService

    async with get_session() as session:  # the exact idiom reap_once uses (run_reaper.py:196-199)
        deleted = await WizardStateService(session).cleanup_expired(
            max_age_minutes=ttl_days * 24 * 60
        )
        await session.commit()
    # F-5: cleanup_expired's docstring warns SQLite may report rowcount=-1
    # (dialect-dependent) — guard against logging a nonsensical negative
    # "deleted" count.
    if deleted and deleted > 0:
        logger.info("Wizard-draft TTL sweep deleted %d idle draft(s)", deleted)


async def sweep_expired_previews(settings: Settings) -> None:
    """Issue #80 (L9): TTL-sweep expired ``csv_import_preview`` rows.

    Mirrors :func:`sweep_wizard_drafts` exactly. The table has a TTL
    (``expires_at``, default 10 minutes — see
    ``models/csv_import_preview.py``) and an
    ``ix_csv_import_preview_expires_at`` index for this purpose, but no
    consumer ever swept it: an abandoned upload (never confirmed/applied)
    left its raw bytes in the DB forever. Uses the SQLAlchemy ``delete()``
    construct (cross-dialect) rather than raw SQL."""
    from idraa.db import get_session

    async with get_session() as session:
        result: CursorResult[Any] = await session.execute(  # type: ignore[assignment]
            delete(CSVImportPreview).where(CSVImportPreview.expires_at < now_utc())
        )
        await session.commit()
    deleted = result.rowcount
    # SQLite may report rowcount=-1 (dialect-dependent) — same guard as
    # sweep_wizard_drafts (run_reaper.py:220-221).
    if deleted and deleted > 0:
        logger.info("CSV import-preview TTL sweep deleted %d expired row(s)", deleted)


async def sweep_expired_sessions(settings: Settings) -> None:
    """Issue #80 (I2): TTL-sweep expired ``auth_sessions`` rows.

    Security-neutral housekeeping (expired sessions are already rejected at
    auth time) — this only bounds table growth. Mirrors
    :func:`sweep_expired_previews` / :func:`sweep_wizard_drafts`."""
    from idraa.db import get_session

    async with get_session() as session:
        result: CursorResult[Any] = await session.execute(  # type: ignore[assignment]
            delete(AuthSession).where(AuthSession.expires_at < now_utc())
        )
        await session.commit()
    deleted = result.rowcount
    if deleted and deleted > 0:
        logger.info("Expired auth_sessions TTL sweep deleted %d row(s)", deleted)


async def periodic_reaper_loop(settings: Settings) -> None:
    """#211 Phase 2: sweep orphaned runs every ``run_reaper_interval_seconds``.

    Spawned as an asyncio task from the FastAPI lifespan (and cancelled on
    shutdown). A sweep failure is logged and the loop continues — the reaper
    must never take the app down. Interval 0 disables the loop (the lifespan
    checks before spawning; the guard here is belt-and-suspenders).

    Scheduling caveat (documented, accepted): the loop shares the event loop
    with everything else, so it cannot fire WHILE a synchronous Monte Carlo
    call is blocking — it fires right after. That is sufficient: a row only
    becomes orphaned when no live task owns it, and the registry (not timing)
    is what protects live runs.
    """
    interval = settings.run_reaper_interval_seconds
    if interval <= 0:
        return
    while True:
        await asyncio.sleep(interval)
        try:
            await reap_once(settings)
        except asyncio.CancelledError:  # shutdown — propagate
            raise
        except Exception:  # never let a sweep bug kill the loop
            logger.exception("Periodic orphaned-run sweep failed; will retry next interval")
        try:
            await sweep_wizard_drafts(settings)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Wizard-draft TTL sweep failed; will retry next interval")
        try:
            await sweep_expired_previews(settings)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("CSV import-preview TTL sweep failed; will retry next interval")
        try:
            await sweep_expired_sessions(settings)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Expired auth_sessions TTL sweep failed; will retry next interval")
