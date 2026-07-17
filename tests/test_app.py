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


async def test_healthz_includes_version(client: AsyncClient) -> None:
    response = await client.get("/healthz")
    body = response.json()
    # Version may be 0.0.0 in phase 0 — just assert the field exists and is a string
    assert isinstance(body.get("version"), str)
