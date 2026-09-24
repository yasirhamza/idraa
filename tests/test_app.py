"""Tests for the FastAPI app. Phase 0 has only /healthz."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from idraa.app import create_app


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def test_healthz_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"


async def test_healthz_is_liveness_only(client: AsyncClient) -> None:
    """Advisory C5: /healthz is unauthenticated and exempt from every
    pre-gate, so it must not disclose the version or security-settings
    state (that operator signal lives on /settings/security)."""
    response = await client.get("/healthz")
    assert response.json() == {"status": "ok"}
