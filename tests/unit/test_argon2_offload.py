"""A3: hot/auth-path Argon2 runs on the dedicated pool via auth._hash_offload.

Both branches of verify_user_password (dummy anti-enumeration path AND real
path) must be offloaded, or the timing-equal enumeration guarantee breaks; the
recovery-code match must be offloaded too, or A3's event-loop block (and B6's
HTTP mask) remains.
"""

from __future__ import annotations

import inspect
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from idraa.db import Base, _install_sqlite_pragmas, strict_json_dumps
from idraa.models.mfa import RecoveryCode
from idraa.models.user import User
from idraa.services import auth, second_factor
from idraa.services.mfa_crypto import hash_recovery_code


def test_verify_user_password_is_coroutine() -> None:
    assert inspect.iscoroutinefunction(auth.verify_user_password)


async def test_missing_user_branch_offloads_dummy_hash(monkeypatch) -> None:
    calls: list[tuple] = []

    async def fake_offload(fn, *args):
        calls.append((fn, args))
        return fn(*args)

    monkeypatch.setattr(auth, "_hash_offload", fake_offload)
    assert await auth.verify_user_password(None, "whatever") is False
    # Exactly one Argon2 verify, offloaded, against the DUMMY hash.
    assert len(calls) == 1
    fn, args = calls[0]
    assert fn is auth.verify_password
    assert args[1] == auth._DUMMY_PW_HASH


async def test_inactive_user_branch_offloads_dummy_hash(monkeypatch) -> None:
    calls: list[tuple] = []

    async def fake_offload(fn, *args):
        calls.append((fn, args))
        return fn(*args)

    monkeypatch.setattr(auth, "_hash_offload", fake_offload)
    user = SimpleNamespace(is_active=False, password_hash="$argon2-inactive")
    assert await auth.verify_user_password(user, "pw") is False
    assert len(calls) == 1
    # Inactive users take the dummy path (no oracle on active vs inactive).
    assert calls[0][1][1] == auth._DUMMY_PW_HASH


async def test_real_user_branch_offloads_real_hash(monkeypatch) -> None:
    calls: list[tuple] = []

    async def fake_offload(fn, *args):
        calls.append((fn, args))
        return True

    monkeypatch.setattr(auth, "_hash_offload", fake_offload)
    user = SimpleNamespace(is_active=True, password_hash="$argon2-real")
    assert await auth.verify_user_password(user, "pw") is True
    assert len(calls) == 1
    assert calls[0][0] is auth.verify_password
    assert calls[0][1][1] == "$argon2-real"


async def _create_schema(engine) -> None:
    import idraa.models  # noqa: F401  register all mappers

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@pytest_asyncio.fixture
async def sm(tmp_path: Path):
    url = f"sqlite+aiosqlite:///{(tmp_path / f'off-{uuid.uuid4().hex}.db').as_posix()}"
    engine = create_async_engine(url, future=True, json_serializer=strict_json_dumps)
    _install_sqlite_pragmas(engine)
    try:
        await _create_schema(engine)
        yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    finally:
        await engine.dispose()


async def test_recovery_match_is_offloaded(monkeypatch, sm) -> None:
    """verify_totp_or_recovery routes its recovery-code Argon2 through
    _hash_offload — driven end-to-end, asserting the spy fired (not a source
    grep). Fails if the offload is dropped."""
    from tests.factories import create_org, create_user

    async with sm() as s:
        org = await create_org(s)
        user = await create_user(s, org, email="off@example.com")
        s.add(RecoveryCode(user_id=user.id, code_hash=hash_recovery_code("aaaaa-bbbbb")))
        await s.commit()
        user_id = user.id

    seen: list[str] = []
    real = second_factor._hash_offload

    async def spy(fn, *args):
        seen.append(getattr(fn, "__name__", str(fn)))
        return await real(fn, *args)

    monkeypatch.setattr(second_factor, "_hash_offload", spy)
    async with sm() as s:
        user = (await s.execute(select(User).where(User.id == user_id))).scalar_one()
        method = await second_factor.verify_totp_or_recovery(
            s, user, "aaaaa-bbbbb", ip_address="127.0.0.1"
        )
        await s.commit()
    assert method == "recovery"
    assert "_match_recovery_code" in seen
