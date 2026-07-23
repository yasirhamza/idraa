from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from idraa.models.audit_log import AuditLog
from idraa.models.user import User
from tests.conftest import csrf_post


@pytest.mark.asyncio
async def test_admin_unlock_clears_and_audits(authed_admin, db_session):
    # SELF-target: the admin's own user (email user@test.local, SAME org). Cross-
    # org fails — the route is org-scoped (get_user(db, id, me.organization_id)),
    # and admin_user/seed_user live in OTHER org fixtures -> 404. A fresh login
    # stamps reauthenticated_at (auth.py:238), so require_recent_auth passes.
    client, _org_id = authed_admin
    me = (
        await db_session.execute(select(User).where(User.email == "user@test.local"))
    ).scalar_one()
    me.locked_until = datetime.now(UTC) + timedelta(seconds=600)
    me.failed_login_count = 5
    await db_session.commit()
    r = await csrf_post(client, f"/users/{me.id}/unlock", {"confirm": "1"})
    assert r.status_code in (302, 303)
    await db_session.refresh(me)
    assert me.locked_until is None and me.failed_login_count == 0
    audit = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.entity_id == me.id, AuditLog.action == "user.login_unlocked"
            )
        )
    ).scalar_one_or_none()
    assert audit is not None


@pytest.mark.asyncio
async def test_unlock_requires_admin(authed_analyst, db_session):
    # require_role(ADMIN) 403s the analyst; target id is irrelevant (any uuid).
    import uuid

    client, _ = authed_analyst
    r = await csrf_post(client, f"/users/{uuid.uuid4()}/unlock", {"confirm": "1"})
    assert r.status_code == 403
