"""Last-admin lockout race guards (idraa#83).

The pre-write count checks in ``routes/users.py`` (edit_post, set_active_post)
and ``services/users.py::delete_user`` are check-then-write: two concurrent
requests each disarming a *different* admin can both observe count==2, both
pass, and both commit — leaving zero active admins (in-app-irrecoverable).

The fix moves the invariant INTO the write: ``guarded_admin_disarm`` (and the
guarded DELETE inside ``delete_user``) re-verify "another active admin
remains" in the same statement that flips the row, so a stale pre-check can
never produce a lockout. These tests replay the racy interleaving as a
deterministic schedule: both pre-checks pass, then both writes execute — the
second write MUST refuse.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import idraa.services.users as users_service
from idraa.errors import UserDeleteError
from idraa.models.enums import UserRole
from idraa.models.organization import Organization
from idraa.models.user import User
from idraa.services.users import delete_user, guarded_admin_disarm
from tests.conftest import csrf_post
from tests.factories import create_user


async def _two_active_admins(db: AsyncSession, org: Organization) -> tuple[User, User]:
    x = await create_user(db, org, email="admin-x@test.local", role=UserRole.ADMIN)
    y = await create_user(db, org, email="admin-y@test.local", role=UserRole.ADMIN)
    return x, y


async def test_race_schedule_second_deactivation_refused(
    db_session: AsyncSession, organization: Organization
) -> None:
    """Write-skew schedule: both pre-checks saw 2 admins; second write refuses."""
    x, y = await _two_active_admins(db_session, organization)

    # Both racing requests already passed the naive count check (count==2).
    # Request A's write disarms X — allowed, Y still covers the org.
    applied_a = await guarded_admin_disarm(
        db_session,
        user_id=x.id,
        org_id=organization.id,
        new_role=UserRole.ADMIN,
        new_active=False,
    )
    assert applied_a is True

    # Request B's write disarms Y — MUST refuse: X is no longer active cover.
    applied_b = await guarded_admin_disarm(
        db_session,
        user_id=y.id,
        org_id=organization.id,
        new_role=UserRole.ADMIN,
        new_active=False,
    )
    assert applied_b is False

    await db_session.refresh(x)
    await db_session.refresh(y)
    assert x.is_active is False
    assert y.is_active is True  # the org keeps one active admin
    assert y.role == UserRole.ADMIN


async def test_guarded_disarm_demote_only(
    db_session: AsyncSession, organization: Organization
) -> None:
    """Demotion (role change, still active) goes through the same guard."""
    x, y = await _two_active_admins(db_session, organization)

    applied = await guarded_admin_disarm(
        db_session,
        user_id=x.id,
        org_id=organization.id,
        new_role=UserRole.ANALYST,
        new_active=True,
    )
    assert applied is True
    await db_session.refresh(x)
    assert x.role == UserRole.ANALYST
    assert x.is_active is True

    # Y is now the sole active admin — demoting them must refuse.
    applied = await guarded_admin_disarm(
        db_session,
        user_id=y.id,
        org_id=organization.id,
        new_role=UserRole.ANALYST,
        new_active=True,
    )
    assert applied is False
    await db_session.refresh(y)
    assert y.role == UserRole.ADMIN


async def test_guarded_disarm_inactive_admin_is_not_cover(
    db_session: AsyncSession, organization: Organization
) -> None:
    """An INACTIVE admin row does not count as remaining admin cover."""
    x = await create_user(db_session, organization, email="admin-x@test.local", role=UserRole.ADMIN)
    z = await create_user(db_session, organization, email="admin-z@test.local", role=UserRole.ADMIN)
    z.is_active = False
    await db_session.flush()

    applied = await guarded_admin_disarm(
        db_session,
        user_id=x.id,
        org_id=organization.id,
        new_role=UserRole.ADMIN,
        new_active=False,
    )
    assert applied is False
    await db_session.refresh(x)
    assert x.is_active is True


async def test_delete_write_guard_survives_stale_precheck(
    db_session: AsyncSession,
    organization: Organization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """delete_user's write-time guard holds even when the pre-check read stale data.

    Simulates the racy interleaving by pinning ``_is_last_admin`` to the stale
    answer (False) that a concurrent request would have observed before the
    other admin was removed. The conditional DELETE must still refuse.
    """
    x = await create_user(db_session, organization, email="admin-x@test.local", role=UserRole.ADMIN)
    actor = await create_user(
        db_session, organization, email="admin-actor@test.local", role=UserRole.ADMIN
    )
    actor.is_active = False  # X is the sole ACTIVE admin
    await db_session.flush()

    async def _stale_false(db: AsyncSession, org_id: object) -> bool:
        return False  # what the racing request saw before the world changed

    monkeypatch.setattr(users_service, "_is_last_admin", _stale_false)

    with pytest.raises(UserDeleteError):
        await delete_user(db_session, user_id=x.id, actor_id=actor.id, org_id=organization.id)

    survivor = (await db_session.execute(select(User).where(User.id == x.id))).scalar_one_or_none()
    assert survivor is not None
    assert survivor.is_active is True


async def test_admin_can_deactivate_other_admin_via_route(
    authed_admin: tuple[AsyncClient, object], db_session: AsyncSession
) -> None:
    """Green path through the rewritten set_active_post guarded write."""
    client, _ = authed_admin
    await csrf_post(
        client,
        "/users/invite",
        {
            "email": "second-admin@test.local",
            "full_name": "Second Admin",
            "role": "admin",
            "password": "pw-12345678",
        },
    )
    target = (
        await db_session.execute(select(User).where(User.email == "second-admin@test.local"))
    ).scalar_one()

    r = await csrf_post(
        client,
        f"/users/{target.id}/set-active",
        {"active": "0"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    await db_session.refresh(target)
    assert target.is_active is False


async def test_admin_can_demote_other_admin_via_route(
    authed_admin: tuple[AsyncClient, object], db_session: AsyncSession
) -> None:
    """Green path through the rewritten edit_post guarded write."""
    client, _ = authed_admin
    await csrf_post(
        client,
        "/users/invite",
        {
            "email": "third-admin@test.local",
            "full_name": "Third Admin",
            "role": "admin",
            "password": "pw-12345678",
        },
    )
    target = (
        await db_session.execute(select(User).where(User.email == "third-admin@test.local"))
    ).scalar_one()

    r = await csrf_post(
        client,
        f"/users/{target.id}/edit",
        {"role": "analyst", "is_active": "on"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    await db_session.refresh(target)
    assert target.role == UserRole.ANALYST
    assert target.is_active is True
