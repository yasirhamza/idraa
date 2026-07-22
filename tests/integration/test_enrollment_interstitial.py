from __future__ import annotations

import pytest
from httpx import AsyncClient

import idraa.config as config


def _require_mfa(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MFA_POLICY", "required")
    config.reset_for_tests()


async def test_unenrolled_required_user_redirected_on_dashboard(
    admin_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _require_mfa(monkeypatch)  # authed fixtures create users with mfa_enrolled_at = None
    r = await admin_client.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/account/security"


async def test_unenrolled_required_user_redirected_on_non_dashboard_route(
    admin_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A non-allowlisted authed route (not just the dashboard) must also be trapped.
    _require_mfa(monkeypatch)
    r = await admin_client.get("/scenarios", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/account/security"


async def test_security_page_reachable_while_unenrolled(
    admin_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _require_mfa(monkeypatch)
    r = await admin_client.get("/account/security", follow_redirects=False)
    assert r.status_code == 200


async def test_logout_reachable_while_unenrolled(
    admin_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.conftest import csrf_post

    _require_mfa(monkeypatch)
    r = await csrf_post(
        admin_client, "/logout", {}, bootstrap_url="/account/security", follow_redirects=False
    )
    assert r.status_code == 303


def test_enrollment_guard_runs_inner_to_session() -> None:
    # Ordering is the security-critical invariant: the guard MUST run after
    # SessionMiddleware so request.state.user is populated. Higher index in
    # user_middleware = added earlier = inner (runs later inbound).
    from typing import cast

    from idraa.app import create_app
    from idraa.middleware.enrollment_guard import EnrollmentGuardMiddleware
    from idraa.middleware.session import SessionMiddleware

    app = create_app()
    # ``Middleware.cls`` is typed as ``_MiddlewareFactory[P]`` (a Protocol); at
    # runtime it is the concrete middleware class passed to ``add_middleware``.
    # Cast to ``type`` so mypy accepts the identity comparison below (matches
    # the pattern in tests/unit/test_app_middleware_order.py).
    classes = [cast(type, m.cls) for m in app.user_middleware]
    assert classes.index(EnrollmentGuardMiddleware) > classes.index(SessionMiddleware)
