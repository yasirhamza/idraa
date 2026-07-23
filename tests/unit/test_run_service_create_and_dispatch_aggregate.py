"""Tests for RunService.create_and_dispatch generalization (PR xi F5)."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.risk_analysis_run import RunType
from idraa.services.runs import RunService


@pytest.mark.asyncio
async def test_single_scenario_creates_single_run(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_scenario_factory: Any,
    seed_user: Any,
    wire_executor_to_test_db: Any,
) -> None:
    """1 scenario -> SINGLE row with scenario_id set."""
    s = await seed_scenario_factory(name="single_test")
    service = RunService(db_session)
    bg = BackgroundTasks()
    run = await service.create_and_dispatch(
        organization_id=seed_organization.id,
        scenario_ids=[s.id],
        mc_iterations_override=10000,
        created_by=seed_user.id,
        background_tasks=bg,
    )
    assert run.run_type == RunType.SINGLE
    assert run.scenario_id == s.id
    assert run.aggregate_scenario_ids is None
    # Issue #89: SINGLE leaves aggregate_control_ids_per_scenario NULL.
    assert run.aggregate_control_ids_per_scenario is None


@pytest.mark.asyncio
async def test_multi_scenario_creates_aggregate_run_with_sorted_ids(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_scenario_factory: Any,
    seed_user: Any,
    wire_executor_to_test_db: Any,
) -> None:
    """2+ scenarios -> AGGREGATE row with sorted aggregate_scenario_ids."""
    s1 = await seed_scenario_factory(name="agg_1")
    s2 = await seed_scenario_factory(name="agg_2")
    service = RunService(db_session)
    bg = BackgroundTasks()
    # Submit DESCENDING to verify sorting
    submitted = sorted([s1.id, s2.id], reverse=True)
    run = await service.create_and_dispatch(
        organization_id=seed_organization.id,
        scenario_ids=submitted,
        mc_iterations_override=10000,
        created_by=seed_user.id,
        background_tasks=bg,
    )
    assert run.run_type == RunType.AGGREGATE
    assert run.scenario_id is None
    assert run.aggregate_scenario_ids == sorted([str(s1.id), str(s2.id)])


@pytest.mark.asyncio
async def test_aggregate_control_ids_used_is_dedup_union(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_scenario_factory: Any,
    seed_control_factory: Any,
    seed_user: Any,
    wire_executor_to_test_db: Any,
) -> None:
    """Issue #89: control_ids_used is the deduplicated union of scenarios' mitigating_controls."""
    c1 = await seed_control_factory(name="ctrl_1")
    c2 = await seed_control_factory(name="ctrl_2")
    s1 = await seed_scenario_factory(name="agg_1")
    s2 = await seed_scenario_factory(name="agg_2")
    # Attach c1 to s1, c2 to s2 (so UNION = [c1, c2])
    from idraa.models.scenario_control import ScenarioControl

    db_session.add(ScenarioControl(scenario_id=s1.id, control_id=c1.id))
    db_session.add(ScenarioControl(scenario_id=s2.id, control_id=c2.id))
    await db_session.commit()
    service = RunService(db_session)
    bg = BackgroundTasks()
    run = await service.create_and_dispatch(
        organization_id=seed_organization.id,
        scenario_ids=[s1.id, s2.id],
        mc_iterations_override=10000,
        created_by=seed_user.id,
        background_tasks=bg,
    )
    # Both controls in the UNION should land in control_ids_used (universe).
    used = set(run.control_ids_used)
    assert str(c1.id) in used
    assert str(c2.id) in used


@pytest.mark.asyncio
async def test_aggregate_freezes_per_scenario_dict_with_disjoint_sets(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_scenario_factory: Any,
    seed_control_factory: Any,
    seed_user: Any,
    wire_executor_to_test_db: Any,
) -> None:
    """Issue #89 core: AGGREGATE freezes per-scenario dict reflecting each scenario's controls."""
    from idraa.models.scenario_control import ScenarioControl

    c1 = await seed_control_factory(name="ctrl_a_only")
    c2 = await seed_control_factory(name="ctrl_b_only")
    s1 = await seed_scenario_factory(name="agg_s1")
    s2 = await seed_scenario_factory(name="agg_s2")
    db_session.add(ScenarioControl(scenario_id=s1.id, control_id=c1.id))
    db_session.add(ScenarioControl(scenario_id=s2.id, control_id=c2.id))
    await db_session.commit()
    service = RunService(db_session)
    bg = BackgroundTasks()
    run = await service.create_and_dispatch(
        organization_id=seed_organization.id,
        scenario_ids=[s1.id, s2.id],
        mc_iterations_override=10000,
        created_by=seed_user.id,
        background_tasks=bg,
    )
    assert run.aggregate_control_ids_per_scenario == {
        str(s1.id): [str(c1.id)],
        str(s2.id): [str(c2.id)],
    }


