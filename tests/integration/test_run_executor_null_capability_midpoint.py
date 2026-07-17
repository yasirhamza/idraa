"""Issue #209 regression — UAT repro: a cleared ELAPSED_TIME capability runs.

Live UAT 2026-05-21: a SIEM control's ``lec_det_monitoring`` (ELAPSED_TIME)
capability was cleared via the × modal (capability_value=NULL). The next run
hard-failed with::

    ValueError: Control 'SIEM' is missing the capability value for sub-function
    'lec_det_monitoring' (capability_value=NULL)...

The UI modal promised a graceful midpoint fallback, and fair_cam already
implements it (``_null_safe_default`` = 0.5 * coverage * reliability, the
documented opeff(median)=0.5 anchor). The stale v3 executor gate blocked the
path. This test reproduces the repro and asserts the run COMPLETES at the
midpoint (the #209 fix), and that the per-assignment breakdown records the
NULL fallback with opeff = 0.5 * 0.8 * 0.8 = 0.32.

#130 FULL MIGRATION re-pin: this control has a SINGLE Detection sub-function
(``lec_det_monitoring``). The engine now composes per Boolean group, and the
LEC_DETECTION group has NO standalone node multiplier — Detection only GATES
the Response group (Detection->Response AND-pair); with no Response sub-function
present the gate contributes nothing. So a Detection-ONLY control now leaves ALE
UNCHANGED (residual == base) and its per-control multipliers stay at identity.
Pre-#130 the coarse `ControlDomain.LOSS_EVENT` bucket wrongly credited it with a
vulnerability reduction (vulnerability_multiplier 0.712) — the mis-routing #130
removes. The #209 completion guarantee and the NULL-fallback breakdown opeff are
the durable parts of this regression and are unchanged.
"""

from __future__ import annotations

import hashlib
import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.control import Control
from idraa.models.control_function_assignment import ControlFunctionAssignment
from idraa.models.enums import (
    ControlType,
    EntityStatus,
    FairCamSubFunction,
    ScenarioType,
    ThreatCategory,
)
from idraa.models.organization import Organization
from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType
from idraa.models.scenario import Scenario
from idraa.models.user import User
from idraa.services.run_executor import execute_run


async def _seed_siem_with_cleared_monitoring(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    created_by: uuid.UUID,
) -> Control:
    """SIEM-like control with one ELAPSED_TIME assignment, capability cleared."""
    ctrl = Control(
        id=uuid.uuid4(),
        organization_id=org_id,
        name="Security Information and Event Management (SIEM)",
        type=ControlType.TECHNICAL,
        annual_cost=Decimal("0"),
        nist_csf_functions=[],
        iso_27001_domains=[],
        compliance_mappings={},
        skill_requirements=[],
        technology_dependencies=[],
        applicable_industries=[],
        applicable_org_sizes=[],
        status=EntityStatus.ACTIVE,
        version="1.0",
        created_by=created_by,
    )
    db.add(ctrl)
    await db.flush()

    asgn = ControlFunctionAssignment(
        control_id=ctrl.id,
        organization_id=org_id,
        # ELAPSED_TIME-unit sub-function (the UAT repro's lec_det_monitoring).
        sub_function=FairCamSubFunction.LEC_DET_MONITORING,
        capability_value=None,  # cleared via the × modal — (re-entry needed)
        coverage=0.8,
        reliability=0.8,
    )
    db.add(asgn)
    await db.flush()
    return ctrl


async def _seed_scenario(db: AsyncSession, *, org_id: uuid.UUID, created_by: uuid.UUID) -> Scenario:
    scenario = Scenario(
        organization_id=org_id,
        name="Ransomware against ICS",
        scenario_type=ScenarioType.CUSTOM,
        threat_category=ThreatCategory.RANSOMWARE,
        threat_event_frequency={"distribution": "pert", "low": 1.0, "mode": 2.0, "high": 4.0},
        vulnerability={"distribution": "pert", "low": 0.3, "mode": 0.5, "high": 0.7},
        primary_loss={
            "distribution": "pert",
            "low": 100_000.0,
            "mode": 500_000.0,
            "high": 1_000_000.0,
        },
        secondary_loss=None,
        status=EntityStatus.ACTIVE,
        created_by=created_by,
    )
    db.add(scenario)
    await db.flush()
    return scenario


@pytest.mark.asyncio
async def test_cleared_elapsed_time_capability_completes_with_midpoint_reduction(
    db_session: AsyncSession,
    seed_organization: Organization,
    seed_user: User,
    wire_executor_to_test_db: None,
) -> None:
    org_id = seed_organization.id

    ctrl = await _seed_siem_with_cleared_monitoring(
        db_session, org_id=org_id, created_by=seed_user.id
    )
    scenario = await _seed_scenario(db_session, org_id=org_id, created_by=seed_user.id)

    run = RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=org_id,
        scenario_id=scenario.id,
        mc_iterations=5000,
        inputs_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        controls_snapshot=[],
        control_ids_used=[str(ctrl.id)],
        status=RunStatus.QUEUED,
        run_type=RunType.SINGLE,
        created_by=seed_user.id,
    )
    db_session.add(run)
    await db_session.commit()

    await execute_run(run.id)

    await db_session.refresh(run)

    # COMPLETES (not FAILED) — the stale gate is gone.
    assert run.status == RunStatus.COMPLETED, run.error_message
    assert run.simulation_results is not None

    results: dict[str, Any] = run.simulation_results

    # #209 durable guarantee: the run COMPLETED at the midpoint instead of
    # hard-failing on the NULL capability. #130: a Detection-ONLY control has no
    # standalone node multiplier, so residual ALE == base ALE (no reduction).
    base_ale = float(results["base_risk"]["annualized_loss_expectancy"])
    residual_ale = float(results["residual_risk"]["annualized_loss_expectancy"])
    assert base_ale > 0.0
    assert residual_ale == pytest.approx(base_ale, rel=1e-9), (
        f"Detection-only control should leave ALE unchanged under per-group "
        f"composition (no standalone Detection node); base={base_ale} "
        f"residual={residual_ale}"
    )

    # The breakdown for the cleared assignment must still record the NULL
    # fallback with opeff = 0.5 * 0.8 * 0.8 = 0.32 (the #209 / #129 contract).
    # The per-control multipliers stay at identity (Detection has no standalone
    # node — #130 full migration; the pre-#130 vulnerability_multiplier=0.712
    # mis-routing is gone).
    ctrl_adj = next(a for a in results["control_adjustments"] if a["control_id"] == str(ctrl.id))
    null_entries = [b for b in ctrl_adj["breakdown"] if b.get("capability_was_null") is True]
    assert null_entries, ctrl_adj["breakdown"]
    assert null_entries[0]["opeff"] == pytest.approx(0.32, abs=1e-9)
    assert ctrl_adj["vulnerability_multiplier"] == pytest.approx(1.0, abs=1e-9)
