"""Validate run executor handles analyst-submitted PERT edge cases gracefully."""

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

pytestmark = pytest.mark.asyncio


def _construct_single_run(
    db: AsyncSession,
    scenario: Scenario,
    mc_iterations: int = 200,
    *,
    user_id: uuid.UUID,
) -> RiskAnalysisRun:
    return RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=scenario.organization_id,
        scenario_id=scenario.id,
        mc_iterations=mc_iterations,
        inputs_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        controls_snapshot=[],
        control_ids_used=[],
        status=RunStatus.QUEUED,
        run_type=RunType.SINGLE,
        created_by=user_id,
    )


async def _seed_scenario_with_dists(
    db_session: AsyncSession,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    name: str,
    tef: dict[str, Any],
    vuln: dict[str, Any],
    pl: dict[str, Any],
    sl: dict[str, Any] | None = None,
) -> Scenario:
    """Seed a Scenario with explicit distribution dicts (bypasses seed_scenario_factory
    defaults, which cannot be overridden via **kwargs for the distribution fields).
    """
    scenario = Scenario(
        organization_id=org_id,
        name=name,
        scenario_type=ScenarioType.CUSTOM,
        threat_category=ThreatCategory.RANSOMWARE,
        threat_event_frequency=tef,
        vulnerability=vuln,
        primary_loss=pl,
        secondary_loss=sl,
        status=EntityStatus.ACTIVE,
        created_by=user_id,
    )
    db_session.add(scenario)
    await db_session.commit()
    return scenario


# C9: degenerate PERT (low=mode=high) — native engine resolves to a point-mass.
async def test_executor_handles_degenerate_pert_as_point_mass(
    db_session: AsyncSession,
    seed_organization: Organization,
    seed_user: User,
    wire_executor_to_test_db: None,
) -> None:
    """Degenerate PERT (low=mode=high) is a legitimate POINT ESTIMATE.

    AUDIT (Epic A #324, Task 8 cutover — was ``test_executor_marks_degenerate
    _pert_as_failed``): this test previously pinned pyfair-SPECIFIC behavior —
    pyfair rejected ``low==high`` with a ``FairException`` and the executor
    flipped the run to FAILED. That was a tooling artefact, not a FAIR-modeling
    invariant: a degenerate PERT is just a deterministic point estimate. The
    test's own docstring named "replace pyfair entirely" (issue #42) as the
    intended resolution — which is exactly this cutover. The native FAIREngine
    short-circuits a degenerate PERT to ``np.full`` (point-mass), so the run now
    COMPLETES with a finite point-mass distribution. ALE = TEF·vuln·PL =
    0.5·0.3·5000 = 750.0 on every sample. Re-pinned to the correct native
    behavior, NOT masked.
    """
    scenario = await _seed_scenario_with_dists(
        db_session,
        seed_organization.id,
        seed_user.id,
        name="degenerate-pert-test",
        tef={"distribution": "pert", "low": 0.5, "mode": 0.5, "high": 0.5},
        vuln={"distribution": "pert", "low": 0.3, "mode": 0.3, "high": 0.3},
        pl={"distribution": "pert", "low": 5000.0, "mode": 5000.0, "high": 5000.0},
    )
    run = _construct_single_run(db_session, scenario, user_id=seed_user.id)
    db_session.add(run)
    await db_session.commit()
    await execute_run(run.id)
    await db_session.refresh(run)
    # Native engine resolves a degenerate PERT to a point-mass — the run COMPLETES.
    assert run.status == RunStatus.COMPLETED, (
        f"expected COMPLETED for degenerate (point-mass) PERT, got {run.status} "
        f"— error_message={run.error_message}"
    )
    assert run.error_message is None, "a successful point-mass run must not set error_message"
    sim_results = run.simulation_results
    assert sim_results is not None
    ale = sim_results["residual_risk"]["annualized_loss_expectancy"]
    # 0.5 (TEF) * 0.3 (vuln) * 5000 (PL) = 750.0, deterministic point-mass.
    assert ale == pytest.approx(750.0), f"degenerate-PERT point-mass ALE should be 750.0, got {ale}"


# C10: wide-spread PERT — executor must produce finite, positive ALE
async def test_executor_handles_wide_spread_pert(
    db_session: AsyncSession,
    seed_organization: Organization,
    seed_user: User,
    wire_executor_to_test_db: None,
) -> None:
    """PERT with very wide spread (3 orders of magnitude) should complete
    and produce a finite, positive ALE.
    """
    scenario = await _seed_scenario_with_dists(
        db_session,
        seed_organization.id,
        seed_user.id,
        name="wide-spread-pert-test",
        tef={"distribution": "pert", "low": 0.001, "mode": 1.0, "high": 100.0},
        vuln={"distribution": "pert", "low": 0.01, "mode": 0.5, "high": 0.99},
        pl={
            "distribution": "pert",
            "low": 1.0,
            "mode": 100_000.0,
            "high": 1_000_000_000.0,
        },
    )
    run = _construct_single_run(db_session, scenario, user_id=seed_user.id)
    db_session.add(run)
    await db_session.commit()
    await execute_run(run.id)
    await db_session.refresh(run)
    assert run.status == RunStatus.COMPLETED, f"wide-spread PERT run FAILED: {run.error_message}"
    sim_results = run.simulation_results
    assert sim_results is not None
    ale = sim_results["residual_risk"]["annualized_loss_expectancy"]
    assert ale > 0, "ALE must be positive"
    assert ale < float("inf"), "ALE must be finite"


# C11: right-skewed PERT (mode close to low) — executor must complete
async def test_executor_handles_right_skewed_pert(
    db_session: AsyncSession,
    seed_organization: Organization,
    seed_user: User,
    wire_executor_to_test_db: None,
) -> None:
    """PERT distribution where mode is very close to low (highly right-skewed)
    should complete without error.
    """
    scenario = await _seed_scenario_with_dists(
        db_session,
        seed_organization.id,
        seed_user.id,
        name="right-skewed-pert-test",
        tef={"distribution": "pert", "low": 1.0, "mode": 1.1, "high": 100.0},
        vuln={"distribution": "pert", "low": 0.1, "mode": 0.15, "high": 0.95},
        pl={"distribution": "pert", "low": 1_000.0, "mode": 1_100.0, "high": 100_000.0},
    )
    run = _construct_single_run(db_session, scenario, user_id=seed_user.id)
    db_session.add(run)
    await db_session.commit()
    await execute_run(run.id)
    await db_session.refresh(run)
    assert run.status == RunStatus.COMPLETED, f"right-skewed PERT run FAILED: {run.error_message}"
