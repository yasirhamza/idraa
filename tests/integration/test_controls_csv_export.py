"""F15: /controls/export.csv streams correct CSV shape."""

from __future__ import annotations

import uuid

from httpx import AsyncClient


async def test_controls_export_csv_returns_attachment(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    client, _ = authed_admin
    resp = await client.get("/controls/export.csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert 'filename="controls.csv"' in resp.headers.get("content-disposition", "")


async def test_controls_export_csv_has_expected_header(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    client, _ = authed_admin
    resp = await client.get("/controls/export.csv")
    first_line = resp.text.split("\r\n", 1)[0]
    for col in ("id", "name", "type", "status"):
        assert col in first_line, f"Header missing column: {col}"


async def test_controls_export_csv_requires_auth(
    client: AsyncClient,
) -> None:
    """Unauthenticated request (no session cookie) must not serve CSV data.

    Uses the shared ``client`` fixture (test DB wired, no auth cookie set) so
    the test runs in the same DB context as other tests in the session.  The
    app's 401 → 303 exception handler (or the 307 /setup redirect when no org
    exists) fires before any CSV data is written to the response.
    """
    resp = await client.get("/controls/export.csv")
    # Expect redirect to login or /setup — not 200 with CSV data.
    # 303: HTML redirect to /login (require_user → 401 → exception handler).
    # 307: setup redirect when no org exists.
    assert resp.status_code in (302, 303, 307, 401, 403)
    assert resp.headers.get("content-type", "").startswith("text/csv") is False
