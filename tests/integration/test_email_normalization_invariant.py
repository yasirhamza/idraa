"""Regression test: email-normalization invariant holds at every write + read site.

The .lower().strip() invariant exists at 6 sites:
1. services/auth.py::load_user_by_email (read path)
2. services/users.py::invite_user (write path)
3. routes/setup.py::setup_post (write path, bypassing invite_user)
4. tests/factories.py::create_user (test-only write path)

Rolling back any of these would cause a user created with trailing
whitespace / mixed case to be un-login-able (written value != lookup
value at the DB's (organization_id, email) unique constraint).

This test seeds via every path with "  A@B.C  " and asserts the stored
email is "a@b.c" and `load_user_by_email` finds it for both the raw and
normalized form.
"""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.user import User
from idraa.services.auth import load_user_by_email
from tests.conftest import csrf_post

RAW_EMAIL = "  A@B.C  "
NORMALIZED_EMAIL = "a@b.c"


async def test_setup_post_normalizes_email(client: AsyncClient, db_session: AsyncSession) -> None:
    """POST /setup with a whitespace-laden mixed-case email stores the normalized form."""
    r = await csrf_post(
        client,
        "/setup",
        {
            "org_name": "A",
            "industry_type": "information",
            "organization_size": "small",
            "email": RAW_EMAIL,
            "full_name": "A",
            "password": "pw-12345678",
        },
    )
    assert r.status_code in (303, 200), f"setup POST failed: {r.status_code}"
    user = (await db_session.execute(select(User))).scalar_one()
    assert user.email == NORMALIZED_EMAIL, (
        f"setup_post stored {user.email!r}, expected {NORMALIZED_EMAIL!r}"
    )
    # And lookup works for both the raw (pre-normalized) and stored form.
    assert await load_user_by_email(db_session, RAW_EMAIL) is not None
    assert await load_user_by_email(db_session, NORMALIZED_EMAIL) is not None


async def test_invite_user_normalizes_email(
    authed_admin: tuple[AsyncClient, object], db_session: AsyncSession
) -> None:
    """/users/invite with a whitespace-laden mixed-case email stores the normalized form."""
    client, _ = authed_admin
    r = await csrf_post(
        client,
        "/users/invite",
        {
            "email": RAW_EMAIL,
            "full_name": "X",
            "role": "analyst",
            "password": "pw-12345678",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, f"invite POST failed: {r.status_code}: {r.text[:200]}"
    # The seeded admin plus the invited user exist; filter to the new one.
    new_user = (
        await db_session.execute(select(User).where(User.email == NORMALIZED_EMAIL))
    ).scalar_one()
    assert new_user.email == NORMALIZED_EMAIL
    assert await load_user_by_email(db_session, RAW_EMAIL) is not None
    assert await load_user_by_email(db_session, NORMALIZED_EMAIL) is not None


async def test_create_user_factory_normalizes_email(
    db_session: AsyncSession,
) -> None:
    """tests/factories.py::create_user normalizes email on write."""
    from tests.factories import create_org, create_user

    org = await create_org(db_session)
    user = await create_user(db_session, org, email=RAW_EMAIL)
    await db_session.flush()
    assert user.email == NORMALIZED_EMAIL
    assert await load_user_by_email(db_session, RAW_EMAIL) is not None
