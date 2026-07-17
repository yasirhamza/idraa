"""Dropdown-first sub-function picker markup contract (#395).

Behavior (open/keyboard/commit) is JS — no Playwright harness is wired for this
form, so the JS is covered by manual UAT. This test pins the server-rendered
contract the JS depends on (combobox button + in-panel search + the unchanged
hidden-select / required / HTMX row-swap wiring).
"""

from __future__ import annotations

import uuid

from httpx import AsyncClient


async def test_picker_renders_button_search_and_hidden_select(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
) -> None:
    client, _ = authed_analyst
    resp = await client.get("/controls/new")
    assert resp.status_code == 200
    body = resp.text
    # Closed state is a button (combobox), not an always-visible text input.
    assert 'role="combobox"' in body
    assert 'x-ref="button"' in body
    # Search lives inside the panel.
    assert 'x-ref="search"' in body
    # Hidden select still the submit source of truth + HTMX-wired + required.
    assert 'name="assignments[0][sub_function]"' in body
    assert 'hx-get="/controls/_assignment_row' in body
    assert "required" in body
