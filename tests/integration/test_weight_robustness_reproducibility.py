"""Issue #419 Task 4: the executor persists ``run.weight_robustness`` and the
ensemble is reproducible from the stored band+seed.

Reproducibility contract (Sec-Repro-1 / Repro-I1 / Sec-I2): the ensemble RNG is
derived from the band's persisted ``seed`` via a DISTINCT ``spawn_key`` namespace.
On a re-run the band (``logit_sigma``, ``distribution``, ``seed``, ``draws``) is read
back from ``run.weight_robustness["band"]``, NOT live Settings — so a Settings drift
(sigma/draws) between the original run and a regeneration cannot alter already-issued
ranges: re-executing reproduces identical per-control ranges.

Includes an n>12-control scenario so the Maleki sampled-Shapley branch (n>12)
is exercised: the brief (Arch-I9) requires its determinism to derive SOLELY from
the outer weight-sampler child stream (the inner shapley_values uses a fixed
seed=0 every draw), which the re-run identity test pins.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import idraa.config as config
from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus


def _small_ensemble_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Constrain the ensemble to a few draws so the n>12 Maleki branch is exercised
    cheaply. wire_executor_to_test_db resets the config singleton at setup; we set
    the env knobs then reset again so the executor's get_settings() picks them up.
    """
    monkeypatch.setenv("WEIGHT_ENSEMBLE_DRAWS", "8")
    monkeypatch.setenv("WEIGHT_ENSEMBLE_MIN_DRAWS", "2")
    config.reset_for_tests()


def _assert_monotone_ranges(per_control: dict[str, dict[str, Any]]) -> None:
    for cid, entry in per_control.items():
        p5, p50, p95 = entry["reduction_p5"], entry["reduction_p50"], entry["reduction_p95"]
        assert p5 <= p50 <= p95, f"non-monotone p5<=p50<=p95 for {cid}: {p5}, {p50}, {p95}"


