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
from idraa.models.control_function_assignment import ControlFunctionAssignment
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


_REFUSAL = "no inheritable Variance Management or Decision Support function"


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
    loud at run-create, before enqueue. The refusal text is fixed: it states the
    eligibility rule and does not carry the control's name."""
    bad = await seed_control_factory(
        name="Zq-Unique-Control-Name",
        domain=ControlDomain.LOSS_EVENT,
        sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
    )
    bad.org_wide_meta = True  # direct ORM write bypasses create_control's check
    s = await seed_scenario_factory(name="Zq-Unique-Scenario-Name")
    await db_session.commit()
    with pytest.raises(RunValidationError, match=_REFUSAL) as excinfo:
        await _create(db_session, seed_organization, seed_user, [s.id])
    assert "Zq-Unique-Control-Name" not in str(excinfo.value)
    assert "Zq-Unique-Scenario-Name" not in str(excinfo.value)
    assert "Correcting Misaligned Decisions link counts only when its source" in str(excinfo.value)


@pytest.mark.asyncio
async def test_run_create_refuses_dsc_corr_link_sourced_from_resilience(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_user: Any,
    seed_control_factory: Any,
    seed_scenario_factory: Any,
    wire_executor_to_test_db: Any,
) -> None:
    """D6 / Review Focus #4: the control's only Decision Support row is a
    Correcting Misaligned Decisions link sourced from an LEC (Resilience) row, so
    it carries no inheritable assignment — run-create refuses it. The services
    cannot produce this combination while the flag is set, so the rows are built
    directly.

    Mutation note (checked by hand when this test was written): dropping the
    ``derived_from_assignment_id is None`` filter at the freeze makes the derived
    row count as a plain Decision Support row, ``has_meta_assignment`` turns True,
    the control is admitted and this test fails — it pins that filter."""
    ctrl = await seed_control_factory(
        name="Zq-Resilience-Linked",
        domain=ControlDomain.LOSS_EVENT,
        sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
    )
    resilience = ControlFunctionAssignment(
        control_id=ctrl.id,
        organization_id=seed_organization.id,
        sub_function=FairCamSubFunction.LEC_RESP_RESILIENCE,
        capability_value=0.7,
        coverage=0.8,
        reliability=0.85,
    )
    db_session.add(resilience)
    await db_session.flush()
    db_session.add(
        ControlFunctionAssignment(
            control_id=ctrl.id,
            organization_id=seed_organization.id,
            sub_function=FairCamSubFunction.DSC_CORR_MISALIGNED,
            capability_value=None,
            coverage=0.0,
            reliability=0.0,
            derived_from_assignment_id=resilience.id,
        )
    )
    ctrl.org_wide_meta = True
    s = await seed_scenario_factory(name="S")
    await db_session.commit()
    with pytest.raises(RunValidationError, match=_REFUSAL) as excinfo:
        await _create(db_session, seed_organization, seed_user, [s.id])
    assert "Zq-Resilience-Linked" not in str(excinfo.value)


@pytest.mark.asyncio
async def test_freeze_keeps_all_n_inherited_controls_sorted_without_duplicates(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_user: Any,
    seed_control_factory: Any,
    seed_scenario_factory: Any,
    wire_executor_to_test_db: Any,
) -> None:
    """N >= 3 list contract (CLAUDE.md "Data contract enforcement"): four
    org-wide controls, one of them carrying THREE meta rows (so a join-based
    repo query would repeat it), one explicitly linked on scenario A only.
    Exact list equality per scenario for AGGREGATE and SINGLE: every inherited
    id kept, sorted, once, and the linked control missing only where it is linked."""
    ow = [
        await seed_control_factory(name=f"OW{i}", domain=ControlDomain.VARIANCE_MANAGEMENT)
        for i in range(4)
    ]
    for c in ow:
        c.org_wide_meta = True
    for sf in (
        FairCamSubFunction.DSC_PREV_DEFINED_EXPECTATIONS,
        FairCamSubFunction.VMC_CORR_IMPLEMENTATION,
    ):
        db_session.add(
            ControlFunctionAssignment(
                control_id=ow[0].id,
                organization_id=seed_organization.id,
                sub_function=sf,
                capability_value=0.7,
                coverage=0.8,
                reliability=0.85,
            )
        )
    edr = await seed_control_factory(name="EDR", domain=ControlDomain.LOSS_EVENT)
    s_a = await seed_scenario_factory(name="A")
    s_b = await seed_scenario_factory(name="B")
    linked = ow[1]
    db_session.add_all(
        [
            ScenarioControl(scenario_id=s_a.id, control_id=edr.id),
            ScenarioControl(scenario_id=s_a.id, control_id=linked.id),
            ScenarioControl(scenario_id=s_b.id, control_id=edr.id),
        ]
    )
    await db_session.commit()

    expected_a = sorted(str(c.id) for c in ow if c is not linked)
    expected_b = sorted(str(c.id) for c in ow)
    assert len(expected_a) == 3 and len(expected_b) == 4

    agg = await _create(db_session, seed_organization, seed_user, [s_a.id, s_b.id])
    assert agg.inherited_control_ids_per_scenario == {
        str(s_a.id): expected_a,
        str(s_b.id): expected_b,
    }
    for ids in (expected_a, expected_b):
        assert len(set(ids)) == len(ids)
    assert agg.control_ids_used is not None
    assert set(agg.control_ids_used) == {str(edr.id), str(linked.id)}  # plain ids, no variants

    single = await _create(db_session, seed_organization, seed_user, [s_b.id])
    assert single.inherited_control_ids_per_scenario == {str(s_b.id): expected_b}
    assert single.control_ids_used == [str(edr.id)]
