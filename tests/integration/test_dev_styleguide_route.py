"""F4: styleguide route — 404 when flag off, 200 when flag on."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_styleguide_404_when_flag_off(authed_admin: tuple[AsyncClient, object]) -> None:
    """Even an admin gets 404 when dev_styleguide_enabled is False (production default)."""
    client, _org_id = authed_admin
    resp = await client.get("/_dev/styleguide")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_styleguide_200_when_flag_on(
    client_styleguide_on: tuple[AsyncClient, object],
) -> None:
    """With the flag flipped + Settings cache cleared, admin gets a rendered page."""
    client, _org_id = client_styleguide_on
    resp = await client.get("/_dev/styleguide")
    assert resp.status_code == 200
    body = resp.text
    assert "Design tokens" in body
    assert "Typography" in body


@pytest.mark.asyncio
async def test_styleguide_every_macro_section_renders_no_placeholders(
    client_styleguide_on: tuple[AsyncClient, object],
) -> None:
    """F14: PR 1 wrap-up — every macro section is non-placeholder."""
    client, _org_id = client_styleguide_on
    resp = await client.get("/_dev/styleguide")
    assert resp.status_code == 200
    body = resp.text
    for heading in (
        "Design tokens",
        "Typography",
        "page_header",
        "breadcrumb",
        "status_pill",
        "empty_state",
        "action_menu",
        "data_table",
        "form_field",
        "form_error_summary",
        "kpi_card",
        "data_grid",
        "viewport_block_authoring",
        "chart (existing macro",
        "unit_aware_inputs (existing macro",
    ):
        assert heading in body, f"Styleguide is missing the '{heading}' section"
    # No placeholder strings
    assert "Filled in at F" not in body, "Styleguide still carries 'Filled in at F<N>' placeholders"
