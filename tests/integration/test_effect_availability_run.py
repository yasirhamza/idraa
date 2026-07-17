"""Slice 1 end-to-end: a SINGLE run of an AVAILABILITY scenario credits a
standalone recovery control (lec_resp_resilience, NO detection partner); the SAME
recovery control on a CONFIDENTIALITY scenario stays $0 (D8 detection-gated).
Regression pins both directions + the scenario_inputs_snapshot effect key."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import idraa.config as config
from idraa.models.enums import ControlDomain, FairCamSubFunction, ScenarioEffect
from idraa.models.risk_analysis_run import RunStatus
from idraa.models.scenario_control import ScenarioControl
from idraa.services.run_executor import execute_run


def _small_ensemble_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the weight-robustness ensemble cheap (single control -> fast Shapley).
    wire_executor_to_test_db resets the config singleton at setup; set env knobs
    then reset again so the executor's get_settings() picks them up."""
    monkeypatch.setenv("WEIGHT_ENSEMBLE_DRAWS", "8")
    monkeypatch.setenv("WEIGHT_ENSEMBLE_MIN_DRAWS", "2")
    config.reset_for_tests()


async def _run_single_recovery_scenario(
    *,
    db_session: AsyncSession,
    seed_scenario_factory: Any,
    seed_control_factory: Any,
    seed_run_factory: Any,
    effect: ScenarioEffect,
    name: str,
) -> tuple[Any, Any]:
    """Build ONE scenario with the given Effect + a recovery-only control
    (LEC_RESP_RESILIENCE, no detection), run a SINGLE analysis, return (run, control)."""
    scenario = await seed_scenario_factory(name=name, effect=effect)
    control = await seed_control_factory(
        name=f"recovery-{name}",
        sub_function=FairCamSubFunction.LEC_RESP_RESILIENCE,
        domain=ControlDomain.LOSS_EVENT,
        capability_value=0.6,
    )
    db_session.add(ScenarioControl(scenario_id=scenario.id, control_id=control.id))
    await db_session.commit()

    run = await seed_run_factory(scenario=scenario, mc_iterations=200, random_seed=101)
    run.control_ids_used = [str(control.id)]  # executor loads the universe from here
    db_session.add(run)
    await db_session.commit()

    await execute_run(run.id)
    await db_session.refresh(run)
    return run, control


@pytest.mark.asyncio
async def test_availability_scenario_credits_recovery_control(
    db_session: AsyncSession,
    seed_scenario_factory: Any,
    seed_control_factory: Any,
    seed_run_factory: Any,
    wire_executor_to_test_db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _small_ensemble_env(monkeypatch)
    run, control = await _run_single_recovery_scenario(
        db_session=db_session,
        seed_scenario_factory=seed_scenario_factory,
        seed_control_factory=seed_control_factory,
        seed_run_factory=seed_run_factory,
        effect=ScenarioEffect.AVAILABILITY,
        name="avail-recovery",
    )
    assert run.status == RunStatus.COMPLETED, run.error_message
    cell = run.weight_robustness["per_control"][str(control.id)]
    # Availability self-detects (§3.3.2 p.19): the raw LEC_RESPONSE magnitude
    # credit scores WITHOUT a detection partner. A scoring control is > $0.
    assert cell["reduction_p50"] > 0.0


@pytest.mark.asyncio
async def test_confidentiality_scenario_recovery_stays_zero(
    db_session: AsyncSession,
    seed_scenario_factory: Any,
    seed_control_factory: Any,
    seed_run_factory: Any,
    wire_executor_to_test_db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _small_ensemble_env(monkeypatch)
    run, control = await _run_single_recovery_scenario(
        db_session=db_session,
        seed_scenario_factory=seed_scenario_factory,
        seed_control_factory=seed_control_factory,
        seed_run_factory=seed_run_factory,
        effect=ScenarioEffect.CONFIDENTIALITY,
        name="conf-recovery",
    )
    assert run.status == RunStatus.COMPLETED, run.error_message
    # Structural-$0 control keeps a non-None canonical value (0.0) so it IS present
    # in per_control (displayed_control_order excludes only absent-only/None cells).
    cell = run.weight_robustness["per_control"][str(control.id)]
    # Stealth C/I stays detection-gated (D8, §3.3 p.18): no detection partner ->
    # identity magnitude multiplier -> exactly $0.
    assert cell["reduction_p50"] == 0.0


@pytest.mark.asyncio
async def test_availability_residual_ale_lower_than_confidentiality(
    db_session: AsyncSession,
    seed_scenario_factory: Any,
    seed_control_factory: Any,
    seed_run_factory: Any,
    wire_executor_to_test_db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MC Site-1 headline: the same recovery control (lec_resp_resilience) lowers
    the residual ALE for an AVAILABILITY scenario but NOT for a CONFIDENTIALITY
    scenario (which stays detection-gated with no detection partner).

    Assertion: avail_residual_ale < conf_residual_ale — the recovery control is
    effective for availability and ineffective (identity magnitude) for confidentiality.
    """
    _small_ensemble_env(monkeypatch)
    run_avail, _ = await _run_single_recovery_scenario(
        db_session=db_session,
        seed_scenario_factory=seed_scenario_factory,
        seed_control_factory=seed_control_factory,
        seed_run_factory=seed_run_factory,
        effect=ScenarioEffect.AVAILABILITY,
        name="site1-avail",
    )
    run_conf, _ = await _run_single_recovery_scenario(
        db_session=db_session,
        seed_scenario_factory=seed_scenario_factory,
        seed_control_factory=seed_control_factory,
        seed_run_factory=seed_run_factory,
        effect=ScenarioEffect.CONFIDENTIALITY,
        name="site1-conf",
    )
    assert run_avail.status == RunStatus.COMPLETED, run_avail.error_message
    assert run_conf.status == RunStatus.COMPLETED, run_conf.error_message

    avail_residual_ale = run_avail.simulation_results["residual_risk"]["annualized_loss_expectancy"]
    conf_residual_ale = run_conf.simulation_results["residual_risk"]["annualized_loss_expectancy"]
    # The recovery control reduces loss for availability but not for confidentiality
    # (detection-gated with no detection partner -> identity magnitude -> no reduction).
    assert avail_residual_ale < conf_residual_ale, (
        f"availability residual ALE ({avail_residual_ale:.0f}) must be lower than "
        f"confidentiality residual ALE ({conf_residual_ale:.0f}): "
        "lec_resp_resilience should credit availability but stay $0 for confidentiality"
    )


@pytest.mark.asyncio
async def test_scenario_inputs_snapshot_carries_effect(
    db_session: AsyncSession,
    seed_scenario_factory: Any,
    seed_control_factory: Any,
    seed_run_factory: Any,
    wire_executor_to_test_db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _small_ensemble_env(monkeypatch)
    run, _control = await _run_single_recovery_scenario(
        db_session=db_session,
        seed_scenario_factory=seed_scenario_factory,
        seed_control_factory=seed_control_factory,
        seed_run_factory=seed_run_factory,
        effect=ScenarioEffect.AVAILABILITY,
        name="snap-effect",
    )
    assert run.status == RunStatus.COMPLETED, run.error_message
    assert run.scenario_inputs_snapshot["scenarios"][0]["effect"] == "availability"
