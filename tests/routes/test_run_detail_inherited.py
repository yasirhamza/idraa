"""#299: run detail shows inherited org-wide controls in their own group,
badges them in the attribution rows, does not flag them as 'overridden', and
shows the legacy note only when the org now has an org-wide control and the
run predates the feature."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import BackgroundTasks
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.control import Control
from idraa.models.enums import ControlDomain
from idraa.models.risk_analysis_run import RiskAnalysisRun
from idraa.models.scenario_control import ScenarioControl
from idraa.services.runs import RunService


async def _seed(
    client_org: uuid.UUID,
    db_session: AsyncSession,
    seed_control_factory: Any,
    seed_scenario_factory: Any,
    *,
    flag: bool,
) -> tuple[Control, RiskAnalysisRun]:
    org_id = client_org
    edr = await seed_control_factory(
        name="EDR", domain=ControlDomain.LOSS_EVENT, organization_id=org_id
    )
    pm = await seed_control_factory(
        name="Patch Mgmt", domain=ControlDomain.VARIANCE_MANAGEMENT, organization_id=org_id
    )
    pm.org_wide_meta = flag
    s = await seed_scenario_factory(name="S", organization_id=org_id, created_by=pm.created_by)
    db_session.add(ScenarioControl(scenario_id=s.id, control_id=edr.id))
    await db_session.commit()
    run = await RunService(db_session).create_and_dispatch(
        organization_id=org_id,
        scenario_ids=[s.id],
        mc_iterations_override=200,
        created_by=pm.created_by,
        background_tasks=BackgroundTasks(),
    )
    return pm, run


@pytest.mark.asyncio
async def test_detail_groups_inherited_badges_and_not_overridden(
    authed_analyst: tuple[AsyncClient, Any],
    db_session: AsyncSession,
    seed_control_factory: Any,
    seed_scenario_factory: Any,
    wire_executor_to_test_db: Any,
) -> None:
    client, org_id = authed_analyst
    _pm, run = await _seed(
        org_id, db_session, seed_control_factory, seed_scenario_factory, flag=True
    )
    r = await client.get(f"/runs/{run.id}")
    assert r.status_code == 200
    body = r.text
    assert "Inherited (organization-wide)" in body
    assert "Patch Mgmt (org-wide)" in body
    # Exactly one attribution-row badge: the pm:meta variant. EDR (plain, linked) has none.
    assert body.count('data-inherited="true"') == 1
    assert "has no standalone reduction of its own" in body
    assert "fair share / if removed" not in body  # SINGLE pages have no such column
    assert "Overridden from scenario default" not in body
    assert "predates organization-wide controls" not in body


@pytest.mark.asyncio
async def test_detail_legacy_note_only_when_org_has_org_wide(
    authed_analyst: tuple[AsyncClient, Any],
    db_session: AsyncSession,
    seed_control_factory: Any,
    seed_scenario_factory: Any,
    wire_executor_to_test_db: Any,
) -> None:
    client, org_id = authed_analyst
    pm, run = await _seed(
        org_id, db_session, seed_control_factory, seed_scenario_factory, flag=False
    )
    run.inherited_control_ids_per_scenario = None  # legacy row
    await db_session.commit()
    assert "predates organization-wide controls" not in (await client.get(f"/runs/{run.id}")).text
    pm.org_wide_meta = True
    await db_session.commit()
    body = (await client.get(f"/runs/{run.id}")).text
    assert "predates organization-wide controls" in body
    assert "1 control is now marked organization-wide" in body
    assert "re-running applies its Variance Management / Decision Support functions" in body
    assert "any scenario that does not link it." in body


@pytest.mark.asyncio
async def test_aggregate_detail_trust_chip_counts_inherited_only_control(
    authed_analyst: tuple[AsyncClient, Any],
    db_session: AsyncSession,
    seed_control_factory: Any,
    seed_scenario_factory: Any,
    wire_executor_to_test_db: Any,
) -> None:
    """Arch-N6: pm is inherited everywhere and linked nowhere, so the snapshot holds
    one plain EDR row (1 assignment) and ONLY a pm:meta variant row (1 assignment —
    the factory's VMC row; its LEC row is dropped by the meta filter). The variant
    row is the only place pm's confirmations exist, so the chip counts it: 1 + 1 = 2."""
    client, org_id = authed_analyst
    edr = await seed_control_factory(
        name="EDR", domain=ControlDomain.LOSS_EVENT, organization_id=org_id
    )
    pm = await seed_control_factory(
        name="Patch Mgmt", domain=ControlDomain.VARIANCE_MANAGEMENT, organization_id=org_id
    )
    pm.org_wide_meta = True
    s1 = await seed_scenario_factory(name="S1", organization_id=org_id, created_by=pm.created_by)
    s2 = await seed_scenario_factory(name="S2", organization_id=org_id, created_by=pm.created_by)
    db_session.add_all(
        [
            ScenarioControl(scenario_id=s1.id, control_id=edr.id),
            ScenarioControl(scenario_id=s2.id, control_id=edr.id),
        ]
    )
    await db_session.commit()
    run = await RunService(db_session).create_and_dispatch(
        organization_id=org_id,
        scenario_ids=[s1.id, s2.id],
        mc_iterations_override=200,
        created_by=pm.created_by,
        background_tasks=BackgroundTasks(),
    )
    body = (await client.get(f"/runs/{run.id}")).text
    assert "of 2 assignments confirmed" in body  # trust_chips.html format; EDR 1 + pm:meta 1
    assert "of 1 assignments confirmed" not in body  # the inherited-only control is not dropped


@pytest.mark.asyncio
async def test_aggregate_detail_trust_chip_counts_hybrid_once(
    authed_analyst: tuple[AsyncClient, Any],
    db_session: AsyncSession,
    seed_control_factory: Any,
    seed_scenario_factory: Any,
    wire_executor_to_test_db: Any,
) -> None:
    """Arch-N4: pm is linked on S1 (plain row: VMC + LEC = 2 assignments) and inherited
    on S2 (pm:meta variant row: VMC = 1 assignment). Its meta confirmation exists in
    BOTH rows; the chip counts the plain row only: EDR 1 + pm 2 = 3, never 4."""
    client, org_id = authed_analyst
    edr = await seed_control_factory(
        name="EDR", domain=ControlDomain.LOSS_EVENT, organization_id=org_id
    )
    pm = await seed_control_factory(
        name="Patch Mgmt", domain=ControlDomain.VARIANCE_MANAGEMENT, organization_id=org_id
    )
    pm.org_wide_meta = True
    s1 = await seed_scenario_factory(name="S1", organization_id=org_id, created_by=pm.created_by)
    s2 = await seed_scenario_factory(name="S2", organization_id=org_id, created_by=pm.created_by)
    db_session.add_all(
        [
            ScenarioControl(scenario_id=s1.id, control_id=edr.id),
            ScenarioControl(scenario_id=s1.id, control_id=pm.id),
            ScenarioControl(scenario_id=s2.id, control_id=edr.id),
        ]
    )
    await db_session.commit()
    run = await RunService(db_session).create_and_dispatch(
        organization_id=org_id,
        scenario_ids=[s1.id, s2.id],
        mc_iterations_override=200,
        created_by=pm.created_by,
        background_tasks=BackgroundTasks(),
    )
    body = (await client.get(f"/runs/{run.id}")).text
    assert "of 3 assignments confirmed" in body  # EDR 1 + pm plain 2; the variant row is skipped
    assert "of 4 assignments confirmed" not in body  # not double-counted
    # Exactly one badge: the pm:meta ledger row. The hybrid's PLAIN pm row composed the full
    # control on S1, so it must not read "inherited"; EDR is plain too.
    assert body.count('data-inherited="true"') == 1


@pytest.mark.asyncio
async def test_aggregate_detail_ledger_badges_inherited_control(
    authed_analyst: tuple[AsyncClient, Any],
    db_session: AsyncSession,
    seed_control_factory: Any,
    seed_scenario_factory: Any,
    wire_executor_to_test_db: Any,
) -> None:
    """The AGGREGATE control ledger badges the inherited variant's row (its name is
    already "<name> (org-wide)"); the plain linked control carries no badge."""
    client, org_id = authed_analyst
    edr = await seed_control_factory(
        name="EDR", domain=ControlDomain.LOSS_EVENT, organization_id=org_id
    )
    pm = await seed_control_factory(
        name="Patch Mgmt", domain=ControlDomain.VARIANCE_MANAGEMENT, organization_id=org_id
    )
    pm.org_wide_meta = True
    s1 = await seed_scenario_factory(name="S1", organization_id=org_id, created_by=pm.created_by)
    s2 = await seed_scenario_factory(name="S2", organization_id=org_id, created_by=pm.created_by)
    db_session.add_all(
        [
            ScenarioControl(scenario_id=s1.id, control_id=edr.id),
            ScenarioControl(scenario_id=s2.id, control_id=edr.id),
        ]
    )
    await db_session.commit()
    run = await RunService(db_session).create_and_dispatch(
        organization_id=org_id,
        scenario_ids=[s1.id, s2.id],
        mc_iterations_override=200,
        created_by=pm.created_by,
        background_tasks=BackgroundTasks(),
    )
    body = (await client.get(f"/runs/{run.id}")).text
    assert "Patch Mgmt (org-wide)" in body
    # Exactly one badge: the pm:meta ledger row. EDR (plain, linked) carries none.
    assert body.count('data-inherited="true"') == 1
    assert "Overridden from scenario default" not in body


@pytest.mark.asyncio
async def test_detail_inherited_only_run_has_no_empty_main_table(
    authed_analyst: tuple[AsyncClient, Any],
    db_session: AsyncSession,
    seed_control_factory: Any,
    seed_scenario_factory: Any,
    wire_executor_to_test_db: Any,
) -> None:
    """A scenario that links nothing and inherits one control has only a variant row in
    its snapshot: the page shows the inherited group, not an empty main table."""
    client, org_id = authed_analyst
    pm = await seed_control_factory(
        name="Patch Mgmt", domain=ControlDomain.VARIANCE_MANAGEMENT, organization_id=org_id
    )
    pm.org_wide_meta = True
    s = await seed_scenario_factory(name="S", organization_id=org_id, created_by=pm.created_by)
    await db_session.commit()
    run = await RunService(db_session).create_and_dispatch(
        organization_id=org_id,
        scenario_ids=[s.id],
        mc_iterations_override=200,
        created_by=pm.created_by,
        background_tasks=BackgroundTasks(),
    )
    body = (await client.get(f"/runs/{run.id}")).text
    assert "Inherited (organization-wide)" in body
    assert "Patch Mgmt (org-wide)" in body
    assert "No controls applied to this run." not in body
    assert '<th class="text-right text-meta uppercase">Assignments</th>' not in body
    assert "Overridden from scenario default" not in body


@pytest.mark.asyncio
async def test_status_fragment_for_legacy_run_renders(
    authed_analyst: tuple[AsyncClient, Any],
    db_session: AsyncSession,
    seed_control_factory: Any,
    seed_scenario_factory: Any,
    wire_executor_to_test_db: Any,
) -> None:
    """The poll fragment takes the same legacy-count path as the detail page."""
    client, org_id = authed_analyst
    pm, run = await _seed(
        org_id, db_session, seed_control_factory, seed_scenario_factory, flag=True
    )
    run.inherited_control_ids_per_scenario = None  # legacy row
    await db_session.commit()
    assert pm.org_wide_meta is True
    r = await client.get(f"/runs/{run.id}/status")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_detail_legacy_note_for_explicitly_linked_org_wide_control(
    authed_analyst: tuple[AsyncClient, Any],
    db_session: AsyncSession,
    seed_control_factory: Any,
    seed_scenario_factory: Any,
    wire_executor_to_test_db: Any,
) -> None:
    """The common migration shape: the run LINKED the control explicitly (its snapshot
    holds it as a plain row), then the org flagged it organization-wide. The run did
    apply it, so the note must not claim it was left out; it says what a re-run adds."""
    client, org_id = authed_analyst
    pm = await seed_control_factory(
        name="Patch Mgmt", domain=ControlDomain.VARIANCE_MANAGEMENT, organization_id=org_id
    )
    s = await seed_scenario_factory(name="S", organization_id=org_id, created_by=pm.created_by)
    db_session.add(ScenarioControl(scenario_id=s.id, control_id=pm.id))
    await db_session.commit()
    run = await RunService(db_session).create_and_dispatch(
        organization_id=org_id,
        scenario_ids=[s.id],
        mc_iterations_override=200,
        created_by=pm.created_by,
        background_tasks=BackgroundTasks(),
    )
    run.inherited_control_ids_per_scenario = None  # pre-feature row
    await db_session.commit()
    pm.org_wide_meta = True
    await db_session.commit()
    body = (await client.get(f"/runs/{run.id}")).text
    # The snapshot holds pm as a plain linked row (no variant, no inherited group).
    assert "Patch Mgmt" in body
    assert "Inherited (organization-wide)" not in body
    # New wording: true whether or not the run linked the control.
    assert "This run predates organization-wide controls." in body
    assert "1 control is now marked organization-wide" in body
    assert "re-running applies its Variance Management / Decision Support functions" in body
    assert "to any scenario that does not link it." in body
    # The old claim is gone.
    assert "not applied here" not in body
    assert "were not applied" not in body


@pytest.mark.asyncio
async def test_detail_legacy_note_plural_agreement(
    authed_analyst: tuple[AsyncClient, Any],
    db_session: AsyncSession,
    seed_control_factory: Any,
    seed_scenario_factory: Any,
    wire_executor_to_test_db: Any,
) -> None:
    """Two org-wide controls: "2 controls are ... their ... link them"."""
    client, org_id = authed_analyst
    pm, run = await _seed(
        org_id, db_session, seed_control_factory, seed_scenario_factory, flag=False
    )
    other = await seed_control_factory(
        name="Governance", domain=ControlDomain.DECISION_SUPPORT, organization_id=org_id
    )
    run.inherited_control_ids_per_scenario = None  # pre-feature row
    pm.org_wide_meta = True
    other.org_wide_meta = True
    await db_session.commit()
    body = (await client.get(f"/runs/{run.id}")).text
    assert "2 controls are now marked organization-wide" in body
    assert "re-running applies their Variance Management / Decision Support functions" in body
    assert "any scenario that does not link them." in body


@pytest.mark.asyncio
async def test_detail_no_legacy_note_on_new_run_that_inherits_nothing(
    authed_analyst: tuple[AsyncClient, Any],
    db_session: AsyncSession,
    seed_control_factory: Any,
    seed_scenario_factory: Any,
    wire_executor_to_test_db: Any,
) -> None:
    """A NEW run carries a map even when nothing was inherited (``{sid: []}``, because
    the scenario links the org-wide control explicitly and explicit supersedes
    inheritance). Only a NULL map is a legacy run: no note, even though the org has
    an org-wide control."""
    client, org_id = authed_analyst
    pm = await seed_control_factory(
        name="Patch Mgmt", domain=ControlDomain.VARIANCE_MANAGEMENT, organization_id=org_id
    )
    pm.org_wide_meta = True
    s = await seed_scenario_factory(name="S", organization_id=org_id, created_by=pm.created_by)
    db_session.add(ScenarioControl(scenario_id=s.id, control_id=pm.id))
    await db_session.commit()
    run = await RunService(db_session).create_and_dispatch(
        organization_id=org_id,
        scenario_ids=[s.id],
        mc_iterations_override=200,
        created_by=pm.created_by,
        background_tasks=BackgroundTasks(),
    )
    await db_session.refresh(run)
    assert run.inherited_control_ids_per_scenario == {str(s.id): []}  # feature-era, empty list
    body = (await client.get(f"/runs/{run.id}")).text
    assert "predates organization-wide controls" not in body
    assert "Inherited (organization-wide)" not in body
