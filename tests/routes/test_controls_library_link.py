"""Regression: the controls list page must always expose a path to the
control library — not only in the zero-controls empty state.

Before this fix the sole link to ``/controls/library`` lived in the
``data_table`` ``empty={...}`` CTA, which renders only when the org has zero
controls. After adopting (or creating) any control the empty state — and the
only way to reach the library to adopt more — disappeared. The fix adds a
persistent "Browse library" action to the page header so it survives a
non-empty list.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.control import Control
from idraa.models.enums import ControlSource, ControlType, EntityStatus


def _make_control(org_id: uuid.UUID, *, name: str) -> Control:
    return Control(
        organization_id=org_id,
        name=name,
        type=ControlType.TECHNICAL,
        annual_cost=Decimal("0"),
        status=EntityStatus.ACTIVE,
        version="1.0",
        source=ControlSource.CUSTOM,
        library_pin=None,
    )


@pytest.mark.asyncio
async def test_library_link_present_when_no_controls(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    """Empty state still offers the library (unchanged baseline)."""
    client, _org_id = authed_admin
    r = await client.get("/controls")
    assert r.status_code == 200, r.text[:300]
    assert "/controls/library" in r.text


@pytest.mark.asyncio
async def test_library_link_present_when_controls_exist(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """The regression: with a non-empty list the empty-state CTA is gone, so
    the header action is the only remaining path to the library."""
    client, org_id = authed_admin
    db_session.add(_make_control(org_id, name="SomeControl"))
    await db_session.commit()

    r = await client.get("/controls")
    assert r.status_code == 200, r.text[:300]
    assert "SomeControl" in r.text  # confirm we're past the empty state
    assert "/controls/library" in r.text, (
        "controls list with existing rows has no link to /controls/library — "
        "the only path to adopt more from the library disappeared"
    )
