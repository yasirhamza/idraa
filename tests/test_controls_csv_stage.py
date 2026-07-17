"""CSV export carries implementation_stage; import defaults it to active (#395)."""

from __future__ import annotations

import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.control import Control
from idraa.models.enums import ControlImplementationStage, ControlType


async def test_export_includes_stage_column(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_analyst
    c = Control(
        organization_id=org_id,
        name="Exp ctl",
        type=ControlType.TECHNICAL,
        implementation_stage=ControlImplementationStage.IN_PROJECT,
    )
    db_session.add(c)
    await db_session.commit()  # cross-engine SQLite: commit so the route's session sees it

    resp = await client.get("/controls/export.csv")
    assert resp.status_code == 200
    assert "implementation_stage" in resp.text  # header
    assert "in_project" in resp.text  # value row
