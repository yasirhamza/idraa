"""RiskAnalysisRun model: field defaults, enum values, JSON round-trip."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.organization import Organization
from idraa.models.risk_analysis_run import (
    RiskAnalysisRun,
    RunStatus,
    RunType,
)
from idraa.models.scenario import Scenario
from idraa.models.user import User


@pytest.mark.asyncio
async def test_run_default_status_is_queued(
    db_session: AsyncSession,
    seed_scenario_with_no_controls: Scenario,
    seed_user: User,
    seed_organization: Organization,
) -> None:
    run = RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=seed_organization.id,
        scenario_id=seed_scenario_with_no_controls.id,
        mc_iterations=1000,
        inputs_hash="a" * 64,
        controls_snapshot=[],
        control_ids_used=[],
        created_by=seed_user.id,
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)
    assert run.status == RunStatus.QUEUED
    assert run.run_type == RunType.SINGLE


@pytest.mark.asyncio
async def test_run_simulation_results_json_roundtrip(
    db_session: AsyncSession,
    seed_scenario_with_no_controls: Scenario,
    seed_user: User,
    seed_organization: Organization,
) -> None:
    payload = {
        "base_risk": {"annualized_loss_expectancy": 1234567.89, "var_95": 2.0e6},
        "residual_risk": {"annualized_loss_expectancy": 567890.12, "var_95": 1.0e6},
        "loss_exceedance_curve": [{"loss": 1e5, "probability": 0.95}],
    }
    run = RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=seed_organization.id,
        scenario_id=seed_scenario_with_no_controls.id,
        mc_iterations=1000,
        inputs_hash="b" * 64,
        controls_snapshot=[],
        control_ids_used=[],
        simulation_results=payload,
        status=RunStatus.COMPLETED,
        created_by=seed_user.id,
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)
    assert run.simulation_results == payload


@pytest.mark.asyncio
async def test_run_controls_snapshot_json_roundtrip(
    db_session: AsyncSession,
    seed_scenario_with_no_controls: Scenario,
    seed_user: User,
    seed_organization: Organization,
) -> None:
    snapshot = [
        {
            "control_id": str(uuid.uuid4()),
            "name": "Firewall",
            "control_strength": 0.75,
            "control_reliability": 0.9,
            "control_coverage": 0.8,
            "domain": "VM",
            "function": "PROTECT",
            "type": "PREVENTIVE",
        },
    ]
    run = RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=seed_organization.id,
        scenario_id=seed_scenario_with_no_controls.id,
        mc_iterations=1000,
        inputs_hash="c" * 64,
        controls_snapshot=snapshot,
        control_ids_used=[],
        created_by=seed_user.id,
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)
    assert run.controls_snapshot == snapshot


def test_run_status_enum_values() -> None:
    assert {s.value for s in RunStatus} == {
        "queued",
        "running",
        "completed",
        "failed",
        "cancelled",
    }


def test_run_type_enum_values() -> None:
    assert {t.value for t in RunType} == {"single", "aggregate"}
