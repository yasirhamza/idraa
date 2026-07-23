"""Store-lifecycle reaper sweeps (issue #80).

Two TTL-bearing tables that had no reaper consumer before this issue:

- ``csv_import_preview`` (L9) — 10-minute TTL, ``ix_csv_import_preview_expires_at``
  index existed but nothing ever swept expired rows.
- ``auth_sessions`` (I2) — expired sessions are already rejected at auth
  time (security-neutral), but nothing ever purged them, so the table grows
  unbounded (2026-06-29 SQLite full-disk-outage lesson: bounded rows +
  purge).

Mirrors ``test_wizard_draft_sweep.py``'s style exactly: local seed helpers,
one behavior per test, plus a periodic-loop resilience test.
"""

from __future__ import annotations

import asyncio
import datetime
import uuid
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

import idraa.services.run_reaper as run_reaper
from idraa.config import get_settings
from idraa.models._types import now_utc
from idraa.models.csv_import_preview import CSVImportPreview
from idraa.models.organization import Organization
from idraa.models.session import AuthSession
from idraa.models.user import User

pytestmark = pytest.mark.asyncio


async def _mk_preview(
    db: AsyncSession, org: Organization, user: User, *, age_seconds: int
) -> uuid.UUID:
    """A staged CSV-import-preview row whose ``expires_at`` is
    ``age_seconds`` in the past (negative = not yet expired).

    The ``ck_csv_import_preview_expiry_after_creation`` CHECK constraint
    (``expires_at > created_at``) is re-evaluated on every INSERT *and*
    UPDATE — back-dating ``expires_at`` alone (after an insert with the
    default ``created_at=now``) would violate it. INSERT with a safely
    future ``expires_at`` first, then (for the expired case) back-date
    BOTH columns together in one UPDATE so the constraint holds at every
    step (mirrors ``test_get_staged_expired_row_raises_and_deletes``'s
    idiom in ``test_register_import_service.py``)."""
    row = CSVImportPreview(
        organization_id=org.id,
        created_by_user_id=user.id,
        entity_type="register:csv",
        csv_bytes=b"title\nfoo\n",
        expires_at=now_utc() + datetime.timedelta(seconds=600),
    )
    db.add(row)
    await db.flush()
    if age_seconds > 0:
        expires_at = now_utc() - datetime.timedelta(seconds=age_seconds)
        await db.execute(
            update(CSVImportPreview)
            .where(CSVImportPreview.id == row.id)
            .values(created_at=expires_at - datetime.timedelta(seconds=1), expires_at=expires_at)
        )
    await db.commit()
    return row.id


async def _mk_session(db: AsyncSession, user: User, *, age_seconds: int) -> uuid.UUID:
    session_id = uuid.uuid4()
    row = AuthSession(
        id=session_id,
        user_id=user.id,
        expires_at=now_utc() - datetime.timedelta(seconds=age_seconds),
    )
    db.add(row)
    await db.flush()
    await db.commit()
    return session_id


# ---------------------------------------------------------------------------
# sweep_expired_previews (L9)
# ---------------------------------------------------------------------------


async def test_sweep_expired_previews_deletes_old_keeps_recent(
    seed_user: User,
    seed_organization: Organization,
    db_session: AsyncSession,
    wire_executor_to_test_db: None,
) -> None:
    expired_id = await _mk_preview(db_session, seed_organization, seed_user, age_seconds=60)
    fresh_id = await _mk_preview(db_session, seed_organization, seed_user, age_seconds=-600)

    await run_reaper.sweep_expired_previews(get_settings())

    remaining = (await db_session.execute(select(CSVImportPreview.id))).scalars().all()
    assert fresh_id in remaining and expired_id not in remaining


async def test_sweep_expired_previews_noop_when_none_expired(
    seed_user: User,
    seed_organization: Organization,
    db_session: AsyncSession,
    wire_executor_to_test_db: None,
) -> None:
    fresh_id = await _mk_preview(db_session, seed_organization, seed_user, age_seconds=-600)

    await run_reaper.sweep_expired_previews(get_settings())

    remaining = (await db_session.execute(select(CSVImportPreview.id))).scalars().all()
    assert fresh_id in remaining


# ---------------------------------------------------------------------------
# sweep_expired_sessions (I2)
# ---------------------------------------------------------------------------


async def test_sweep_expired_sessions_deletes_old_keeps_recent(
    seed_user: User, db_session: AsyncSession, wire_executor_to_test_db: None
) -> None:
    expired_id = await _mk_session(db_session, seed_user, age_seconds=60)
    fresh_id = await _mk_session(db_session, seed_user, age_seconds=-3600)

    await run_reaper.sweep_expired_sessions(get_settings())

    remaining = (await db_session.execute(select(AuthSession.id))).scalars().all()
    assert fresh_id in remaining and expired_id not in remaining


async def test_sweep_expired_sessions_noop_when_none_expired(
    seed_user: User, db_session: AsyncSession, wire_executor_to_test_db: None
) -> None:
    fresh_id = await _mk_session(db_session, seed_user, age_seconds=-3600)

    await run_reaper.sweep_expired_sessions(get_settings())

    remaining = (await db_session.execute(select(AuthSession.id))).scalars().all()
    assert fresh_id in remaining


# ---------------------------------------------------------------------------
# periodic_reaper_loop resilience — a sweep bug in either new sweep must not
# kill the loop or block the other sweeps (mirrors
# test_wizard_draft_sweep.py::test_sweep_exception_does_not_kill_reaper_loop).
# ---------------------------------------------------------------------------


async def test_new_sweep_exceptions_do_not_kill_reaper_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _StubSettings:
        run_reaper_interval_seconds = 0.01

    reap_once_calls = 0

    async def _counting_reap_once(settings: Any) -> int:
        nonlocal reap_once_calls
        reap_once_calls += 1
        return 0

    async def _noop_wizard_sweep(settings: Any) -> None:
        return None

    async def _raising_preview_sweep(settings: Any) -> None:
        raise RuntimeError("boom — simulated csv_import_preview sweep failure")

    async def _raising_session_sweep(settings: Any) -> None:
        raise RuntimeError("boom — simulated auth_sessions sweep failure")

    monkeypatch.setattr(run_reaper, "reap_once", _counting_reap_once)
    monkeypatch.setattr(run_reaper, "sweep_wizard_drafts", _noop_wizard_sweep)
    monkeypatch.setattr(run_reaper, "sweep_expired_previews", _raising_preview_sweep)
    monkeypatch.setattr(run_reaper, "sweep_expired_sessions", _raising_session_sweep)

    task = asyncio.create_task(run_reaper.periodic_reaper_loop(_StubSettings()))  # type: ignore[arg-type]
    await asyncio.sleep(0.05)  # >= 2 intervals at 0.01s each
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()
    assert reap_once_calls >= 2, "reap_once must keep running despite both sweep exceptions"
