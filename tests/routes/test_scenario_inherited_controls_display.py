"""#299 spec §7: scenario view, scenario edit form, wizard step 5 and the
run-create preview show the org-wide controls a scenario inherits (not the
ones it links explicitly), with the meta-only sentence.

Every surface reads the org-wide list through
``ControlRepo.list_org_wide_meta(..., with_assignments=False)`` (a raiseload
variant). Each test below that renders an UNLINKED org-wide control therefore
also proves that path: if a template or context reached for
``control.assignments`` on such a row the render would raise and the status
assertion would fail."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.control import Control
from idraa.models.enums import ControlDomain, ControlImplementationStage
from idraa.models.scenario import Scenario
from idraa.models.scenario_control import ScenarioControl

SENTENCE = "Only their VMC/DSC functions apply here"
HEADING = "Inherited organization-wide"


@pytest_asyncio.fixture
async def org_wide_pair(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_control_factory: Any,
    seed_scenario_factory: Any,
) -> tuple[AsyncClient, Scenario, Control, Control]:
    """``pm`` (org-wide, linked on S) and ``sat`` (org-wide, NOT linked on S)."""
    client, org_id = authed_analyst
    pm = await seed_control_factory(
        name="Patch Mgmt", domain=ControlDomain.VARIANCE_MANAGEMENT, organization_id=org_id
    )
    sat = await seed_control_factory(
        name="SAT", domain=ControlDomain.DECISION_SUPPORT, organization_id=org_id
    )
    pm.org_wide_meta = sat.org_wide_meta = True
    s = await seed_scenario_factory(name="S", organization_id=org_id, created_by=pm.created_by)
    db_session.add(ScenarioControl(scenario_id=s.id, control_id=pm.id))
    await db_session.commit()
    return client, s, pm, sat


def _inherited_block(body: str) -> str:
    assert HEADING in body
    return body.split(HEADING, 1)[1].split("</ul>", 1)[0]


def _x_data_json(body: str, key: str) -> Any:
    """The JSON value of one ``key: {...},`` entry of the run-create form's x-data."""
    m = re.search(rf"^\s*{key}: (.+),$", body, flags=re.MULTILINE)
    assert m is not None, f"{key} missing from the x-data island"
    return json.loads(m.group(1))


@pytest.mark.asyncio
async def test_scenario_view_lists_inherited_not_linked(
    org_wide_pair: tuple[AsyncClient, Scenario, Control, Control],
) -> None:
    client, s, _pm, _sat = org_wide_pair
    r = await client.get(f"/scenarios/{s.id}")
    assert r.status_code == 200
    block = _inherited_block(r.text)
    assert "SAT" in block and "Patch Mgmt" not in block
    assert SENTENCE in r.text
    assert "org-wide</span>" in block  # badge beside the inherited name


@pytest.mark.asyncio
async def test_scenario_edit_form_lists_inherited_not_linked(
    org_wide_pair: tuple[AsyncClient, Scenario, Control, Control],
) -> None:
    client, s, _pm, _sat = org_wide_pair
    r = await client.get(f"/scenarios/{s.id}/edit")
    assert r.status_code == 200
    block = _inherited_block(r.text)
    assert "SAT" in block and "Patch Mgmt" not in block
    assert SENTENCE in r.text
    assert "<input" not in block  # read-only: the inherited list carries no inputs


@pytest.mark.asyncio
async def test_inherited_list_absent_when_org_has_no_org_wide_control(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_control_factory: Any,
    seed_scenario_factory: Any,
) -> None:
    client, org_id = authed_analyst
    await seed_control_factory(name="Plain", organization_id=org_id)
    s = await seed_scenario_factory(name="S", organization_id=org_id)
    await db_session.commit()
    for path in (f"/scenarios/{s.id}", f"/scenarios/{s.id}/edit"):
        r = await client.get(path)
        assert r.status_code == 200
        assert HEADING not in r.text
        assert SENTENCE not in r.text


@pytest.mark.asyncio
async def test_inherited_list_uses_effective_status_not_the_raw_flag(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_control_factory: Any,
    seed_scenario_factory: Any,
) -> None:
    """A control flagged org-wide but not operating (stage != ACTIVE) is not
    inherited right now, so no surface lists it as inherited."""
    client, org_id = authed_analyst
    planned = await seed_control_factory(
        name="Planned Policy", domain=ControlDomain.VARIANCE_MANAGEMENT, organization_id=org_id
    )
    planned.org_wide_meta = True
    planned.implementation_stage = ControlImplementationStage.PLANNED
    s = await seed_scenario_factory(name="S", organization_id=org_id, created_by=planned.created_by)
    await db_session.commit()
    for path in (f"/scenarios/{s.id}", f"/scenarios/{s.id}/edit"):
        r = await client.get(path)
        assert r.status_code == 200
        assert HEADING not in r.text
    preview = await client.get("/analyses/new")
    assert preview.status_code == 200
    assert "Planned Policy (org-wide)" not in preview.text
    assert _x_data_json(preview.text, "scenarioToInherited") == {str(s.id): []}


