"""Task 10 (Spec-B1): surface the ES Monte Carlo standard error (Task 9,
``expected_shortfall_se``) as a 95% MC interval on the view-model + shared
dist_stats-row builder.

The view-model builders (``build_display_results`` / ``build_aggregate_display_results``)
return plain dicts (not attribute-access objects) — see
tests/unit/test_run_view_model.py for the established convention this file
follows. The three cases under test, matching ``_es_ci_fields`` in
services/_view_model_helpers.py:

  1. ``expected_shortfall_se`` ABSENT entirely (legacy row) -> se is None,
     ci_half is None, ci_insufficient is False (bare ES, no annotation).
  2. dict present but a level's value is None (< 2 tail samples at this N,
     NaN before persist) -> se is None, ci_half is None, ci_insufficient is
     True (insufficient-tail-samples label).
  3. dict present with a float -> se is that float, ci_half == 1.96 * se,
     ci_insufficient is False.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from idraa.services._view_model_helpers import (
    ES_CI_Z_95,
    _build_tail_risk,
    _es_ci_fields,
    build_dist_stats_rows,
)
from idraa.services.aggregate_run_view_model import build_aggregate_display_results
from idraa.services.reporting_currency import ReportingCurrency
from idraa.services.run_view_model import build_display_results

EUR_RC = ReportingCurrency(
    code="EUR",
    rate=Decimal("0.92"),
    is_pinned=True,
    provenance="Converted from USD at 1 USD = 0.92 EUR, as-of 2026-06-14, source ECB",
)


def _risk_with_se(
    scale: float,
    *,
    es_95_se: float | None = 10_000.0,
    es_99_se: float | None = 20_000.0,
    es_999_se: float | None = None,
) -> dict[str, Any]:
    """A tail-capable risk dict carrying expected_shortfall_se (Task 9 shape)."""
    return {
        "mean": 3_000_000.0 * scale,
        "median": 2_000_000.0 * scale,
        "std_deviation": 1_500_000.0 * scale,
        "var_90": 4_000_000.0 * scale,
        "var_95": 4_500_000.0 * scale,
        "var_99": 5_500_000.0 * scale,
        "var_999": 7_000_000.0 * scale,
        "expected_shortfall": {
            "es_95": 5_000_000.0 * scale,
            "es_99": 6_200_000.0 * scale,
            "es_999": 8_000_000.0 * scale,
        },
        "expected_shortfall_se": {
            "es_95": es_95_se,
            "es_99": es_99_se,
            "es_999": es_999_se,
        },
    }


def _risk_without_se(scale: float) -> dict[str, Any]:
    """Legacy-shaped risk dict: expected_shortfall present, SE sibling absent."""
    return {
        "mean": 3_000_000.0 * scale,
        "median": 2_000_000.0 * scale,
        "std_deviation": 1_500_000.0 * scale,
        "var_90": 4_000_000.0 * scale,
        "var_95": 4_500_000.0 * scale,
        "var_99": 5_500_000.0 * scale,
        "var_999": 7_000_000.0 * scale,
        "expected_shortfall": {
            "es_95": 5_000_000.0 * scale,
            "es_99": 6_200_000.0 * scale,
            "es_999": 8_000_000.0 * scale,
        },
        # NOTE: no "expected_shortfall_se" key at all.
    }


# ---- _es_ci_fields (the pure per-level helper) -----------------------------


def test_es_ci_fields_absent_dict_is_bare_es() -> None:
    """No expected_shortfall_se key at all -> legacy row: no annotation."""
    risk = _risk_without_se(1.0)
    fields = _es_ci_fields(risk, "es_95")
    assert fields == {"se": None, "ci_half": None, "ci_insufficient": False}


def test_es_ci_fields_none_value_is_insufficient() -> None:
    """Dict present, this level's value is None -> insufficient flag True."""
    risk = _risk_with_se(1.0, es_999_se=None)
    fields = _es_ci_fields(risk, "es_999")
    assert fields["se"] is None
    assert fields["ci_half"] is None
    assert fields["ci_insufficient"] is True


def test_es_ci_fields_present_value_scales_to_95pct_interval() -> None:
    """Dict present with a float SE -> ci_half == ES_CI_Z_95 * se, exactly."""
    risk = _risk_with_se(1.0, es_95_se=12_345.0)
    fields = _es_ci_fields(risk, "es_95")
    assert fields["se"] == 12_345.0
    assert fields["ci_half"] == pytest.approx(ES_CI_Z_95 * 12_345.0)
    assert ES_CI_Z_95 == 1.96  # named constant, not a scattered literal
    assert fields["ci_insufficient"] is False


