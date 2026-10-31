"""#299 cross-layer contract: form -> service -> executor -> fair_cam.

Equivalence is asserted on the SAME scenario with the SAME seed across two
SINGLE runs (inherit vs. explicit meta twin): identical spawn index, identical
adjusted params -> bit-identical results and sample arrays. Cross-scenario
numbers are never compared (independent seed streams). The hand-math anchor
lives in fair_cam/tests/risk_engine/test_inherited_meta_equivalence_pin.py and
tests/contracts/test_inherited_variant_adapter.py."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.control import Control
from idraa.models.control_function_assignment import ControlFunctionAssignment
from idraa.models.enums import ControlDomain, FairCamSubFunction
from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus
from idraa.models.run_samples import RunSamples
from idraa.models.scenario_control import ScenarioControl
from idraa.services.inherited_controls import variant_id
from idraa.services.runs import RunService
from idraa.services.sample_codec import decode_sample_arrays


async def _run(
    db: AsyncSession,
    org: Any,
    user: Any,
    scenario_ids: list[uuid.UUID],
    seed: int = 7,
) -> RiskAnalysisRun:
    run = await RunService(db).create_and_dispatch(
        organization_id=org.id,
        scenario_ids=scenario_ids,
        mc_iterations_override=300,
        created_by=user.id,
        background_tasks=BackgroundTasks(),
        random_seed=seed,
    )
    await db.refresh(run)
    assert run.status == RunStatus.COMPLETED, run.error_message
    return run


def _ps(run: RiskAnalysisRun, sid: uuid.UUID) -> dict[str, Any]:
    assert run.simulation_results is not None
    return next(p for p in run.simulation_results["per_scenario"] if p["scenario_id"] == str(sid))


def _adj(payload: dict[str, Any], cid: str) -> dict[str, Any]:
    return next(a for a in payload["control_adjustments"] if a["control_id"] == cid)


async def _samples(db: AsyncSession, run_id: uuid.UUID) -> Any:
    s = (await db.execute(select(RunSamples).where(RunSamples.run_id == run_id))).scalar_one()
    assert s.arrays_codec is not None
    return decode_sample_arrays(s.arrays_codec)


def _lec_row(cid: uuid.UUID) -> ControlFunctionAssignment:
    return ControlFunctionAssignment(
        control_id=cid,
        sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
        capability_value=0.9,
        coverage=1.0,
        reliability=1.0,
    )


async def _hybrid(
    db: AsyncSession,
    seed_control_factory: Any,
    seed_organization: Any,
    name: str = "Patch Mgmt",
) -> Control:
    """One VMC row (from the factory) + one LEC row."""
    pm: Control = await seed_control_factory(
        name=name,
        domain=ControlDomain.VARIANCE_MANAGEMENT,
        sub_function=FairCamSubFunction.VMC_PREV_REDUCE_VARIANCE_PROB,
    )
    row = _lec_row(pm.id)
    row.organization_id = seed_organization.id  # NOT NULL (Meth-I6)
    db.add(row)
    await db.commit()
    return pm


@pytest.mark.asyncio
async def test_inherited_equals_explicit_meta_twin_same_scenario_same_seed(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_user: Any,
    seed_control_factory: Any,
    seed_scenario_factory: Any,
    wire_executor_to_test_db: Any,
) -> None:
    edr = await seed_control_factory(name="EDR", domain=ControlDomain.LOSS_EVENT, reliability=0.8)
    pm = await _hybrid(db_session, seed_control_factory, seed_organization)
    twin = await seed_control_factory(
        name="Patch Mgmt META twin",
        domain=ControlDomain.VARIANCE_MANAGEMENT,
        sub_function=FairCamSubFunction.VMC_PREV_REDUCE_VARIANCE_PROB,
    )
    s = await seed_scenario_factory(name="S")
    db_session.add(ScenarioControl(scenario_id=s.id, control_id=edr.id))
    await db_session.commit()

    # Run X: pm org-wide, S links only EDR -> S inherits pm:meta
    pm.org_wide_meta = True
    await db_session.commit()
    x = await _run(db_session, seed_organization, seed_user, [s.id])
    assert x.inherited_control_ids_per_scenario == {str(s.id): [str(pm.id)]}
    ids = [c["control_id"] for c in x.controls_snapshot]
    assert ids == [str(edr.id), variant_id(str(pm.id))]  # no plain pm row (spec §5.1)
    var = x.controls_snapshot[1]
    assert var["inherited_variant_of"] == str(pm.id)
    assert {a["sub_function"] for a in var["assignments"]} == {"vmc_prev_reduce_variance_prob"}
    # METH-T7-I1: the workbook's SINGLE call site keys on str(run.scenario_id); a
    # key miss against the frozen map would silently drop the variant row.
    from idraa.services.verification_workbook import _per_scenario_controls_snapshot

    assert str(x.scenario_id) == str(s.id)
    wb_x = _per_scenario_controls_snapshot(x, x.controls_snapshot, str(x.scenario_id))
    assert {c["control_id"] for c in wb_x} == {str(edr.id), variant_id(str(pm.id))}

    # Run Y: nothing org-wide, S links EDR + the meta-only twin
    pm.org_wide_meta = False
    db_session.add(ScenarioControl(scenario_id=s.id, control_id=twin.id))
    await db_session.commit()
    # The session is expire_on_commit=False and run X's create already loaded
    # s.mitigating_controls, so the new link row is invisible until the collection
    # is reloaded (without this run Y silently executes WITHOUT the twin).
    await db_session.refresh(s, ["mitigating_controls"])
    y = await _run(db_session, seed_organization, seed_user, [s.id])
    assert y.inherited_control_ids_per_scenario == {str(s.id): []}

    # bit-identity: same scenario, same seed (spawn index 0), same adjusted params
    assert x.simulation_results is not None and y.simulation_results is not None
    assert x.simulation_results["residual_risk"] == y.simulation_results["residual_risk"]
    assert await _samples(db_session, x.id) == await _samples(db_session, y.id)
    xv = _adj(x.simulation_results, variant_id(str(pm.id)))
    yt = _adj(y.simulation_results, str(twin.id))
    for k in (
        "tef_multiplier",
        "vulnerability_multiplier",
        "primary_loss_multiplier",
        "secondary_loss_multiplier",
        "effectiveness",
        "risk_reduction_value",
    ):
        assert xv[k] == yt[k], k
    assert all(not row["sub_function"].startswith("lec_") for row in xv["breakdown"])
    xe, ye = _adj(x.simulation_results, str(edr.id)), _adj(y.simulation_results, str(edr.id))
    assert xe["tef_multiplier"] == ye["tef_multiplier"]
    assert xe["vulnerability_multiplier"] == ye["vulnerability_multiplier"]


@pytest.mark.asyncio
async def test_inherited_everywhere_linked_nowhere_completes_with_variant_rows_only(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_user: Any,
    seed_control_factory: Any,
    seed_scenario_factory: Any,
    wire_executor_to_test_db: Any,
) -> None:
    """Review-focus #6 / Meth-B2 / Arch-B1: fair_cam's union-equality check passes
    because the universe is explicit ids + variant ids, never the inherited plain id."""
    edr = await seed_control_factory(name="EDR", domain=ControlDomain.LOSS_EVENT)
    pm = await _hybrid(db_session, seed_control_factory, seed_organization)
    pm.org_wide_meta = True
    s1 = await seed_scenario_factory(name="S1")
    s2 = await seed_scenario_factory(name="S2")
    db_session.add_all(
        [
            ScenarioControl(scenario_id=s1.id, control_id=edr.id),
            ScenarioControl(scenario_id=s2.id, control_id=edr.id),
        ]
    )
    await db_session.commit()
    run = await _run(db_session, seed_organization, seed_user, [s1.id, s2.id])
    assert str(pm.id) in run.control_ids_used  # fetched for the variant
    ids = [c["control_id"] for c in run.controls_snapshot]
    assert ids.count(str(pm.id)) == 0 and ids.count(variant_id(str(pm.id))) == 1
    for sid in (s1.id, s2.id):
        adj_ids = {a["control_id"] for a in _ps(run, sid)["control_adjustments"]}
        assert adj_ids == {str(edr.id), variant_id(str(pm.id))}
        assert "shapley_value" in _adj(_ps(run, sid), variant_id(str(pm.id)))  # attribution player
    assert run.simulation_results is not None
    assert run.simulation_results["cost_summary"]["n_unique_controls"] == 2


@pytest.mark.asyncio
async def test_explicit_link_supersedes_inheritance_cost_once_two_attribution_rows(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_user: Any,
    seed_control_factory: Any,
    seed_scenario_factory: Any,
    wire_executor_to_test_db: Any,
) -> None:
    """Review-focus #3 / spec D7 / D8 / M7: hybrid linked to A, inherited by B."""
    from decimal import Decimal

    edr = await seed_control_factory(name="EDR", domain=ControlDomain.LOSS_EVENT)
    pm = await _hybrid(db_session, seed_control_factory, seed_organization)
    edr.annual_cost = Decimal("1000")
    pm.annual_cost = Decimal("5000")
    pm.org_wide_meta = True
    s_a = await seed_scenario_factory(name="A links pm")
    s_b = await seed_scenario_factory(name="B inherits pm")
    db_session.add_all(
        [
            ScenarioControl(scenario_id=s_a.id, control_id=edr.id),
            ScenarioControl(scenario_id=s_a.id, control_id=pm.id),
            ScenarioControl(scenario_id=s_b.id, control_id=edr.id),
        ]
    )
    await db_session.commit()
    run = await _run(db_session, seed_organization, seed_user, [s_a.id, s_b.id])
    assert run.inherited_control_ids_per_scenario == {str(s_a.id): [], str(s_b.id): [str(pm.id)]}
    assert run.control_ids_used.count(str(pm.id)) == 1
    ids = [c["control_id"] for c in run.controls_snapshot]
    assert ids.count(str(pm.id)) == 1 and ids.count(variant_id(str(pm.id))) == 1
    a_ids = {adj["control_id"] for adj in _ps(run, s_a.id)["control_adjustments"]}
    b_ids = {adj["control_id"] for adj in _ps(run, s_b.id)["control_adjustments"]}
    assert a_ids == {str(edr.id), str(pm.id)}
    assert b_ids == {str(edr.id), variant_id(str(pm.id))}
    # A's pm row DOES carry its LEC contribution (full control)
    assert any(
        r["sub_function"] == "lec_prev_resistance"
        for r in _adj(_ps(run, s_a.id), str(pm.id))["breakdown"]
    )
    # D7: cost counted once by base id
    assert run.simulation_results is not None
    cs = run.simulation_results["cost_summary"]
    assert cs["total_annual_cost"] == pytest.approx(6000.0)
    assert cs["n_unique_controls"] == 2
    # D8 / M7: two labelled players, not merged, not double-counted
    assert _adj(_ps(run, s_a.id), str(pm.id))["control_name"] == "Patch Mgmt"
    assert _adj(_ps(run, s_b.id), variant_id(str(pm.id)))["control_name"] == "Patch Mgmt (org-wide)"
    # Efficiency Σφ = v(N) holds on the CLOSED-FORM attribution game: `shapley_value`
    # is the typical-basis closed form (run_executor._make_subset_value_fn,
    # statistic="typical", over fair_cam control_attribution.subset_reduction_closed_form)
    # — NOT the Monte-Carlo base − residual ALE, which is a different statistic on a
    # different basis (typical-basis closed-form base 50,000 vs mean-basis closed-form
    # base ≈ 138,944 — `scenario_base_ale(rp, "mean")`, the figure the MC mean headline
    # approximates, not an MC statistic — on this seed scenario; Meth-B4 / Meth-N7).
    # v(N) is computed here by calling fair_cam — no re-derivation.
    from fair_cam.risk_engine.control_attribution import subset_reduction_closed_form

    from idraa.services.run_executor import _scenario_to_fair_parameters, _v3_to_fair_cam_control

    for ctrl in (edr, pm):
        await db_session.refresh(ctrl, ["assignments"])  # lazy="selectin" rows, loaded explicitly
    for s in (s_a, s_b):
        await db_session.refresh(s)
    edr_dto = _v3_to_fair_cam_control(edr)
    pm_full, pm_meta = _v3_to_fair_cam_control(pm), _v3_to_fair_cam_control(pm, meta_only=True)
    rp_a, rp_b = _scenario_to_fair_parameters(s_a), _scenario_to_fair_parameters(s_b)
    # factory scenarios are not availability-effect -> default availability_self_detection=False
    v_a = subset_reduction_closed_form(rp_a, [edr_dto, pm_full])
    v_b = subset_reduction_closed_form(rp_b, [edr_dto, pm_meta])
    v_b_edr_only = subset_reduction_closed_form(rp_b, [edr_dto])
    for sid, v_n in ((s_a.id, v_a), (s_b.id, v_b)):
        adjs = _ps(run, sid)["control_adjustments"]
        assert all("shapley_value" in a for a in adjs)  # two players: never skipped
        assert sum(a["shapley_value"] for a in adjs) == pytest.approx(v_n, rel=1e-9)
    # Two-player anchor on B, hand math (plan-gate r2 methodology §3) vs engine:
    #   typical base ALE = 0.5 * 0.4 * 250_000 = 50_000 (factory PERT modes, no secondary loss)
    #   v({edr}) = 32_303.264 ; v(N) = 33_303.0297 (EDR lec_prev_resistance 0.7/0.8/0.85 and
    #   pm vmc_prev_reduce_variance_prob 0.7/0.8/0.85 are the seed_control_factory defaults)
    #   phi_meta = 0.5 * (v(N) - v({edr})) = 499.88  -> absent != 0
    phi_meta = _adj(_ps(run, s_b.id), variant_id(str(pm.id)))["shapley_value"]
    assert phi_meta == pytest.approx(0.5 * (v_b - v_b_edr_only), rel=1e-9)
    assert phi_meta == pytest.approx(499.88, abs=0.01)
    assert phi_meta > 0.0
    # METH-T7-I1: the verification workbook recomposes from the SAME per-scenario
    # subset the executor composed. A scenario-key miss between the freeze
    # (runs.py str(s.id)) and the workbook silently drops the ":meta" row, so pin
    # the exact id sets through the real freeze + executor + workbook chain.
    from idraa.services.verification_workbook import _per_scenario_controls_snapshot

    wb_a = _per_scenario_controls_snapshot(run, run.controls_snapshot, str(s_a.id))
    wb_b = _per_scenario_controls_snapshot(run, run.controls_snapshot, str(s_b.id))
    assert {c["control_id"] for c in wb_a} == {str(edr.id), str(pm.id)}
    assert {c["control_id"] for c in wb_b} == {str(edr.id), variant_id(str(pm.id))}


