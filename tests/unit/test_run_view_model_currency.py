"""Unit tests: Task 5 — web view-models convert USD→presentation currency.

Tests that:
  - build_display_results(run, rc=<EUR rate 0.92>) converts all money fields.
  - build_aggregate_display_results(run, rc=<EUR rate 0.92>) converts all money fields.
  - Both functions add `currency` + `currency_provenance` to the top-level dict.
  - USD-identity (default, no rc) leaves all values unchanged.
  - Shapley cells still reconcile to converted `total_reduction` (no-double-convert).
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from idraa.services.aggregate_run_view_model import build_aggregate_display_results
from idraa.services.reporting_currency import ReportingCurrency
from idraa.services.run_view_model import build_display_results

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

EUR_RATE = Decimal("0.92")
EUR_RC = ReportingCurrency(
    code="EUR",
    rate=EUR_RATE,
    is_pinned=True,
    provenance="Converted from USD at 1 USD = 0.92 EUR, as-of 2026-06-14, source ECB",
)

USD_RC = ReportingCurrency(
    code="USD",
    rate=Decimal("1"),
    is_pinned=True,
    provenance=None,
)


def _run_with_results(simulation_results: dict[str, Any] | None) -> SimpleNamespace:
    return SimpleNamespace(
        simulation_results=simulation_results,
        controls_snapshot=[],
        presentation_fx_snapshot={
            "code": "EUR",
            "usd_rate": "0.92",
            "as_of_date": "2026-06-14",
            "source": "ECB",
        },
    )


def _full_single_sr() -> dict[str, Any]:
    """A realistic single-run simulation_results payload (USD values)."""
    return {
        "base_risk": {
            "annualized_loss_expectancy": 2_000_000.0,
            "mean": 2_000_000.0,
            "median": 1_800_000.0,
            "std_deviation": 500_000.0,
            "var_90": 2_500_000.0,
            "var_95": 3_000_000.0,
            "var_99": 4_000_000.0,
            "var_999": 5_000_000.0,
            "expected_shortfall": {
                "es_95": 3_500_000.0,
                "es_99": 4_500_000.0,
                "es_999": 6_000_000.0,
            },
        },
        "residual_risk": {
            "annualized_loss_expectancy": 1_000_000.0,
            "mean": 1_000_000.0,
            "median": 900_000.0,
            "std_deviation": 250_000.0,
            "var_90": 1_200_000.0,
            "var_95": 1_500_000.0,
            "var_99": 2_000_000.0,
            "var_999": 2_500_000.0,
            "expected_shortfall": {
                "es_95": 1_750_000.0,
                "es_99": 2_200_000.0,
                "es_999": 3_000_000.0,
            },
        },
        "control_adjustments": [
            {"control_id": "c1", "effectiveness": 0.85},
        ],
        "confidence_intervals": {
            "lower_bound": 800_000.0,
            "upper_bound": 1_200_000.0,
            "interval_pct": 95,
        },
        "loss_exceedance_curve": [
            {"loss": 500_000.0, "probability": 0.5},
            {"loss": 1_000_000.0, "probability": 0.2},
            {"loss": 2_000_000.0, "probability": 0.05},
        ],
        "exceedance_probability_curve": [
            {"percentile": 0.5, "loss": 900_000.0},
            {"percentile": 0.95, "loss": 1_500_000.0},
        ],
    }


def _full_aggregate_sr() -> dict[str, Any]:
    """A realistic aggregate-run simulation_results payload (USD values)."""
    return {
        "aggregate_with_controls": {
            "annualized_loss_expectancy": 1_500_000.0,
            "mean": 1_500_000.0,
            "median": 1_400_000.0,
            "std_deviation": 300_000.0,
            "var_90": 2_000_000.0,
            "var_95": 2_500_000.0,
            "var_99": 3_000_000.0,
            "var_999": 4_000_000.0,
            "expected_shortfall": {
                "es_95": 2_800_000.0,
                "es_99": 3_500_000.0,
                "es_999": 4_500_000.0,
            },
            "loss_exceedance_curve": [
                {"loss": 1_000_000.0, "probability": 0.3},
                {"loss": 2_000_000.0, "probability": 0.1},
            ],
        },
        "aggregate_without_controls": {
            "annualized_loss_expectancy": 3_000_000.0,
            "mean": 3_000_000.0,
            "median": 2_800_000.0,
            "std_deviation": 600_000.0,
            "var_90": 4_000_000.0,
            "var_95": 5_000_000.0,
            "var_99": 6_000_000.0,
            "var_999": 8_000_000.0,
            "expected_shortfall": {
                "es_95": 5_500_000.0,
                "es_99": 7_000_000.0,
                "es_999": 9_000_000.0,
            },
            "loss_exceedance_curve": [
                {"loss": 2_000_000.0, "probability": 0.3},
                {"loss": 4_000_000.0, "probability": 0.1},
            ],
        },
        "confidence_intervals": {
            "lower_bound": 1_200_000.0,
            "upper_bound": 1_800_000.0,
            "interval_pct": 95,
        },
        "control_value": {"dollars": 1_500_000.0, "percent": 50.0},
        "dual_epc": {
            "with_controls": [
                {"percentile": 0.5, "loss": 1_400_000.0},
                {"percentile": 0.95, "loss": 2_500_000.0},
            ],
            "without_controls": [
                {"percentile": 0.5, "loss": 2_800_000.0},
                {"percentile": 0.95, "loss": 5_000_000.0},
            ],
        },
        "per_scenario": [
            {
                "scenario_id": "s1",
                "scenario_name": "Scenario A",
                "base_risk": {"annualized_loss_expectancy": 2_000_000.0},
                "residual_risk": {"annualized_loss_expectancy": 1_000_000.0},
                "control_adjustments": [
                    {"control_id": "c1", "control_name": "MFA", "shapley_value": 800_000.0},
                    {"control_id": "c2", "control_name": "EDR", "shapley_value": 200_000.0},
                ],
            },
            {
                "scenario_id": "s2",
                "scenario_name": "Scenario B",
                "base_risk": {"annualized_loss_expectancy": 1_000_000.0},
                "residual_risk": {"annualized_loss_expectancy": 500_000.0},
                "control_adjustments": [
                    {"control_id": "c1", "control_name": "MFA", "shapley_value": 400_000.0},
                    {"control_id": "c2", "control_name": "EDR", "shapley_value": 100_000.0},
                ],
            },
            {
                "scenario_id": "s3",
                "scenario_name": "Scenario C",
                "base_risk": {"annualized_loss_expectancy": 500_000.0},
                "residual_risk": {"annualized_loss_expectancy": 500_000.0},
                "control_adjustments": [
                    {"control_id": "c1", "control_name": "MFA", "shapley_value": 0.0},
                    {"control_id": "c2", "control_name": "EDR", "shapley_value": 0.0},
                ],
            },
        ],
        "n_scenarios": 3,
        "n_simulations": 10_000,
    }


# ---------------------------------------------------------------------------
# Tests: build_display_results (SINGLE run)
# ---------------------------------------------------------------------------


def test_single_usd_identity_unchanged() -> None:
    """USD identity (default rc) leaves all money fields unchanged."""
    run = _run_with_results(_full_single_sr())
    vm = build_display_results(run)
    assert vm is not None
    assert vm["headline_ale"]["value"] == 1_000_000.0
    assert vm["headline_ale"]["lo"] == 800_000.0
    assert vm["headline_ale"]["hi"] == 1_200_000.0
    assert vm["risk_comparison"]["base"] == 2_000_000.0
    assert vm["risk_comparison"]["residual"] == 1_000_000.0
    assert vm["risk_comparison"]["reduction"] == 1_000_000.0
    # LEC loss values unchanged
    lec = vm["loss_exceedance_curve"]
    assert lec[0]["loss"] == 500_000.0
    assert lec[1]["loss"] == 1_000_000.0


def test_single_usd_default_rc_returns_currency_usd() -> None:
    """Default (no rc passed) view-model carries currency.code=USD."""
    run = _run_with_results(_full_single_sr())
    vm = build_display_results(run)
    assert vm is not None
    assert vm["currency"]["code"] == "USD"
    assert vm["currency_provenance"] is None


def test_single_eur_headline_ale_converted() -> None:
    """EUR rc: headline ALE and CI bounds are usd*0.92."""
    run = _run_with_results(_full_single_sr())
    vm = build_display_results(run, rc=EUR_RC)
    assert vm is not None
    assert vm["headline_ale"]["value"] == pytest.approx(1_000_000.0 * 0.92)
    assert vm["headline_ale"]["lo"] == pytest.approx(800_000.0 * 0.92)
    assert vm["headline_ale"]["hi"] == pytest.approx(1_200_000.0 * 0.92)


def test_single_eur_risk_comparison_converted() -> None:
    """EUR rc: base/residual/reduction all converted."""
    run = _run_with_results(_full_single_sr())
    vm = build_display_results(run, rc=EUR_RC)
    assert vm is not None
    cmp = vm["risk_comparison"]
    assert cmp["base"] == pytest.approx(2_000_000.0 * 0.92)
    assert cmp["residual"] == pytest.approx(1_000_000.0 * 0.92)
    assert cmp["reduction"] == pytest.approx(1_000_000.0 * 0.92)


def test_single_eur_lec_loss_values_converted() -> None:
    """EUR rc: loss values in LEC array are converted."""
    run = _run_with_results(_full_single_sr())
    vm = build_display_results(run, rc=EUR_RC)
    assert vm is not None
    lec = vm["loss_exceedance_curve"]
    assert lec[0]["loss"] == pytest.approx(500_000.0 * 0.92)
    assert lec[1]["loss"] == pytest.approx(1_000_000.0 * 0.92)
    # probabilities are NOT money — must remain unchanged
    assert lec[0]["probability"] == pytest.approx(0.5)


def test_single_eur_epc_loss_values_converted() -> None:
    """EUR rc: loss values in EPC array are converted."""
    run = _run_with_results(_full_single_sr())
    vm = build_display_results(run, rc=EUR_RC)
    assert vm is not None
    epc = vm["exceedance_probability_curve"]
    assert epc[0]["loss"] == pytest.approx(900_000.0 * 0.92)
    # percentiles are NOT money
    assert epc[0]["percentile"] == pytest.approx(0.5)


def test_single_eur_dist_stats_var_converted() -> None:
    """EUR rc: dist_stats VaR/ES dollar rows are converted."""
    run = _run_with_results(_full_single_sr())
    vm = build_display_results(run, rc=EUR_RC)
    assert vm is not None
    rows = vm["dist_stats"]["rows"]
    var95_row = next(r for r in rows if r["label"] == "VaR 95%")
    # base var_95 = 3_000_000; residual var_95 = 1_500_000
    assert var95_row["base"] == pytest.approx(3_000_000.0 * 0.92)
    assert var95_row["residual"] == pytest.approx(1_500_000.0 * 0.92)


def test_single_eur_currency_meta_in_view_model() -> None:
    """EUR rc: currency dict and provenance are present."""
    run = _run_with_results(_full_single_sr())
    vm = build_display_results(run, rc=EUR_RC)
    assert vm is not None
    assert vm["currency"]["code"] == "EUR"
    assert "symbol" in vm["currency"]
    assert vm["currency_provenance"] == EUR_RC.provenance


# ---------------------------------------------------------------------------
# Tests: build_aggregate_display_results (AGGREGATE run)
# ---------------------------------------------------------------------------


def _agg_run(simulation_results: dict[str, Any] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        simulation_results=simulation_results,
        presentation_fx_snapshot={
            "code": "EUR",
            "usd_rate": "0.92",
            "as_of_date": "2026-06-14",
            "source": "ECB",
        },
    )


def test_aggregate_usd_identity_unchanged() -> None:
    """USD identity (default rc) leaves all money fields unchanged."""
    run = _agg_run(_full_aggregate_sr())
    vm = build_aggregate_display_results(run)
    assert vm is not None
    assert vm["headline_ale"]["value"] == 1_500_000.0
    assert vm["control_value_headline"]["dollars"] == 1_500_000.0


def test_aggregate_eur_headline_ale_converted() -> None:
    """EUR rc: aggregate headline ALE (with_controls) converted."""
    run = _agg_run(_full_aggregate_sr())
    vm = build_aggregate_display_results(run, rc=EUR_RC)
    assert vm is not None
    assert vm["headline_ale"]["value"] == pytest.approx(1_500_000.0 * 0.92)
    assert vm["headline_ale"]["lo"] == pytest.approx(1_200_000.0 * 0.92)
    assert vm["headline_ale"]["hi"] == pytest.approx(1_800_000.0 * 0.92)


def test_aggregate_eur_control_value_headline_converted() -> None:
    """EUR rc: control value dollars converted (percent is NOT money)."""
    run = _agg_run(_full_aggregate_sr())
    vm = build_aggregate_display_results(run, rc=EUR_RC)
    assert vm is not None
    cvh = vm["control_value_headline"]
    assert cvh["dollars"] == pytest.approx(1_500_000.0 * 0.92)
    assert cvh["percent"] == pytest.approx(50.0)  # percent unchanged


def test_aggregate_eur_dual_lec_converted() -> None:
    """EUR rc: dual LEC loss values converted."""
    run = _agg_run(_full_aggregate_sr())
    vm = build_aggregate_display_results(run, rc=EUR_RC)
    assert vm is not None
    with_lec = vm["dual_lec"]["with_controls"]
    assert with_lec[0]["loss"] == pytest.approx(1_000_000.0 * 0.92)
    # probability unchanged
    assert with_lec[0]["probability"] == pytest.approx(0.3)
    without_lec = vm["dual_lec"]["without_controls"]
    assert without_lec[0]["loss"] == pytest.approx(2_000_000.0 * 0.92)


def test_aggregate_eur_dual_epc_converted() -> None:
    """EUR rc: dual EPC loss values converted."""
    run = _agg_run(_full_aggregate_sr())
    vm = build_aggregate_display_results(run, rc=EUR_RC)
    assert vm is not None
    with_epc = vm["dual_epc"]["with_controls"]
    assert with_epc[0]["loss"] == pytest.approx(1_400_000.0 * 0.92)
    assert with_epc[0]["percentile"] == pytest.approx(0.5)  # percentile unchanged


def test_aggregate_eur_per_scenario_ale_rows_converted() -> None:
    """EUR rc: per-scenario base_ale and residual_ale converted."""
    run = _agg_run(_full_aggregate_sr())
    vm = build_aggregate_display_results(run, rc=EUR_RC)
    assert vm is not None
    rows = vm["per_scenario_ale_rows"]
    # Sorted desc by base_ale → s1 (2M), s2 (1M), s3 (0.5M)
    assert rows[0]["base_ale"] == pytest.approx(2_000_000.0 * 0.92)
    assert rows[0]["residual_ale"] == pytest.approx(1_000_000.0 * 0.92)
    assert rows[1]["base_ale"] == pytest.approx(1_000_000.0 * 0.92)


def test_aggregate_eur_shapley_matrix_cells_converted() -> None:
    """EUR rc: Shapley cells and total_reduction converted."""
    run = _agg_run(_full_aggregate_sr())
    vm = build_aggregate_display_results(run, rc=EUR_RC)
    assert vm is not None
    matrix = vm["per_scenario_control_matrix"]
    # total_reduction for MFA: 800k + 400k + 0 = 1_200_000 → * 0.92
    mfa_ctrl = next(c for c in matrix["controls"] if c["control_name"] == "MFA")
    assert mfa_ctrl["total_reduction"] == pytest.approx(1_200_000.0 * 0.92)
    # Cell from s1/MFA: 800_000 → * 0.92
    s1_row = next(r for r in matrix["rows"] if r["scenario_name"] == "Scenario A")
    mfa_cell = next(c for c in s1_row["cells"] if c["control_id"] == "c1")
    assert mfa_cell["value"] == pytest.approx(800_000.0 * 0.92)


def test_aggregate_eur_shapley_cell_reconciles_to_total() -> None:
    """No-double-convert invariant: cells sum to column total_reduction."""
    run = _agg_run(_full_aggregate_sr())
    vm = build_aggregate_display_results(run, rc=EUR_RC)
    assert vm is not None
    matrix = vm["per_scenario_control_matrix"]
    for ctrl in matrix["controls"]:
        cid = ctrl["control_id"]
        total = ctrl["total_reduction"]
        if total is None:
            continue
        cell_sum = sum(
            c["value"]
            for row in matrix["rows"]
            for c in row["cells"]
            if c["control_id"] == cid and c["value"] is not None
        )
        assert cell_sum == pytest.approx(total, rel=1e-9), (
            f"Shapley efficiency broken for {ctrl['control_name']}: "
            f"cells sum={cell_sum}, total={total}"
        )


def test_aggregate_eur_dist_stats_converted() -> None:
    """EUR rc: aggregate dist_stats (without/with_controls) VaR rows converted."""
    run = _agg_run(_full_aggregate_sr())
    vm = build_aggregate_display_results(run, rc=EUR_RC)
    assert vm is not None
    rows = vm["dist_stats"]["rows"]
    var95 = next(r for r in rows if r["label"] == "VaR 95%")
    # base (without controls) var_95 = 5_000_000; residual (with controls) = 2_500_000
    assert var95["base"] == pytest.approx(5_000_000.0 * 0.92)
    assert var95["residual"] == pytest.approx(2_500_000.0 * 0.92)


def test_aggregate_eur_currency_meta_in_view_model() -> None:
    """EUR rc: currency dict and provenance present in aggregate view-model."""
    run = _agg_run(_full_aggregate_sr())
    vm = build_aggregate_display_results(run, rc=EUR_RC)
    assert vm is not None
    assert vm["currency"]["code"] == "EUR"
    assert "symbol" in vm["currency"]
    assert vm["currency_provenance"] == EUR_RC.provenance


def test_aggregate_usd_default_rc_returns_currency_usd() -> None:
    """Default (no rc) aggregate view-model carries currency.code=USD."""
    run = _agg_run(_full_aggregate_sr())
    vm = build_aggregate_display_results(run)
    assert vm is not None
    assert vm["currency"]["code"] == "USD"
    assert vm["currency_provenance"] is None
