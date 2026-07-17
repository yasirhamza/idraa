"""Users admin — list + invite + edit + self-lockout / last-admin guards.

POST paths use the shared ``csrf_post`` helper (tests/conftest.py) because
CSRFMiddleware rejects un-tokened POSTs with a flat 403. ``authed_admin``
sets only the ``idraa_session`` cookie; ``csrf_post`` does a GET to /setup
(the default bootstrap URL — always allowlisted) so the middleware's
response-path issues a ``csrf_token`` cookie, then posts with ``_csrf``
injected into the form data.

The self-lockout and last-admin guards added in Task 1.1.9 are phase-1
hardening: the plan's invite-then-disable flow only covers disabling a
different user. The guard tests here cover the two dangerous operations
the plan's happy-path tests don't touch — an admin disabling or demoting
themselves — which would lock the sole admin out of the app.
"""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.user import User
from tests.conftest import csrf_post


async def test_admin_can_list_users(authed_admin: tuple[AsyncClient, object]) -> None:
    client, _ = authed_admin
    r = await client.get("/users")
    assert r.status_code == 200
    assert "Users" in r.text
    assert "user@test.local" in r.text  # factory seeded admin


async def test_admin_can_invite(
    authed_admin: tuple[AsyncClient, object], db_session: AsyncSession
) -> None:
    client, _ = authed_admin
    r = await csrf_post(
        client,
        "/users/invite",
        {
            "email": "b@test.local",
            "full_name": "B",
            "role": "analyst",
            "password": "pw-12345678",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    users = (await db_session.execute(select(User))).scalars().all()
    assert any(u.email == "b@test.local" and u.role.value == "analyst" for u in users)


async def test_admin_can_disable_user(
    authed_admin: tuple[AsyncClient, object], db_session: AsyncSession
) -> None:
    client, _ = authed_admin
    # Invite a second user (an analyst) so the disable below touches a
    # different user — otherwise the self-edit guard would reject it.
    await csrf_post(
        client,
        "/users/invite",
        {
            "email": "c@test.local",
            "full_name": "C",
            "role": "analyst",
            "password": "pw-12345678",
        },
    )
    target = (
        await db_session.execute(select(User).where(User.email == "c@test.local"))
    ).scalar_one()
    r = await csrf_post(
        client,
        f"/users/{target.id}/edit",
        {"role": "analyst", "is_active": ""},
        follow_redirects=False,
    )
    assert r.status_code == 303
    await db_session.refresh(target)
    assert target.is_active is False


async def test_non_admin_forbidden(
    authed_admin: tuple[AsyncClient, object], db_session: AsyncSession
) -> None:
    from sqlalchemy import select as _s

    from idraa.models.enums import UserRole
    from idraa.models.organization import Organization
    from idraa.services.auth import SESSION_COOKIE
    from tests.factories import create_user, login_client_as

    client, _ = authed_admin
    org = (await db_session.execute(_s(Organization))).scalar_one()
    non_admin = await create_user(
        db_session, org, email="analyst@test.local", role=UserRole.ANALYST
    )
    cookie = await login_client_as(db_session, non_admin)
    client.cookies.set(SESSION_COOKIE, cookie)
    r = await client.get("/users")
    assert r.status_code == 403


async def test_admin_cannot_deactivate_self(
    authed_admin: tuple[AsyncClient, object], db_session: AsyncSession
) -> None:
    """Self-lockout guard: admin cannot deactivate their own account.

    1.1.9.a: the 400 path now re-renders ``users/edit.html`` with the
    error banner instead of raising ``HTTPException`` (which would leak a
    JSON payload through ``_auth_redirect_handler``). The error string is
    the visible text of the ``{{ error }}`` span — assert on it so we
    also catch an accidental revert to the JSON ``{"detail": ...}`` shape.
    """
    client, _ = authed_admin
    me = (
        await db_session.execute(select(User).where(User.email == "user@test.local"))
    ).scalar_one()
    r = await csrf_post(
        client,
        f"/users/{me.id}/edit",
        {"role": "admin", "is_active": ""},
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert "cannot deactivate yourself" in r.text.lower()
    # No mutation.
    await db_session.refresh(me)
    assert me.is_active is True


async def test_admin_cannot_demote_self(
    authed_admin: tuple[AsyncClient, object], db_session: AsyncSession
) -> None:
    """Self-lockout guard: admin cannot demote their own role.

    1.1.9.a: asserts the re-rendered error banner text, same reason as
    ``test_admin_cannot_deactivate_self`` above.
    """
    client, _ = authed_admin
    me = (
        await db_session.execute(select(User).where(User.email == "user@test.local"))
    ).scalar_one()
    r = await csrf_post(
        client,
        f"/users/{me.id}/edit",
        {"role": "analyst", "is_active": "on"},
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert "cannot demote yourself" in r.text.lower()
    await db_session.refresh(me)
    assert me.role.value == "admin"


async def test_admin_invite_duplicate_email_returns_400(
    authed_admin: tuple[AsyncClient, object], db_session: AsyncSession
) -> None:
    """Inviting an email that already exists returns 400 re-render, not 500.

    1.1.9.a (FIX I1): the ``uq_users_org_email`` unique constraint fires at
    ``db.flush()`` with ``IntegrityError`` when an admin invites a duplicate
    email. Before the fix this propagated uncaught to a 500. The handler
    now catches, rolls back the transaction, and re-renders
    ``users/invite.html`` with an error banner.
    """
    client, _ = authed_admin
    # Invite once — succeeds.
    r1 = await csrf_post(
        client,
        "/users/invite",
        {
            "email": "dup@test.local",
            "full_name": "Dup",
            "role": "analyst",
            "password": "pw-12345678",
        },
        follow_redirects=False,
    )
    assert r1.status_code == 303
    # Invite same email again — 400 re-render.
    r2 = await csrf_post(
        client,
        "/users/invite",
        {
            "email": "dup@test.local",
            "full_name": "Dup2",
            "role": "analyst",
            "password": "pw-12345678",
        },
        follow_redirects=False,
    )
    assert r2.status_code == 400
    assert "already exists" in r2.text
    # No second user created.
    users = (
        (await db_session.execute(select(User).where(User.email == "dup@test.local")))
        .scalars()
        .all()
    )
    assert len(users) == 1
