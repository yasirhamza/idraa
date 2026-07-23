from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.session import AuthSession
from idraa.services.auth import SESSION_COOKIE, unsign_session_id
from tests.conftest import csrf_post

_UUID = str(uuid.uuid4())

# Route inventory under step-up. The dependency fires BEFORE handler-level
# 404s, so nonexistent entity ids are fine — a stale session must be
# challenged regardless.
GET_TARGETS = [
    "/users/export.csv",
    "/scenarios/export",
    "/analyses/export.csv",
    "/library/export.csv",
    "/library/export",
    "/controls/export.csv",
    "/controls/library/export.csv",
    "/reports/export.csv",
    f"/reports/run/{_UUID}",
    f"/reports/run/{_UUID}/verification.xlsx",
    "/overlays/export.csv",
    "/account/security/totp/enroll",
    f"/runs/{_UUID}/samples.csv.gz",
]
POST_TARGETS = [
    "/users/invite",
    f"/users/{_UUID}/edit",
    f"/users/{_UUID}/set-active",
    f"/users/{_UUID}/delete",
    f"/scenarios/{_UUID}/delete",
    f"/runs/{_UUID}/delete",
    f"/runs/{_UUID}/purge-samples",
    f"/library/overrides/{_UUID}/delete",
    f"/library/entries/{_UUID}/delete",
    f"/controls/{_UUID}/delete",
    f"/qualitative-bands/{_UUID}/delete",
    f"/register-import/profiles/{_UUID}/delete",
    f"/overlays/{_UUID}/deactivate",
    "/account/security/totp/enroll",
    "/account/security/recovery-codes/generate",
    "/account/security/passkey/options",
    "/account/security/passkey/verify",
    f"/account/security/passkey/{_UUID}/delete",
    f"/users/{_UUID}/reset-mfa",
]


async def _make_stale(db_session: AsyncSession, client: AsyncClient) -> None:
    signed = client.cookies.get(SESSION_COOKIE)
    assert signed is not None
    sid = unsign_session_id(signed)
    assert sid is not None
    sess = await db_session.get(AuthSession, sid)
    assert sess is not None
    sess.reauthenticated_at = datetime.now(UTC) - timedelta(seconds=999)
    await db_session.commit()


@pytest.mark.parametrize("url", GET_TARGETS)
async def test_stale_get_targets_are_challenged(
    url: str, db_session: AsyncSession, admin_client: AsyncClient
) -> None:
    await _make_stale(db_session, admin_client)
    r = await admin_client.get(url, follow_redirects=False)
    assert r.status_code == 303, url
    assert r.headers["location"].startswith("/auth/step-up?next="), url


@pytest.mark.parametrize("url", POST_TARGETS)
async def test_stale_post_targets_are_challenged(
    url: str, db_session: AsyncSession, admin_client: AsyncClient
) -> None:
    await _make_stale(db_session, admin_client)
    r = await csrf_post(
        admin_client, url, {}, bootstrap_url="/auth/step-up", follow_redirects=False
    )
    assert r.status_code == 303, url
    assert r.headers["location"].startswith("/auth/step-up?next="), url


async def test_fresh_session_passes_a_gated_route(
    admin_client: AsyncClient,
) -> None:
    # Fixture sessions are freshly stamped by create_session — no challenge.
    r = await admin_client.get("/users/export.csv", follow_redirects=False)
    assert r.status_code == 200


async def test_htmx_stale_gets_hx_redirect(
    db_session: AsyncSession, admin_client: AsyncClient
) -> None:
    await _make_stale(db_session, admin_client)
    r = await admin_client.get(
        "/users/export.csv", headers={"HX-Request": "true"}, follow_redirects=False
    )
    assert r.status_code == 204
    assert r.headers["HX-Redirect"].startswith("/auth/step-up?next=")
