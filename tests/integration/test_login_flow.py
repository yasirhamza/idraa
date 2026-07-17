"""Login + logout flow end-to-end.

Uses the shared ``csrf_post`` helper (tests/conftest.py) so every POST
carries a valid ``_csrf`` field. Bypassing the CSRF dance would get a
flat 403 from CSRFMiddleware — the login/logout handlers would never
run, and the tests would false-positive "rejected by CSRF" as "rejected
by login" (or vice-versa).
"""

from __future__ import annotations

import re

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.audit_log import AuditLog
from idraa.models.session import AuthSession
from tests.conftest import csrf_post


async def _seed_setup(client: AsyncClient) -> None:
    """Create the first admin via /setup, then drop only the session cookie.

    Leaves the ``csrf_token`` cookie in place so the next ``csrf_post`` call
    is a cheap re-use rather than a fresh GET-then-POST. Deleting only
    ``idraa_session`` forces the following request to start un-authenticated,
    which is what the login tests want to exercise.
    """
    await csrf_post(
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
    )
    # cookies.delete raises KeyError when the cookie isn't present; we just
    # seeded setup which sets idraa_session, so it's always present here.
    client.cookies.delete("idraa_session")


async def test_login_rejects_bad_password(client: AsyncClient) -> None:
    await _seed_setup(client)
    r = await csrf_post(
        client,
        "/login",
        {"email": "a@b.c", "password": "wrong"},
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert "Invalid email or password" in r.text


async def test_login_accepts_good_password(client: AsyncClient) -> None:
    await _seed_setup(client)
    r = await csrf_post(
        client,
        "/login",
        {"email": "a@b.c", "password": "pw-12345678"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    assert "idraa_session" in r.cookies or "idraa_session" in "".join(
        r.headers.get_list("set-cookie")
    )


async def _login_audit_rows(db_session: AsyncSession) -> list[AuditLog]:
    # ORDER BY timestamp: rows[-1] must be the newest row (SQLite gives no
    # ordering guarantee without it — review finding).
    return list(
        (
            await db_session.execute(
                select(AuditLog).where(AuditLog.action == "login").order_by(AuditLog.timestamp)
            )
        )
        .scalars()
        .all()
    )


async def test_login_writes_audit_row(client: AsyncClient, db_session: AsyncSession) -> None:
    """Successful login writes an action='login' AuditLog row attributing the
    new session to the user + ip. Delta-based: _seed_setup's bootstrap
    auto-login writes its own row. (Logout audit was tested; login was not.)"""
    await _seed_setup(client)
    before = len(await _login_audit_rows(db_session))

    r = await csrf_post(
        client,
        "/login",
        {"email": "a@b.c", "password": "pw-12345678"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    rows = await _login_audit_rows(db_session)
    assert len(rows) == before + 1
    row = rows[-1]
    assert row.entity_type == "session"
    assert row.user_id is not None
    assert row.entity_id is not None  # the AuthSession id


async def test_failed_login_writes_no_audit_row(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Bad credentials must NOT write a login audit row (no anon-writable
    append-only amplification — same rationale as the stale-cookie logout)."""
    await _seed_setup(client)
    before = len(await _login_audit_rows(db_session))

    r = await csrf_post(
        client,
        "/login",
        {"email": "a@b.c", "password": "wrong"},
        follow_redirects=False,
    )
    assert r.status_code == 400

    assert len(await _login_audit_rows(db_session)) == before


async def test_login_honours_safe_next(client: AsyncClient) -> None:
    """Issue #265: post-login redirect honours a safe ?next path."""
    await _seed_setup(client)
    r = await csrf_post(
        client,
        "/login?next=/scenarios",
        {"email": "a@b.c", "password": "pw-12345678"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/scenarios"


async def test_login_rejects_open_redirect_next(client: AsyncClient) -> None:
    """Issue #265: protocol-relative //evil.example next is rejected, defaults to /."""
    await _seed_setup(client)
    r = await csrf_post(
        client,
        "/login?next=//evil.example/phish",
        {"email": "a@b.c", "password": "pw-12345678"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/"


async def test_login_get_renders_next_hidden_field(client: AsyncClient) -> None:
    """Issue #265: GET /login?next=<path> renders next as a hidden form field
    so the round-trip survives the POST."""
    await _seed_setup(client)
    r = await client.get("/login?next=/runs")
    assert r.status_code == 200
    assert re.search(r'<input[^>]*type="hidden"[^>]*name="next"[^>]*value="/runs"', r.text) or (
        re.search(r'<input[^>]*name="next"[^>]*value="/runs"', r.text)
    )


async def test_login_get_drops_unsafe_next(client: AsyncClient) -> None:
    """Issue #265: GET /login with a protocol-relative next does not echo it."""
    await _seed_setup(client)
    r = await client.get("/login?next=//evil.example")
    assert r.status_code == 200
    assert "//evil.example" not in r.text


async def test_logout_clears_cookie_and_session(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_setup(client)
    await csrf_post(
        client,
        "/login",
        {"email": "a@b.c", "password": "pw-12345678"},
    )
    # After login the client now has a valid idraa_session cookie. csrf_post
    # defaults to bootstrapping via GET /setup, which 303s once a user is
    # seeded — Starlette still runs the CSRFMiddleware response path on a
    # 303, so the csrf_token cookie does get issued. If that ever changes,
    # swap bootstrap_url to "/login" (always 200).
    r = await csrf_post(
        client,
        "/logout",
        {},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/login"

    audits = (await db_session.execute(select(AuditLog))).scalars().all()
    logout_audits = [a for a in audits if a.action == "logout"]
    assert len(logout_audits) == 1

    # AuthSession row for the logged-out session is gone — direct delete
    # (not a user cascade) is the path 1.1.6's logout uses, so verify it
    # here rather than leaving it to "hope no one stamped a future user
    # delete on this". The /setup flow leaves a separate AuthSession row
    # (the setup wizard's auto-login), which is NOT what we're deleting;
    # match against the audit entity_id to assert the specific session
    # is the one that went away.
    logout_sess_id = logout_audits[0].entity_id
    remaining = (await db_session.execute(select(AuthSession))).scalars().all()
    assert all(s.id != logout_sess_id for s in remaining)


async def test_nav_logout_form_has_csrf_and_works(
    authed_admin: tuple[AsyncClient, object],
) -> None:
    """Regression test for the missing-csrf-field bug in the nav logout form.

    Renders the real HTML, scrapes the form, submits it with only the token
    the page itself emitted. Catches the class of bug where csrf_post's
    manual token injection would hide (because every existing logout test
    injects _csrf via the helper rather than using the rendered form).
    """
    client, _ = authed_admin
    r = await client.get("/")
    assert r.status_code == 200

    # Find the hidden _csrf input inside the logout form. The form is
    # keyed by action="/logout" so we can anchor the search there.
    logout_form = re.search(
        r'<form[^>]*action="/logout"[^>]*>(.*?)</form>',
        r.text,
        re.DOTALL,
    )
    assert logout_form is not None, "logout form not in dashboard HTML"
    token_match = re.search(r'name="_csrf"\s+value="([^"]+)"', logout_form.group(1))
    assert token_match is not None, (
        f"logout form missing _csrf hidden input:\n{logout_form.group(1)}"
    )
    token = token_match.group(1)

    # Submit the form with ONLY the rendered token — no helper injection.
    r2 = await client.post(
        "/logout",
        data={"_csrf": token},
        follow_redirects=False,
    )
    assert r2.status_code == 303, (
        f"expected 303 logout redirect, got {r2.status_code}: {r2.text[:200]}"
    )
    assert r2.headers["location"] == "/login"
