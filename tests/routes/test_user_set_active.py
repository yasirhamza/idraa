"""Route tests for ``POST /users/{user_id}/set-active`` (#296).

The per-row Activate / Deactivate toggle carries security-critical guards
(self-deactivation lockout, last-active-admin lockout) plus RBAC + org
scoping. These tests pin the handler's *actual* behavior:

  - form field is ``active`` ("1"/"0"; falsey = "", "0", "false", "False").
  - self-deactivation -> 400 ("You cannot deactivate yourself").
  - last active admin deactivation -> 400 ("Cannot deactivate the last
    active admin").
  - reviewer -> 403 (rejected at ``require_role(ADMIN)``).
  - cross-org target -> 404 (org-scoped fetch returns None).
  - successful (de)activation -> 303 redirect to ``/users`` + ``is_active``
    flips + an ``action="update"`` audit row with ``changes["is_active"]``.

Reuses the same ``csrf_post`` helper + ``authed_admin`` / ``authed_reviewer``
clients + user-factory helpers as ``test_user_delete.py``.

Reachability note on the last-admin guard for a *non-self* target: the
handler's last-admin branch only fires when the (admin) target is the sole
active admin in the org. But the acting admin (``user@test.local``) is
ALWAYS an active admin, so any *second* admin target leaves the active-admin
count at >= 2 and the guard cannot fire on them. The last-admin lockout is
therefore only reachable via self-deactivation — where the self-guard fires
first. ``test_set_active_deactivate_last_admin_self_returns_400`` covers that
reachable path; ``test_set_active_deactivate_second_admin_succeeds`` confirms
a non-last admin can be deactivated (count stays >= 1).
"""

from __future__ import annotations

import uuid

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.audit_log import AuditLog
from idraa.models.enums import UserRole
from idraa.models.user import User
from tests.conftest import csrf_post
from tests.factories import create_org, create_user


async def _admin_org_id(db_session: AsyncSession) -> uuid.UUID:
    me = (
        await db_session.execute(select(User).where(User.email == "user@test.local"))
    ).scalar_one()
    return me.organization_id


async def _seed_user_in_org(
    db_session: AsyncSession,
    org_id: uuid.UUID,
    *,
    email: str,
    role: UserRole = UserRole.ANALYST,
    is_active: bool = True,
) -> User:
    from idraa.models.organization import Organization

    org = (
        await db_session.execute(select(Organization).where(Organization.id == org_id))
    ).scalar_one()
    user = await create_user(db_session, org, email=email, role=role)
    if not is_active:
        user.is_active = False
    return user


