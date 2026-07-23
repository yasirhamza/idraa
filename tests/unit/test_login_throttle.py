from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.sql.dml import Insert

from idraa.config import get_settings
from idraa.models.login_attempt import LoginAttempt
from idraa.services.login_throttle import (
    is_ip_blocked,
    register_failed_source,
    reset_source_throttle,
)


async def _row(db, key):
    return (
        await db.execute(select(LoginAttempt).where(LoginAttempt.source_key == key))
    ).scalar_one_or_none()


@pytest.mark.asyncio
async def test_threshold_trips_block(db_session, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "auth_ip_max_failed_logins", 3, raising=False)
    for _ in range(2):
        await register_failed_source(db_session, "login:1.2.3.4")
    assert await is_ip_blocked(db_session, "login:1.2.3.4") is None
    await register_failed_source(db_session, "login:1.2.3.4")
    blocked = await is_ip_blocked(db_session, "login:1.2.3.4")
    assert blocked is not None and blocked > datetime.now(UTC)


@pytest.mark.asyncio
async def test_reset_and_disabled_and_none(db_session, monkeypatch):
    await register_failed_source(db_session, "login:9.9.9.9")
    await reset_source_throttle(db_session, "login:9.9.9.9")
    assert await _row(db_session, "login:9.9.9.9") is None
    monkeypatch.setattr(get_settings(), "auth_ip_max_failed_logins", 0, raising=False)
    for _ in range(50):
        await register_failed_source(db_session, "login:5.5.5.5")
    assert await is_ip_blocked(db_session, "login:5.5.5.5") is None
    await register_failed_source(db_session, None)  # no-op, no raise
    assert await is_ip_blocked(db_session, None) is None


@pytest.mark.asyncio
async def test_window_expiry_resets_count(db_session, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "auth_ip_window_seconds", 1, raising=False)
    monkeypatch.setattr(s, "auth_ip_max_failed_logins", 5, raising=False)
    await register_failed_source(db_session, "login:2.2.2.2")
    row = await _row(db_session, "login:2.2.2.2")
    row.window_started_at = datetime.now(UTC) - timedelta(seconds=5)
    await db_session.commit()
    await register_failed_source(db_session, "login:2.2.2.2")
    assert (await _row(db_session, "login:2.2.2.2")).failed_count == 1


@pytest.mark.asyncio
async def test_upsert_idempotent_one_row(db_session, monkeypatch):
    # Repeated registers from one source take the ON CONFLICT path — exactly ONE
    # row, count accumulates, no duplicate-INSERT IntegrityError on the unique
    # source_key. (True multi-session concurrency is guaranteed by the DB-level
    # ON CONFLICT DO UPDATE, not unit-testable on one AsyncSession — a single
    # session forbids concurrent operations / concurrent savepoints; the no-500
    # property is covered by the Task 4 route test.)
    monkeypatch.setattr(get_settings(), "auth_ip_max_failed_logins", 100, raising=False)
    for _ in range(5):
        await register_failed_source(db_session, "login:7.7.7.7")
    rows = (
        (
            await db_session.execute(
                select(LoginAttempt).where(LoginAttempt.source_key == "login:7.7.7.7")
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1 and rows[0].failed_count == 5


@pytest.mark.asyncio
async def test_register_failed_source_fail_open_leaves_outer_txn_usable(db_session, monkeypatch):
    # Fail-open contract: a store error inside the begin_nested() savepoint
    # must be (1) swallowed by register_failed_source's except Exception, AND
    # (2) confined to the savepoint so the outer session transaction is still
    # usable afterward. Without the savepoint, a failed statement poisons the
    # whole txn and the caller's get_db teardown commit raises
    # PendingRollbackError -> 500, defeating the fail-open promise.
    #
    # A synthetic RuntimeError raised in Python *before* touching the DBAPI
    # never poisons the SQLAlchemy Session, so it can't discriminate
    # savepoint-vs-no-savepoint (verified empirically: that variant passes
    # identically with or without `begin_nested()`, i.e. it's tautological).
    # `Session.flush()` failures, however, deactivate the enclosing
    # SessionTransaction on a real DBAPI error regardless of backend -- so we
    # force the throttle's write to collide with a real UNIQUE violation and
    # flush it for real, reproducing the exact PendingRollbackError condition
    # the savepoint exists to contain.
    s = get_settings()
    monkeypatch.setattr(s, "auth_ip_max_failed_logins", 3, raising=False)

    db_session.add(
        LoginAttempt(
            source_key="login:9.9.9.9", failed_count=1, window_started_at=datetime.now(UTC)
        )
    )
    await db_session.commit()

    original_execute = db_session.execute

    async def _raising_execute(statement, *args, **kwargs):
        if isinstance(statement, Insert):
            # A second row with the same unique source_key -> real
            # IntegrityError on flush, not a synthetic Python exception.
            db_session.add(
                LoginAttempt(
                    source_key="login:9.9.9.9",
                    failed_count=1,
                    window_started_at=datetime.now(UTC),
                )
            )
            await db_session.flush()
        return await original_execute(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "execute", _raising_execute)

    # Must not raise: the IntegrityError from the forced flush is caught and
    # swallowed by register_failed_source's fail-open except clause.
    await register_failed_source(db_session, "login:9.9.9.9")

    # Restore the real execute before touching the session again.
    monkeypatch.undo()

    # Proves the savepoint rolled back ONLY the inner failure: the outer
    # transaction is still usable, so commit succeeds (no PendingRollbackError).
    await db_session.commit()
