"""Control.implementation_stage column default + persistence (#395)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.control import Control
from idraa.models.enums import ControlImplementationStage, ControlType
from idraa.models.organization import Organization


async def test_new_control_defaults_to_active(
    db_session: AsyncSession, organization: Organization
) -> None:
    c = Control(
        organization_id=organization.id,
        name="Default-stage control",
        type=ControlType.TECHNICAL,
    )
    db_session.add(c)
    await db_session.flush()
    await db_session.refresh(c)
    assert c.implementation_stage is ControlImplementationStage.ACTIVE


async def test_stage_round_trips(db_session: AsyncSession, organization: Organization) -> None:
    c = Control(
        organization_id=organization.id,
        name="Planned control",
        type=ControlType.TECHNICAL,
        implementation_stage=ControlImplementationStage.PLANNED,
    )
    db_session.add(c)
    await db_session.flush()
    fetched = await db_session.get(Control, c.id)
    assert fetched is not None
    assert fetched.implementation_stage is ControlImplementationStage.PLANNED
