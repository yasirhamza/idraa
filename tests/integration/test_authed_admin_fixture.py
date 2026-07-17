"""Smoke test for the ``authed_admin`` fixture (Task 1.1.7).

Verifies that the fixture's signed cookie makes it through the session
middleware chain and the dashboard template renders its authenticated branch.
"""

from __future__ import annotations

from httpx import AsyncClient


async def test_authed_admin_sees_signout(authed_admin: tuple[AsyncClient, object]) -> None:
    client, _ = authed_admin
    r = await client.get("/")
    assert r.status_code == 200
    assert "Sign out" in r.text
