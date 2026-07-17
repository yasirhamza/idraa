"""Slice 2 (#439) end-to-end: a real AGGREGATE run credits a SAT-like meta
control (VMC/DSC families) via the kappa meta->reliability coupling when a
co-present Loss-Event control exists to strengthen, and a meta control with
NO co-present LEC partner scores exactly $0 with an honest reason label.

Hand-math anchor (mirrors fair_cam/tests/risk_engine/test_meta_reliability_coupling.py
::test_meta_uplifts_co_present_lec_effectiveness, at the canonical kappa=0.5
default -- KAPPA_META_RELIABILITY):

  SAT-like meta control: dsc_prev_communication (NULL cap, cov 0.8, rel 0.8)
                        + vmc_id_control_monitoring (NULL cap, cov 0.8, rel 0.8)
    E_dsc_prev = opeff(NULL cap) * cov * rel = 0.5 * 0.8 * 0.8 = 0.32
    e_vmc      = 0.0  (vmc_id_control_monitoring alone has no correction
                        partner -- the prescribed find-AND-fix pair, §4 p.21 --
                        so it contributes nothing to meta_strength)
    E_meta     = OR-compose(E_dsc_prev, e_vmc) = 0.32

  LEC preventer: lec_prev_resistance (cap 0.9, cov 0.8, rel 0.7) -> r0 = 0.7
    r_eff(0.7) = r0 + (1 - r0) * kappa * E_meta
               = 0.7 + 0.3 * 0.5 * 0.32
               = 0.748
    LEC_PREVENTION group effectiveness = cap * cov * r_eff = 0.9 * 0.8 * 0.748

This drives the meta control's Shapley credit strictly positive in an
AGGREGATE run scenario where it co-occurs with the LEC preventer + a
recovery control (lec_resp_resilience, availability self-detects -- Slice 1
/ FAIR-CAM §3.3.2 p.19). A second, ISOLATED single-control run (the SAME
sub-function shape, but with NO other control anywhere in that run's
universe) has E_meta > 0 but nothing to uplift -- v({meta}) = 0 exactly --
and the view-model's zero-value classifier reports the honest
"no co-present loss-event control to strengthen" reason (_META_NO_PARTNER_REASON).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from fair_cam.models.composition_topology import BooleanGroup
from fair_cam.models.control import Control as FCControl
from fair_cam.models.control import ControlDomain as FCControlDomain
from fair_cam.models.control import ControlType as FCControlType
from fair_cam.models.control import FairCamControlFunctionAssignment as FCAssignment
from fair_cam.models.sub_function import FairCamSubFunction as FCSubFunction
from fair_cam.risk_engine.group_composition import compose_groups
from sqlalchemy.ext.asyncio import AsyncSession

import idraa.config as config
from idraa.models.control_function_assignment import ControlFunctionAssignment
from idraa.models.enums import ControlDomain, FairCamSubFunction, ScenarioEffect
from idraa.models.organization import Organization
from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType
from idraa.models.user import User
from idraa.services._view_model_helpers import _META_NO_PARTNER_REASON
from idraa.services.run_executor import execute_run
from idraa.services.run_inputs_hash import build_aggregate_inputs_hash
from idraa.services.run_view_model import build_display_results


def _small_ensemble_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the weight-robustness ensemble cheap. wire_executor_to_test_db resets
    the config singleton at setup; set env knobs then reset again so the
    executor's get_settings() picks them up. kappa is perturbed on EVERY draw
    (sample_ensemble_draw, weight_robustness.py) so a co-present-partner meta
    control gets a genuine reduction_p5 < reduction_p95 spread even at 8 draws."""
    monkeypatch.setenv("WEIGHT_ENSEMBLE_DRAWS", "8")
    monkeypatch.setenv("WEIGHT_ENSEMBLE_MIN_DRAWS", "2")
    config.reset_for_tests()


