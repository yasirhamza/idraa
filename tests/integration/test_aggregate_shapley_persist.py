"""An AGGREGATE run persists a shapley_value on each control_adjustment; per-scenario
Σφ == v(N) (efficiency); over-cap + budget + non-finite + error scenarios degrade gracefully."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from fair_cam.risk_engine.control_attribution import subset_reduction_closed_form
from fair_cam.risk_engine.native_control_aware import NativeControlAwareRiskCalculator
from fair_cam.tests.risk_engine._helpers import make_control, make_fair_parameters
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.audit_log import AuditLog
from idraa.models.risk_analysis_run import RunStatus
from idraa.services.run_executor import (
    _compute_shapley_by_scenario,
    _inject_shapley,
    _sanitize_shapley,
)


def _ctrl(cid, cap=0.5):
    return make_control(control_id=cid, assignments=[("lec_prev_resistance", "probability", cap)])


def _fp():
    return make_fair_parameters(tef=2.0, vuln=0.5, primary=1_000_000, secondary=400_000)


def test_compute_shapley_by_scenario_efficiency():
    """Σ shapley_value over a scenario's controls == v(full set) (N>=3, B-Spec-N3)."""
    a, b, c = _ctrl("a", 0.5), _ctrl("b", 0.4), _ctrl("c", 0.3)
    calc = NativeControlAwareRiskCalculator(controls=[a, b, c])
    fp = _fp()
    by_scenario, skipped = _compute_shapley_by_scenario(
        calc,
        per_scenario_inputs=[("s1", "Scenario 1", fp)],
        per_scenario_control_ids={"s1": ["a", "b", "c"]},
        universe_control_ids=["a", "b", "c"],
    )
    assert skipped == []
    sv = by_scenario["s1"]
    assert sum(sv.values()) == pytest.approx(subset_reduction_closed_form(fp, [a, b, c]))
    assert all(sv[k] > 0 for k in ("a", "b", "c"))


def test_full_universe_fallback_when_per_scenario_map_is_none():
    """Legacy AGGREGATE rows (None map) attribute over the full universe — mirrors the engine."""
    a, b = _ctrl("a", 0.5), _ctrl("b", 0.4)
    calc = NativeControlAwareRiskCalculator(controls=[a, b])
    fp = _fp()
    by_scenario, skipped = _compute_shapley_by_scenario(calc, [("s1", "S1", fp)], None, ["a", "b"])
    assert skipped == []
    assert set(by_scenario["s1"]) == {"a", "b"}


def test_over_cap_scenario_is_skipped_with_reason():
    """A scenario above the control cap is returned in `skipped`, not computed (B-Sec-B1)."""
    ctrls = [_ctrl(f"c{i}") for i in range(5)]
    calc = NativeControlAwareRiskCalculator(controls=ctrls)
    by_scenario, skipped = _compute_shapley_by_scenario(
        calc,
        per_scenario_inputs=[("s1", "S1", _fp())],
        per_scenario_control_ids={"s1": [f"c{i}" for i in range(5)]},
        universe_control_ids=[f"c{i}" for i in range(5)],
        max_controls=3,
    )
    assert skipped == [("s1", "over_cap")]
    assert "s1" not in by_scenario


def test_total_eval_budget_skips_remaining_scenarios():
    """Once the global eval budget is exhausted, remaining scenarios skip (B-Sec-I1-r2)."""
    a, b = _ctrl("a", 0.5), _ctrl("b", 0.4)
    calc = NativeControlAwareRiskCalculator(controls=[a, b])
    fp = _fp()
    # budget of 4 evals: scenario 1 (n=2 -> 2^2=4) fits; scenario 2 would exceed.
    by_scenario, skipped = _compute_shapley_by_scenario(
        calc,
        per_scenario_inputs=[("s1", "S1", fp), ("s2", "S2", fp)],
        per_scenario_control_ids={"s1": ["a", "b"], "s2": ["a", "b"]},
        universe_control_ids=["a", "b"],
        total_eval_budget=4,
    )
    assert "s1" in by_scenario
    assert skipped == [("s2", "over_budget")]


