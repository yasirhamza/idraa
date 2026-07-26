"""PR2 Task 8 (D16): per-scenario capacity-cap disclosure — route/template.

Drives the REAL page render (GET /runs/{id}) so the run_view_model wiring +
the runs/detail.html markup are asserted on the composed result, matching
the convention in test_run_detail_components.py / test_run_detail_v2_banner_and_log.py
(fixtures are file-local, not shared via conftest, to avoid a cross-module
ruff F811 on the parameter shadowing the import).

Math correctness (the hand-derived percentage) is pinned at the unit layer
in tests/unit/test_run_view_model_capacity_cap.py; this file only proves the
route/template actually surfaces it.
"""

from __future__ import annotations

import hashlib
import math
import uuid
from typing import Any

import numpy as np
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.enums import EntityStatus, ScenarioType, ThreatCategory
from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType
from idraa.models.scenario import Scenario
from idraa.models.user import User

_MU = math.log(1_000_000.0)
_SIGMA = 1.7
_CAP = 50_000_000.0  # Case A from the unit-test module: cap_effect ~= 26.601004%


async def _seed_scenario(
    db_session: AsyncSession,
    organization_id: uuid.UUID,
    created_by: uuid.UUID,
    *,
    name: str,
    primary_loss: dict[str, Any],
    secondary_loss: dict[str, Any] | None = None,
) -> Scenario:
    scenario = Scenario(
        organization_id=organization_id,
        name=name,
        scenario_type=ScenarioType.CUSTOM,
        threat_category=ThreatCategory.RANSOMWARE,
        threat_event_frequency={"distribution": "pert", "low": 0.1, "mode": 0.5, "high": 2.0},
        vulnerability={"distribution": "pert", "low": 0.2, "mode": 0.4, "high": 0.6},
        primary_loss=primary_loss,
        secondary_loss=secondary_loss,
        status=EntityStatus.ACTIVE,
        created_by=created_by,
    )
    db_session.add(scenario)
    await db_session.commit()
    await db_session.refresh(scenario)
    return scenario


def _minimal_simulation_results() -> dict[str, Any]:
    rng = np.random.default_rng(seed=7)
    samples = rng.lognormal(mean=10.0, sigma=0.5, size=200).tolist()
    ale = float(np.mean(samples))
    risk = {
        "annualized_loss_expectancy": ale,
        "mean": ale,
        "median": float(np.median(samples)),
        "std_deviation": float(np.std(samples)),
        "var_95": float(np.percentile(samples, 95)),
        "var_99": float(np.percentile(samples, 99)),
        "loss_event_frequency": 1.0,
        "loss_magnitude": ale,
        "simulation_results": samples,
        "n_simulations": 200,
    }
    return {
        "base_risk": dict(risk),
        "residual_risk": dict(risk),
        "control_adjustments": [],
        "confidence_intervals": {
            "lower_bound": ale * 0.9,
            "upper_bound": ale * 1.1,
            "interval_pct": 95,
            "sample_size": 200,
        },
        "loss_exceedance_curve": [],
        "exceedance_probability_curve": [],
    }


async def _seed_run(
    db_session: AsyncSession,
    scenario: Scenario,
    created_by: uuid.UUID,
    *,
    scenario_inputs_snapshot: dict[str, Any] | None,
) -> RiskAnalysisRun:
    run = RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=scenario.organization_id,
        scenario_id=scenario.id,
        mc_iterations=200,
        inputs_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        controls_snapshot=[],
        control_ids_used=[],
        status=RunStatus.COMPLETED,
        run_type=RunType.SINGLE,
        created_by=created_by,
        simulation_results=_minimal_simulation_results(),
        scenario_inputs_snapshot=scenario_inputs_snapshot,
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)
    return run


def _snapshot_for(scenario: Scenario) -> dict[str, Any]:
    return {
        "scenarios": [
            {
                "scenario_id": str(scenario.id),
                "scenario_name": scenario.name,
                "threat_event_frequency": scenario.threat_event_frequency,
                "vulnerability": scenario.vulnerability,
                "primary_loss": scenario.primary_loss,
                "secondary_loss": scenario.secondary_loss,
                "effect": None,
            }
        ]
    }


