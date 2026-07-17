"""scenario_controls many-to-many join.

CASCADE on scenario_id (deleting a scenario removes its control refs).
RESTRICT on control_id (deleting a Control referenced by a scenario is
blocked at DB level).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.scenario import Scenario
from idraa.models.scenario_control import ScenarioControl

SeedControlFactory = Callable[..., Awaitable[Any]]


@pytest.mark.asyncio
async def test_scenario_control_compound_pk(
    db_session: AsyncSession,
    seed_scenario_with_no_controls: Scenario,
    seed_control_factory: SeedControlFactory,
) -> None:
    """Composite PK (scenario_id, control_id) — same pair cannot be inserted twice."""
    scenario = seed_scenario_with_no_controls
    control = await seed_control_factory(name="Firewall")

    db_session.add(ScenarioControl(scenario_id=scenario.id, control_id=control.id))
    await db_session.flush()

    db_session.add(ScenarioControl(scenario_id=scenario.id, control_id=control.id))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_scenario_delete_cascades_join_rows(
    db_session: AsyncSession,
    seed_scenario_with_no_controls: Scenario,
    seed_control_factory: SeedControlFactory,
) -> None:
    """Deleting a scenario removes its scenario_controls rows."""
    scenario = seed_scenario_with_no_controls
    control = await seed_control_factory(name="EDR")

    db_session.add(ScenarioControl(scenario_id=scenario.id, control_id=control.id))
    await db_session.commit()

    await db_session.delete(scenario)
    await db_session.commit()

    rows = (
        (
            await db_session.execute(
                select(ScenarioControl).where(ScenarioControl.control_id == control.id)
            )
        )
        .scalars()
        .all()
    )
    assert rows == []


@pytest.mark.asyncio
async def test_control_delete_blocked_when_referenced(
    db_session: AsyncSession,
    seed_scenario_with_no_controls: Scenario,
    seed_control_factory: SeedControlFactory,
) -> None:
    """Deleting a Control referenced by a scenario is blocked (RESTRICT)."""
    scenario = seed_scenario_with_no_controls
    control = await seed_control_factory(name="MFA")

    db_session.add(ScenarioControl(scenario_id=scenario.id, control_id=control.id))
    await db_session.commit()

    await db_session.delete(control)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_scenario_mitigating_controls_relationship(
    db_session: AsyncSession,
    seed_scenario_with_no_controls: Scenario,
    seed_control_factory: SeedControlFactory,
) -> None:
    """Scenario.mitigating_controls returns the joined Control rows."""
    scenario = seed_scenario_with_no_controls
    c1 = await seed_control_factory(name="A")
    c2 = await seed_control_factory(name="B")

    db_session.add(ScenarioControl(scenario_id=scenario.id, control_id=c1.id))
    db_session.add(ScenarioControl(scenario_id=scenario.id, control_id=c2.id))
    await db_session.commit()

    await db_session.refresh(scenario, attribute_names=["mitigating_controls"])
    names = sorted(c.name for c in scenario.mitigating_controls)
    assert names == ["A", "B"]
