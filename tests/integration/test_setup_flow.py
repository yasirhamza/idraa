"""Setup wizard end-to-end: guard redirect, GET form, POST creation, bounce-after-seed."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.audit_log import AuditLog
from idraa.models.organization import Organization
from idraa.models.user import User
from tests.conftest import csrf_post


async def test_no_users_redirects_to_setup(client: AsyncClient) -> None:
    r = await client.get("/", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/setup"


async def test_setup_get_renders_form(client: AsyncClient) -> None:
    r = await client.get("/setup")
    assert r.status_code == 200
    assert "First-time setup" in r.text
    assert 'name="org_name"' in r.text
    assert 'name="email"' in r.text


async def test_setup_post_creates_org_and_admin(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    r = await csrf_post(
        client,
        "/setup",
        {
            "org_name": "Acme Corp",
            "industry_type": "information",
            "organization_size": "medium",
            "email": "admin@acme.test",
            "full_name": "Ada Admin",
            "password": "pw-12345678",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    # idraa_session cookie set on the redirect response.
    assert "idraa_session" in r.cookies or any(
        "idraa_session" in c for c in r.headers.get_list("set-cookie")
    )

    orgs = (await db_session.execute(select(Organization))).scalars().all()
    users = (await db_session.execute(select(User))).scalars().all()
    audits = (await db_session.execute(select(AuditLog))).scalars().all()
    assert len(orgs) == 1 and orgs[0].name == "Acme Corp"
    assert len(users) == 1 and users[0].email == "admin@acme.test"
    assert users[0].role.value == "admin"
    # Audit events: org create + user create + login (sorted -> 2 creates then login)
    actions = sorted(a.action for a in audits)
    assert actions == ["create", "create", "login"]


async def test_setup_blocked_once_user_exists(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # Seed a user via the real setup flow.
    first = await csrf_post(
        client,
        "/setup",
        {
            "org_name": "A",
            "industry_type": "information",
            "organization_size": "small",
            "email": "a@b.c",
            "full_name": "A",
            "password": "pw-12345678",
        },
        follow_redirects=False,
    )
    assert first.status_code == 303
    # Second GET /setup should 303 back to / (already-seeded guard in the handler).
    r = await client.get("/setup", follow_redirects=False)
    assert r.status_code == 303


async def test_setup_post_without_csrf_returns_403(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """/setup POST without _csrf must 403 and not create any rows.

    Regression test: if a future dev decorates /setup with a csrf_exempt
    escape hatch or otherwise bypasses the CSRFMiddleware, this test fails
    loudly. Also probes the body-replay fix: if the middleware drains the
    body before returning 403, later Form(...) handlers would see an empty
    dict — this test catches that indirectly because no 4xx body-issue
    would surface the right assertion shape.
    """
    # Seed the CSRF cookie via a GET first so "cookie missing" is not the
    # reason for the 403; we want "_csrf form field missing" to be the
    # failure mode.
    await client.get("/setup")
    r = await client.post(
        "/setup",
        data={
            "org_name": "x",
            "industry_type": "information",
            "organization_size": "small",
            "email": "a@b.c",
            "full_name": "x",
            "password": "pw-12345678",
        },
        follow_redirects=False,
    )
    assert r.status_code == 403
    # No rows should have been created.
    from sqlalchemy import select as _select

    from idraa.models.organization import Organization as _Org
    from idraa.models.user import User as _User

    orgs = (await db_session.execute(_select(_Org))).scalars().all()
    users = (await db_session.execute(_select(_User))).scalars().all()
    assert orgs == []
    assert users == []
