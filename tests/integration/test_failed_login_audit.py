"""C2: sub-threshold failed logins are audited (detection blind-spot fix).

Before this, only the failed attempt that TRIPPED the lockout wrote an audit
row — attempts 1..N-1 (and, with lockout disabled, every attempt) were
invisible, so a low-and-slow campaign staying under the threshold left no
trace. The password path now mirrors the /login/mfa path: every failed
attempt by a KNOWN, unlocked user writes a ``user.login_failed`` row, bounded
by the per-source throttle. Unknown emails write nothing (no user to attribute
to, and no enumeration oracle).
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.config import get_settings
from idraa.models.audit_log import AuditLog
from idraa.models.user import User
from idraa.routes.auth import _FAILED_LOGIN_AUDIT_CAP
from tests.conftest import csrf_post
from tests.integration.test_login_flow import _seed_setup


async def _one_failed_login(client: AsyncClient) -> None:
    r = await csrf_post(
        client, "/login", {"email": "a@b.c", "password": "wrong"}, follow_redirects=False
    )
    assert r.status_code == 400


async def _seed_at_count(db: AsyncSession, count: int) -> None:
    """Push the seeded admin's failed_login_count directly (driving 50 HTTP
    misses would be slow). locked_until stays None so is_locked() is False."""
    user = (await db.execute(select(User).where(User.email == "a@b.c"))).scalar_one()
    user.failed_login_count = count
    await db.commit()


async def _failed_rows(db: AsyncSession) -> list[AuditLog]:
    return list(
        (
            await db.execute(
                select(AuditLog)
                .where(AuditLog.action == "user.login_failed")
                .order_by(AuditLog.timestamp)
            )
        )
        .scalars()
        .all()
    )


async def test_each_sub_threshold_failed_login_is_audited(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_setup(client)
    before = len(await _failed_rows(db_session))

    # Three misses — below the default lockout threshold (5), so none trips
    # the lockout; each must still be audited individually.
    for _ in range(3):
        r = await csrf_post(
            client,
            "/login",
            {"email": "a@b.c", "password": "wrong"},
            follow_redirects=False,
        )
        assert r.status_code == 400

    rows = await _failed_rows(db_session)
    assert len(rows) == before + 3
    row = rows[-1]
    assert row.entity_type == "user"
    assert row.user_id is not None


async def test_unknown_email_failed_login_is_not_audited(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_setup(client)
    before = len(await _failed_rows(db_session))
    r = await csrf_post(
        client,
        "/login",
        {"email": "nobody@example.test", "password": "wrong"},
        follow_redirects=False,
    )
    assert r.status_code == 400
    # No user → no user-scoped audit row (and no existence oracle).
    assert len(await _failed_rows(db_session)) == before


@pytest.mark.asyncio
async def test_audit_stops_at_config_independent_ceiling(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
) -> None:
    """The `_FAILED_LOGIN_AUDIT_CAP` ceiling bounds rows even when BOTH the
    account lockout and the per-source IP throttle are disabled — the exact
    self-hosted config where nothing else would bound the write (Sec-I1)."""
    await _seed_setup(client)
    s = get_settings()
    monkeypatch.setattr(s, "auth_max_failed_logins", 0, raising=False)  # lockout off
    monkeypatch.setattr(s, "auth_ip_max_failed_logins", 0, raising=False)  # IP throttle off

    # Sitting AT the cap: the next miss increments to CAP+1, over the ceiling.
    await _seed_at_count(db_session, _FAILED_LOGIN_AUDIT_CAP)
    before = len(await _failed_rows(db_session))
    await _one_failed_login(client)
    assert len(await _failed_rows(db_session)) == before  # over-ceiling miss NOT audited


@pytest.mark.asyncio
async def test_audit_writes_up_to_the_ceiling(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
) -> None:
    """The ceiling is inclusive — the CAP-th miss is still audited, so the
    detection signal is preserved right up to the bound."""
    await _seed_setup(client)
    s = get_settings()
    monkeypatch.setattr(s, "auth_max_failed_logins", 0, raising=False)
    monkeypatch.setattr(s, "auth_ip_max_failed_logins", 0, raising=False)

    # One below the cap: the next miss increments to exactly CAP (<= CAP).
    await _seed_at_count(db_session, _FAILED_LOGIN_AUDIT_CAP - 1)
    before = len(await _failed_rows(db_session))
    await _one_failed_login(client)
    assert len(await _failed_rows(db_session)) == before + 1  # the CAP-th miss IS audited
