"""Tests for MaintenanceBadgeCountMiddleware (issue #87)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_middleware_sets_zero_for_anonymous(client: AsyncClient) -> None:
    """Anonymous requests get count=0; never breaks the response."""
    r = await client.get("/login")
    assert r.status_code == 200
    # No badge in nav because no authenticated user
    assert 'href="/controls/maintenance"' not in r.text


async def test_middleware_sets_count_for_authenticated_user(
    authed_analyst,
) -> None:
    """Authenticated user with a clean org gets count=0 (no badge)."""
    client, _ = authed_analyst
    r = await client.get("/")
    assert r.status_code == 200
    # Clean state → no badge in nav
    assert 'href="/controls/maintenance"' not in r.text
