"""Empty-state CTAs match each target route's RBAC gate (PR-5 review fix).

The empty_state macro applies no gating of its own, so the list templates
build the CTA list per-role: library browse (viewer+) always shows;
authoring CTAs only for analyst+; import only for admin. A button that
403s for the viewer who clicks it must never render.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_viewer_empty_scenarios_list_hides_gated_ctas(
    authed_viewer: tuple[AsyncClient, uuid.UUID],
) -> None:
    client, _ = authed_viewer
    r = await client.get("/scenarios")
    assert r.status_code == 200
    assert "No scenarios yet" in r.text
    assert "Browse library" in r.text  # /library is viewer+
    # Page-wide assertions: BOTH the empty-state CTAs and the page-header
    # actions are role-gated now.
    assert "Import CSV" not in r.text  # /scenarios/import is admin-only
    assert "/scenarios/new/wizard" not in r.text  # wizard is analyst+


@pytest.mark.asyncio
async def test_viewer_empty_controls_list_hides_gated_ctas(
    authed_viewer: tuple[AsyncClient, uuid.UUID],
) -> None:
    client, _ = authed_viewer
    r = await client.get("/controls")
    assert r.status_code == 200
    assert "No controls yet" in r.text
    assert "Browse library" in r.text  # /controls/library is viewer+
    # Page-wide assertions: BOTH the empty-state CTAs and the page-header
    # actions are role-gated now.
    assert "Import CSV" not in r.text  # /controls/import is admin-only
    assert "/controls/new" not in r.text  # analyst+


@pytest.mark.asyncio
async def test_admin_empty_scenarios_list_shows_all_ctas(
    admin_client: AsyncClient,
) -> None:
    r = await admin_client.get("/scenarios")
    assert r.status_code == 200
    assert "Browse library" in r.text
    assert "Import CSV" in r.text
    assert "/scenarios/new/wizard" in r.text
