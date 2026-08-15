"""Per-account lockout counter is atomic (guarded UPDATE + RETURNING).

Same lost-update class as B6: concurrent failed logins must EACH count, and the
lock must trip at the true threshold. Non-vacuous concurrency test (#1) + a
deterministic statement-count discriminator for the set_committed_value choice
(#2) + threshold (#3) + no-dirty-flush (#4). See the design doc §2/§5.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest_asyncio
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from idraa.db import Base, _install_sqlite_pragmas, strict_json_dumps
from idraa.models.user import User
from idraa.services import auth


async def _create_schema(engine) -> None:
    import idraa.models  # noqa: F401  register all mappers

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@pytest_asyncio.fixture
async def engine(tmp_path: Path):
    url = f"sqlite+aiosqlite:///{(tmp_path / f'lck-{uuid.uuid4().hex}.db').as_posix()}"
    eng = create_async_engine(url, future=True, json_serializer=strict_json_dumps)
    _install_sqlite_pragmas(eng)
    try:
        await _create_schema(eng)
        yield eng
    finally:
        await eng.dispose()


@pytest_asyncio.fixture
async def sm(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def _settings(max_failed: int = 5, lockout: int = 900) -> SimpleNamespace:
    return SimpleNamespace(auth_max_failed_logins=max_failed, auth_lockout_seconds=lockout)


async def _seed_user(sm) -> uuid.UUID:
    from tests.factories import create_org, create_user

    async with sm() as s:
        org = await create_org(s)
        user = await create_user(s, org, email="lock@example.com")
        await s.commit()
        return user.id


async def _count(sm, uid: uuid.UUID) -> int:
    async with sm() as s:
        return int(
            (await s.execute(select(User.failed_login_count).where(User.id == uid))).scalar()
        )


# --- #1: concurrent increments each count (non-vacuous; red on old `+= 1`) ---


async def test_concurrent_misses_all_count(sm, monkeypatch) -> None:
    monkeypatch.setattr(auth, "get_settings", lambda: _settings(max_failed=100))  # avoid lock
    uid = await _seed_user(sm)

    # WARM the pool (per the B6 lesson — a cold engine serializes the coroutines).
    warm = [sm() for _ in range(5)]
    opened = [await w.__aenter__() for w in warm]
    for o in opened:
        await o.execute(select(func.count()).select_from(User))
    for w in warm:
        await w.__aexit__(None, None, None)

    async def miss() -> None:
        async with sm() as s:
            user = (await s.execute(select(User).where(User.id == uid))).scalar_one()
            await auth.register_failed_login(s, user)
            await s.commit()

    await asyncio.gather(*[miss() for _ in range(5)])
    assert await _count(sm, uid) == 5  # OLD `user.failed_login_count += 1` -> < 5 (RED)


# --- #2: exactly one `UPDATE users` per call (discriminates set_committed_value) ---


async def test_one_update_users_per_call(engine, sm, monkeypatch) -> None:
    monkeypatch.setattr(auth, "get_settings", lambda: _settings(max_failed=100))
    uid = await _seed_user(sm)

    updates: list[str] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _count_updates(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith("UPDATE USERS"):
            updates.append(statement)

    async with sm() as s:
        user = (await s.execute(select(User).where(User.id == uid))).scalar_one()
        await auth.register_failed_login(s, user)
        await s.commit()

    # set_committed_value -> exactly ONE UPDATE users. A plain attribute set would
    # emit a SECOND, redundant UPDATE users at flush (RED). Deterministic only
    # because the UPDATE pins synchronize_session=False.
    assert len(updates) == 1, f"expected 1 UPDATE users, got {len(updates)}: {updates}"


# --- #3: lockout trips exactly at the threshold; max=0 never locks ---


async def test_lock_trips_at_threshold(sm, monkeypatch) -> None:
    monkeypatch.setattr(auth, "get_settings", lambda: _settings(max_failed=3, lockout=900))
    uid = await _seed_user(sm)
    async with sm() as s:
        user = (await s.execute(select(User).where(User.id == uid))).scalar_one()
        await auth.register_failed_login(s, user)
        assert not auth.is_locked(user)  # 1
        await auth.register_failed_login(s, user)
        assert not auth.is_locked(user)  # 2
        await auth.register_failed_login(s, user)
        assert auth.is_locked(user)  # 3 -> locked
        assert user.locked_until is not None
        await s.commit()


async def test_max_zero_never_locks(sm, monkeypatch) -> None:
    monkeypatch.setattr(auth, "get_settings", lambda: _settings(max_failed=0))
    uid = await _seed_user(sm)
    async with sm() as s:
        user = (await s.execute(select(User).where(User.id == uid))).scalar_one()
        for _ in range(10):
            await auth.register_failed_login(s, user)
        assert not auth.is_locked(user)
        assert user.locked_until is None
        assert user.failed_login_count == 10
        await s.commit()


# --- #4: no dirty flush (set_committed_value keeps the instance clean) ---


async def test_register_leaves_user_clean(sm, monkeypatch) -> None:
    monkeypatch.setattr(auth, "get_settings", lambda: _settings(max_failed=100))
    uid = await _seed_user(sm)
    async with sm() as s:
        user = (await s.execute(select(User).where(User.id == uid))).scalar_one()
        await auth.register_failed_login(s, user)
        # In-memory reads (callers depend on these) are authoritative...
        assert user.failed_login_count == 1
        # ...AND the instance is NOT dirty -> no redundant blind UPDATE at flush.
        # (A plain attribute set would leave it in db.dirty.)
        assert user not in s.dirty
        await s.commit()
