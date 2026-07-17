"""Control.source / library_pin provenance columns (P2b Task 1)."""

from __future__ import annotations

import pytest

from idraa.models.control import Control
from idraa.models.enums import ControlSource
from idraa.models.organization import Organization


@pytest.mark.asyncio
async def test_control_source_defaults_to_custom(db_session, organization: Organization):
    c = Control(organization_id=organization.id, name="X", type="technical", annual_cost=0)
    db_session.add(c)
    await db_session.flush()
    assert c.source == ControlSource.CUSTOM
    assert c.library_pin is None


@pytest.mark.asyncio
async def test_control_library_pin_roundtrips(db_session, organization: Organization):
    c = Control(
        organization_id=organization.id,
        name="Y",
        type="technical",
        annual_cost=0,
        source=ControlSource.LIBRARY_DERIVED,
        library_pin={"entry_id": "abc", "version": 1},
    )
    db_session.add(c)
    await db_session.flush()
    await db_session.refresh(c)
    assert c.source == ControlSource.LIBRARY_DERIVED
    assert c.library_pin == {"entry_id": "abc", "version": 1}
