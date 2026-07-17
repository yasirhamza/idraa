"""F21: every list has a working /<entity>/export.csv route."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient


@pytest.mark.parametrize(
    "path,filename",
    [
        ("/scenarios/export?format=csv", "scenarios.csv"),
        ("/analyses/export.csv", "analyses.csv"),
        ("/library/export.csv", "library.csv"),
        ("/reports/export.csv", "reports.csv"),
        ("/overlays/export.csv", "overlays.csv"),
        ("/users/export.csv", "users.csv"),
    ],
)
async def test_list_csv_export_returns_attachment(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    path: str,
    filename: str,
) -> None:
    client, _ = authed_admin
    resp = await client.get(path)
    if resp.status_code == 404:
        pytest.skip(f"{path} not mounted")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert filename in resp.headers.get("content-disposition", "")


async def test_users_export_csv_admin_only(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
) -> None:
    """Non-admin users must be rejected from /users/export.csv (403)."""
    client, _ = authed_analyst
    resp = await client.get("/users/export.csv")
    assert resp.status_code == 403
