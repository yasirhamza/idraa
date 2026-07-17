"""Edit form labels stage-demoted linked controls + does not un-attach them (#395).

Task 9.5 (plan-gate Arch-A1 + Arch-A2). The active-only picker
(``ControlRepo.list_for_org`` filters out non-active controls) means a control
that was attached while ACTIVE then demoted to PLANNED now falls into the edit
form's ``inactive_linked_controls`` block. That block previously rendered the
contradictory ``(inactive: ACTIVE)`` — the EntityStatus is still ACTIVE; it's
the *implementation_stage* that demoted. This module pins:

1. the edit form labels such a control by its stage (Arch-A2), and
2. the edit POST does NOT un-attach it — the issue #217 eligible-set mechanism
   scopes removals to the controls the form could render, so a control outside
   that set survives an edit submitted without its id (Arch-A1).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from idraa.models.enums import ControlImplementationStage
from idraa.models.scenario import Scenario
from idraa.models.scenario_control import ScenarioControl
from tests.conftest import csrf_post


@pytest.mark.asyncio
async def test_edit_form_labels_stage_demoted_control(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: Any,
    seed_scenario_factory: Any,
    seed_control_factory: Any,
) -> None:
    """An attached-while-ACTIVE, then-PLANNED control is labeled by its stage."""
    client, org_id = authed_analyst

    scenario = await seed_scenario_factory(name="edit-demoted-scenario", organization_id=org_id)
    control = await seed_control_factory(
        name="Demoted-but-attached control", organization_id=org_id
    )
    db_session.add(ScenarioControl(scenario_id=scenario.id, control_id=control.id))
    # Demote the ACTIVE-created control to PLANNED — EntityStatus stays ACTIVE.
    # cross-engine SQLite needs commit (not flush) for the route layer to see it.
    control.implementation_stage = ControlImplementationStage.PLANNED
    await db_session.commit()

    resp = await client.get(f"/scenarios/{scenario.id}/edit")

    assert resp.status_code == 200
    # Must NOT render the contradictory "inactive: ACTIVE"; must name the stage.
    assert "inactive: ACTIVE" not in resp.text
    assert "Proposed / Planned" in resp.text  # the canonical .label


@pytest.mark.asyncio
async def test_edit_post_preserves_demoted_linked_control(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: Any,
    seed_scenario_factory: Any,
    seed_control_factory: Any,
) -> None:
    """Issue #217 mechanism: a demoted (non-active) control is outside the
    eligible set, so POSTing the edit WITHOUT its id must PRESERVE the link,
    not strip it."""
    client, org_id = authed_analyst

    scenario = await seed_scenario_factory(name="edit-survive-scenario", organization_id=org_id)
    control = await seed_control_factory(name="Survives-edit control", organization_id=org_id)
    db_session.add(ScenarioControl(scenario_id=scenario.id, control_id=control.id))
    control.implementation_stage = ControlImplementationStage.PLANNED
    await db_session.commit()

    # POST the edit form with all required fields but NO mitigating_control_ids
    # for `control` (it has no checkbox now — it is not in the eligible set).
    payload = {
        "name": scenario.name,
        "threat_category": scenario.threat_category,
        "tef_low": str(scenario.threat_event_frequency["low"]),
        "tef_mode": str(scenario.threat_event_frequency["mode"]),
        "tef_high": str(scenario.threat_event_frequency["high"]),
        "vuln_low": str(scenario.vulnerability["low"]),
        "vuln_mode": str(scenario.vulnerability["mode"]),
        "vuln_high": str(scenario.vulnerability["high"]),
        "pl_low": str(scenario.primary_loss["low"]),
        "pl_mode": str(scenario.primary_loss["mode"]),
        "pl_high": str(scenario.primary_loss["high"]),
        "expected_row_version": str(scenario.row_version),
    }
    r = await csrf_post(client, f"/scenarios/{scenario.id}", payload, follow_redirects=False)
    assert r.status_code == 303

    # The link must survive the edit (it was outside the eligible set).
    refreshed = (
        await db_session.execute(select(Scenario).where(Scenario.id == scenario.id))
    ).scalar_one()
    await db_session.refresh(refreshed, attribute_names=["mitigating_controls"])
    assert control.id in {c.id for c in refreshed.mitigating_controls}  # survived