# ---- _build_tail_risk (flat, single-side; feeds run_view_model's top-level
#      "tail_risk" key and reports.py's PDF tail dicts) --------------------


def test_build_tail_risk_exposes_se_ci_half_and_insufficient_flag() -> None:
    risk = _risk_with_se(1.0, es_95_se=10_000.0, es_99_se=20_000.0, es_999_se=None)
    tail = _build_tail_risk(risk)
    assert tail["es_95_se"] == 10_000.0
    assert tail["es_95_ci_half"] == pytest.approx(1.96 * 10_000.0)
    assert tail["es_95_ci_insufficient"] is False
    assert tail["es_999_se"] is None
    assert tail["es_999_ci_half"] is None
    assert tail["es_999_ci_insufficient"] is True


def test_build_tail_risk_legacy_row_has_no_se_no_crash() -> None:
    risk = _risk_without_se(1.0)
    tail = _build_tail_risk(risk)
    assert tail["es_95_se"] is None
    assert tail["es_95_ci_half"] is None
    assert tail["es_95_ci_insufficient"] is False  # legacy, not "insufficient"
    # The original 7 core keys are unaffected.
    assert tail["es_95"] == 5_000_000.0


# ---- build_dist_stats_rows (base vs residual delta table; feeds the web
#      run-detail template + is shared by SINGLE and AGGREGATE) -----------


def test_dist_stats_es_rows_carry_per_side_ci_fields() -> None:
    base = _risk_with_se(1.0, es_95_se=10_000.0, es_99_se=20_000.0, es_999_se=None)
    residual = _risk_with_se(0.5, es_95_se=4_000.0, es_99_se=None, es_999_se=None)
    out = build_dist_stats_rows(base, residual)
    rows_by_label = {r["label"]: r for r in out["rows"]}

    es95 = rows_by_label["ES 95%"]
    assert es95["base_se"] == 10_000.0
    assert es95["base_ci_half"] == pytest.approx(1.96 * 10_000.0)
    assert es95["base_ci_insufficient"] is False
    assert es95["residual_se"] == 4_000.0
    assert es95["residual_ci_half"] == pytest.approx(1.96 * 4_000.0)
    assert es95["residual_ci_insufficient"] is False

    es99 = rows_by_label["ES 99%"]
    assert es99["base_se"] == 20_000.0
    assert es99["residual_se"] is None
    assert es99["residual_ci_insufficient"] is True  # dict present, value None

    # Non-ES rows carry the uniform (empty) schema, never a stray real value.
    var95 = rows_by_label["VaR 95%"]
    assert var95["base_se"] is None
    assert var95["base_ci_insufficient"] is False
    assert var95["residual_se"] is None
    assert var95["residual_ci_insufficient"] is False


def test_dist_stats_es_rows_legacy_both_sides_bare_es() -> None:
    """Neither side has expected_shortfall_se -> bare ES, no insufficient flag."""
    base = _risk_without_se(1.0)
    residual = _risk_without_se(0.5)
    out = build_dist_stats_rows(base, residual)
    es95 = next(r for r in out["rows"] if r["label"] == "ES 95%")
    assert es95["base_se"] is None
    assert es95["base_ci_insufficient"] is False
    assert es95["residual_se"] is None
    assert es95["residual_ci_insufficient"] is False
    # has_tail is unaffected by SE presence/absence — driven by the original
    # 7 core keys only (regression guard for the has_tail_metrics scope fix).
    assert out["has_tail"] is True


# ---- build_display_results / build_aggregate_display_results (full VM) ----


def _run_with_single_sr(base: dict[str, Any], residual: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        simulation_results={
            "base_risk": base,
            "residual_risk": residual,
            "confidence_intervals": {
                "lower_bound": 1_000_000.0,
                "upper_bound": 2_000_000.0,
                "interval_pct": 95,
            },
            "control_adjustments": [],
            "loss_exceedance_curve": [],
        },
        controls_snapshot=[],
    )


def test_build_display_results_dist_stats_es_row_has_ci_fields() -> None:
    run = _run_with_single_sr(
        _risk_with_se(1.0, es_95_se=10_000.0),
        _risk_with_se(0.5, es_95_se=4_000.0),
    )
    vm = build_display_results(run)
    assert vm is not None
    es95 = next(r for r in vm["dist_stats"]["rows"] if r["label"] == "ES 95%")
    assert es95["residual_se"] == 4_000.0
    assert es95["residual_ci_half"] == pytest.approx(1.96 * 4_000.0)