@pytest.mark.asyncio
async def test_inherited_list_is_org_scoped(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_control_factory: Any,
    seed_scenario_factory: Any,
) -> None:
    """An org-wide control of ANOTHER org (the factory defaults to the seeded
    org, not the analyst's) never shows up as inherited."""
    client, org_id = authed_analyst
    foreign = await seed_control_factory(
        name="Foreign Org-Wide", domain=ControlDomain.VARIANCE_MANAGEMENT
    )
    foreign.org_wide_meta = True
    s = await seed_scenario_factory(name="S", organization_id=org_id)
    await db_session.commit()
    assert foreign.organization_id != org_id
    for path in (f"/scenarios/{s.id}", f"/scenarios/{s.id}/edit", "/analyses/new"):
        r = await client.get(path)
        assert r.status_code == 200
        assert "Foreign Org-Wide" not in r.text


@pytest.mark.asyncio
async def test_run_create_preview_embeds_inherited_map_without_renaming_explicit_chips(
    org_wide_pair: tuple[AsyncClient, Scenario, Control, Control],
) -> None:
    client, s, pm, sat = org_wide_pair
    r = await client.get("/analyses/new")
    assert r.status_code == 200
    body = r.text
    assert "scenarioToInherited" in body and str(sat.id) in body
    assert "inheritedNameById" in body
    assert "SAT (org-wide)" in body
    assert "Patch Mgmt (org-wide)" not in body  # pm is linked on S -> explicit chip keeps its name
    assert _x_data_json(body, "scenarioToInherited") == {str(s.id): [str(sat.id)]}
    assert _x_data_json(body, "inheritedNameById") == {str(sat.id): "SAT (org-wide)"}
    # The explicit chip map is untouched: pm keeps its plain name (Arch-N4).
    assert _x_data_json(body, "nameById")[str(pm.id)] == "Patch Mgmt"
    assert "Inherited (org-wide)" in body


@pytest.mark.asyncio
async def test_run_create_preview_inherits_per_scenario_with_three_scenarios(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_control_factory: Any,
    seed_scenario_factory: Any,
) -> None:
    """N >= 3 scenarios: each gets exactly the org-wide controls it does not link,
    and a control linked on EVERY listed scenario never gets an "(org-wide)" name."""
    client, org_id = authed_analyst
    pm = await seed_control_factory(
        name="Patch Mgmt", domain=ControlDomain.VARIANCE_MANAGEMENT, organization_id=org_id
    )
    sat = await seed_control_factory(
        name="SAT", domain=ControlDomain.DECISION_SUPPORT, organization_id=org_id
    )
    cm = await seed_control_factory(
        name="Change Mgmt", domain=ControlDomain.VARIANCE_MANAGEMENT, organization_id=org_id
    )
    pm.org_wide_meta = sat.org_wide_meta = cm.org_wide_meta = True
    scenarios = [
        await seed_scenario_factory(name=f"S{i}", organization_id=org_id, created_by=pm.created_by)
        for i in range(3)
    ]
    # pm linked everywhere; sat linked on S0 only; cm linked nowhere.
    for s in scenarios:
        db_session.add(ScenarioControl(scenario_id=s.id, control_id=pm.id))
    db_session.add(ScenarioControl(scenario_id=scenarios[0].id, control_id=sat.id))
    await db_session.commit()

    r = await client.get("/analyses/new")
    assert r.status_code == 200
    inherited = _x_data_json(r.text, "scenarioToInherited")
    assert {sid: sorted(ids) for sid, ids in inherited.items()} == {
        str(scenarios[0].id): sorted([str(cm.id)]),
        str(scenarios[1].id): sorted([str(cm.id), str(sat.id)]),
        str(scenarios[2].id): sorted([str(cm.id), str(sat.id)]),
    }
    names = _x_data_json(r.text, "inheritedNameById")
    assert names == {str(cm.id): "Change Mgmt (org-wide)", str(sat.id): "SAT (org-wide)"}
    assert "Patch Mgmt (org-wide)" not in r.text  # linked on every listed scenario (SC-I17)
