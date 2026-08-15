"""B6: recovery-code burn is atomic (guarded UPDATE + rowcount).

Layer 1 (deterministic primitive) + Layer 2 (WARM concurrent race, non-vacuous)
+ Layer 3 (loser / already-consumed contracts) + Layer 4b (call-site source
tripwire). See docs/superpowers/specs/2026-08-15-b6-a3-...-design.md §6.
"""

from __future__ import annotations

import asyncio
import inspect
import uuid
from pathlib import Path

import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from idraa.db import Base, _install_sqlite_pragmas, strict_json_dumps
from idraa.models._types import now_utc
from idraa.models.audit_log import AuditLog
from idraa.models.mfa import RecoveryCode
from idraa.models.user import User
from idraa.services import second_factor
from idraa.services.mfa_crypto import hash_recovery_code

# asyncio_mode = "auto" (pyproject) collects async tests without an explicit
# mark; NOT applying a module-level pytest.mark.asyncio here so the one sync
# test (the source tripwire) isn't spuriously marked.


async def _create_schema(engine) -> None:
    import idraa.models  # noqa: F401  register all mappers

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@pytest_asyncio.fixture
async def sm(tmp_path: Path):
    url = f"sqlite+aiosqlite:///{(tmp_path / f'atom-{uuid.uuid4().hex}.db').as_posix()}"
    engine = create_async_engine(url, future=True, json_serializer=strict_json_dumps)
    _install_sqlite_pragmas(engine)
    try:
        await _create_schema(engine)
        yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _seed(sm) -> tuple[uuid.UUID, uuid.UUID]:
    from tests.factories import create_org, create_user

    async with sm() as s:
        org = await create_org(s)
        user = await create_user(s, org, email="rc@example.com")
        rc = RecoveryCode(user_id=user.id, code_hash=hash_recovery_code("aaaaa-bbbbb"))
        s.add(rc)
        await s.commit()
        return user.id, rc.id


async def _audit_count(sm, action: str) -> int:
    async with sm() as s:
        return int(
            (
                await s.execute(
                    select(func.count()).select_from(AuditLog).where(AuditLog.action == action)
                )
            ).scalar()
        )


# --- Layer 1: deterministic primitive (offload-independent) ---


async def test_claim_recovery_code_is_won_exactly_once(sm) -> None:
    _uid, rc_id = await _seed(sm)
    now = now_utc()
    async with sm() as s1, sm() as s2:
        won_1 = await second_factor._claim_recovery_code(s1, rc_id, now)
        await s1.commit()
        won_2 = await second_factor._claim_recovery_code(s2, rc_id, now)
        await s2.commit()
    assert won_1 is True
    assert won_2 is False
    async with sm() as s:
        rc = (await s.execute(select(RecoveryCode).where(RecoveryCode.id == rc_id))).scalar_one()
        assert rc.used_at is not None


# --- Layer 2: WARM concurrent race (non-vacuous by construction) ---


async def test_concurrent_redemption_wins_once_warm(sm) -> None:
    user_id, rc_id = await _seed(sm)

    # WARM the pool: open both sessions and run a trivial query so aiosqlite
    # connection creation is NOT on the race's critical path (a cold engine
    # serializes the coroutines past the first commit -> vacuous test).
    s_a, s_b = sm(), sm()
    a = await s_a.__aenter__()
    b = await s_b.__aenter__()
    await a.execute(select(func.count()).select_from(User))
    await b.execute(select(func.count()).select_from(User))

    async def redeem(sess: AsyncSession) -> str | None:
        user = (await sess.execute(select(User).where(User.id == user_id))).scalar_one()
        m = await second_factor.verify_totp_or_recovery(
            sess, user, "aaaaa-bbbbb", ip_address="127.0.0.1"
        )
        await sess.commit()
        return m

    try:
        results = await asyncio.gather(redeem(a), redeem(b))
    finally:
        await s_a.__aexit__(None, None, None)
        await s_b.__aexit__(None, None, None)

    assert sorted(r or "none" for r in results) == ["none", "recovery"]
    assert await _audit_count(sm, "user.recovery_code_used") == 1
    async with sm() as s:
        rc = (await s.execute(select(RecoveryCode).where(RecoveryCode.id == rc_id))).scalar_one()
        assert rc.used_at is not None


# --- Layer 3: loser + already-consumed contracts (deterministic) ---


async def test_lost_claim_rejects_and_audits(sm, monkeypatch) -> None:
    """A matched code whose atomic claim loses -> None + recovery_code_claim_lost
    audit, never "recovery", never a 500."""
    user_id, _rc_id = await _seed(sm)

    async def always_lose(db, rc_id, now):
        return False

    monkeypatch.setattr(second_factor, "_claim_recovery_code", always_lose)
    async with sm() as s:
        user = (await s.execute(select(User).where(User.id == user_id))).scalar_one()
        method = await second_factor.verify_totp_or_recovery(
            s, user, "aaaaa-bbbbb", ip_address="127.0.0.1"
        )
        await s.commit()
    assert method is None
    assert await _audit_count(sm, "user.recovery_code_claim_lost") == 1
    assert await _audit_count(sm, "user.recovery_code_used") == 0


async def test_already_consumed_code_rejects_cleanly(sm) -> None:
    """A code already burned (used_at set) is filtered by the SELECT, so it never
    reaches the claim -> clean None, no claim_lost row, no error."""
    user_id, rc_id = await _seed(sm)
    async with sm() as s:
        rc = (await s.execute(select(RecoveryCode).where(RecoveryCode.id == rc_id))).scalar_one()
        rc.used_at = now_utc()
        await s.commit()
    async with sm() as s:
        user = (await s.execute(select(User).where(User.id == user_id))).scalar_one()
        method = await second_factor.verify_totp_or_recovery(
            s, user, "aaaaa-bbbbb", ip_address="127.0.0.1"
        )
        await s.commit()
    assert method is None
    assert await _audit_count(sm, "user.recovery_code_claim_lost") == 0


# --- Layer 4b: call-site source tripwire (scoped to the functions) ---


def test_recovery_burn_is_guarded_update_at_call_site() -> None:
    helper = inspect.getsource(second_factor._claim_recovery_code)
    assert "update(RecoveryCode)" in helper
    assert "RecoveryCode.used_at.is_(None)" in helper
    assert "rowcount == 1" in helper
    body = inspect.getsource(second_factor.verify_totp_or_recovery)
    assert "_claim_recovery_code(" in body
    assert "used_at =" not in body  # no plain attribute-set burn (the B6 defect)