async def test_set_active_deactivate_self_returns_400(
    authed_admin: tuple[AsyncClient, object], db_session: AsyncSession
) -> None:
    """Admin deactivating themselves -> 400 (self-lockout); actor stays active."""
    client, _ = authed_admin
    me = (
        await db_session.execute(select(User).where(User.email == "user@test.local"))
    ).scalar_one()
    me_id = me.id

    r = await csrf_post(
        client,
        f"/users/{me_id}/set-active",
        {"active": "0"},
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert "yourself" in r.text.lower()
    db_session.expire_all()
    still = (await db_session.execute(select(User).where(User.id == me_id))).scalar_one()
    assert still.is_active is True


async def test_set_active_deactivate_last_admin_self_returns_400(
    authed_admin: tuple[AsyncClient, object], db_session: AsyncSession
) -> None:
    """The last active admin (the actor) cannot be deactivated -> 400.

    The actor IS the sole active admin. Deactivating them hits the self-guard
    first (it precedes the last-admin guard in the handler), so the message is
    the self-lockout one — but the security-critical outcome (the org keeps an
    active admin) holds either way.
    """
    client, _ = authed_admin
    me = (
        await db_session.execute(select(User).where(User.email == "user@test.local"))
    ).scalar_one()
    me_id = me.id
    # Confirm precondition: exactly one active admin (the actor).
    from sqlalchemy import func

    count = await db_session.scalar(
        select(func.count())
        .select_from(User)
        .where(
            User.organization_id == me.organization_id,
            User.role == UserRole.ADMIN,
            User.is_active == True,  # noqa: E712
        )
    )
    assert count == 1

    r = await csrf_post(
        client,
        f"/users/{me_id}/set-active",
        {"active": "0"},
        follow_redirects=False,
    )
    assert r.status_code == 400
    db_session.expire_all()
    still = (await db_session.execute(select(User).where(User.id == me_id))).scalar_one()
    assert still.is_active is True


async def test_set_active_reviewer_forbidden(
    authed_reviewer: tuple[AsyncClient, object], db_session: AsyncSession
) -> None:
    """Reviewer calling set-active -> 403 at the require_role(ADMIN) gate."""
    client, org_id = authed_reviewer
    target = await _seed_user_in_org(
        db_session, org_id, email="toggleme@test.local", is_active=True
    )
    await db_session.commit()
    target_id = target.id

    r = await csrf_post(
        client,
        f"/users/{target_id}/set-active",
        {"active": "0"},
        follow_redirects=False,
    )
    assert r.status_code == 403
    db_session.expire_all()
    still = (await db_session.execute(select(User).where(User.id == target_id))).scalar_one()
    assert still.is_active is True


async def test_set_active_cross_org_returns_404(
    authed_admin: tuple[AsyncClient, object], db_session: AsyncSession
) -> None:
    """Target in a different org -> 404 (org-scoped fetch returns None)."""
    client, _ = authed_admin
    other_org = await create_org(db_session, name="Other Org SetActive")
    other_user = await create_user(db_session, other_org, email="outsider-sa@test.local")
    await db_session.commit()
    other_id = other_user.id

    r = await csrf_post(
        client,
        f"/users/{other_id}/set-active",
        {"active": "0"},
        follow_redirects=False,
    )
    assert r.status_code == 404
    db_session.expire_all()
    still = (await db_session.execute(select(User).where(User.id == other_id))).scalar_one()
    # Untouched.
    assert still.is_active is True


async def test_set_active_deactivate_analyst_succeeds_and_audits(
    authed_admin: tuple[AsyncClient, object], db_session: AsyncSession
) -> None:
    """Deactivating a non-self, non-admin user succeeds: 303 + is_active flips
    to False + an action="update" audit row carrying changes["is_active"]."""
    client, _ = authed_admin
    org_id = await _admin_org_id(db_session)
    target = await _seed_user_in_org(
        db_session, org_id, email="deactivate-me@test.local", is_active=True
    )
    await db_session.commit()
    target_id = target.id

    r = await csrf_post(
        client,
        f"/users/{target_id}/set-active",
        {"active": "0"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/users"
    db_session.expire_all()
    flipped = (await db_session.execute(select(User).where(User.id == target_id))).scalar_one()
    assert flipped.is_active is False

    row = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "update",
                AuditLog.entity_type == "user",
                AuditLog.entity_id == target_id,
            )
        )
    ).scalar_one()
    assert row.changes is not None
    assert "is_active" in row.changes
    assert row.changes["is_active"] == [True, False]


async def test_set_active_reactivate_inactive_user_succeeds(
    authed_admin: tuple[AsyncClient, object], db_session: AsyncSession
) -> None:
    """Activating an inactive user succeeds (activation has no guards): 303 +
    is_active flips to True + audit row changes["is_active"] == [False, True]."""
    client, _ = authed_admin
    org_id = await _admin_org_id(db_session)
    target = await _seed_user_in_org(
        db_session, org_id, email="reactivate-me@test.local", is_active=False
    )
    await db_session.commit()
    target_id = target.id

    r = await csrf_post(
        client,
        f"/users/{target_id}/set-active",
        {"active": "1"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/users"
    db_session.expire_all()
    flipped = (await db_session.execute(select(User).where(User.id == target_id))).scalar_one()
    assert flipped.is_active is True

    row = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "update",
                AuditLog.entity_type == "user",
                AuditLog.entity_id == target_id,
            )
        )
    ).scalar_one()
    assert row.changes["is_active"] == [False, True]


async def test_set_active_deactivate_second_admin_succeeds(
    authed_admin: tuple[AsyncClient, object], db_session: AsyncSession
) -> None:
    """Deactivating a SECOND active admin succeeds (not the last admin): the
    actor remains an active admin so the active-admin count stays >= 1.

    This exercises the admin-target branch of the last-admin guard WITHOUT
    tripping it, confirming the guard is scoped to the genuine last-admin case.
    """
    client, _ = authed_admin
    org_id = await _admin_org_id(db_session)
    target = await _seed_user_in_org(
        db_session, org_id, email="second-admin@test.local", role=UserRole.ADMIN, is_active=True
    )
    await db_session.commit()
    target_id = target.id

    r = await csrf_post(
        client,
        f"/users/{target_id}/set-active",
        {"active": "0"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db_session.expire_all()
    flipped = (await db_session.execute(select(User).where(User.id == target_id))).scalar_one()
    assert flipped.is_active is False