def test_meta_uplift_hand_math_via_compose_groups_directly() -> None:
    """Pure fair_cam sub-assert (no DB, no executor): the LEC_PREVENTION group
    effectiveness composed from [meta, lec] equals 0.9*0.8*0.748 at the
    canonical kappa=0.5 default -- confirms the hand-math anchor documented in
    the module docstring before trusting the ORM/executor path below."""
    meta = FCControl(
        control_id="sat",
        name="sat",
        domain=FCControlDomain.LOSS_EVENT,  # domain is irrelevant to composition routing
        control_type=FCControlType.PREVENTIVE,
        assignments=[
            FCAssignment(
                sub_function=FCSubFunction.DSC_PREV_COMMUNICATION,
                capability_value=None,
                coverage=0.8,
                reliability=0.8,
            ),
            FCAssignment(
                sub_function=FCSubFunction.VMC_ID_CONTROL_MONITORING,
                capability_value=None,
                coverage=0.8,
                reliability=0.8,
            ),
        ],
    )
    lec = FCControl(
        control_id="mfa",
        name="mfa",
        domain=FCControlDomain.LOSS_EVENT,
        control_type=FCControlType.PREVENTIVE,
        assignments=[
            FCAssignment(
                sub_function=FCSubFunction.LEC_PREV_RESISTANCE,
                capability_value=0.9,
                coverage=0.8,
                reliability=0.7,
            )
        ],
    )
    comp = compose_groups([meta, lec])  # default kappa = KAPPA_META_RELIABILITY = 0.5
    assert comp.meta_strength == pytest.approx(0.32)
    assert comp.group_effectiveness[BooleanGroup.LEC_PREVENTION] == pytest.approx(0.9 * 0.8 * 0.748)
    assert comp.group_effectiveness[BooleanGroup.LEC_PREVENTION] == pytest.approx(
        0.9 * 0.8 * (0.7 + 0.3 * 0.5 * 0.32)
    )


async def _seed_meta_control(
    db_session: AsyncSession,
    seed_control_factory: Any,
    seed_organization: Organization,
    *,
    name: str,
) -> Any:
    """A SAT-like meta control: dsc_prev_communication + vmc_id_control_monitoring,
    both NULL-capability, cov/rel 0.8/0.8. seed_control_factory only builds ONE
    ControlFunctionAssignment; the second is added manually (both sub-functions
    intentionally live on the SAME control -- meta_strength composes across
    assignments regardless of which control they're attached to)."""
    control = await seed_control_factory(
        name=name,
        sub_function=FairCamSubFunction.DSC_PREV_COMMUNICATION,
        domain=ControlDomain.DECISION_SUPPORT,
        capability_value=None,
        coverage=0.8,
        reliability=0.8,
    )
    db_session.add(
        ControlFunctionAssignment(
            control_id=control.id,
            organization_id=seed_organization.id,
            sub_function=FairCamSubFunction.VMC_ID_CONTROL_MONITORING,
            capability_value=None,
            coverage=0.8,
            reliability=0.8,
            confirmed_by_user_at=datetime.now(UTC),
        )
    )
    await db_session.commit()
    return control


