"""End-to-end CSRF-before-Session wire-order regression test.

The unit test ``tests/unit/test_app_middleware_order.py`` only asserts list
ordering of ``app.user_middleware``. This test proves the RUNTIME behavior:
a POST with a valid ``idraa_session`` cookie but an INVALID ``_csrf`` token must
get 403 WITHOUT the session layer burning a DB round trip.

Patch target: ``idraa.middleware.session.load_active_session`` — the name
``SessionMiddleware`` imported, NOT ``idraa.services.auth.load_active_session``
(the definition site). Patching the definition-site binding would leave the
already-imported reference in session.py untouched and the spy would always
read zero calls, masking the invariant. If session.py ever switches to a
lazy import, update this patch target.
"""

from __future__ import annotations

from unittest.mock import patch

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.services.auth import load_active_session as _real_load_active_session
from tests.conftest import csrf_post


async def test_csrf_rejected_before_session_load(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # 1. Bootstrap a user via /setup (uses real CSRF). After this returns,
    #    the httpx client has a valid idraa_session cookie.
    await csrf_post(
        client,
        "/setup",
        {
            "org_name": "A",
            "industry_type": "information",
            "organization_size": "small",
            "email": "admin@acme.test",
            "full_name": "A",
            "password": "pw-12345678",
        },
    )

    # 2. POST /logout with a *forged* _csrf token — session cookie is still
    #    valid. Spy load_active_session: it MUST NOT be called because
    #    CSRFMiddleware rejects before SessionMiddleware runs.
    with patch("idraa.middleware.session.load_active_session") as spy:
        r = await client.post(
            "/logout",
            data={"_csrf": "obviously-forged.not-a-real-signature"},
            follow_redirects=False,
        )
    assert r.status_code == 403, f"expected 403 from CSRF; got {r.status_code}"
    spy.assert_not_called()


async def test_csrf_valid_session_load_happens(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Control case for test_csrf_rejected_before_session_load.

    With a VALID _csrf token and a VALID idraa_session cookie, SessionMiddleware
    MUST call load_active_session exactly once. Without this control, a
    broken patch target would make the negative test trivially pass.
    """
    # Seed a user via /setup (real CSRF).
    await csrf_post(
        client,
        "/setup",
        {
            "org_name": "A",
            "industry_type": "information",
            "organization_size": "small",
            "email": "admin@acme.test",
            "full_name": "A",
            "password": "pw-12345678",
        },
    )

    # wraps=... so the real function still runs — otherwise the session
    # wouldn't load and logout would no-op on its `sess is not None` check.
    with patch(
        "idraa.middleware.session.load_active_session",
        wraps=_real_load_active_session,
    ) as spy:
        # A valid POST /logout — real CSRF + real session cookie. csrf_post
        # itself issues a GET bootstrap request first (to refresh the CSRF
        # cookie), which ALSO traverses SessionMiddleware — so the spy sees
        # multiple calls. The invariant we care about is "at least one"
        # (proves the spy is attached); the negative test proves zero on
        # CSRF rejection, so the asymmetry is what exercises wire-order.
        await csrf_post(client, "/logout", {}, follow_redirects=False)

    assert spy.call_count >= 1, (
        f"expected load_active_session called on authenticated traffic; got {spy.call_count}"
    )
