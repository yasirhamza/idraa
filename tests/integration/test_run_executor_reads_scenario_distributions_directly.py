"""PR pi -- lock the new pipeline contract.

Two scenarios with different stored distributions must produce different
Monte Carlo results when run together in an AGGREGATE run. This is the
canonical regression for the "per-scenario chart shows uniform values"
bug surfaced in PR #39's E2E.

Locked in F2 as xfail-strict; turned green in F4 once both SINGLE
(F3) and AGGREGATE (F4) branches read scenario distributions directly.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.enums import EntityStatus, ScenarioType, ThreatCategory
from idraa.models.organization import Organization
from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType
from idraa.models.scenario import Scenario
from idraa.models.user import User
from idraa.services.run_executor import execute_run

# asyncio_mode=auto in pyproject.toml handles async-test detection; no
# pytestmark needed here.


@pytest.fixture
def low_distributions() -> dict[str, dict[str, Any] | None]:
    return {
        "threat_event_frequency": {"distribution": "pert", "low": 0.1, "mode": 0.5, "high": 1.0},
        "vulnerability": {"distribution": "pert", "low": 0.05, "mode": 0.1, "high": 0.2},
        "primary_loss": {"distribution": "pert", "low": 1_000.0, "mode": 5_000.0, "high": 10_000.0},
        "secondary_loss": None,
    }


@pytest.fixture
def high_distributions() -> dict[str, dict[str, Any] | None]:
    return {
        "threat_event_frequency": {"distribution": "pert", "low": 5.0, "mode": 10.0, "high": 20.0},
        "vulnerability": {"distribution": "pert", "low": 0.5, "mode": 0.7, "high": 0.9},
        "primary_loss": {
            "distribution": "pert",
            "low": 1_000_000.0,
            "mode": 5_000_000.0,
            "high": 10_000_000.0,
        },
        "secondary_loss": None,
    }


def _make_scenario(
    *,
    organization_id: uuid.UUID,
    created_by: uuid.UUID,
    name: str,
    distributions: dict[str, dict[str, Any] | None],
) -> Scenario:
    """Build a schema-valid Scenario with caller-supplied distributions.

    seed_scenario_factory hardcodes the JSON-column dicts, so we construct
    directly via the ORM to vary threat_event_frequency / vulnerability /
    primary_loss / secondary_loss per scenario.
    """
    return Scenario(
        organization_id=organization_id,
        name=name,
        scenario_type=ScenarioType.CUSTOM,
        threat_category=ThreatCategory.RANSOMWARE,
        threat_event_frequency=distributions["threat_event_frequency"],
        vulnerability=distributions["vulnerability"],
        primary_loss=distributions["primary_loss"],
        secondary_loss=distributions["secondary_loss"],
        status=EntityStatus.ACTIVE,
        created_by=created_by,
    )


async def test_two_scenarios_with_different_distributions_produce_different_mc_results(
    db_session: AsyncSession,
    seed_organization: Organization,
    seed_user: User,
    wire_executor_to_test_db: None,
    low_distributions: dict[str, dict[str, Any] | None],
    high_distributions: dict[str, dict[str, Any] | None],
) -> None:
    org_id = seed_organization.id

    low_scenario = _make_scenario(
        organization_id=org_id,
        created_by=seed_user.id,
        name="Low-impact scenario",
        distributions=low_distributions,
    )
    high_scenario = _make_scenario(
        organization_id=org_id,
        created_by=seed_user.id,
        name="High-impact scenario",
        distributions=high_distributions,
    )
    db_session.add_all([low_scenario, high_scenario])
    await db_session.commit()
    await db_session.refresh(low_scenario)
    await db_session.refresh(high_scenario)

    # Construct AGGREGATE run by hand -- seed_run_factory only creates SINGLE.
    run = RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=org_id,
        scenario_id=low_scenario.id,  # placeholder; ignored for AGGREGATE
        aggregate_scenario_ids=[str(low_scenario.id), str(high_scenario.id)],
        mc_iterations=500,
        inputs_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        controls_snapshot=[],
        control_ids_used=[],
        status=RunStatus.QUEUED,
        run_type=RunType.AGGREGATE,
        created_by=seed_user.id,
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)

    await execute_run(run.id)

    await db_session.refresh(run)
    assert run.status == RunStatus.COMPLETED, run.error_message
    assert run.simulation_results is not None  # narrow Optional for mypy

    # Persisted payloads carry the schema-version stamp (hygiene item from
    # the whole-project eval); legacy rows lack the key and read as 0.
    from idraa.services.simulation_payload import (
        SIMULATION_RESULTS_SCHEMA_VERSION,
        results_schema_version,
    )

    assert results_schema_version(run.simulation_results) == SIMULATION_RESULTS_SCHEMA_VERSION

    per_scenario = run.simulation_results["per_scenario"]
    low_entry = next(p for p in per_scenario if p["scenario_id"] == str(low_scenario.id))
    high_entry = next(p for p in per_scenario if p["scenario_id"] == str(high_scenario.id))

    # Per-scenario payload shape (from _build_results_payload): each entry has
    # base_risk + residual_risk dicts; residual ALE lives at
    # residual_risk["annualized_loss_expectancy"].
    low_residual_ale = low_entry["residual_risk"]["annualized_loss_expectancy"]
    high_residual_ale = high_entry["residual_risk"]["annualized_loss_expectancy"]

    # The user-visible regression: residuals MUST differ.
    assert low_residual_ale != high_residual_ale
    assert low_residual_ale < high_residual_ale
