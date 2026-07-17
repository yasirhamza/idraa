"""Probe that the async DB + client fixtures actually work.

``asyncio_mode = "auto"`` in pyproject.toml means ``async def test_*`` functions
don't need a ``@pytest.mark.asyncio`` decorator — pytest-asyncio wires them up
automatically.
"""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def test_db_session_roundtrip(db_session: AsyncSession) -> None:
    result = await db_session.execute(text("SELECT 1"))
    assert result.scalar() == 1


async def test_client_healthz(client: AsyncClient) -> None:
    r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