@pytest.mark.asyncio
async def test_aggregate_with_one_empty_scenario(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_scenario_factory: Any,
    seed_control_factory: Any,
    seed_user: Any,
    wire_executor_to_test_db: Any,
) -> None:
    """Issue #89: scenario with no mitigating_controls -> empty list in per-scenario dict."""
    from idraa.models.scenario_control import ScenarioControl

    c1 = await seed_control_factory(name="ctrl_only_for_s1")
    s1 = await seed_scenario_factory(name="agg_s1_has_ctrl")
    s2 = await seed_scenario_factory(name="agg_s2_empty")
    db_session.add(ScenarioControl(scenario_id=s1.id, control_id=c1.id))
    await db_session.commit()
    service = RunService(db_session)
    bg = BackgroundTasks()
    run = await service.create_and_dispatch(
        organization_id=seed_organization.id,
        scenario_ids=[s1.id, s2.id],
        mc_iterations_override=10000,
        created_by=seed_user.id,
        background_tasks=bg,
    )
    per_scenario = run.aggregate_control_ids_per_scenario
    assert per_scenario is not None
    assert per_scenario[str(s1.id)] == [str(c1.id)]
    assert per_scenario[str(s2.id)] == []


@pytest.mark.asyncio
async def test_aggregate_with_both_empty_scenarios(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_scenario_factory: Any,
    seed_user: Any,
    wire_executor_to_test_db: Any,
) -> None:
    """Issue #89 M6 (plan-gate): both scenarios empty -> empty universe + per-scenario dict."""
    s1 = await seed_scenario_factory(name="agg_empty_1")
    s2 = await seed_scenario_factory(name="agg_empty_2")
    service = RunService(db_session)
    bg = BackgroundTasks()
    run = await service.create_and_dispatch(
        organization_id=seed_organization.id,
        scenario_ids=[s1.id, s2.id],
        mc_iterations_override=10000,
        created_by=seed_user.id,
        background_tasks=bg,
    )
    assert run.control_ids_used == []
    assert run.aggregate_control_ids_per_scenario == {
        str(s1.id): [],
        str(s2.id): [],
    }


@pytest.mark.asyncio
async def test_aggregate_rejects_cross_org_control_via_poisoned_scenario_controls(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_scenario_factory: Any,
    seed_user: Any,
    seed_organization_factory: Any,
    wire_executor_to_test_db: Any,
) -> None:
    """Plan-gate sec-2 (security-auditor finding 2): defense-in-depth against
    cross-org control_id leaking through scenario_controls into a run.

    Constructs the attack: an in-org scenario has scenario_controls row
    referencing a control owned by a different org (direct DB insert; bypasses
    the ScenarioRepo write-side org check). RunService derives control_ids from
    scenario.mitigating_controls (unfiltered relationship), so the cross-org id
    DOES land on run.control_ids_used and aggregate_control_ids_per_scenario.
    At execute_run, ControlRepo.fetch_by_ids_for_org filters by org, so the
    loaded universe is SMALLER than the frozen sets — the M5 fail-loud check
    fires and the run is FAILED with an audit row. This pins the layered
    defense.

    With the previous control_ids_override path, a similar attack was caught
    pre-create via ControlNotFoundForRunError at the override-validation step.
    The deletion of that test is paired with THIS test (different attack vector,
    different defense layer).
    """
    from datetime import UTC, datetime

    from sqlalchemy import select

    from idraa.models.audit_log import AuditLog
    from idraa.models.control import Control
    from idraa.models.control_function_assignment import ControlFunctionAssignment
    from idraa.models.enums import (
        ControlType,
        EntityStatus,
        FairCamSubFunction,
    )
    from idraa.models.risk_analysis_run import RunStatus
    from idraa.models.scenario_control import ScenarioControl

    # Cross-org control belongs to a DIFFERENT org.
    other_org = await seed_organization_factory(name="other-org-poison-test")
    foreign_ctrl = Control(
        id=uuid.uuid4(),
        organization_id=other_org.id,
        name="Cross-org Control",
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
    )
    db_session.add(foreign_ctrl)
    await db_session.flush()
    db_session.add(
        ControlFunctionAssignment(
            control_id=foreign_ctrl.id,
            organization_id=other_org.id,
            sub_function=FairCamSubFunction.VMC_PREV_REDUCE_VARIANCE_PROB,
            capability_value=0.5,
            coverage=0.5,
            reliability=0.5,
            confirmed_by_user_at=datetime.now(UTC),
        )
    )

    # In-org scenarios; poison s1 with the cross-org control via direct DB insert.
    s1 = await seed_scenario_factory(name="poisoned_scenario")
    s2 = await seed_scenario_factory(name="clean_scenario")
    db_session.add(ScenarioControl(scenario_id=s1.id, control_id=foreign_ctrl.id))
    await db_session.commit()

    service = RunService(db_session)
    bg = BackgroundTasks()
    # The run is created (the cross-field @validates check passes because
    # control_ids_used and aggregate_control_ids_per_scenario are derived from
    # the SAME poisoned scenario.mitigating_controls).
    run = await service.create_and_dispatch(
        organization_id=seed_organization.id,
        scenario_ids=[s1.id, s2.id],
        mc_iterations_override=200,
        created_by=seed_user.id,
        background_tasks=bg,
    )
    await db_session.refresh(run)
    # Executor's M5 check catches the cross-org id (ControlRepo filters cross-org;
    # loaded_universe is missing the foreign id).
    assert run.status == RunStatus.FAILED, run.error_message
    assert run.error_message is not None
    # #82: the user-facing error_message is genericized (no internal detail
    # leak). The stale/race forensic evidence lives in the audit row below.
    from idraa.services.run_executor import _RUN_FAILURE_MESSAGE

    assert run.error_message == _RUN_FAILURE_MESSAGE
    # Audit row pins the forensic trail for the bad-data event.
    rs = await db_session.execute(
        select(AuditLog).where(
            AuditLog.entity_id == run.id,
            AuditLog.action == "run.stale_per_scenario_control_ids",
        )
    )
    audit = rs.scalar_one_or_none()
    assert audit is not None
    assert str(foreign_ctrl.id) in str(audit.changes)


