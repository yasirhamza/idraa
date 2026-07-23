"""Per-source (per-IP) login throttle store (idraa#81).

DB-backed (LoginAttempt) because the app auto-stops to zero; in-memory state
would evaporate between the request gaps an attacker exploits. All store ops
FAIL OPEN — a store error is logged + swallowed and its write is confined to a
savepoint, so the throttle can never 500 or wedge login (the per-account
throttle is the independent primary defense).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.config import get_settings
from idraa.models.login_attempt import LoginAttempt

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:  # aiosqlite may strip tzinfo
        return dt.replace(tzinfo=UTC)
    return dt


async def is_ip_blocked(db: AsyncSession, source: str | None) -> datetime | None:
    if not source:
        return None
    try:
        row = (
            await db.execute(select(LoginAttempt).where(LoginAttempt.source_key == source))
        ).scalar_one_or_none()
    except Exception:  # fail-open: a store error must never block/allow-wrongly
        logger.exception("is_ip_blocked store read failed; treating as not blocked")
        return None
    if row is None:
        return None
    bu = _aware(row.blocked_until)
    return bu if bu is not None and bu > _now() else None


async def register_failed_source(db: AsyncSession, source: str | None) -> None:
    settings = get_settings()
    if not source or settings.auth_ip_max_failed_logins <= 0:
        return
    now = _now()
    try:
        # Savepoint: a store error rolls back ONLY the throttle write, leaving the
        # outer request txn (e.g. the per-account increment) intact. Without it,
        # a failed statement on Postgres aborts the whole txn and the terminal
        # get_db commit raises PendingRollbackError -> 500. This is the fail-open.
        async with db.begin_nested():
            dialect = db.bind.dialect.name if db.bind is not None else "sqlite"
            if dialect == "postgresql":
                from sqlalchemy.dialects.postgresql import insert as _insert
            else:
                from sqlalchemy.dialects.sqlite import insert as _insert  # type: ignore[assignment]
            # ATOMIC increment via ON CONFLICT — no read-then-INSERT collision on
            # the unique source_key, no lost-update on the count (the DB does +1).
            # updated_at in set_ because onupdate does NOT fire on ON CONFLICT SET,
            # and the sweep anchors on updated_at.
            stmt = _insert(LoginAttempt).values(
                source_key=source, failed_count=1, window_started_at=now
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["source_key"],
                set_={"failed_count": LoginAttempt.failed_count + 1, "updated_at": now},
            )
            await db.execute(stmt)
            # Re-read is FRESH: SQLAlchemy 2.0 expires the identity map after a DML
            # executed through the session (verified by repro), so this returns the
            # post-increment count, not a stale cached instance.
            row = (
                await db.execute(select(LoginAttempt).where(LoginAttempt.source_key == source))
            ).scalar_one()
            ws = _aware(row.window_started_at) or now
            if (now - ws).total_seconds() > settings.auth_ip_window_seconds:
                row.window_started_at = now
                row.failed_count = 1
            if row.failed_count >= settings.auth_ip_max_failed_logins:
                row.blocked_until = now + timedelta(seconds=settings.auth_ip_lockout_seconds)
    except Exception:  # fail-open — the savepoint rolled back; outer txn survives
        logger.exception("register_failed_source failed; continuing (fail-open)")
    # outer commit owned by the caller's get_db dependency


async def reset_source_throttle(db: AsyncSession, source: str | None) -> None:
    if not source:
        return
    try:
        async with db.begin_nested():  # same fail-open savepoint discipline
            row = (
                await db.execute(select(LoginAttempt).where(LoginAttempt.source_key == source))
            ).scalar_one_or_none()
            if row is not None:
                await db.delete(row)
    except Exception:
        logger.exception("reset_source_throttle failed; continuing (fail-open)")
