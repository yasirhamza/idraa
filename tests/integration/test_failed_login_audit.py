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

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.audit_log import AuditLog
from tests.conftest import csrf_post
from tests.integration.test_login_flow import _seed_setup


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
