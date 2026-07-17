"""Issue #129 T4 — unit_display macro applied to 3 templates.

Regression: prior to T4, controls/detail.html, controls/maintenance.html, and
runs/detail.html rendered capability_value with a bare ``"%.3f"`` format call
that produced strings like "14.000" or "5000.000" regardless of the
sub-function's underlying unit. T4 replaces those format calls with the
``unit_display`` macro (introduced in T3) so:

- ELAPSED_TIME sub-functions render as ``"<X.X> days"``
- CURRENCY sub-functions render as ``"$<X,XXX> per event"``
- PROBABILITY / PERCENT_REDUCTION sub-functions keep the ``"%.3f"`` format
  (deferred per macros/unit_aware_inputs.html docstring)

Sub-functions chosen for these tests:
- LEC_DET_MONITORING       — ELAPSED_TIME
- LEC_RESP_LOSS_REDUCTION  — CURRENCY
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
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
from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType
from idraa.models.scenario import Scenario
from idraa.models.user import User


async def _make_control_single_assignment(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    sub_function: FairCamSubFunction,
    capability_value: float,
    confirmed: bool,
    name: str,
) -> Control:
    """Seed a Control with exactly ONE assignment of the given unit type.

    Single-assignment shape keeps the rendered HTML deterministic: there is
    only one capability cell to grep for in the test's response body.
    """
    ctrl = Control(
        organization_id=org_id,
        created_by=None,
        name=name,
        type=ControlType.TECHNICAL,
        status=EntityStatus.ACTIVE,
        version="1.0",
    )
    db.add(ctrl)
    await db.flush()

    now = datetime.now(UTC) if confirmed else None
    db.add(
        ControlFunctionAssignment(
            control_id=ctrl.id,
            organization_id=org_id,
            sub_function=sub_function,
            capability_value=capability_value,
            coverage=0.8,
            reliability=0.85,
            confirmed_by_user_at=now,
            measured_by=None,
            measured_at=now,
        )
    )
    await db.commit()
    await db.refresh(ctrl, attribute_names=["assignments"])
    return ctrl


@pytest_asyncio.fixture
async def control_with_elapsed_time_assignment(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> Control:
    """Confirmed control with one ELAPSED_TIME assignment (14.0 days, MTTD)."""
    _, org_id = authed_analyst
    return await _make_control_single_assignment(
        db_session,
        org_id,
        sub_function=FairCamSubFunction.LEC_DET_MONITORING,
        capability_value=14.0,
        confirmed=True,
        name="MTTD-14d monitoring control",
    )


@pytest_asyncio.fixture
async def control_with_elapsed_time_assignment_unconfirmed(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> Control:
    """UNCONFIRMED control with one ELAPSED_TIME assignment — surfaces on
    /controls/maintenance (which only lists assignments needing attention).

    Seeded under ``authed_admin``'s org so the maintenance page route
    (org-scoped) returns this assignment.
    """
    _, org_id = authed_admin
    return await _make_control_single_assignment(
        db_session,
        org_id,
        sub_function=FairCamSubFunction.LEC_DET_MONITORING,
        capability_value=14.0,
        confirmed=False,
        name="MTTD-14d monitoring control (unconfirmed)",
    )


@pytest_asyncio.fixture
async def control_with_currency_assignment(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> Control:
    """Confirmed control with one CURRENCY assignment ($5,000 per event)."""
    _, org_id = authed_analyst
    return await _make_control_single_assignment(
        db_session,
        org_id,
        sub_function=FairCamSubFunction.LEC_RESP_LOSS_REDUCTION,
        capability_value=5000.0,
        confirmed=True,
        name="Loss-reduction $5k control",
    )


def _minimal_simulation_results() -> dict[str, Any]:
    """Minimal simulation_results payload sufficient for runs/detail.html render."""
    ale = 100_000.0
    samples = [ale] * 10
    return {
        "base_risk": {
            "annualized_loss_expectancy": ale,
            "mean": ale,
            "median": ale,
            "std_deviation": 0.0,
            "var_95": ale,
            "var_99": ale,
            "loss_event_frequency": 1.0,
            "loss_magnitude": ale,
            "simulation_results": samples,
            "n_simulations": 10,
        },
        "residual_risk": {
            "annualized_loss_expectancy": ale,
            "mean": ale,
            "median": ale,
            "std_deviation": 0.0,
            "var_95": ale,
            "var_99": ale,
            "loss_event_frequency": 1.0,
            "loss_magnitude": ale,
            "simulation_results": samples,
            "n_simulations": 10,
        },
        "control_adjustments": [],
        "confidence_intervals": {
            "lower_bound": ale * 0.9,
            "upper_bound": ale * 1.1,
            "interval_pct": 95,
            "sample_size": 10,
        },
        "loss_exceedance_curve": [{"loss": ale, "probability": 0.5}],
        "exceedance_probability_curve": [{"percentile": 0.5, "loss": ale}],
    }


def _v3_snapshot_with_elapsed_time() -> list[dict[str, Any]]:
    """V3 snapshot list with an ELAPSED_TIME sub-function assignment.

    V3 (snapshot_version=3) is the post-#131 shape the macro must handle in
    runs/detail.html. The macro's ``sub_function_units_map`` is keyed by
    FairCamSubFunction (StrEnum), so the bare string slug from the snapshot
    JSON resolves via str-equality.
    """
    return [
        {
            "snapshot_version": 3,
            "control_id": str(uuid.uuid4()),
            "name": "MTTD-14d monitoring (snapshot)",
            "domains": ["loss_event"],
            "type": "technical",
            "assignments": [
                {
                    "sub_function": "lec_det_monitoring",
                    "capability_value": 14.0,
                    "coverage": 0.8,
                    "reliability": 0.85,
                    "unit_type": "elapsed_time",
                }
            ],
        }
    ]


@pytest_asyncio.fixture
async def completed_run_with_elapsed_time_control(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_user: User,
) -> RiskAnalysisRun:
    """COMPLETED run in the analyst's org with an ELAPSED_TIME-bearing snapshot.

    Hand-rolls the run (rather than using ``seed_run_factory``) so the
    scenario lives in ``authed_analyst``'s org — otherwise the org-scoped
    run-detail route returns 404. seed_run_factory's default scenario is
    seeded in ``seed_organization`` which is a different org.
    """
    _, org_id = authed_analyst
    scenario = Scenario(
        organization_id=org_id,
        name="T4 unit_display run-detail test scenario",
        scenario_type=ScenarioType.CUSTOM,
        threat_category=ThreatCategory.RANSOMWARE,
        threat_event_frequency={"distribution": "PERT", "low": 0.1, "mode": 0.5, "high": 2.0},
        vulnerability={"distribution": "PERT", "low": 0.2, "mode": 0.4, "high": 0.6},
        primary_loss={
            "distribution": "PERT",
            "low": 50_000,
            "mode": 250_000,
            "high": 2_000_000,
        },
        status=EntityStatus.ACTIVE,
        created_by=seed_user.id,
    )
    db_session.add(scenario)
    await db_session.commit()
    await db_session.refresh(scenario)

    run = RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=org_id,
        scenario_id=scenario.id,
        mc_iterations=10,
        inputs_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        controls_snapshot=_v3_snapshot_with_elapsed_time(),
        control_ids_used=[],
        status=RunStatus.COMPLETED,
        run_type=RunType.SINGLE,
        created_by=seed_user.id,
        simulation_results=_minimal_simulation_results(),
        completed_at=datetime.now(UTC),
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)
    return run


@pytest.mark.asyncio
async def test_controls_detail_renders_days_for_elapsed_time(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    control_with_elapsed_time_assignment: Control,
) -> None:
    """controls/detail.html capability column shows 'X days' not 'X.XXX'."""
    client, _ = authed_analyst
    r = await client.get(f"/controls/{control_with_elapsed_time_assignment.id}")
    assert r.status_code == 200, r.text
    assert "14.0 days" in r.text
    assert "14.000" not in r.text


@pytest.mark.asyncio
async def test_controls_detail_renders_dollars_for_currency(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    control_with_currency_assignment: Control,
) -> None:
    """controls/detail.html capability column shows '$X per event' for CURRENCY."""
    client, _ = authed_analyst
    r = await client.get(f"/controls/{control_with_currency_assignment.id}")
    assert r.status_code == 200, r.text
    assert "$5,000 per event" in r.text


@pytest.mark.asyncio
async def test_controls_maintenance_renders_unit_aware(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    control_with_elapsed_time_assignment_unconfirmed: Control,
) -> None:
    """controls/maintenance.html capability column shows unit suffix ('days')."""
    client, _ = authed_admin
    r = await client.get("/controls/maintenance")
    assert r.status_code == 200, r.text
    assert "days" in r.text


@pytest.mark.asyncio
async def test_runs_detail_renders_unit_aware(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    completed_run_with_elapsed_time_control: RiskAnalysisRun,
) -> None:
    """runs/detail.html capability cell in snapshot table shows 'days' suffix."""
    client, _ = authed_analyst
    r = await client.get(f"/runs/{completed_run_with_elapsed_time_control.id}")
    assert r.status_code == 200, r.text
    assert "days" in r.text