@pytest.mark.asyncio
async def test_meta_control_credits_via_kappa_coupling_in_aggregate_run(
    db_session: AsyncSession,
    seed_organization: Organization,
    seed_user: User,
    seed_scenario_factory: Any,
    seed_control_factory: Any,
    wire_executor_to_test_db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AGGREGATE run, scenario A (availability) carries the LEC preventer +
    recovery control + SAT-like meta control; scenario B carries the SAME meta
    control ALONE (per-scenario Shapley is scenario-local, so this exercises
    the no-partner v(S)=0 case independently of scenario A's LEC control)."""
    _small_ensemble_env(monkeypatch)

    lec_control = await seed_control_factory(
        name="lec-preventer",
        sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
        domain=ControlDomain.LOSS_EVENT,
        capability_value=0.9,
        coverage=0.8,
        reliability=0.7,
    )
    recovery_control = await seed_control_factory(
        name="recovery",
        sub_function=FairCamSubFunction.LEC_RESP_RESILIENCE,
        domain=ControlDomain.LOSS_EVENT,
        capability_value=None,
        coverage=0.8,
        reliability=0.8,
    )
    meta_control = await _seed_meta_control(
        db_session, seed_control_factory, seed_organization, name="sat-meta"
    )

    scenario_a = await seed_scenario_factory(
        name="meta-coupling-scenario-a", effect=ScenarioEffect.AVAILABILITY
    )
    scenario_b = await seed_scenario_factory(name="meta-coupling-scenario-b")

    universe = [lec_control, recovery_control, meta_control]
    run = RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=seed_organization.id,
        run_type=RunType.AGGREGATE,
        scenario_id=None,
        aggregate_scenario_ids=sorted(str(s.id) for s in (scenario_a, scenario_b)),
        control_ids_used=sorted(str(c.id) for c in universe),
        aggregate_control_ids_per_scenario={
            str(scenario_a.id): sorted(
                str(c.id) for c in (lec_control, recovery_control, meta_control)
            ),
            str(scenario_b.id): [str(meta_control.id)],
        },
        mc_iterations=200,
        random_seed=101,
        inputs_hash=build_aggregate_inputs_hash(
            scenarios=[scenario_a, scenario_b],
            control_ids=[c.id for c in universe],
            mc_iterations=200,
        ),
        controls_snapshot=[],
        created_by=seed_user.id,
        status=RunStatus.QUEUED,
    )
    db_session.add(run)
    await db_session.commit()

    await execute_run(run.id)
    await db_session.refresh(run)

    assert run.status == RunStatus.COMPLETED, run.error_message
    assert run.simulation_results is not None
    assert run.weight_robustness is not None

    per_scenario = run.simulation_results["per_scenario"]
    by_scenario_id = {ps["scenario_id"]: ps for ps in per_scenario}

    # --- Scenario A: meta control's shapley_value is strictly positive ---
    adj_a = {a["control_id"]: a for a in by_scenario_id[str(scenario_a.id)]["control_adjustments"]}
    assert adj_a[str(meta_control.id)]["shapley_value"] > 0.0

    # --- Scenario B: meta ALONE -> shapley is exactly 0.0 (no partner to uplift) ---
    adj_b = {a["control_id"]: a for a in by_scenario_id[str(scenario_b.id)]["control_adjustments"]}
    assert adj_b[str(meta_control.id)]["shapley_value"] == 0.0

    # --- Stored ranges reflect kappa (plan-gate Spec-I3): a genuine spread, ---
    # --- not a collapsed point -- kappa is perturbed on every ensemble draw. ---
    per_control = run.weight_robustness["per_control"]
    meta_cell = per_control[str(meta_control.id)]
    assert meta_cell["reduction_p5"] < meta_cell["reduction_p95"], meta_cell


@pytest.mark.asyncio
async def test_meta_control_alone_scores_zero_with_no_partner_label(
    db_session: AsyncSession,
    seed_organization: Organization,
    seed_user: User,
    seed_scenario_factory: Any,
    seed_control_factory: Any,
    seed_run_factory: Any,
    wire_executor_to_test_db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A SINGLE run whose ENTIRE control universe is the SAT-like meta control
    (no LEC control anywhere in the run) -> reduction_p50 == 0.0 exactly, and
    the view-model's zero-value classifier reports _META_NO_PARTNER_REASON
    (has_co_present_lec is derived from the run's OTHER controls -- there are
    none here, unlike the shared-run case above)."""
    _small_ensemble_env(monkeypatch)

    meta_control = await _seed_meta_control(
        db_session, seed_control_factory, seed_organization, name="sat-meta-alone"
    )
    scenario = await seed_scenario_factory(
        name="meta-alone-scenario", effect=ScenarioEffect.AVAILABILITY
    )

    run = await seed_run_factory(scenario=scenario, mc_iterations=200, random_seed=101)
    run.control_ids_used = [str(meta_control.id)]
    db_session.add(run)
    await db_session.commit()

    await execute_run(run.id)
    await db_session.refresh(run)

    assert run.status == RunStatus.COMPLETED, run.error_message
    assert run.weight_robustness is not None

    meta_cell = run.weight_robustness["per_control"][str(meta_control.id)]
    assert meta_cell["reduction_p50"] == 0.0

    view_model = build_display_results(run)
    assert view_model is not None
    vm_cell = view_model["weight_robustness"]["per_control"][str(meta_control.id)]
    assert vm_cell["zero_reason"] == _META_NO_PARTNER_REASON