@pytest.mark.asyncio
async def test_variant_alone_composes_node_neutral_without_raising(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_user: Any,
    seed_control_factory: Any,
    seed_scenario_factory: Any,
    wire_executor_to_test_db: Any,
) -> None:
    """A scenario that links NO control and inherits one org-wide meta control: the
    variant is the only registered control and there is no LEC channel for its
    reliability coupling to uplift, so it composes to node-neutral multipliers
    (spec §5.2) and the run still completes."""
    pm = await _hybrid(db_session, seed_control_factory, seed_organization)
    pm.org_wide_meta = True
    s = await seed_scenario_factory(name="S")
    await db_session.commit()
    run = await _run(db_session, seed_organization, seed_user, [s.id])
    assert run.inherited_control_ids_per_scenario == {str(s.id): [str(pm.id)]}
    assert [c["control_id"] for c in run.controls_snapshot] == [variant_id(str(pm.id))]
    assert run.simulation_results is not None
    adj = _adj(run.simulation_results, variant_id(str(pm.id)))
    for k in (
        "tef_multiplier",
        "vulnerability_multiplier",
        "primary_loss_multiplier",
        "secondary_loss_multiplier",
    ):
        assert adj[k] == pytest.approx(1.0, rel=1e-12), k


async def _requeue(db: AsyncSession, run: RiskAnalysisRun) -> None:
    """Reset a COMPLETED run to QUEUED for a second execute_run (precedent:
    tests/integration/test_weight_robustness_reproducibility.py:106-113). The
    run_samples row is 1:1 keyed on run_id (models/run_samples.py:26-29) and the
    executor inserts it unconditionally on success (the RunSamples insert after the guarded COMPLETED flip), so
    it MUST be deleted first or the re-execution dies on the PK (SC-I16)."""
    samples = (
        await db.execute(select(RunSamples).where(RunSamples.run_id == run.id))
    ).scalar_one_or_none()
    if samples is not None:
        await db.delete(samples)
    run.status = RunStatus.QUEUED
    run.simulation_results = None
    run.controls_snapshot = []
    run.error_message = None
    run.started_at = None
    run.completed_at = None
    db.add(run)
    await db.commit()