@pytest_asyncio.fixture
async def capped_run(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_user: User,
) -> RiskAnalysisRun:
    """A COMPLETED SINGLE run whose as-executed PL snapshot carries a capacity `max`."""
    _, org_id = authed_analyst
    scenario = await _seed_scenario(
        db_session,
        org_id,
        seed_user.id,
        name="capacity-capped test scenario",
        primary_loss={"distribution": "lognormal", "mean": _MU, "sigma": _SIGMA, "max": _CAP},
    )
    return await _seed_run(
        db_session, scenario, seed_user.id, scenario_inputs_snapshot=_snapshot_for(scenario)
    )


@pytest_asyncio.fixture
async def uncapped_pert_run(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_user: User,
) -> RiskAnalysisRun:
    """A COMPLETED SINGLE run with a snapshot, but nothing capped (PERT-only)."""
    _, org_id = authed_analyst
    scenario = await _seed_scenario(
        db_session,
        org_id,
        seed_user.id,
        name="uncapped PERT test scenario",
        primary_loss={
            "distribution": "pert",
            "low": 50_000.0,
            "mode": 250_000.0,
            "high": 2_000_000.0,
        },
    )
    return await _seed_run(
        db_session, scenario, seed_user.id, scenario_inputs_snapshot=_snapshot_for(scenario)
    )


@pytest_asyncio.fixture
async def legacy_no_snapshot_run(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_user: User,
) -> RiskAnalysisRun:
    """A COMPLETED SINGLE run predating the T2/#351 snapshot column (NULL)."""
    _, org_id = authed_analyst
    scenario = await _seed_scenario(
        db_session,
        org_id,
        seed_user.id,
        name="legacy no-snapshot test scenario",
        primary_loss={"distribution": "lognormal", "mean": _MU, "sigma": _SIGMA, "max": _CAP},
    )
    return await _seed_run(db_session, scenario, seed_user.id, scenario_inputs_snapshot=None)


@pytest.mark.asyncio
async def test_capacity_cap_note_renders_with_live_percentage_and_basis_label(
    authed_analyst: tuple[AsyncClient, uuid.UUID], capped_run: RiskAnalysisRun
) -> None:
    client, _ = authed_analyst
    resp = await client.get(f"/runs/{capped_run.id}")
    assert resp.status_code == 200
    html = resp.text
    assert "Capacity cap applied to this scenario's loss draws." in html
    # Basis label (Decision 1) must ship explicitly -- never an unlabelled number.
    assert "inherent-basis" in html
    # The live figure (hand-derived in the unit-test module: 0.2660100403787624 -> 26.6010%).
    assert "26.6010%" in html
    # The bound_P + bound_S caveat must be preserved.
    assert "bounded separately" in html
    # Never the banned hardcoded phrasings.
    assert "capped at one year's revenue" not in html
    assert "~0.0%" not in html


@pytest.mark.asyncio
async def test_capacity_cap_note_absent_when_nothing_capped(
    authed_analyst: tuple[AsyncClient, uuid.UUID], uncapped_pert_run: RiskAnalysisRun
) -> None:
    client, _ = authed_analyst
    resp = await client.get(f"/runs/{uncapped_pert_run.id}")
    assert resp.status_code == 200
    assert "Capacity cap applied to this scenario's loss draws." not in resp.text


@pytest.mark.asyncio
async def test_capacity_cap_note_absent_and_no_500_on_legacy_null_snapshot(
    authed_analyst: tuple[AsyncClient, uuid.UUID], legacy_no_snapshot_run: RiskAnalysisRun
) -> None:
    client, _ = authed_analyst
    resp = await client.get(f"/runs/{legacy_no_snapshot_run.id}")
    assert resp.status_code == 200
    assert "Capacity cap applied to this scenario's loss draws." not in resp.text
