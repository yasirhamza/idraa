"""Enrollment guard reads the resolver, not raw env (idraa#85 admin knobs, Task 5).

``middleware/enrollment_guard.py`` was the LAST direct reader of
``Settings.auth_mfa_policy`` outside the resolver/config — it now consults
``effective_mfa_policy()`` so a DB ``required`` override actually flips the
guard. These tests exercise the REAL boot warm path (``warm_cache``, not a
direct ``load_security_settings`` call): a plan-gate security BLOCKER flagged
that a fake-warmed test would hide a broken boot path while env-fallback
silently relaxes a DB ``required`` override back to whatever env says.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import idraa.services.security_settings as ss
from idraa import config, db
from idraa.config import get_settings
from idraa.models.organization import Organization
from idraa.models.security_settings import SecuritySettings
from idraa.services.auth import SESSION_COOKIE
from tests.factories import create_user, login_client_as


async def test_db_required_override_flips_guard_via_boot_warm(
    client: AsyncClient,
    db_session: AsyncSession,
    seed_organization: Organization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # env optional, DB says required -> after the REAL warm_cache, a
    # factor-less user is forced to enroll.
    monkeypatch.setattr(get_settings(), "auth_mfa_policy", "optional", raising=False)
    db_session.add(SecuritySettings(organization_id=seed_organization.id, mfa_policy="required"))
    await db_session.commit()
    await ss.warm_cache(get_settings())  # the actual boot path (ASGITransport skips lifespan)
    assert ss.effective_mfa_policy() == "required"

    # A logged-in FACTOR-LESS user hitting a non-allowlisted page is
    # redirected to enroll. Mirrors tests/conftest.py::authed_admin
    # (create_user defaults to no mfa_enrolled_at + login_client_as signs the
    # session cookie) — same pattern test_enrollment_interstitial.py relies on
    # via the admin_client fixture, transcribed inline here since this test
    # needs its own seed_organization/db_session for the SecuritySettings row.
    user = await create_user(db_session, seed_organization)
    cookie = await login_client_as(db_session, user)
    client.cookies.set(SESSION_COOKIE, cookie)

    r = await client.get("/scenarios", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/account/security"


async def test_warm_failure_falls_back_to_env(db_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # warm_cache resolves its OWN session via idraa.db.get_session() (the
    # process-wide engine singleton) rather than taking an injected session
    # -- that's the real boot-path shape. Wire DATABASE_URL to the per-test
    # file + reset the config/db singletons first, exactly like
    # tests/integration/test_run_reaper.py's real-lifespan-boot tests do:
    # without this, get_session() lazily creates+caches idraa.db._engine
    # against whatever the ambient default DATABASE_URL is (the real dev
    # DB), and that stale engine then leaks into the next `client`-fixture
    # test (which only resets _engine at its OWN teardown, not at setup).
    monkeypatch.setenv("DATABASE_URL", db_url)
    config.reset_for_tests()
    db.reset_for_tests()
    try:
        ss.invalidate()
        monkeypatch.setattr(get_settings(), "auth_mfa_policy", "optional", raising=False)

        async def _boom(db: object) -> None:
            raise RuntimeError("boot db down")

        # warm_cache does `from idraa.services.org import get_sole_org` at call
        # time, so patch it on that module. warm_cache must SWALLOW the error
        # (not raise).
        monkeypatch.setattr("idraa.services.org.get_sole_org", _boom)
        await ss.warm_cache(get_settings())  # must not raise
        assert ss.effective_mfa_policy() == "optional"  # empty cache -> env fallback
    finally:
        config.reset_for_tests()
        db.reset_for_tests()
