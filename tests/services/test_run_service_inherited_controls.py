"""#299: run-create freezes inherited = org-wide − explicitly linked, per
scenario, for SINGLE and AGGREGATE; non-operating org-wide controls and
other orgs' controls are excluded; the audit row carries the map; an
ineligible org-wide control is refused at run-create."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.errors import RunValidationError
from idraa.models.audit_log import AuditLog
from idraa.models.control import Control
from idraa.models.enums import ControlDomain, ControlImplementationStage, FairCamSubFunction
from idraa.models.risk_analysis_run import RiskAnalysisRun
from idraa.models.scenario import Scenario
from idraa.models.scenario_control import ScenarioControl
from idraa.services.runs import RunService


async def _setup(
    db_session: AsyncSession, seed_control_factory: Any, seed_scenario_factory: Any
) -> tuple[Control, Control, Control, Control, Scenario, Scenario]:
    pm = await seed_control_factory(name="Patch Mgmt", domain=ControlDomain.VARIANCE_MANAGEMENT)
    sat = await seed_control_factory(name="SAT", domain=ControlDomain.DECISION_SUPPORT)
    planned = await seed_control_factory(name="Planned", domain=ControlDomain.VARIANCE_MANAGEMENT)
    edr = await seed_control_factory(name="EDR", domain=ControlDomain.LOSS_EVENT)
    pm.org_wide_meta = sat.org_wide_meta = planned.org_wide_meta = True
    planned.implementation_stage = ControlImplementationStage.PLANNED
    s_a = await seed_scenario_factory(name="A")
    s_b = await seed_scenario_factory(name="B")
    db_session.add_all(
        [
            ScenarioControl(scenario_id=s_a.id, control_id=edr.id),
            ScenarioControl(scenario_id=s_a.id, control_id=pm.id),  # explicit link supersedes
            ScenarioControl(scenario_id=s_b.id, control_id=edr.id),
        ]
    )
    await db_session.commit()
    return pm, sat, planned, edr, s_a, s_b


async def _create(
    db: AsyncSession, org: Any, user: Any, scenario_ids: list[uuid.UUID]
) -> RiskAnalysisRun:
    run = await RunService(db).create_and_dispatch(
        organization_id=org.id,
        scenario_ids=scenario_ids,
        mc_iterations_override=200,
        created_by=user.id,
        background_tasks=BackgroundTasks(),
    )
    await db.refresh(run)
    return run


@pytest.mark.asyncio
async def test_aggregate_freeze_inherits_org_wide_minus_explicit_and_audits(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_user: Any,
    seed_control_factory: Any,
    seed_scenario_factory: Any,
    wire_executor_to_test_db: Any,
) -> None:
    pm, sat, planned, edr, s_a, s_b = await _setup(
        db_session, seed_control_factory, seed_scenario_factory
    )
    run = await _create(db_session, seed_organization, seed_user, [s_a.id, s_b.id])
    inh = run.inherited_control_ids_per_scenario
    assert inh is not None
    assert set(inh[str(s_a.id)]) == {str(sat.id)}  # pm linked explicitly -> not inherited
    assert set(inh[str(s_b.id)]) == {str(pm.id), str(sat.id)}
    assert str(planned.id) not in set(inh[str(s_a.id)]) | set(inh[str(s_b.id)])
    assert run.aggregate_control_ids_per_scenario is not None
    assert set(run.aggregate_control_ids_per_scenario[str(s_a.id)]) == {str(edr.id), str(pm.id)}
    row = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "risk_analysis_run.create", AuditLog.entity_id == run.id
            )
        )
    ).scalar_one()
    assert row.changes["inherited_control_ids_per_scenario"] == inh


@pytest.mark.asyncio
async def test_single_freeze_uses_one_key_and_empty_dict_when_nothing_inherited(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_user: Any,
    seed_control_factory: Any,
    seed_scenario_factory: Any,
    wire_executor_to_test_db: Any,
) -> None:
    pm, sat, planned, edr, s_a, s_b = await _setup(
        db_session, seed_control_factory, seed_scenario_factory
    )
    run = await _create(db_session, seed_organization, seed_user, [s_b.id])
    inh = run.inherited_control_ids_per_scenario
    assert inh is not None
    assert set(inh[str(s_b.id)]) == {str(pm.id), str(sat.id)}

    pm.org_wide_meta = sat.org_wide_meta = False
    await db_session.commit()
    run2 = await _create(db_session, seed_organization, seed_user, [s_b.id])
    assert run2.inherited_control_ids_per_scenario == {str(s_b.id): []}
    assert run.inputs_hash != run2.inputs_hash


@pytest.mark.asyncio
async def test_other_orgs_org_wide_controls_never_inherited(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_organization_factory: Any,
    seed_user: Any,
    seed_control_factory: Any,
    seed_scenario_factory: Any,
    wire_executor_to_test_db: Any,
) -> None:
    """Spec §9 T1."""
    other = await seed_organization_factory(name="Other")
    theirs = await seed_control_factory(
        name="Theirs", domain=ControlDomain.VARIANCE_MANAGEMENT, organization_id=other.id
    )
    theirs.org_wide_meta = True
    edr = await seed_control_factory(name="EDR", domain=ControlDomain.LOSS_EVENT)
    s = await seed_scenario_factory(name="S")
    db_session.add(ScenarioControl(scenario_id=s.id, control_id=edr.id))
    await db_session.commit()
    run = await _create(db_session, seed_organization, seed_user, [s.id])
    assert run.inherited_control_ids_per_scenario == {str(s.id): []}
    assert str(theirs.id) not in run.control_ids_used


@pytest.mark.asyncio
async def test_run_create_refuses_org_wide_control_without_inheritable_row(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_user: Any,
    seed_control_factory: Any,
    seed_scenario_factory: Any,
    wire_executor_to_test_db: Any,
) -> None:
    """Sec-I3 defence in depth: a flag that bypassed the service invariant fails
    loud at run-create, naming the control, before enqueue."""
    bad = await seed_control_factory(
        name="Bad",
        domain=ControlDomain.LOSS_EVENT,
        sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
    )
    bad.org_wide_meta = True  # direct ORM write bypasses create_control's check
    s = await seed_scenario_factory(name="S")
    await db_session.commit()
    with pytest.raises(RunValidationError, match="Bad"):
        await _create(db_session, seed_organization, seed_user, [s.id])
