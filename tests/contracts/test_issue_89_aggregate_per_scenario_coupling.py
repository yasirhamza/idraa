"""Cross-layer regression test for issue #89.

Exercises all 5 layers in concert: form -> route -> service -> executor -> fair_cam.
Scenario A's per-scenario control_adjustments must NOT include scenario B's controls.

Living under tests/contracts/ per CLAUDE.md "Data contract enforcement" policy:
the per-scenario coupling is a data contract that crosses the v3<->fair_cam
boundary.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.risk_analysis_run import RunStatus, RunType
from idraa.services.runs import RunService


@pytest.mark.asyncio
async def test_issue_89_aggregate_applies_only_scenarios_own_controls(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_scenario_factory: Any,
    seed_control_factory: Any,
    seed_user: Any,
    wire_executor_to_test_db: Any,
) -> None:
    """3 scenarios with disjoint+overlapping+empty control sets.

    A: [siem, mfa]
    B: [training, siem]   (overlap with A on siem)
    C: []                  (empty)

    Each per-scenario block in simulation_results.per_scenario MUST reference
    ONLY that scenario's configured controls. The bug this regresses: prior
    AGGREGATE runs applied the UNION ([siem, mfa, training]) to every
    scenario, modeling phantom risk reductions for controls that don't apply.
    """
    from idraa.models.scenario_control import ScenarioControl

    siem = await seed_control_factory(name="SIEM")
    mfa = await seed_control_factory(name="MFA")
    training = await seed_control_factory(name="Security Awareness Training")
    s_a = await seed_scenario_factory(name="API Key Leak")
    s_b = await seed_scenario_factory(name="Phishing")
    s_c = await seed_scenario_factory(name="Empty Scenario")
    db_session.add_all(
        [
            ScenarioControl(scenario_id=s_a.id, control_id=siem.id),
            ScenarioControl(scenario_id=s_a.id, control_id=mfa.id),
            ScenarioControl(scenario_id=s_b.id, control_id=training.id),
            ScenarioControl(scenario_id=s_b.id, control_id=siem.id),
        ]
    )
    await db_session.commit()

    service = RunService(db_session)
    bg = BackgroundTasks()
    run = await service.create_and_dispatch(
        organization_id=seed_organization.id,
        scenario_ids=[s_a.id, s_b.id, s_c.id],
        mc_iterations_override=200,
        created_by=seed_user.id,
        background_tasks=bg,
    )
    await db_session.refresh(run)
    assert run.status == RunStatus.COMPLETED, run.error_message
    assert run.run_type == RunType.AGGREGATE

    # ---- Layer assertions ----

    # 1. Service freezes the correct per-scenario dict (order-independent — the
    # ORM relationship doesn't guarantee insertion-order iteration on SQLite).
    per_scenario_dict = run.aggregate_control_ids_per_scenario
    assert per_scenario_dict is not None
    assert set(per_scenario_dict.keys()) == {str(s_a.id), str(s_b.id), str(s_c.id)}
    assert set(per_scenario_dict[str(s_a.id)]) == {str(siem.id), str(mfa.id)}
    assert set(per_scenario_dict[str(s_b.id)]) == {str(training.id), str(siem.id)}
    assert per_scenario_dict[str(s_c.id)] == []
    # 2. control_ids_used is the deduplicated union (universe).
    assert set(run.control_ids_used) == {str(siem.id), str(mfa.id), str(training.id)}
    assert len(run.control_ids_used) == 3, "dedup failed"

    # 3. Executor passed per-scenario dict to fair_cam (proven by per-scenario
    # control_adjustments shape below).
    results = run.simulation_results
    assert results is not None
    per_scenario = results["per_scenario"]
    by_sid = {ps["scenario_id"]: ps for ps in per_scenario}

    # Scenario A: control_adjustments references ONLY siem and mfa, NOT training.
    a_adj_ids = {adj["control_id"] for adj in by_sid[str(s_a.id)]["control_adjustments"]}
    assert a_adj_ids == {str(siem.id), str(mfa.id)}, (
        f"scenario A leaked phantom controls: {a_adj_ids ^ {str(siem.id), str(mfa.id)}}"
    )
    # Scenario B: ONLY training and siem, NOT mfa.
    b_adj_ids = {adj["control_id"] for adj in by_sid[str(s_b.id)]["control_adjustments"]}
    assert b_adj_ids == {str(training.id), str(siem.id)}, (
        f"scenario B leaked phantom controls: {b_adj_ids ^ {str(training.id), str(siem.id)}}"
    )
    # Scenario C: no adjustments at all.
    assert by_sid[str(s_c.id)]["control_adjustments"] == []
    # And residual == base for C (no controls applied).
    c_base_ale = by_sid[str(s_c.id)]["base_risk"]["annualized_loss_expectancy"]
    c_resid_ale = by_sid[str(s_c.id)]["residual_risk"]["annualized_loss_expectancy"]
    assert c_resid_ale == pytest.approx(c_base_ale, rel=1e-9)
