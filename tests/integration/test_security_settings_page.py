import pytest
from sqlalchemy import select

import idraa.services.security_settings as ss
from idraa.models._types import now_utc
from idraa.models.audit_log import AuditLog
from idraa.models.security_settings import SecuritySettings
from idraa.models.session import AuthSession
from idraa.models.user import User
from idraa.services.auth import SESSION_COOKIE, unsign_session_id
from tests.conftest import csrf_post


async def _enroll_mfa(db_session, client) -> None:
    """Mark the client's logged-in user as MFA-enrolled (mirrors
    tests/integration/test_step_up_flow.py::_enroll_totp's session-cookie
    lookup, trimmed to just the enrollment stamp this suite needs).

    Required before a test flips ``mfa_policy`` to "required" and then makes
    a SECOND request as the same admin: per the adjudicated posture
    (EnrollmentGuardMiddleware review), a factor-less admin is intentionally
    funneled to /account/security by their OWN policy flip — there is no
    /settings/security allowlist escape hatch. A test that needs the second
    request to actually reach the route must enroll the admin first, exactly
    as a real admin would be expected to.
    """
    signed = client.cookies.get(SESSION_COOKIE)
    assert signed is not None
    sid = unsign_session_id(signed)
    assert sid is not None
    sess = await db_session.get(AuthSession, sid)
    assert sess is not None
    user = await db_session.get(User, sess.user_id)
    assert user is not None
    user.mfa_enrolled_at = now_utc()
    await db_session.commit()


@pytest.mark.asyncio
async def test_admin_sets_and_audits_each_field(authed_admin, db_session):
    client, org_id = authed_admin
    r = await csrf_post(
        client,
        "/settings/security",
        {
            "mfa_policy": "required",
            "step_up_window_seconds": "300",
            "step_up_exports": "off",
            "step_up_destructive": "on",
            "step_up_admin": "",
            "step_up_credentials": "on",
        },
    )  # admin -> "" = follow-env(NULL)
    assert r.status_code in (302, 303)
    row = (
        await db_session.execute(
            select(SecuritySettings).where(SecuritySettings.organization_id == org_id)
        )
    ).scalar_one()
    assert row.mfa_policy == "required" and row.step_up_window_seconds == 300
    assert row.step_up_exports is False and row.step_up_destructive is True
    assert row.step_up_admin is None and row.step_up_credentials is True
    rows = (
        (
            await db_session.execute(
                select(AuditLog).where(AuditLog.action == "security_settings.changed")
            )
        )
        .scalars()
        .all()
    )
    # merge every changed-field payload; assert each field's [from, to] is present
    merged: dict = {}
    for a in rows:
        merged.update(a.changes)
    assert merged["mfa_policy"] == [None, "required"]  # None (unset) -> required
    assert merged["step_up_window_seconds"] == [None, 300]
    assert merged["step_up_exports"] == [None, False]
    assert "step_up_admin" not in merged  # "" -> None == no change, not audited


@pytest.mark.asyncio
async def test_reset_to_follow_env_is_audited(authed_admin, db_session):
    client, org_id = authed_admin
    await _enroll_mfa(
        db_session, client
    )  # else the required-flip below traps the admin's OWN next request
    await csrf_post(client, "/settings/security", {"mfa_policy": "required"})  # set the override
    await csrf_post(
        client, "/settings/security", {"mfa_policy": ""}
    )  # clear -> follow-env (DOWNGRADE)
    rows = (
        (
            await db_session.execute(
                select(AuditLog)
                .where(AuditLog.action == "security_settings.changed")
                .order_by(AuditLog.timestamp)
            )
        )
        .scalars()
        .all()
    )
    # the most recent row's changes must record the required->None downgrade
    assert rows[-1].changes.get("mfa_policy") == ["required", None]


@pytest.mark.asyncio
async def test_requires_admin(authed_analyst):
    client, _ = authed_analyst
    r = await csrf_post(client, "/settings/security", {"mfa_policy": "required"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_invalid_values_rejected_no_write(authed_admin, db_session):
    # strict-reject: an unknown value must 400 and write NOTHING (no silent coerce/clear).
    client, org_id = authed_admin
    for bad in (
        {"mfa_policy": "nope"},
        {"step_up_window_seconds": "-5"},
        {"step_up_exports": "maybe"},
    ):
        r = await csrf_post(client, "/settings/security", bad)
        assert r.status_code == 400
    row = (
        await db_session.execute(
            select(SecuritySettings).where(SecuritySettings.organization_id == org_id)
        )
    ).scalar_one_or_none()
    assert row is None  # no partial write from any rejected submit


@pytest.mark.asyncio
async def test_factorless_admin_post_is_intercepted_by_enrollment_guard(authed_admin, db_session):
    """Pins the adjudicated posture (idraa#85 Task 6 review): there is NO
    ``/settings/security`` allowlist entry in ``EnrollmentGuardMiddleware``.
    Once ``mfa_policy=="required"`` is in effect, a factor-less admin's POST
    to /settings/security is intercepted by the guard (303 -> /account/security)
    same as any other non-allowlisted route, and the row is never touched —
    the guard stays enforceable against admins, including the one who set
    the policy, not just everyone else.
    """
    client, org_id = authed_admin
    db_session.add(SecuritySettings(organization_id=org_id, mfa_policy="required"))
    await db_session.commit()
    await ss.load_security_settings(db_session, org_id)  # resolver cache <- required

    r = await csrf_post(
        client, "/settings/security", {"mfa_policy": "optional"}, follow_redirects=False
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/account/security"

    row = (
        await db_session.execute(
            select(SecuritySettings).where(SecuritySettings.organization_id == org_id)
        )
    ).scalar_one()
    assert row.mfa_policy == "required"  # unchanged -- the POST never reached the handler
