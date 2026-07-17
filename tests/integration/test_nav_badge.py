"""Tests that the nav-bar Maintenance badge reflects org maintenance state (issue #87)."""

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


async def test_nav_badge_renders_when_zero_cost_control_exists(
    authed_analyst,
    db_session: AsyncSession,
) -> None:
    """A $0-cost control alone should fire the nav badge."""
    client, org_id = authed_analyst
    ctrl = Control(
        organization_id=org_id,
        name="Imported placeholder",
        description="from importer",
        type=ControlType.ADMINISTRATIVE,
        annual_cost=Decimal("0"),
        status=EntityStatus.ACTIVE,
        version="1.0",
    )
    db_session.add(ctrl)
    await db_session.commit()
    await db_session.close()

    r = await client.get("/")
    assert r.status_code == 200
    body = r.text
    assert 'href="/controls/maintenance"' in body
    # Badge count rendered
    assert "badge-sm" in body


async def test_nav_badge_absent_when_clean(
    authed_analyst,
    db_session: AsyncSession,
) -> None:
    """No badge when there are no $0-cost controls and no unconfirmed assignments."""
    client, _ = authed_analyst
    r = await client.get("/")
    assert r.status_code == 200
    body = r.text
    assert 'href="/controls/maintenance"' not in body