@pytest.mark.asyncio
async def test_pre_feature_run_row_executes_without_inheritance(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_user: Any,
    seed_control_factory: Any,
    seed_scenario_factory: Any,
    wire_executor_to_test_db: Any,
) -> None:
    """Review-focus #5: a QUEUED row with the column NULL (legacy) runs as before
    even though the org now has an org-wide control."""
    from idraa.services.run_executor import execute_run

    edr = await seed_control_factory(name="EDR", domain=ControlDomain.LOSS_EVENT)
    pm = await seed_control_factory(name="PM", domain=ControlDomain.VARIANCE_MANAGEMENT)
    pm.org_wide_meta = True
    s = await seed_scenario_factory(name="S")
    db_session.add(ScenarioControl(scenario_id=s.id, control_id=edr.id))
    await db_session.commit()
    run = await _run(db_session, seed_organization, seed_user, [s.id])
    # simulate a legacy row: column NULL, control_ids_used holds ONLY linked ids (SC-N6)
    run.inherited_control_ids_per_scenario = None
    run.control_ids_used = [str(edr.id)]
    await _requeue(db_session, run)
    await execute_run(run.id)
    await db_session.refresh(run)
    assert run.status == RunStatus.COMPLETED, run.error_message
    assert [c["control_id"] for c in run.controls_snapshot] == [str(edr.id)]