@pytest.mark.asyncio
async def test_aggregate_weight_robustness_persisted_and_reproducible(
    db_session: AsyncSession,
    seed_aggregate_run_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AGGREGATE: weight_robustness populated with monotone ranges + band/seed, and
    re-running from the stored band reproduces identical per-control ranges even
    after live Settings (sigma + draws) drift (Sec-I2 — band read-back, not Settings).

    n_controls=13 forces the n>12 Maleki sampled-Shapley branch (Arch-I9).
    """
    from idraa.services.run_executor import execute_run

    _small_ensemble_env(monkeypatch)
    run = await seed_aggregate_run_factory(n_scenarios=2, n_controls=13, n_simulations=200)
    run.random_seed = 4242
    db_session.add(run)
    await db_session.commit()
    run_id = run.id

    await execute_run(run_id)
    await db_session.refresh(run)

    assert run.status == RunStatus.COMPLETED
    wr = run.weight_robustness
    assert wr is not None, "weight_robustness must be persisted on a COMPLETED AGGREGATE run"

    # Band + seed present and reproducible.
    assert "band" in wr
    assert wr["band"]["seed"] == 4242
    assert wr["band"]["distribution"] == "logit_normal"
    assert "logit_sigma" in wr["band"]
    assert "draws" in wr["band"]

    # Per-control ranges present + monotone; canonical_value reference present.
    assert wr["per_control"], "expected per_control entries"
    _assert_monotone_ranges(wr["per_control"])
    assert wr["canonical_value"], "expected canonical_value reference dict"
    assert set(wr["per_control"]).issubset(set(wr["canonical_value"]))

    # AGGREGATE => rank stability available (compute_rank_stability=True).
    assert wr["rank_stability_available"] is True
    assert wr["state"] in ("ok", "insufficient_budget")

    first_per_control = wr["per_control"]
    first_headline = wr["headline"]

    # --- Re-run from the stored band+seed, AFTER drifting live Settings (Sec-I2) ---
    # The persisted band {logit_sigma, distribution, seed, draws} must be read back
    # from the snapshot, NOT re-read from live Settings, so a settings change between
    # the original run and a regeneration cannot alter already-issued ranges. Drift
    # the live sigma + draws; the re-run must still reproduce IDENTICAL ranges because
    # it reads the band back from run.weight_robustness["band"].
    stored_sigma = wr["band"]["logit_sigma"]
    monkeypatch.setenv("WEIGHT_BAND_LOGIT_SIGMA", str(min(4.0, stored_sigma + 1.0)))
    monkeypatch.setenv("WEIGHT_ENSEMBLE_DRAWS", "16")  # drift draws too
    config.reset_for_tests()

    # Re-execute the SAME run (same controls/scenarios/seed) so control IDs match.
    # Reset it to QUEUED and clear simulation_results, but KEEP weight_robustness so
    # the executor takes the band read-back path. Detach the old samples row first.
    from idraa.models.run_samples import RunSamples

    existing_samples = (
        await db_session.execute(select(RunSamples).where(RunSamples.run_id == run_id))
    ).scalar_one_or_none()
    if existing_samples is not None:
        await db_session.delete(existing_samples)
    run.status = RunStatus.QUEUED
    run.simulation_results = None
    run.completed_at = None
    run.started_at = None
    # run.weight_robustness retains the stored band -> read-back path.
    db_session.add(run)
    await db_session.commit()

    await execute_run(run_id)
    await db_session.refresh(run)

    assert run.status == RunStatus.COMPLETED
    wr2 = run.weight_robustness
    assert wr2 is not None
    assert wr2["band"] == wr["band"], "band must be reproduced verbatim from the stored snapshot"

    # Identical per-control ranges despite the live Settings drift (band read-back).
    for cid, entry in first_per_control.items():
        assert cid in wr2["per_control"], f"control {cid} dropped on re-run"
        for key in ("reduction_p5", "reduction_p50", "reduction_p95"):
            assert wr2["per_control"][cid][key] == pytest.approx(entry[key]), (
                f"range {key} for {cid} not reproduced: "
                f"{wr2['per_control'][cid][key]} != {entry[key]}"
            )
    for key in ("reduction_p5", "reduction_p50", "reduction_p95"):
        assert wr2["headline"][key] == pytest.approx(first_headline[key])


@pytest.mark.asyncio
async def test_single_weight_robustness_ranges_only(
    db_session: AsyncSession,
    seed_run_factory: Any,
    seed_scenario_factory: Any,
    seed_control_factory: Any,
    wire_executor_to_test_db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SINGLE: weight_robustness populated with monotone ranges, rank stability
    SUPPRESSED (compute_rank_stability=False -> rank_stability_available False).

    Includes an n>12-control SINGLE scenario (Arch-I9/Arch-N6): SINGLE now incurs
    the (1+K) x eval cost and degrades like AGGREGATE under the Maleki branch.
    """
    from idraa.models.scenario_control import ScenarioControl
    from idraa.services.run_executor import execute_run

    _small_ensemble_env(monkeypatch)
    scenario = await seed_scenario_factory(name="single-weight-robustness")
    controls = [await seed_control_factory(name=f"swr_ctrl_{i}") for i in range(13)]
    for c in controls:
        db_session.add(ScenarioControl(scenario_id=scenario.id, control_id=c.id))
    await db_session.commit()

    run = await seed_run_factory(scenario=scenario, mc_iterations=200, random_seed=777)
    run.control_ids_used = sorted(str(c.id) for c in controls)
    db_session.add(run)
    await db_session.commit()

    await execute_run(run.id)
    await db_session.refresh(run)

    assert run.status == RunStatus.COMPLETED
    wr = run.weight_robustness
    assert wr is not None, "SINGLE COMPLETED run must persist weight_robustness"
    assert wr["band"]["seed"] == 777
    assert wr["per_control"], "expected per_control ranges on SINGLE"
    _assert_monotone_ranges(wr["per_control"])
    # Meth-B6: SINGLE display is effectiveness-sorted, NOT the Shapley basis the
    # ensemble ranks -> only basis-agnostic dollar RANGES; stability suppressed.
    assert wr["rank_stability_available"] is False
    assert wr["kendall_tau_p50"] is None
    assert wr["indistinguishable_pairs"] == []


@pytest.mark.asyncio
async def test_degraded_path_reproducible(
    db_session: AsyncSession,
    seed_aggregate_run_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Degraded/insufficient-budget path reproducibility (Finding 1 fix).

    Force the band-endpoint fallback (eval_budget so tiny that k=0 < min_draws)
    by using n=13 controls so _realized_eval_cost ~120k/draw >> budget.
    Run. Drift live weight_band_logit_sigma. Re-run. Assert per-control endpoint
    ranges are IDENTICAL -- proving band_endpoint_mappings honors the PINNED
    band sigma (not live Settings) in the degraded path.

    Also confirms eval_budget + min_draws are pinned in the stored band and
    survive the re-run gate: re-run must stay in insufficient_budget state even
    if live Settings would allow more draws.
    """
    from idraa.services.run_executor import execute_run

    # n=13 controls -> eval_cost_per_draw ~ 2 * maleki(0.02,0.05)*13 = ~120k.
    # eval_budget=10000 passes the Settings validator (>= min_draws*4096 = 8192)
    # but forces k = 10000//120k = 0 < min_draws(2) -> band-endpoint fallback.
    monkeypatch.setenv("WEIGHT_ENSEMBLE_DRAWS", "8")
    monkeypatch.setenv("WEIGHT_ENSEMBLE_MIN_DRAWS", "2")
    monkeypatch.setenv("WEIGHT_ENSEMBLE_EVAL_BUDGET", "10000")
    config.reset_for_tests()

    run = await seed_aggregate_run_factory(n_scenarios=2, n_controls=13, n_simulations=200)
    run.random_seed = 7777
    db_session.add(run)
    await db_session.commit()
    run_id = run.id

    await execute_run(run_id)
    await db_session.refresh(run)

    assert run.status == RunStatus.COMPLETED
    wr = run.weight_robustness
    assert wr is not None
    assert wr["state"] == "insufficient_budget", (
        f"expected insufficient_budget with tiny eval_budget; got {wr['state']!r} "
        f"(draws_used={wr.get('draws_used')}, degraded={wr.get('degraded')})"
    )
    assert wr["degraded"] is True
    assert wr["band"]["eval_budget"] == 10000, "eval_budget must be pinned in the stored band"
    assert wr["band"]["min_draws"] == 2, "min_draws must be pinned in the stored band"

    first_per_control = wr["per_control"]
    stored_sigma = wr["band"]["logit_sigma"]
    assert first_per_control, "expected per_control entries from envelope fallback"
    _assert_monotone_ranges(first_per_control)

    # Drift live sigma significantly -- if band_endpoint_mappings used live Settings
    # instead of the pinned band sigma, the endpoints would change.
    drifted_sigma = min(4.0, stored_sigma + 1.5)
    monkeypatch.setenv("WEIGHT_BAND_LOGIT_SIGMA", str(drifted_sigma))
    # Also drift eval_budget UP past the degradation threshold -- if NOT pinned, the
    # re-run would unexpectedly escape the insufficient_budget state.
    monkeypatch.setenv("WEIGHT_ENSEMBLE_EVAL_BUDGET", str(10_000_000))
    config.reset_for_tests()

    # Reset for re-run, keeping weight_robustness (band read-back path).
    from idraa.models.run_samples import RunSamples

    existing_samples = (
        await db_session.execute(select(RunSamples).where(RunSamples.run_id == run_id))
    ).scalar_one_or_none()
    if existing_samples is not None:
        await db_session.delete(existing_samples)
    run.status = RunStatus.QUEUED
    run.simulation_results = None
    run.completed_at = None
    run.started_at = None
    db_session.add(run)
    await db_session.commit()

    await execute_run(run_id)
    await db_session.refresh(run)

    assert run.status == RunStatus.COMPLETED
    wr2 = run.weight_robustness
    assert wr2 is not None
    # The stored band pins eval_budget=10000 -> re-run MUST stay in insufficient_budget
    # even though live Settings now allow a full ensemble.
    assert wr2["state"] == "insufficient_budget", (
        "re-run must stay in insufficient_budget: pinned eval_budget=10000 from band "
        f"(live drifted to {drifted_sigma:.2f} sigma, 10M budget); got {wr2['state']!r}"
    )
    assert wr2["band"]["logit_sigma"] == stored_sigma, "band sigma must be reproduced verbatim"

    # Identical per-control endpoint ranges despite sigma + budget drift.
    for cid, entry in first_per_control.items():
        assert cid in wr2["per_control"], f"control {cid} dropped on re-run"
        for key in ("reduction_p5", "reduction_p50", "reduction_p95"):
            assert wr2["per_control"][cid][key] == pytest.approx(entry[key]), (
                f"degraded-path range {key} for {cid} not reproduced after sigma/budget drift: "
                f"{wr2['per_control'][cid][key]} != {entry[key]}"
            )


@pytest.mark.asyncio
async def test_weight_robustness_null_on_failed_run(
    db_session: AsyncSession,
    seed_aggregate_run_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A FAILED run leaves weight_robustness NULL (column default) — it is only
    written inside the guarded COMPLETED UPDATE."""
    import idraa.services.run_executor as rex
    from idraa.services.run_executor import execute_run

    run = await seed_aggregate_run_factory(n_scenarios=2, n_controls=1, n_simulations=200)
    run_id = run.id

    def _boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("boom")

    # Fail before the terminal COMPLETED UPDATE.
    monkeypatch.setattr(rex, "split_simulation_payload", _boom)

    await execute_run(run_id)

    refreshed = (
        await db_session.execute(
            select(RiskAnalysisRun)
            .where(RiskAnalysisRun.id == run_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert refreshed.status == RunStatus.FAILED
    assert refreshed.weight_robustness is None


@pytest.mark.asyncio
async def test_failed_run_error_message_is_generic(
    db_session: AsyncSession,
    seed_aggregate_run_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #82: a FAILED run's ``error_message`` must be the fixed generic
    string, never the raw exception repr — ``_status_poll.html`` renders
    ``error_message`` verbatim, and a raw ``f"{type(exc).__name__}: {exc}"``
    could leak internal detail (paths, SQL fragments, class names) to the UI.
    Full diagnostics still land server-side via the audit log's
    ``error_class`` field and ``logger.exception`` — this test only pins what
    reaches the stored/rendered column.
    """
    import idraa.services.run_executor as rex
    from idraa.services.run_executor import _RUN_FAILURE_MESSAGE, execute_run

    run = await seed_aggregate_run_factory(n_scenarios=2, n_controls=1, n_simulations=200)
    run_id = run.id

    def _boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("internal detail that must not reach the client")

    monkeypatch.setattr(rex, "split_simulation_payload", _boom)

    await execute_run(run_id)

    refreshed = (
        await db_session.execute(
            select(RiskAnalysisRun)
            .where(RiskAnalysisRun.id == run_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert refreshed.status == RunStatus.FAILED
    assert refreshed.error_message == _RUN_FAILURE_MESSAGE
    assert refreshed.error_message is not None
    assert "RuntimeError" not in refreshed.error_message
    assert "internal detail" not in refreshed.error_message
