"""Control create/edit form renders an implementation_stage select (#395)."""

from __future__ import annotations

import uuid

from httpx import AsyncClient


async def test_new_control_form_has_stage_select(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
) -> None:
    client, _ = authed_analyst
    resp = await client.get("/controls/new")
    assert resp.status_code == 200
    body = resp.text
    assert 'name="implementation_stage"' in body
    # All four stage options present.
    for v in ("non_existent", "planned", "in_project", "active"):
        assert f'value="{v}"' in body