@pytest.mark.asyncio
async def test_aggregate_audit_log_includes_per_scenario_dict(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_scenario_factory: Any,
    seed_control_factory: Any,
    seed_user: Any,
    wire_executor_to_test_db: Any,
) -> None:
    """M3 (plan-gate, security-auditor): audit_changes captures per-scenario dict for AGGREGATE."""
    from sqlalchemy import select

    from idraa.models.audit_log import AuditLog
    from idraa.models.scenario_control import ScenarioControl

    c1 = await seed_control_factory(name="ctrl_audit_a")
    s1 = await seed_scenario_factory(name="audit_agg_s1")
    s2 = await seed_scenario_factory(name="audit_agg_s2")
    db_session.add(ScenarioControl(scenario_id=s1.id, control_id=c1.id))
    await db_session.commit()
    service = RunService(db_session)
    bg = BackgroundTasks()
    run = await service.create_and_dispatch(
        organization_id=seed_organization.id,
        scenario_ids=[s1.id, s2.id],
        mc_iterations_override=10000,
        created_by=seed_user.id,
        background_tasks=bg,
    )
    rs = await db_session.execute(
        select(AuditLog).where(
            AuditLog.entity_type == "risk_analysis_run",
            AuditLog.entity_id == run.id,
            AuditLog.action == "risk_analysis_run.create",
        )
    )
    row = rs.scalar_one()
    assert "aggregate_control_ids_per_scenario" in row.changes
    assert row.changes["aggregate_control_ids_per_scenario"] == {
        str(s1.id): [str(c1.id)],
        str(s2.id): [],
    }


@pytest.mark.asyncio
async def test_rejects_empty_scenario_ids(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_user: Any,
) -> None:
    from idraa.errors import RunValidationError

    service = RunService(db_session)
    bg = BackgroundTasks()
    with pytest.raises(RunValidationError, match="non-empty"):
        await service.create_and_dispatch(
            organization_id=seed_organization.id,
            scenario_ids=[],
            mc_iterations_override=10000,
            created_by=seed_user.id,
            background_tasks=bg,
        )


@pytest.mark.asyncio
async def test_aggregate_hash_is_order_independent(
    seed_organization: Any,
    seed_scenario_factory: Any,
) -> None:
    """Same scenarios in different orders -> same hash."""
    from idraa.services.run_inputs_hash import build_aggregate_inputs_hash

    s1 = await seed_scenario_factory(name="hash_test_1")
    s2 = await seed_scenario_factory(name="hash_test_2")
    s3 = await seed_scenario_factory(name="hash_test_3")
    h_a = build_aggregate_inputs_hash(scenarios=[s1, s2, s3], control_ids=[], mc_iterations=10000)
    h_b = build_aggregate_inputs_hash(scenarios=[s3, s1, s2], control_ids=[], mc_iterations=10000)
    assert h_a == h_b
