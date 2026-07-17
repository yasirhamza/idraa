"""Scenario view flags attached-but-non-active controls (#395).

Task 9: the scenario DETAIL view must surface that an attached control whose
``implementation_stage`` is not ACTIVE is excluded from analyses (it never
reaches the FAIR-CAM composition). Operator transparency: a control wired to a
scenario but sitting at PLANNED silently contributes nothing, so the view
labels it "not counted" rather than presenting it as if it were live.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient

from idraa.models.enums import ControlImplementationStage
from idraa.models.scenario_control import ScenarioControl


@pytest.mark.asyncio
async def test_view_notes_excluded_control(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: Any,
    seed_scenario_factory: Any,
    seed_control_factory: Any,
) -> None:
    """An attached PLANNED control renders with a 'not counted' note + its name."""
    client, org_id = authed_analyst

    scenario = await seed_scenario_factory(name="excl-view-scenario", organization_id=org_id)
    control = await seed_control_factory(
        name="Planned-but-attached control", organization_id=org_id
    )
    db_session.add(ScenarioControl(scenario_id=scenario.id, control_id=control.id))
    # Demote the ACTIVE-created control to PLANNED — it stays attached but is
    # gated out of analyses. cross-engine SQLite needs commit (not flush).
    control.implementation_stage = ControlImplementationStage.PLANNED
    await db_session.commit()

    resp = await client.get(f"/scenarios/{scenario.id}")

    assert resp.status_code == 200
    assert "not counted" in resp.text.lower()
    assert control.name in resp.text
