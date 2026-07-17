"""Non-active controls contribute nothing to the FAIR-CAM composition (#395).

Methodology-critical: a control at any stage other than ACTIVE must not
reduce modeled risk. Verified by comparing the run's stored control_ids_used
and the resulting ALE between an active and a demoted control.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.enums import ControlImplementationStage
from idraa.models.organization import Organization
from idraa.models.risk_analysis_run import RunStatus
from idraa.models.user import User
from idraa.services.runs import RunService

# Fixed seed so the three runs in the ALE test are bit-for-bit comparable
# (the gate is the ONLY thing that changes between them).
_SEED = 12345
# < _SYNC_THRESHOLD (1000) so create_and_dispatch executes the run inline and
# returns a COMPLETED run with simulation_results populated.
_ITERS = 200


@pytest_asyncio.fixture
async def scenario_with_one_control(
    db_session: AsyncSession,
    seed_scenario_factory: Any,
    seed_control_factory: Any,
) -> tuple[Any, Any]:
    """A scenario with exactly ONE mitigating control (active by default).

    One control (not the conftest 2-control fixture) so the ALE delta is
    attributable to a single gate flip.
    """
    from idraa.models.scenario_control import ScenarioControl

    scenario = await seed_scenario_factory(name="stage-gate-scenario")
    control = await seed_control_factory(name="Gate-test control")
    db_session.add(ScenarioControl(scenario_id=scenario.id, control_id=control.id))
    await db_session.commit()
    await db_session.refresh(scenario)
    return scenario, control


async def _run_and_get_ale(
    db_session: AsyncSession,
    organization_id: uuid.UUID,
    created_by: uuid.UUID,
    scenario: Any,
) -> float:
    """Execute one SINGLE run end-to-end (inline) and return its modeled ALE.

    The modeled (post-composition) ALE is ``residual_risk.annualized_loss_expectancy``.
    When a control composes it sits below ``base_risk``; when no control
    composes (detached OR gated out) ``residual_risk`` == ``base_risk``.
    """
    service = RunService(db_session)
    run = await service.create_and_dispatch(
        organization_id=organization_id,
        scenario_ids=[scenario.id],
        mc_iterations_override=_ITERS,
        created_by=created_by,
        background_tasks=BackgroundTasks(),
        random_seed=_SEED,
    )
    await db_session.refresh(run)
    assert run.status == RunStatus.COMPLETED, f"run did not complete: {run.error_message!r}"
    assert run.simulation_results is not None
    return float(run.simulation_results["residual_risk"]["annualized_loss_expectancy"])


@pytest.mark.asyncio
async def test_demoted_control_excluded_from_control_ids_used(
    db_session: AsyncSession,
    seed_organization: Organization,
    seed_user: User,
    scenario_with_one_control: tuple[Any, Any],
    wire_executor_to_test_db: None,
) -> None:
    scenario, control = scenario_with_one_control
    service = RunService(db_session)

    # Active control → present in control_ids_used.
    run_active = await service.create_and_dispatch(
        organization_id=seed_organization.id,
        scenario_ids=[scenario.id],
        mc_iterations_override=_ITERS,
        created_by=seed_user.id,
        background_tasks=BackgroundTasks(),
        random_seed=_SEED,
    )
    assert str(control.id) in run_active.control_ids_used

    # Demote → excluded from control_ids_used.
    control.implementation_stage = ControlImplementationStage.PLANNED
    await db_session.flush()
    run_planned = await service.create_and_dispatch(
        organization_id=seed_organization.id,
        scenario_ids=[scenario.id],
        mc_iterations_override=_ITERS,
        created_by=seed_user.id,
        background_tasks=BackgroundTasks(),
        random_seed=_SEED,
    )
    assert str(control.id) not in run_planned.control_ids_used


@pytest.mark.asyncio
async def test_aggregate_demoted_control_excluded(
    db_session: AsyncSession,
    seed_organization: Organization,
    seed_user: User,
    seed_scenario_factory: Any,
    seed_control_factory: Any,
    wire_executor_to_test_db: None,
) -> None:
    """AGGREGATE path of the #395 gate: a demoted control is excluded from BOTH
    the deduplicated ``control_ids_used`` union AND its scenario's frozen entry
    in ``aggregate_control_ids_per_scenario``; an active control is present in
    both.

    Covers the gap left by the SINGLE-only tests above: the AGGREGATE branch
    gates control gathering twice — once in the ``per_scenario_dict``
    comprehension and once in the dedup-union loop — and neither was exercised.
    """
    from idraa.models.scenario_control import ScenarioControl

    org_id = seed_organization.id

    scenario_a = await seed_scenario_factory(name="agg-gate-scenario-a")
    scenario_b = await seed_scenario_factory(name="agg-gate-scenario-b")
    active_control = await seed_control_factory(name="Agg-gate active control")
    demoted_control = await seed_control_factory(name="Agg-gate demoted control")

    # Scenario A → active control (ACTIVE by default).
    # Scenario B → control we then demote to PLANNED.
    db_session.add_all(
        [
            ScenarioControl(scenario_id=scenario_a.id, control_id=active_control.id),
            ScenarioControl(scenario_id=scenario_b.id, control_id=demoted_control.id),
        ]
    )
    demoted_control.implementation_stage = ControlImplementationStage.PLANNED
    await db_session.commit()

    service = RunService(db_session)
    run = await service.create_and_dispatch(
        organization_id=org_id,
        scenario_ids=[scenario_a.id, scenario_b.id],
        mc_iterations_override=_ITERS,
        created_by=seed_user.id,
        background_tasks=BackgroundTasks(),
        random_seed=_SEED,
    )
    await db_session.refresh(run)

    per_scenario = run.aggregate_control_ids_per_scenario
    assert per_scenario is not None

    # Demoted control: absent from the union AND from scenario B's frozen set.
    assert str(demoted_control.id) not in run.control_ids_used
    assert str(demoted_control.id) not in per_scenario[str(scenario_b.id)]

    # Active control: present in the union AND in scenario A's frozen set.
    assert str(active_control.id) in run.control_ids_used
    assert str(active_control.id) in per_scenario[str(scenario_a.id)]


@pytest.mark.asyncio
async def test_demoted_control_yields_inherent_risk_ale(
    db_session: AsyncSession,
    seed_organization: Organization,
    seed_user: User,
    scenario_with_one_control: tuple[Any, Any],
    wire_executor_to_test_db: None,
) -> None:
    # REQUIRED (plan-gate I2). The id-list check above is a proxy; THIS proves
    # the math: a demoted control must produce the same ALE as no control at
    # all. Execute three runs end to end and compare ALE side-by-side:
    #   ale_active   — control ACTIVE   (controls reduce risk)
    #   ale_planned  — control PLANNED  (gated out)
    #   ale_none     — control detached (true inherent risk)
    # Assert ale_planned == ale_none, and ale_active < ale_none (control helps).
    scenario, control = scenario_with_one_control
    org_id = seed_organization.id
    user_id = seed_user.id

    ale_active = await _run_and_get_ale(db_session, org_id, user_id, scenario)

    control.implementation_stage = ControlImplementationStage.PLANNED
    await db_session.flush()
    ale_planned = await _run_and_get_ale(db_session, org_id, user_id, scenario)

    # Detach the control entirely → ground-truth inherent risk.
    scenario.mitigating_controls.clear()
    # Stage is irrelevant once detached, but reset it so the comparison run is
    # a clean "no control" baseline regardless of the gate.
    control.implementation_stage = ControlImplementationStage.ACTIVE
    await db_session.flush()
    ale_none = await _run_and_get_ale(db_session, org_id, user_id, scenario)

    # gated == not-attached (the methodology-critical assertion):
    assert ale_planned == pytest.approx(ale_none, rel=1e-9)
    # active control helps:
    assert ale_active < ale_none

    # Surface the numbers for the verification-reporting convention.
    print(
        f"\nALE comparison (seed={_SEED}, iters={_ITERS}):\n"
        f"  ale_active  = {ale_active:,.2f}\n"
        f"  ale_planned = {ale_planned:,.2f}\n"
        f"  ale_none    = {ale_none:,.2f}\n"
    )
