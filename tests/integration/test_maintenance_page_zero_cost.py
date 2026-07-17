"""Tests that /controls/maintenance renders the $0-cost section (issue #87)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.control import Control
from idraa.models.enums import (
    ControlType,
    EntityStatus,
)

pytestmark = pytest.mark.asyncio


async def test_zero_cost_section_renders_with_controls(
    authed_analyst,
    db_session: AsyncSession,
) -> None:
    """Maintenance page should list $0-cost controls in a dedicated section."""
    client, org_id = authed_analyst
    placeholder = Control(
        organization_id=org_id,
        name="Imported Placeholder MFA",
        description="from importer",
        type=ControlType.ADMINISTRATIVE,
        annual_cost=Decimal("0"),
        status=EntityStatus.ACTIVE,
        version="1.0",
    )
    db_session.add(placeholder)
    await db_session.commit()
    await db_session.close()

    r = await client.get("/controls/maintenance")
    assert r.status_code == 200
    body = r.text
    assert "Controls with $0 annual cost" in body
    assert "Imported Placeholder MFA" in body
    # Each row should deep-link to the control's edit page.
    assert f"/controls/{placeholder.id}/edit" in body


async def test_zero_cost_section_absent_when_no_zero_cost_controls(
    authed_analyst,
) -> None:
    """Section header should not render if there are no $0-cost controls."""
    client, _ = authed_analyst
    r = await client.get("/controls/maintenance")
    assert r.status_code == 200
    assert "Controls with $0 annual cost" not in r.text
