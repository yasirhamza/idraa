import pytest
from sqlalchemy import select

from idraa.models.audit_log import AuditLog
from idraa.models.security_settings import SecuritySettings
from tests.conftest import csrf_post


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