def test_build_display_results_eur_converts_se_and_ci_half() -> None:
    """EUR rc: es_95_se and es_95_ci_half in dist_stats rows scale by the rate,
    mirroring how expected_shortfall itself is converted (convert-once, at the
    _convert_risk_dict boundary, before _es_ci_fields ever runs)."""
    run = _run_with_single_sr(
        _risk_with_se(1.0, es_95_se=10_000.0),
        _risk_with_se(0.5, es_95_se=4_000.0),
    )
    vm = build_display_results(run, rc=EUR_RC)
    assert vm is not None
    es95 = next(r for r in vm["dist_stats"]["rows"] if r["label"] == "ES 95%")
    assert es95["residual_se"] == pytest.approx(4_000.0 * 0.92)
    assert es95["residual_ci_half"] == pytest.approx(1.96 * 4_000.0 * 0.92)
    assert es95["residual_ci_insufficient"] is False


def test_build_display_results_eur_legacy_row_se_stays_none_no_crash() -> None:
    """EUR rc must not crash converting a risk dict with no expected_shortfall_se
    key at all (out.get returns None, isinstance guard skips it)."""
    run = _run_with_single_sr(_risk_without_se(1.0), _risk_without_se(0.5))
    vm = build_display_results(run, rc=EUR_RC)
    assert vm is not None
    es95 = next(r for r in vm["dist_stats"]["rows"] if r["label"] == "ES 95%")
    assert es95["residual_se"] is None
    assert es95["residual_ci_insufficient"] is False


def test_aggregate_display_results_eur_converts_se_and_ci_half() -> None:
    """Task 11 (folded Task-10 review Minor): closes the currency-coverage gap
    for the AGGREGATE builder's SE conversion. Mirrors
    ``test_build_display_results_eur_converts_se_and_ci_half`` (the SINGLE
    builder's EUR test) but for ``build_aggregate_display_results`` — both
    builders share the same ``_convert_risk_dict`` boundary, so es_<level>_se
    and es_<level>_ci_half must scale by the reporting-currency rate here too."""
    run = SimpleNamespace(
        simulation_results={
            "aggregate_without_controls": _risk_with_se(1.0, es_95_se=10_000.0),
            "aggregate_with_controls": _risk_with_se(0.5, es_95_se=4_000.0),
            "confidence_intervals": {
                "lower_bound": 1_000_000.0,
                "upper_bound": 2_000_000.0,
                "interval_pct": 95,
            },
            "control_value": {"dollars": 500_000.0, "percent": 50.0},
            "n_scenarios": 2,
            "n_simulations": 10_000,
            "per_scenario": [],
        },
        controls_snapshot=[],
        weight_robustness=None,
    )
    vm = build_aggregate_display_results(run, rc=EUR_RC)
    assert vm is not None
    es95 = next(r for r in vm["dist_stats"]["rows"] if r["label"] == "ES 95%")
    assert es95["residual_se"] == pytest.approx(4_000.0 * 0.92)
    assert es95["residual_ci_half"] == pytest.approx(1.96 * 4_000.0 * 0.92)
    assert es95["residual_ci_insufficient"] is False


def test_aggregate_display_results_dist_stats_es_row_has_ci_fields() -> None:
    """AGGREGATE view-model reuses the SAME _convert_risk_dict +
    build_dist_stats_rows path as SINGLE — verifying it here guards against a
    future divergence between the two builders."""
    run = SimpleNamespace(
        simulation_results={
            "aggregate_without_controls": _risk_with_se(1.0, es_95_se=10_000.0),
            "aggregate_with_controls": _risk_with_se(0.5, es_95_se=4_000.0),
            "confidence_intervals": {
                "lower_bound": 1_000_000.0,
                "upper_bound": 2_000_000.0,
                "interval_pct": 95,
            },
            "control_value": {"dollars": 500_000.0, "percent": 50.0},
            "n_scenarios": 2,
            "n_simulations": 10_000,
            "per_scenario": [],
        },
        controls_snapshot=[],
        weight_robustness=None,
    )
    vm = build_aggregate_display_results(run)
    assert vm is not None
    es95 = next(r for r in vm["dist_stats"]["rows"] if r["label"] == "ES 95%")
    assert es95["residual_se"] == 4_000.0
    assert es95["residual_ci_half"] == pytest.approx(1.96 * 4_000.0)
