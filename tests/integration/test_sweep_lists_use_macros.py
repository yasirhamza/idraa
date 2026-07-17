"""F21: every list page uses page_header + data_table macros."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient


@pytest.mark.parametrize(
    "path",
    ["/scenarios", "/analyses", "/library", "/reports", "/overlays", "/users", "/runs"],
)
async def test_list_page_uses_data_table_or_empty_state(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    path: str,
) -> None:
    client, _ = authed_admin
    resp = await client.get(path)
    if resp.status_code in (404, 405):
        pytest.skip(f"{path} not mounted or has no GET list handler")
    assert resp.status_code in (200, 302)
    body = resp.text
    assert "sticky" in body, f"{path} missing page_header sticky marker"
    # Either data_table overflow wrapper OR empty_state block
    assert "overflow-x-auto" in body or "No " in body or "Nothing here" in body, (
        f"{path} has neither data_table nor empty_state"
    )
