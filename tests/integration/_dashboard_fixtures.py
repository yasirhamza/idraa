"""Shared helpers for omicron-1 dashboard integration tests.

Build minimal-but-valid Scenario and RiskAnalysisRun rows scoped to
explicit org_ids. Not a conftest — leading-underscore module name
keeps pytest from auto-discovering it as fixtures.

F4 introduces _make_scenario; F13 appends _make_completed_aggregate_run
and _make_completed_single_run.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from typing import Any

from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType
from idraa.models.scenario import Scenario


def _make_scenario(
    *,
    org_id: uuid.UUID,
    name: str,
) -> Scenario:
    """Minimal-valid Scenario for dashboard tests scoped to a SPECIFIC org.

    The conftest's seed_scenario_factory is bound by closure to
    seed_organization (a different org from authed_admin's org), so it
    can't be used here without cross-wiring the orgs. This builder lets
    each test attach scenarios to whichever org_id its authentication
    fixture resolved to.

    Defaults mirror tests/conftest.py:438-486 (seed_scenario_factory)
    minus the created_by=seed_user.id (created_by is nullable on Scenario;
    tests that don't need an author leave it None).
    """
    from idraa.models.enums import ScenarioType, ThreatCategory

    return Scenario(
        id=uuid.uuid4(),
        organization_id=org_id,
        name=name,
        scenario_type=ScenarioType.CUSTOM,
        threat_category=ThreatCategory.RANSOMWARE,
        threat_event_frequency={
            "distribution": "PERT",
            "low": 0.1,
            "mode": 0.5,
            "high": 2.0,
        },
        vulnerability={
            "distribution": "PERT",
            "low": 0.2,
            "mode": 0.4,
            "high": 0.6,
        },
        primary_loss={
            "distribution": "PERT",
            "low": 50_000,
            "mode": 250_000,
            "high": 2_000_000,
        },
    )


def _make_completed_aggregate_run(
    *,
    org_id: uuid.UUID,
    name: str | None = "Portfolio",
    scenario_ids: list[uuid.UUID],
    ale_with_controls: float = 100_000.0,
    ale_without_controls: float = 500_000.0,
    control_value_dollars: float = 400_000.0,
    control_value_percent: float = 80.0,
    per_scenario: list[dict[str, Any]] | None = None,
    created_at: dt.datetime | None = None,
) -> RiskAnalysisRun:
    """COMPLETED AGGREGATE run with simulation_results matching the shape
    aggregate_run_view_model.build_aggregate_display_results expects."""
    if per_scenario is None:
        per_scenario = [
            {
                "scenario_id": str(sid),
                "scenario_name": f"Scenario {i}",
                "base_risk": {"annualized_loss_expectancy": 250_000.0 - i * 50_000},
                "residual_risk": {"annualized_loss_expectancy": 50_000.0 - i * 10_000},
            }
            for i, sid in enumerate(scenario_ids)
        ]
    return RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=org_id,
        name=name,
        run_type=RunType.AGGREGATE,
        status=RunStatus.COMPLETED,
        scenario_id=None,
        aggregate_scenario_ids=sorted(str(s) for s in scenario_ids),
        control_ids_used=[],
        controls_snapshot=[],
        mc_iterations=1000,
        inputs_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        created_at=created_at or dt.datetime.now(dt.UTC),
        completed_at=created_at or dt.datetime.now(dt.UTC),
        simulation_results={
            "aggregate_with_controls": {
                "annualized_loss_expectancy": ale_with_controls,
                "loss_exceedance_curve": [
                    {"loss": 0.0, "probability": 1.0},
                    {"loss": ale_with_controls * 5, "probability": 0.0},
                ],
            },
            "aggregate_without_controls": {
                "annualized_loss_expectancy": ale_without_controls,
                "loss_exceedance_curve": [
                    {"loss": 0.0, "probability": 1.0},
                    {"loss": ale_without_controls * 5, "probability": 0.0},
                ],
            },
            "dual_epc": {
                "with_controls": [
                    {"percentile": 0.05, "loss": ale_with_controls * 0.5},
                    {"percentile": 0.95, "loss": ale_with_controls * 5},
                ],
                "without_controls": [
                    {"percentile": 0.05, "loss": ale_without_controls * 0.5},
                    {"percentile": 0.95, "loss": ale_without_controls * 5},
                ],
            },
            "confidence_intervals": {
                "lower_bound": ale_with_controls * 0.8,
                "upper_bound": ale_with_controls * 1.2,
                "interval_pct": 95,  # #202: empirical central-95% band marker
            },
            "control_value": {
                "dollars": control_value_dollars,
                "percent": control_value_percent,
            },
            "per_scenario": per_scenario,
            "n_scenarios": len(scenario_ids),
            "n_simulations": 1000,
        },
    )


def _make_completed_single_run(
    *,
    org_id: uuid.UUID,
    name: str | None,
    scenario_id: uuid.UUID,
    residual_ale: float,
    base_ale: float | None = None,
    created_at: dt.datetime | None = None,
) -> RiskAnalysisRun:
    """COMPLETED SINGLE run with residual_risk.ale shape consumed by the
    SINGLE-fallback path."""
    if base_ale is None:
        base_ale = residual_ale * 2
    return RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=org_id,
        name=name,
        run_type=RunType.SINGLE,
        status=RunStatus.COMPLETED,
        scenario_id=scenario_id,
        aggregate_scenario_ids=None,
        control_ids_used=[],
        controls_snapshot=[],
        mc_iterations=200,
        inputs_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        created_at=created_at or dt.datetime.now(dt.UTC),
        completed_at=created_at or dt.datetime.now(dt.UTC),
        simulation_results={
            "base_risk": {"annualized_loss_expectancy": base_ale},
            "residual_risk": {"annualized_loss_expectancy": residual_ale},
        },
    )