def test_value_fn_error_degrades_scenario_not_raises(monkeypatch):
    """A per-scenario compute error degrades that scenario (reason 'error'), never raises (B-Arch-N3)."""
    a = _ctrl("a", 0.5)
    calc = NativeControlAwareRiskCalculator(controls=[a])

    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    # NOTE: shapley_values is imported INSIDE _compute_shapley_by_scenario — patch
    # it at its source module so the local import picks up the patch:
    monkeypatch.setattr("idraa.services.shapley.shapley_values", _boom)
    by_scenario, skipped = _compute_shapley_by_scenario(
        calc, [("s1", "S1", _fp())], {"s1": ["a"]}, ["a"]
    )
    assert skipped == [("s1", "error")]
    assert "s1" not in by_scenario


def test_sanitize_drops_non_finite_scenario_does_not_raise():
    """Degrade-not-fail (B-Arch-I4/B-Sec-I1): non-finite scenario dropped, others kept."""
    clean, dropped = _sanitize_shapley({"s1": {"a": 30.0, "b": 40.0}, "s2": {"a": float("nan")}})
    assert dropped == ["s2"]
    assert clean == {"s1": {"a": 30.0, "b": 40.0}}


def test_inject_writes_present_skips_absent():
    """shapley_value injected for scenarios in the map; absent scenarios get NO key
    (-> view-model renders 'unavailable'), N>=3 controls all survive (B-Spec-N3)."""
    per_scenario = [
        {
            "scenario_id": "s1",
            "control_adjustments": [{"control_id": "a"}, {"control_id": "b"}, {"control_id": "c"}],
        },
        {"scenario_id": "s2", "control_adjustments": [{"control_id": "a"}]},  # absent from map
    ]
    _inject_shapley(per_scenario, {"s1": {"a": 26.0, "b": 34.5, "c": 18.5}})
    s1 = {adj["control_id"]: adj["shapley_value"] for adj in per_scenario[0]["control_adjustments"]}
    assert s1 == {"a": pytest.approx(26.0), "b": pytest.approx(34.5), "c": pytest.approx(18.5)}
    assert "shapley_value" not in per_scenario[1]["control_adjustments"][0]


def test_shapley_efficiency_holds_under_binding_currency_clamp():
    """Σφ == v(N) exactly even when the currency floor binds (B-Spec-I1)."""
    a = _ctrl("a", 0.5)
    ins = make_control(
        control_id="ins", assignments=[("lec_resp_loss_reduction", "currency", 300_000.0)]
    )
    calc = NativeControlAwareRiskCalculator(controls=[a, ins])
    fp = make_fair_parameters(
        tef=2.0, vuln=0.5, primary=1_000_000, secondary=100_000
    )  # 300k > 100k -> binds
    by_scenario, _ = _compute_shapley_by_scenario(
        calc, [("s1", "S1", fp)], {"s1": ["a", "ins"]}, ["a", "ins"]
    )
    sv = by_scenario["s1"]
    assert sum(sv.values()) == pytest.approx(subset_reduction_closed_form(fp, [a, ins]))


# ---- End-to-end: AGGREGATE execute_run persists shapley_value on every control_adjustment ----


@pytest.mark.asyncio
async def test_aggregate_execute_run_persists_shapley_values(
    db_session: AsyncSession,
    seed_aggregate_run_factory: Any,
) -> None:
    """B-Arch-N2: real AGGREGATE execute_run persists shapley_value on every
    per-scenario control_adjustment and ends COMPLETED."""
    from idraa.services.run_executor import execute_run

    # Use n_controls=1 (matches the factory default) — the single control maps to
    # every scenario via the full-universe fallback (aggregate_control_ids_per_scenario=None).
    # n_simulations=500 keeps the test fast.
    run = await seed_aggregate_run_factory(n_scenarios=2, n_controls=1, n_simulations=500)
    await execute_run(run.id)
    await db_session.refresh(run)

    assert run.status == RunStatus.COMPLETED
    per_scenario = run.simulation_results["per_scenario"]
    assert len(per_scenario) >= 2
    for ps in per_scenario:
        assert ps["control_adjustments"], "fixture must attach controls to every scenario"
        for adj in ps["control_adjustments"]:
            assert "shapley_value" in adj, f"shapley_value missing from adj: {adj}"
            assert isinstance(adj["shapley_value"], float)


