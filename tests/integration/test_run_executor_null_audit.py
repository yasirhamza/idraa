"""Issue #209: a cleared (NULL) capability_value no longer fails the run.

Prior contract (T11 PR κ / paranoid-review fix S3): NULL capability_value made
``_v3_to_fair_cam_control`` raise ValueError; ``execute_run`` wrote a
``run.null_capability`` audit row and flipped the run to FAILED.

New contract (issue #209): the stale executor NULL-reject gate is deleted.
fair_cam already handles NULL via its documented opeff(median)=0.5 midpoint
anchor (``_null_safe_default`` = 0.5 * coverage * reliability). The run now
COMPLETES at the midpoint, and the persisted snapshot breakdown records
``capability_was_null: True`` for the cleared assignment.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.audit_log import AuditLog
from idraa.models.control import Control
from idraa.models.control_function_assignment import ControlFunctionAssignment
from idraa.models.enums import (
    ControlType,
    EntityStatus,
    FairCamSubFunction,
)
from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType
from idraa.services.run_executor import execute_run


async def _seed_null_capability_control(
    db: AsyncSession,
    org_id: uuid.UUID,
    created_by: uuid.UUID,
) -> Control:
    """Seed a Control with a single assignment whose capability_value is NULL."""
    ctrl = Control(
        id=uuid.uuid4(),
        organization_id=org_id,
        name="Null-Capability Control",
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
    await db.flush()  # populate ctrl.id

    asgn = ControlFunctionAssignment(
        control_id=ctrl.id,
        organization_id=org_id,
        sub_function=FairCamSubFunction.VMC_PREV_REDUCE_VARIANCE_PROB,
        capability_value=None,  # intentionally NULL (cleared via the × modal)
        coverage=0.8,
        reliability=0.85,
    )
    db.add(asgn)
    await db.flush()
    return ctrl


async def _seed_run_for_control(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    scenario_id: uuid.UUID,
    control_id: uuid.UUID,
    created_by: uuid.UUID,
) -> RiskAnalysisRun:
    """Seed a QUEUED RiskAnalysisRun targeting a single control."""
    run = RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=org_id,
        scenario_id=scenario_id,
        mc_iterations=200,
        inputs_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        controls_snapshot=[],
        control_ids_used=[str(control_id)],
        status=RunStatus.QUEUED,
        run_type=RunType.SINGLE,
        created_by=created_by,
    )
    db.add(run)
    await db.flush()
    return run


@pytest.mark.asyncio
async def test_execute_run_null_capability_completes_at_midpoint(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_user: Any,
    seed_scenario_factory: Callable[..., Awaitable[Any]],
    wire_executor_to_test_db: None,
) -> None:
    """A run with a cleared (NULL) capability_value COMPLETES at the midpoint.

    Setup: scenario with one control whose capability_value is NULL.
    Expected (issue #209):
      - execute_run completes the run (status == COMPLETED), not FAILED
      - no run.null_capability audit row is written (the reject gate is gone)
      - the persisted snapshot breakdown records capability_was_null: True for
        the cleared assignment
    """
    scenario = await seed_scenario_factory(
        name="null-cap-midpoint-test",
    )

    ctrl = await _seed_null_capability_control(
        db_session,
        org_id=seed_organization.id,
        created_by=seed_user.id,
    )

    run = await _seed_run_for_control(
        db_session,
        org_id=seed_organization.id,
        scenario_id=scenario.id,
        control_id=ctrl.id,
        created_by=seed_user.id,
    )
    run_id = run.id
    await db_session.commit()

    await execute_run(run_id)

    # execute_run opens its own session; force a fresh read of the run row.
    stmt_run = (
        select(RiskAnalysisRun)
        .where(RiskAnalysisRun.id == run_id)
        .execution_options(populate_existing=True)
    )
    refreshed_run = (await db_session.execute(stmt_run)).scalar_one_or_none()

    # 1. Run must COMPLETE (no longer FAILED) — the stale gate is gone.
    assert refreshed_run is not None
    assert refreshed_run.status == RunStatus.COMPLETED, refreshed_run.error_message

    # 2. No run.null_capability audit row should exist any more.
    stmt_audit = select(AuditLog).where(
        AuditLog.entity_id == run_id,
        AuditLog.action == "run.null_capability",
    )
    audit_rows = (await db_session.execute(stmt_audit)).scalars().all()
    assert len(audit_rows) == 0, (
        f"Expected no 'run.null_capability' audit rows for run {run_id}; "
        f"found {len(audit_rows)} (the reject gate should be removed)"
    )

    # 3. The snapshot breakdown records capability_was_null: True for the
    #    cleared assignment.
    assert refreshed_run.simulation_results is not None
    adjustments = refreshed_run.simulation_results["control_adjustments"]
    ctrl_adj = next(a for a in adjustments if a["control_id"] == str(ctrl.id))
    null_entries = [b for b in ctrl_adj["breakdown"] if b.get("capability_was_null") is True]
    assert null_entries, (
        f"Expected at least one breakdown entry with capability_was_null=True "
        f"for control {ctrl.id}; breakdown={ctrl_adj['breakdown']}"
    )
