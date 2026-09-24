"""B5: WebAuthn challenge single-use claim is atomic (advisory GHSA-46jj-823j-mjj9).

Layer 1 (deterministic primitive) + Layer 2 (WARM concurrent race,
non-vacuous). Mirrors tests/services/test_recovery_code_atomicity.py's ``sm``
fixture + warm-pool race harness verbatim — see
docs/superpowers/specs/2026-09-23-webauthn-challenge-single-use-design.md
Tests 1-2.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from idraa.db import Base, _install_sqlite_pragmas, strict_json_dumps
from idraa.models._types import now_utc
from idraa.models.enums import WebAuthnChallengePurpose
from idraa.models.webauthn_challenge_consumed import WebAuthnChallengeConsumed
from idraa.services import webauthn_challenge
from idraa.services.auth import WEBAUTHN_CHALLENGE_MAX_AGE

# asyncio_mode = "auto" (pyproject) collects async tests without an explicit
# mark.


async def _create_schema(engine) -> None:
    import idraa.models  # noqa: F401  register all mappers

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@pytest_asyncio.fixture
async def sm(tmp_path: Path):
    url = f"sqlite+aiosqlite:///{(tmp_path / f'wac-{uuid.uuid4().hex}.db').as_posix()}"
    engine = create_async_engine(url, future=True, json_serializer=strict_json_dumps)
    _install_sqlite_pragmas(engine)
    try:
        await _create_schema(engine)
        yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    finally:
        await engine.dispose()


# --- Layer 1: deterministic primitive (offload-independent) ---


async def test_consume_challenge_true_once_then_false(sm) -> None:
    async with sm() as s1, sm() as s2:
        won_1 = await webauthn_challenge.consume_challenge(
            s1, "challenge-a", WebAuthnChallengePurpose.LOGIN
        )
        await s1.commit()
        won_2 = await webauthn_challenge.consume_challenge(
            s2, "challenge-a", WebAuthnChallengePurpose.LOGIN
        )
        await s2.commit()
    assert won_1 is True
    assert won_2 is False


async def test_consume_challenge_true_for_a_different_challenge(sm) -> None:
    async with sm() as s1, sm() as s2:
        won_1 = await webauthn_challenge.consume_challenge(
            s1, "challenge-a", WebAuthnChallengePurpose.LOGIN
        )
        await s1.commit()
        won_2 = await webauthn_challenge.consume_challenge(
            s2, "challenge-b", WebAuthnChallengePurpose.LOGIN
        )
        await s2.commit()
    assert won_1 is True
    assert won_2 is True


async def test_consume_challenge_expiry_window_matches_max_age(sm) -> None:
    async with sm() as s:
        assert await webauthn_challenge.consume_challenge(
            s, "challenge-c", WebAuthnChallengePurpose.STEPUP
        )
        await s.commit()
    async with sm() as s:
        row = (
            await s.execute(
                select(WebAuthnChallengeConsumed).where(
                    WebAuthnChallengeConsumed.challenge_digest
                    == webauthn_challenge._digest("challenge-c")
                )
            )
        ).scalar_one()
        assert row.expires_at - row.consumed_at >= timedelta(seconds=WEBAUTHN_CHALLENGE_MAX_AGE)


async def test_consume_challenge_raises_on_dirty_session(sm) -> None:
    """S3: the claim must be won BEFORE any ORM mutation in the caller's
    session — consume_challenge defensively refuses a session that already
    carries a pending (unflushed) mutation."""
    async with sm() as s:
        s.add(
            WebAuthnChallengeConsumed(
                challenge_digest="0" * 64,
                purpose=WebAuthnChallengePurpose.LOGIN,
                consumed_at=now_utc(),
                expires_at=now_utc(),
            )
        )  # populates db.new — a pending mutation, not yet flushed/committed
        with pytest.raises(RuntimeError):
            await webauthn_challenge.consume_challenge(
                s, "challenge-d", WebAuthnChallengePurpose.LOGIN
            )


# --- Layer 2: WARM concurrent race (non-vacuous by construction) ---


async def test_concurrent_claim_wins_once_warm(sm) -> None:
    # WARM the pool: open both sessions and run a trivial query so aiosqlite
    # connection creation is NOT on the race's critical path (a cold engine
    # serializes the coroutines past the first commit -> vacuous test).
    s_a, s_b = sm(), sm()
    a = await s_a.__aenter__()
    b = await s_b.__aenter__()
    await a.execute(select(WebAuthnChallengeConsumed))
    await b.execute(select(WebAuthnChallengeConsumed))

    async def claim(sess: AsyncSession) -> bool:
        won = await webauthn_challenge.consume_challenge(
            sess, "race-challenge", WebAuthnChallengePurpose.LOGIN
        )
        await sess.commit()
        return won

    try:
        results = await asyncio.gather(claim(a), claim(b))
    finally:
        await s_a.__aexit__(None, None, None)
        await s_b.__aexit__(None, None, None)

    assert sorted(results) == [False, True]