# ---- Arch-I1: AGGREGATE TOCTOU — lost-race rollback discards Shapley audit rows ----


@pytest.mark.asyncio
async def test_aggregate_cancel_during_complete_window_rolls_back_shapley_audit(
    db_session: AsyncSession,
    seed_aggregate_run_factory: Callable[..., Awaitable[Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Arch-I1: CANCELLED committed between the post-Shapley cancel-check and
    the guarded terminal UPDATE must survive; the flushed Shapley audit rows
    (run.shapley_skipped) must be rolled back with the lost-race flip.

    Classic TOCTOU seam: the executor's own SELECT (inside each
    _check_cancelled_or_continue call) sees RUNNING, but by the time the
    terminal UPDATE ... WHERE status=RUNNING fires, the row is already CANCELLED
    in the database (committed by another actor). rowcount==0 → the fix path
    must ROLLBACK the pending transaction (which includes the flushed Shapley
    audit rows from the AGGREGATE branch), not commit it.

    Call sequence for a 2-scenario AGGREGATE run:
      call 1 — :977 initial check after QUEUED→RUNNING
      call 2 — :1018 per-scenario loop (scenario 0)
      call 3 — :1018 per-scenario loop (scenario 1)
      call 4 — :1023 after discriminator branch
      call 5 — :1099 after calculate_aggregate_enhanced_risk  ← inject CANCEL here
      call 6 — :1172 last check before terminal flip (post-Shapley)  ← lie True here

    We commit CANCELLED at call #5 (executor has no open write transaction at
    that point — the prior `:975` commit released the lock). Then we lie True on
    calls #5 and #6 so the executor proceeds through the Shapley audit flush and
    reaches the terminal UPDATE, which finds rowcount==0.

    We patch _compute_shapley_by_scenario directly (not MAX_ATTRIBUTION_CONTROLS,
    which is a default-argument constant captured at function definition time).
    """
    import idraa.services.run_executor as rex
    from idraa.services.runs import RunService

    run = await seed_aggregate_run_factory(n_scenarios=2, n_controls=1, n_simulations=200)
    run_id = run.id
    org_id = run.organization_id
    user_id = run.created_by

    # Force shapley_skipped audit rows so the AGGREGATE branch flushes them
    # into the executor's pending transaction before the terminal UPDATE.
    def _forced_skip(calc, per_scenario_inputs, per_scenario_dict, universe_ids, **kwargs):
        skipped = [(sid, "over_cap") for sid, _name, _fp in per_scenario_inputs]
        return ({}, skipped)

    real_check = rex._check_cancelled_or_continue
    state = {"calls": 0}

    async def _check_cancel_on_5_lie_on_6(session: AsyncSession, rid: uuid.UUID) -> bool:
        state["calls"] += 1
        if state["calls"] == 5:
            # Call 5 fires at :1099 — the executor has no open write transaction
            # (the prior :975 QUEUED→RUNNING commit released the lock). Commit
            # CANCELLED via db_session, then lie True so the executor proceeds.
            await RunService(db_session).cancel(
                organization_id=org_id,
                run_id=rid,
                cancelled_by=user_id,
            )
            return True  # lie — executor doesn't know about the committed CANCEL
        if state["calls"] == 6:
            # Call 6 fires at :1172 — post-Shapley-flush, executor holds a write
            # lock. Lie True again so it reaches the terminal UPDATE which will
            # find rowcount==0 (DB is already CANCELLED).
            return True
        return await real_check(session, rid)

    monkeypatch.setattr(rex, "_compute_shapley_by_scenario", _forced_skip)
    monkeypatch.setattr(rex, "_check_cancelled_or_continue", _check_cancel_on_5_lie_on_6)

    from idraa.services.run_executor import execute_run

    await execute_run(run_id)

    from idraa.models.risk_analysis_run import RiskAnalysisRun

    refreshed = (
        await db_session.execute(
            select(RiskAnalysisRun)
            .where(RiskAnalysisRun.id == run_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert refreshed.status == RunStatus.CANCELLED, (
        f"cancel was overwritten by the complete flip: status={refreshed.status}"
    )

    # The flushed Shapley audit rows must have been rolled back — none should exist.
    shapley_audit = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.entity_id == run_id,
                    AuditLog.action.in_(["run.shapley_skipped", "run.non_finite_shapley"]),
                )
            )
        )
        .scalars()
        .all()
    )
    assert shapley_audit == [], (
        f"Shapley audit rows survived a rolled-back lost-race flip: {shapley_audit}"
    )

    # No complete audit row should have been written.
    complete_audit = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.entity_id == run_id,
                    AuditLog.action == "risk_analysis_run.complete",
                )
            )
        )
        .scalars()
        .all()
    )
    assert complete_audit == []


# ---- Spec-N-1: happy degrade path — over-cap audit row committed with COMPLETED ----


@pytest.mark.asyncio
async def test_aggregate_degrade_audit_row_committed_with_completed(
    db_session: AsyncSession,
    seed_aggregate_run_factory: Callable[..., Awaitable[Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spec-N-1: when a scenario is over-cap (no race), execute_run completes
    with status COMPLETED and the run.shapley_skipped audit row IS committed
    alongside the terminal COMPLETED flip.

    Degrade path: patch _compute_shapley_by_scenario to return over-cap skips
    for every scenario. The run still completes; the audit rows ride the terminal
    transaction (atomicity contract).

    MAX_ATTRIBUTION_CONTROLS is a default-argument constant captured at function
    definition time, so we patch _compute_shapley_by_scenario directly.
    """
    import idraa.services.run_executor as rex

    run = await seed_aggregate_run_factory(n_scenarios=2, n_controls=1, n_simulations=200)
    run_id = run.id

    def _forced_skip(calc, per_scenario_inputs, per_scenario_dict, universe_ids, **kwargs):
        skipped = [(sid, "over_cap") for sid, _name, _fp in per_scenario_inputs]
        return ({}, skipped)

    monkeypatch.setattr(rex, "_compute_shapley_by_scenario", _forced_skip)

    from idraa.services.run_executor import execute_run

    await execute_run(run_id)

    await db_session.refresh(run)
    assert run.status == RunStatus.COMPLETED

    # Every scenario was over-cap → each scenario produced a shapley_skipped audit row.
    shapley_skipped = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.entity_id == run_id,
                    AuditLog.action == "run.shapley_skipped",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(shapley_skipped) >= 1, (
        "expected >=1 run.shapley_skipped audit rows for the over-cap degrade path"
    )
    # Each audit row must carry the scenario_id and reason.
    for row in shapley_skipped:
        assert row.changes is not None
        assert row.changes.get("reason") == [None, "over_cap"]
        assert row.changes.get("scenario_id") is not None

    # control_adjustments must carry NO shapley_value (skipped scenarios are absent from map).
    per_scenario = run.simulation_results["per_scenario"]
    for ps in per_scenario:
        for adj in ps.get("control_adjustments", []):
            assert "shapley_value" not in adj, (
                f"over-cap scenario must NOT have shapley_value injected: {adj}"
            )


# ---- LOO-Meth-2: leave-one-out degradations get the same first-class audit ----


@pytest.mark.asyncio
async def test_aggregate_loo_degrade_audit_row_committed_with_completed(
    db_session: AsyncSession,
    seed_aggregate_run_factory: Callable[..., Awaitable[Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LOO-Meth-2: a degraded leave-one-out pass is user-visible (a '—' cell or
    a partial aggregate sum), so it must leave a durable run.loo_skipped audit
    row in the terminal transaction — parity with run.shapley_skipped, not just
    a log line.

    Degrade path: patch _compute_loo_by_scenario to report an error skip for
    every scenario (the only trigger reachable in production — the eval budget
    is unreachable at linear cost). The run still completes.
    """
    import idraa.services.run_executor as rex

    run = await seed_aggregate_run_factory(n_scenarios=2, n_controls=1, n_simulations=200)
    run_id = run.id

    def _forced_loo_skip(calc, per_scenario_inputs, per_scenario_dict, universe_ids, **kwargs):
        skipped = [(sid, "error") for sid, _name, _fp in per_scenario_inputs]
        return ({}, skipped)

    monkeypatch.setattr(rex, "_compute_loo_by_scenario", _forced_loo_skip)

    from idraa.services.run_executor import execute_run

    await execute_run(run_id)

    await db_session.refresh(run)
    assert run.status == RunStatus.COMPLETED

    loo_skipped = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.entity_id == run_id,
                    AuditLog.action == "run.loo_skipped",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(loo_skipped) >= 1, (
        "expected >=1 run.loo_skipped audit rows for the degraded leave-one-out path"
    )
    for row in loo_skipped:
        assert row.changes is not None
        assert row.changes.get("reason") == [None, "error"]
        assert row.changes.get("scenario_id") is not None
    # The Shapley pass itself was healthy — degraded LOO must NOT masquerade
    # as a Shapley degradation (distinct audit actions).
    shapley_rows = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.entity_id == run_id,
                    AuditLog.action.in_(["run.shapley_skipped", "run.non_finite_shapley"]),
                )
            )
        )
        .scalars()
        .all()
    )
    assert shapley_rows == []

    # LOO was skipped everywhere → no adjustment may carry if_removed_value
    # (absent≠0.0 convention: skipped scenarios are absent from the map).
    per_scenario = run.simulation_results["per_scenario"]
    for ps in per_scenario:
        for adj in ps.get("control_adjustments", []):
            assert "if_removed_value" not in adj, (
                f"skipped-LOO scenario must NOT have if_removed_value injected: {adj}"
            )


# ---- Arch-new-1: AGGREGATE FAIL-path TOCTOU — lost-race rollback on FAIL path ----


def _make_fail_window_patches(
    db_session: AsyncSession,
    org_id: Any,
    user_id: Any,
) -> tuple[Any, Any, Any]:
    """Return (_forced_skip, _check_cancel_on_5_lie_on_6, _boom) for FAIL-window TOCTOU.

    Pulled outside the test body so inner closures bind module-level names (not
    loop variables), satisfying ruff B023.
    """
    import idraa.services.run_executor as _rex
    from idraa.services.runs import RunService

    def _forced_skip(calc, per_scenario_inputs, per_scenario_dict, universe_ids, **kwargs):  # type: ignore[misc]
        skipped = [(sid, "over_cap") for sid, _name, _fp in per_scenario_inputs]
        return ({}, skipped)

    real_check = _rex._check_cancelled_or_continue
    state: dict[str, int] = {"calls": 0}

    async def _check_cancel_on_5_lie_on_6(session: AsyncSession, rid: uuid.UUID) -> bool:
        state["calls"] += 1
        if state["calls"] == 5:
            # Call 5 fires at :1099 — executor has no open write transaction.
            # Commit CANCELLED via db_session, then lie True so the executor
            # proceeds through the Shapley flush to call #6.
            await RunService(db_session).cancel(
                organization_id=org_id,
                run_id=rid,
                cancelled_by=user_id,
            )
            return True  # lie — executor doesn't know about the committed CANCEL
        if state["calls"] == 6:
            # Call 6 fires at :1173 — post-Shapley-flush, executor holds write
            # lock. Lie True again so it proceeds to split_simulation_payload
            # (which we make raise), driving the except handler.
            return True
        return await real_check(session, rid)

    def _boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("boom")

    return _forced_skip, _check_cancel_on_5_lie_on_6, _boom


@pytest.mark.asyncio
async def test_aggregate_cancel_during_fail_window_rolls_back_shapley_audit(
    db_session: AsyncSession,
    seed_aggregate_run_factory: Callable[..., Awaitable[Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Arch-new-1: exception in the FAIL window after Shapley flush with concurrent
    CANCEL must roll back all flushed Shapley audit rows and preserve CANCELLED.

    The FAIL window opens after the Shapley audit flush (:1151-1169) and closes
    at the terminal UPDATE. We use split_simulation_payload (called after call #6
    and after the Shapley flush, before the terminal UPDATE) as the injection
    point: it raises RuntimeError("boom"), driving execution into the except
    handler with:
      (a) Shapley audit rows already flushed in the pending transaction, AND
      (b) CANCELLED already committed by another actor.

    The guarded FAILED UPDATE matches no row (rowcount==0) → the FAIL path must
    ROLLBACK, not commit. This mirrors the fix applied to the COMPLETE path's
    rowcount==0 branch.

    Expected pending-state trace:
      1. Shapley audit rows flushed (run.shapley_skipped) — in pending txn.
      2. Call #6 (_check_cancelled_or_continue at :1173) returns True (lie) —
         executor holds write lock, Shapley rows pending.
      3. split_simulation_payload raises RuntimeError("boom") — execution enters
         except handler with pending flush + CANCELLED already committed.
      4. Guarded FAILED UPDATE fires: WHERE status=RUNNING matches no row
         (DB already has CANCELLED) → rowcount==0.
      5. Fix path: await session.rollback() — discards flushed Shapley rows and
         the no-match UPDATE atomically.
      6. Row stays CANCELLED, no error_message written, no Shapley audit rows,
         no risk_analysis_run.complete row.

    Run 5× for stability (Arch-NTH).
    """
    import idraa.services.run_executor as rex
    from idraa.models.risk_analysis_run import RiskAnalysisRun

    for _iteration in range(5):
        run = await seed_aggregate_run_factory(n_scenarios=2, n_controls=1, n_simulations=200)
        run_id = run.id

        # Build fresh closures each iteration (state dict resets) without
        # capturing loop variables (_make_fail_window_patches binds its own
        # locals, satisfying ruff B023).
        _forced_skip, _check_cancel_on_5, _boom = _make_fail_window_patches(
            db_session, run.organization_id, run.created_by
        )

        monkeypatch.setattr(rex, "_compute_shapley_by_scenario", _forced_skip)
        monkeypatch.setattr(rex, "_check_cancelled_or_continue", _check_cancel_on_5)
        monkeypatch.setattr("idraa.services.run_executor.split_simulation_payload", _boom)

        from idraa.services.run_executor import execute_run

        await execute_run(run_id)

        # Undo monkeypatches for the next iteration (monkeypatch fixture auto-undoes
        # on teardown, but we need clean state within the loop).
        monkeypatch.undo()

        refreshed = (
            await db_session.execute(
                select(RiskAnalysisRun)
                .where(RiskAnalysisRun.id == run_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        assert refreshed.status == RunStatus.CANCELLED, (
            f"iteration {_iteration}: cancel was overwritten by fail flip: "
            f"status={refreshed.status}"
        )
        assert refreshed.error_message is None, (
            f"iteration {_iteration}: error_message written onto a CANCELLED row: "
            f"{refreshed.error_message}"
        )

        # Flushed Shapley audit rows must have been rolled back — none should exist.
        shapley_audit = (
            (
                await db_session.execute(
                    select(AuditLog).where(
                        AuditLog.entity_id == run_id,
                        AuditLog.action.in_(["run.shapley_skipped", "run.non_finite_shapley"]),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert shapley_audit == [], (
            f"iteration {_iteration}: Shapley audit rows survived a rolled-back "
            f"FAIL-path lost-race: {shapley_audit}"
        )

        # No complete audit row should exist.
        complete_audit = (
            (
                await db_session.execute(
                    select(AuditLog).where(
                        AuditLog.entity_id == run_id,
                        AuditLog.action == "risk_analysis_run.complete",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert complete_audit == [], (
            f"iteration {_iteration}: spurious complete audit row found: {complete_audit}"
        )
