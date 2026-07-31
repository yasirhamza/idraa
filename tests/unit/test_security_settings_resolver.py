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


@pytest.mark.asyncio
async def test_warm_cache_marks_warmed_on_no_org(monkeypatch):
    """#107 review R1 pin: warm_cache sets the warmed flag even with NO org/row.

    This drives the REAL warm path (not a hand-poked flag): the `_warmed =
    True` statement must sit outside the `if org is not None:` block. Moving
    it inside — the exact regression the tri-state exists to prevent — makes
    /healthz report "cold" (= boot warm FAILED) on every healthy fresh
    install, and this test fails.
    """
    import contextlib

    @contextlib.asynccontextmanager
    async def _fake_session():
        yield object()  # never touched: get_sole_org is patched to ignore it

    async def _no_org(db):
        return None

    monkeypatch.setattr("idraa.db.get_session", _fake_session)
    monkeypatch.setattr("idraa.services.org.get_sole_org", _no_org)
    ss.invalidate()
    assert ss.cache_state() == "cold"
    await ss.warm_cache(get_settings())
    assert ss.cache_state() == "empty"  # warmed, no row — NOT "cold"


@pytest.mark.asyncio
async def test_warm_cache_failure_stays_cold(monkeypatch):
    """A failed warm must swallow the exception AND leave the state "cold"."""
    import contextlib

    @contextlib.asynccontextmanager
    async def _boom():
        raise RuntimeError("db down at boot")
        yield  # pragma: no cover

    monkeypatch.setattr("idraa.db.get_session", _boom)
    ss.invalidate()
    await ss.warm_cache(get_settings())  # must not raise (boot must not block)
    assert ss.cache_state() == "cold"
