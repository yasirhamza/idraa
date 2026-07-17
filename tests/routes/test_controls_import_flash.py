"""Regression test for issue #152: POST /controls/import → flash on /controls.

Pre-fix: POST /controls/import discarded the import_csv tuple return value
and returned `RedirectResponse("/controls", 303)` with no flash mechanism.
Admin saw the redirect to /controls list with zero feedback about what
happened.

Post-fix: POST appends `?imported={n}&skipped={k}` to the redirect URL;
GET /controls reads those Query params and renders the canonical
"Imported X controls (N created, K skipped)..." flash via the same
_format_import_flash helper the library-import flow uses.

Pattern matches `routes/organization.py:82-90` (`?saved=1` query-string
flash — "the lightest pattern that gives the user a confirmation
without breaking POST-redirect-GET").
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_post_import_redirects_with_counts_in_query_string(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    """POST /controls/import → 303 with `?imported=N&skipped=K` query string.

    Asserts the redirect-Location header contains both counts so the GET
    handler can render the flash. The Location format matches the
    query-string flash pattern at routes/organization.py.
    """
    client, _org_id = authed_admin
    csv_bytes = b",FlashTestCtrl,desc,LEC - Prevention - Avoidance,preventive,1000,0.7\n"
    # multipart/form-data upload
    files = {"file": ("test.csv", csv_bytes, "text/csv")}
    # Need CSRF
    page = (await client.get("/controls/import")).text
    import re

    m = re.search(r'name="_csrf"[^>]*value="([^"]+)"', page)
    assert m, "no CSRF token on /controls/import"
    csrf = m.group(1)

    resp = await client.post(
        "/controls/import",
        data={"_csrf": csrf},
        files=files,
        follow_redirects=False,
    )
    assert resp.status_code == 303
    location = resp.headers["location"]
    assert "/controls" in location
    assert "imported=1" in location
    assert "skipped=0" in location


@pytest.mark.asyncio
async def test_get_controls_with_imported_query_renders_flash(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    """GET /controls?imported=1&skipped=0 renders the 'Imported' flash banner."""
    client, _ = authed_admin
    r = await client.get("/controls?imported=1&skipped=0")
    assert r.status_code == 200
    # Match the _format_import_flash output format
    assert "Imported 1 controls" in r.text
    assert "1 created, 0 skipped" in r.text


@pytest.mark.asyncio
async def test_get_controls_without_query_params_no_flash(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    """GET /controls (no query params) → no 'Imported' banner."""
    client, _ = authed_admin
    r = await client.get("/controls")
    assert r.status_code == 200
    assert "Imported" not in r.text


@pytest.mark.asyncio
async def test_get_controls_with_partial_query_params_no_flash(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    """GET /controls?imported=1 (missing skipped) → no flash (both required)."""
    client, _ = authed_admin
    r = await client.get("/controls?imported=1")
    assert r.status_code == 200
    assert "Imported 1 controls" not in r.text
