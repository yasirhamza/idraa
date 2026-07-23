from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from idraa.config import get_settings
from idraa.models.login_attempt import LoginAttempt


@pytest.mark.asyncio
async def test_sweep_predicate_deletes_inactive_keeps_active(db_session):
    s = get_settings()
    old = datetime.now(UTC) - timedelta(
        seconds=s.auth_ip_window_seconds + s.auth_ip_lockout_seconds + 10
    )
    inactive = LoginAttempt(
        source_key="login:1.1.1.1", failed_count=2, window_started_at=old, blocked_until=None
    )
    inactive.updated_at = old
    active = LoginAttempt(
        source_key="login:2.2.2.2",
        failed_count=9,
        window_started_at=datetime.now(UTC),
        blocked_until=datetime.now(UTC) + timedelta(seconds=600),
    )
    db_session.add_all([inactive, active])
    await db_session.commit()
    max_age = max(s.auth_ip_window_seconds, s.auth_ip_lockout_seconds)
    cutoff = datetime.now(UTC) - timedelta(seconds=max_age)
    await db_session.execute(
        delete(LoginAttempt).where(
            (LoginAttempt.blocked_until.is_(None))
            | (LoginAttempt.blocked_until < datetime.now(UTC)),
            LoginAttempt.updated_at < cutoff,
        )
    )
    await db_session.commit()
    remaining = {r.source_key for r in (await db_session.execute(select(LoginAttempt))).scalars()}
    assert remaining == {"login:2.2.2.2"}