@pytest.mark.asyncio
async def test_variant_that_cannot_be_built_fails_the_run_loud(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_user: Any,
    seed_control_factory: Any,
    seed_scenario_factory: Any,
    wire_executor_to_test_db: Any,
) -> None:
    """Arch-I8 / spec §5.2 "fail loud": the plan is built in executor phase 1,
    OUTSIDE the try that flips a run to FAILED (the main `try:` after phase 1). A QUEUED
    run whose inherited control lost its meta rows at the DB level after the
    freeze must still reach FAILED with a risk_analysis_run.fail audit row —
    never strand QUEUED for the reaper. Arch-I12 / #82: the UI-facing
    error_message is the fixed generic _RUN_FAILURE_MESSAGE (_status_poll.html:48
    renders it verbatim; the raw ValueError text carries a control UUID and
    "eligibility invariant violated upstream"); the cause is recorded
    server-side only — error_class in the audit row, full text in the log."""
    from sqlalchemy import delete

    from idraa.models.audit_log import AuditLog
    from idraa.services.run_executor import _RUN_FAILURE_MESSAGE, execute_run

    edr = await seed_control_factory(name="EDR", domain=ControlDomain.LOSS_EVENT)
    pm = await _hybrid(db_session, seed_control_factory, seed_organization)  # VMC + LEC rows
    pm.org_wide_meta = True
    s = await seed_scenario_factory(name="S")
    db_session.add(ScenarioControl(scenario_id=s.id, control_id=edr.id))
    await db_session.commit()
    run = await _run(db_session, seed_organization, seed_user, [s.id])
    assert run.inherited_control_ids_per_scenario == {str(s.id): [str(pm.id)]}
    await _requeue(db_session, run)
    # strip the meta row underneath the frozen map: pm is now LEC-only -> meta_assignments() raises
    await db_session.execute(
        delete(ControlFunctionAssignment).where(
            ControlFunctionAssignment.control_id == pm.id,
            ControlFunctionAssignment.sub_function
            == FairCamSubFunction.VMC_PREV_REDUCE_VARIANCE_PROB,
        )
    )
    await db_session.commit()
    await execute_run(run.id)
    await db_session.refresh(run)
    assert run.status == RunStatus.FAILED
    # Arch-I12 / #82: generic message to the UI (mirrors tests/unit/test_run_executor.py:356-359
    # and tests/integration/test_weight_robustness_reproducibility.py:333-360) ...
    assert run.error_message == _RUN_FAILURE_MESSAGE
    assert "inheritable" not in run.error_message and "ValueError" not in run.error_message
    assert run.completed_at is not None
    rows = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.action == "risk_analysis_run.fail", AuditLog.entity_id == run.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].changes["status"] == ["queued", "failed"]
    # ... and the cause lives in the audit row: error_class is a SCALAR string, the
    # same shape as the executor's own fail row (the `risk_analysis_run.fail` audit in the except branch;
    # tests/integration/test_run_executor_codec_overflow.py:97), plus a fixed,
    # non-exception stage discriminator.
    assert rows[0].changes["error_class"] == "ValueError"
    assert rows[0].changes["stage"] == "inherited_control_plan"
