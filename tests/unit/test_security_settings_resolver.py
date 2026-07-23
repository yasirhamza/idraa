import pytest

import idraa.services.security_settings as ss
from idraa.config import get_settings
from idraa.models.enums import StepUpCategory
from idraa.models.security_settings import SecuritySettings


def test_env_fallback_when_cache_empty(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "auth_mfa_policy", "optional", raising=False)
    monkeypatch.setattr(s, "auth_step_up_max_age_seconds", 600, raising=False)
    assert ss.effective_mfa_policy() == "optional"
    assert ss.effective_step_up_window() == 600
    assert ss.step_up_required(StepUpCategory.EXPORTS) is True  # default-on, window>0


@pytest.mark.asyncio
async def test_db_override_and_window_kill_switch(db_session, seed_organization):
    db_session.add(
        SecuritySettings(
            organization_id=seed_organization.id,
            mfa_policy="required",
            step_up_window_seconds=0,
            step_up_exports=False,
        )
    )
    await db_session.commit()
    await ss.load_security_settings(db_session, seed_organization.id)
    assert ss.effective_mfa_policy() == "required"
    assert ss.effective_step_up_window() == 0
    assert ss.step_up_required(StepUpCategory.DESTRUCTIVE) is False  # window 0 -> all off


@pytest.mark.asyncio
async def test_per_category(db_session, seed_organization, monkeypatch):
    monkeypatch.setattr(get_settings(), "auth_step_up_max_age_seconds", 600, raising=False)
    db_session.add(
        SecuritySettings(
            organization_id=seed_organization.id, step_up_exports=False, step_up_destructive=True
        )
    )
    await db_session.commit()
    await ss.load_security_settings(db_session, seed_organization.id)
    assert ss.step_up_required(StepUpCategory.EXPORTS) is False  # override off
    assert ss.step_up_required(StepUpCategory.DESTRUCTIVE) is True  # override on
    assert ss.step_up_required(StepUpCategory.ADMIN) is True  # NULL -> default on


@pytest.mark.asyncio
async def test_snapshot_survives_session_close(db_session, seed_organization):
    # The cache must be a plain snapshot, readable after the session that loaded it is gone.
    db_session.add(SecuritySettings(organization_id=seed_organization.id, mfa_policy="required"))
    await db_session.commit()
    await ss.load_security_settings(db_session, seed_organization.id)
    await db_session.close()
    assert ss.effective_mfa_policy() == "required"  # no DetachedInstanceError
