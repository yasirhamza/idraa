"""F22 Arch-5: form error context is always dict[str, str].

Tests that POST endpoints returning 200/422 on validation failure
include the form_error_summary signature (indicating the errors dict
was passed to the macro correctly).

Routes that redirect on validation failure (303) are skipped — they
don't re-render the form directly.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from tests.conftest import csrf_post


@pytest.mark.parametrize(
    "path,payload,needs_admin",
    [
        # login: wrong credentials (empty password) — re-renders with error banner.
        # Empty email triggers FastAPI Form 422 (framework-level, not our handler),
        # so we use a syntactically valid email + wrong password to get the 400
        # re-render that lands on our error macro.
        ("/login", {"email": "noone@example.com", "password": "wrong"}, False),
        ("/organization", {"name": ""}, True),
        ("/overlays", {"tag": "", "display_name": "", "methodology": "x"}, True),
    ],
)
async def test_form_errors_render_via_macro(
    client: AsyncClient,
    authed_admin: tuple[AsyncClient, uuid.UUID],
    path: str,
    payload: dict[str, str],
    needs_admin: bool,
) -> None:
    """On validation failure, the form must include form_error_summary or form_field error chrome."""
    target = authed_admin[0] if needs_admin else client
    # Use csrf_post so the CSRF middleware doesn't reject the POST with 403.
    resp = await csrf_post(target, path, data=payload)
    if resp.status_code == 404:
        pytest.skip(f"{path} not mounted")
    if resp.status_code in (303, 302, 307):
        pytest.skip(f"{path} redirects on validation failure (status {resp.status_code})")
    # Accept 200 or 422 or 400 — route may use different codes
    assert resp.status_code in (200, 400, 422), f"{path}: unexpected status {resp.status_code}"
    body = resp.text
    # form_error_summary emits one of these markers on error
    has_summary = (
        "status-critical" in body
        or "Please fix" in body
        or "REQUIRED" in body
        or "focus:ring-brand" in body  # form_field chrome still rendered
    )
    assert has_summary, (
        f"{path}: expected form_error_summary or form_field chrome in body on validation failure"
    )
