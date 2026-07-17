"""PR π — /calibration-overrides route surface is gone (404 regression)
AND /overlays admin UI survives (positive smoke). AC #5 both clauses.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize(
    "path",
    [
        "/calibration-overrides",
        "/calibration-overrides/template.csv",
        "/calibration-overrides/import",
        "/calibration-overrides/new",
        "/calibration-overrides/00000000-0000-0000-0000-000000000000",
        "/calibration-overrides/00000000-0000-0000-0000-000000000000/edit",
    ],
)
async def test_calibration_overrides_routes_return_404(
    authed_admin: tuple[AsyncClient, uuid.UUID], path: str
) -> None:
    """AC #5 first clause: /calibration-overrides surface is gone (404)."""
    client, _org_id = authed_admin
    resp = await client.get(path)
    assert resp.status_code == 404, f"{path} should 404 post-PR π; got {resp.status_code}"


# AC #5 second clause: /overlays admin UI survives.
@pytest.mark.parametrize("path", ["/overlays", "/overlays/new"])
async def test_overlays_admin_routes_survive(
    authed_admin: tuple[AsyncClient, uuid.UUID], path: str
) -> None:
    """Spec AC #5: '/overlays admin UI survives unchanged.' Smoke 200-OK."""
    client, _org_id = authed_admin
    resp = await client.get(path)
    assert resp.status_code == 200, f"{path} should be reachable post-PR π"
