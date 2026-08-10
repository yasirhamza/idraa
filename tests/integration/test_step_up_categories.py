import pytest

import idraa.services.security_settings as ss
from idraa.models.security_settings import SecuritySettings
from tests.conftest import csrf_post
from tests.integration.test_step_up_flow import _make_stale  # reuse the real stale-session helper


async def _apply(db, org_id, **kw):
    db.add(SecuritySettings(organization_id=org_id, step_up_window_seconds=600, **kw))
    await db.commit()
    await ss.load_security_settings(db, org_id)


@pytest.mark.asyncio
async def test_exports_off_lets_stale_session_export(authed_admin, db_session):
    client, org_id = authed_admin
    await _apply(db_session, org_id, step_up_exports=False)
    await _make_stale(db_session, client)  # session now beyond the window
    r = await client.get("/controls/export.csv", follow_redirects=False)
    assert r.status_code == 200  # EXPORTS off -> no step-up redirect


@pytest.mark.asyncio
async def test_exports_off_does_NOT_drop_admin_export(authed_admin, db_session):
    # cross-category isolation: /users/export.csv is ADMIN, not EXPORTS
    client, org_id = authed_admin
    await _apply(db_session, org_id, step_up_exports=False, step_up_admin=True)
    await _make_stale(db_session, client)
    r = await client.get("/users/export.csv", follow_redirects=False)
    assert r.status_code == 303 and "/auth/step-up" in r.headers["location"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url,category_kw",
    [
        ("/runs/{id}/delete", {"step_up_destructive": True}),  # DESTRUCTIVE
        ("/users/{id}/reset-mfa", {"step_up_admin": True}),  # ADMIN
        ("/account/security/passkey/{id}/delete", {"step_up_credentials": True}),  # CREDENTIALS
    ],
)
async def test_category_on_and_stale_gates(authed_admin, db_session, url, category_kw):
    import uuid

    client, org_id = authed_admin
    await _apply(db_session, org_id, **category_kw)
    await _make_stale(db_session, client)
    # The require_step_up dependency runs BEFORE the handler, so a dummy id 303s
    # at the gate (never reaching the would-be-404). csrf_post satisfies the
    # outermost CSRFMiddleware; follow_redirects=False exposes the 303.
    r = await csrf_post(client, url.format(id=uuid.uuid4()), {}, follow_redirects=False)
    assert r.status_code == 303 and "/auth/step-up" in r.headers["location"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "/organization",  # B2: org profile feeds org-wide FAIR/financial computation
        "/fx-rates",  # B2: FX rates sit behind every ALE/loss figure
        "/sme-directory/new",  # B2: admin-only SME-directory mutation (ADMIN category)
    ],
)
async def test_b2_admin_config_routes_gate_stale_session(authed_admin, db_session, url):
    """B2: the newly-gated admin-config writes bounce a STALE admin session to
    step-up (ADMIN category), same as /settings/security. The gate runs before
    the handler, so an empty body 303s at the gate."""
    client, org_id = authed_admin
    await _apply(db_session, org_id, step_up_admin=True)
    await _make_stale(db_session, client)
    r = await csrf_post(client, url, {}, follow_redirects=False)
    assert r.status_code == 303 and "/auth/step-up" in r.headers["location"]


@pytest.mark.asyncio
async def test_b2_admin_config_route_fresh_session_not_gated(authed_admin, db_session):
    """B2 no-regression: a FRESH admin session is NOT bounced — the gate is a
    no-op while the session is step-up-fresh, so ordinary admin config edits
    are unaffected. (The gate reaches the handler, which 422s on the empty
    body — anything that is NOT the step-up 303 proves the gate passed.)"""
    client, org_id = authed_admin
    await _apply(db_session, org_id, step_up_admin=True)
    # No _make_stale — session stays fresh.
    r = await csrf_post(client, "/fx-rates", {}, follow_redirects=False)
    assert r.status_code != 303 or "/auth/step-up" not in r.headers.get("location", "")


@pytest.mark.asyncio
async def test_destructive_off_does_NOT_drop_passkey_delete(authed_admin, db_session):
    # cross-category isolation: passkey/{id}/delete is CREDENTIALS, not DESTRUCTIVE
    import uuid

    client, org_id = authed_admin
    await _apply(db_session, org_id, step_up_destructive=False, step_up_credentials=True)
    await _make_stale(db_session, client)
    r = await csrf_post(
        client, f"/account/security/passkey/{uuid.uuid4()}/delete", {}, follow_redirects=False
    )
    assert r.status_code == 303 and "/auth/step-up" in r.headers["location"]
