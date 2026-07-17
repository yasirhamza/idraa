"""Unit tests for services/run_view_model.py.

These tests use plain dicts (no DB / ORM) — the view-model builder is a
pure transformation. The route layer is responsible for fetching the
RiskAnalysisRun ORM row; this module only converts the JSON payload.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from idraa.services._view_model_helpers import build_dist_stats_rows
from idraa.services.reports import DIST_STATS_DEFINITIONAL_NOTE
from idraa.services.run_view_model import (
    _build_control_effectiveness_rows,
    _build_headline_ale,
    _build_risk_comparison,
    _has_ci_band,
    _strip_samples,
    build_display_results,
)

# ---- _strip_samples ----------------------------------------------------


def test_strip_samples_drops_simulation_results_key() -> None:
    """The 1000+ float sample array is the only key that should be stripped."""
    risk = {
        "annualized_loss_expectancy": 1_500_000.0,
        "mean": 1_500_000.0,
        "median": 1_400_000.0,
        "std_deviation": 250_000.0,
        "simulation_results": [1.0, 2.0, 3.0, 4.0],
    }
    out = _strip_samples(risk)
    assert "simulation_results" not in out
    # All other keys preserved with original values:
    assert out["annualized_loss_expectancy"] == 1_500_000.0
    assert out["mean"] == 1_500_000.0
    assert out["median"] == 1_400_000.0
    assert out["std_deviation"] == 250_000.0


def test_strip_samples_handles_missing_key_gracefully() -> None:
    """If simulation_results is already absent, returns dict unchanged."""
    risk = {"annualized_loss_expectancy": 100.0, "mean": 100.0}
    assert _strip_samples(risk) == risk


def test_strip_samples_handles_empty_dict() -> None:
    """Defensive: an empty dict (e.g. from sr.get('base_risk', {})) is safe."""
    assert _strip_samples({}) == {}


# ---- _has_ci_band ------------------------------------------------------


def test_has_ci_band_real_bounds() -> None:
    # #202: a real band requires the interval_pct marker AND lower < upper.
    ci = {"lower_bound": 980_000.0, "upper_bound": 1_510_000.0, "interval_pct": 95}
    assert _has_ci_band(ci) is True


def test_has_ci_band_default_zeros() -> None:
    ci = {"lower_bound": 0.0, "upper_bound": 0.0, "interval_pct": 95}
    assert _has_ci_band(ci) is False


def test_has_ci_band_missing_key() -> None:
    assert _has_ci_band({}) is False


def test_has_ci_band_legacy_no_interval_pct_suppressed() -> None:
    # #202: legacy rows carry the retired Gaussian SE band geometry under
    # lower/upper but NO interval_pct — they MUST be suppressed (not relabeled
    # "95% interval", which would be an affirmative mislabel).
    ci = {"lower_bound": 980_000.0, "upper_bound": 1_510_000.0}
    assert _has_ci_band(ci) is False


def test_has_ci_band_equal_bounds_treated_as_no_ci() -> None:
    # Equal bounds = degenerate distribution; no meaningful band to render.
    ci = {"lower_bound": 1_000_000.0, "upper_bound": 1_000_000.0, "interval_pct": 95}
    assert _has_ci_band(ci) is False


# ---- _build_headline_ale ----------------------------------------------


def test_build_headline_ale_with_ci() -> None:
    residual = {"annualized_loss_expectancy": 1_240_000.0}
    ci = {"lower_bound": 980_000.0, "upper_bound": 1_510_000.0, "interval_pct": 95}
    headline = _build_headline_ale(residual, ci)
    assert headline == {
        "value": 1_240_000.0,
        "lo": 980_000.0,
        "hi": 1_510_000.0,
        "has_ci_band": True,
    }


def test_build_headline_ale_legacy_no_ci() -> None:
    residual = {"annualized_loss_expectancy": 1_240_000.0}
    ci = {"lower_bound": 0.0, "upper_bound": 0.0}
    headline = _build_headline_ale(residual, ci)
    assert headline["has_ci_band"] is False
    assert headline["value"] == 1_240_000.0


def test_build_headline_ale_zero_value() -> None:
    residual = {"annualized_loss_expectancy": 0.0}
    ci = {"lower_bound": 0.0, "upper_bound": 0.0}
    headline = _build_headline_ale(residual, ci)
    assert headline["value"] == 0.0
    assert headline["has_ci_band"] is False


# ---- _build_risk_comparison -------------------------------------------


def test_build_risk_comparison_typical() -> None:
    base = {"annualized_loss_expectancy": 2_100_000.0}
    residual = {"annualized_loss_expectancy": 1_240_000.0}
    cmp = _build_risk_comparison(base, residual)
    assert cmp["base"] == 2_100_000.0
    assert cmp["residual"] == 1_240_000.0
    assert cmp["reduction"] == 860_000.0
    assert cmp["reduction_pct"] == pytest.approx(40.95, abs=0.01)


def test_build_risk_comparison_zero_base() -> None:
    base = {"annualized_loss_expectancy": 0.0}
    residual = {"annualized_loss_expectancy": 0.0}
    cmp = _build_risk_comparison(base, residual)
    assert cmp["reduction"] == 0.0
    assert cmp["reduction_pct"] is None  # avoids division by zero


def test_build_risk_comparison_negative_reduction() -> None:
    # Controls made it worse (e.g. unfavorable parameter combinations).
    base = {"annualized_loss_expectancy": 1_000_000.0}
    residual = {"annualized_loss_expectancy": 1_200_000.0}
    cmp = _build_risk_comparison(base, residual)
    assert cmp["reduction"] == -200_000.0
    assert cmp["reduction_pct"] == pytest.approx(-20.0, abs=0.01)


def test_build_risk_comparison_no_controls_residual_equals_base() -> None:
    # A baseline-only run has residual == base; reduction == 0.
    base = {"annualized_loss_expectancy": 1_500_000.0}
    residual = {"annualized_loss_expectancy": 1_500_000.0}
    cmp = _build_risk_comparison(base, residual)
    assert cmp["reduction"] == 0.0
    assert cmp["reduction_pct"] == 0.0


def test_build_risk_comparison_negative_base_returns_none_pct() -> None:
    """Theoretical edge: a negative base ALE would produce sign-flipped
    percentages under naive division. The implementation gates `b > 0`
    so negative base produces reduction_pct = None — caller renders '—'."""
    base = {"annualized_loss_expectancy": -1_000_000.0}
    residual = {"annualized_loss_expectancy": -500_000.0}
    cmp = _build_risk_comparison(base, residual)
    assert cmp["base"] == -1_000_000.0
    assert cmp["residual"] == -500_000.0
    assert cmp["reduction"] == -500_000.0  # b - r = -1M - -500k = -500k
    assert cmp["reduction_pct"] is None


# ---- _build_control_effectiveness_rows --------------------------------


def test_build_control_effectiveness_rows_sorted_desc() -> None:
    adjustments = [
        {"control_id": "c1", "effectiveness": 0.50},
        {"control_id": "c2", "effectiveness": 0.85},
        {"control_id": "c3", "effectiveness": 0.65},
    ]
    snapshot = [
        {"control_id": "c1", "name": "Backups"},
        {"control_id": "c2", "name": "MFA"},
        {"control_id": "c3", "name": "Patching"},
    ]
    rows = _build_control_effectiveness_rows(adjustments, snapshot)
    assert [r["name"] for r in rows] == ["MFA", "Patching", "Backups"]
    assert [r["effectiveness"] for r in rows] == [0.85, 0.65, 0.50]


def test_build_control_effectiveness_rows_tie_break_by_name_asc() -> None:
    adjustments = [
        {"control_id": "c1", "effectiveness": 0.85},
        {"control_id": "c2", "effectiveness": 0.85},
        {"control_id": "c3", "effectiveness": 0.85},
    ]
    snapshot = [
        {"control_id": "c1", "name": "Zeta"},
        {"control_id": "c2", "name": "Alpha"},
        {"control_id": "c3", "name": "Mu"},
    ]
    rows = _build_control_effectiveness_rows(adjustments, snapshot)
    assert [r["name"] for r in rows] == ["Alpha", "Mu", "Zeta"]


def test_build_control_effectiveness_rows_unknown_control_id() -> None:
    adjustments = [
        {"control_id": "c-missing", "effectiveness": 0.50},
    ]
    snapshot: list[dict[str, Any]] = []
    rows = _build_control_effectiveness_rows(adjustments, snapshot)
    assert rows == [
        {"control_id": "c-missing", "name": "(unknown)", "effectiveness": 0.50},
    ]


def test_build_control_effectiveness_rows_v1_snapshot_shape() -> None:
    # V1 snapshot has control_id + name at top level (same as V2).
    adjustments = [{"control_id": "c1", "effectiveness": 0.70}]
    snapshot = [
        {
            "snapshot_version": 1,
            "control_id": "c1",
            "name": "Legacy Control",
            "control_strength": 0.7,
            "control_reliability": 0.8,
            "control_coverage": 0.9,
        },
    ]
    rows = _build_control_effectiveness_rows(adjustments, snapshot)
    assert rows[0]["name"] == "Legacy Control"


def test_build_control_effectiveness_rows_v2_snapshot_shape() -> None:
    adjustments = [{"control_id": "c1", "effectiveness": 0.70}]
    snapshot = [
        {
            "snapshot_version": 2,
            "control_id": "c1",
            "name": "Modern Control",
            "domain": "VMC",
            "assignments": [],
        },
    ]
    rows = _build_control_effectiveness_rows(adjustments, snapshot)
    assert rows[0]["name"] == "Modern Control"


def test_build_control_effectiveness_rows_missing_effectiveness_key() -> None:
    # Defensive: an adjustment dict missing "effectiveness" sorts to bottom (0.0).
    adjustments: list[dict[str, Any]] = [
        {"control_id": "c1", "effectiveness": 0.70},
        {"control_id": "c2"},  # no effectiveness key
    ]
    snapshot = [
        {"control_id": "c1", "name": "Has Score"},
        {"control_id": "c2", "name": "No Score"},
    ]
    rows = _build_control_effectiveness_rows(adjustments, snapshot)
    assert rows[0]["name"] == "Has Score"
    assert rows[1]["name"] == "No Score"
    assert rows[1]["effectiveness"] == 0.0


def test_build_control_effectiveness_rows_empty_adjustments() -> None:
    rows = _build_control_effectiveness_rows([], [])
    assert rows == []


# ---- build_display_results (top level) --------------------------------


def test_build_display_results_returns_none_when_no_simulation_results() -> None:
    run = SimpleNamespace(simulation_results=None, controls_snapshot=[])
    assert build_display_results(run) is None


def test_build_display_results_builds_full_view_model() -> None:
    run = SimpleNamespace(
        simulation_results={
            "base_risk": {
                "annualized_loss_expectancy": 2_100_000.0,
                "mean": 2_100_000.0,
                "median": 2_000_000.0,
                "std_deviation": 500_000.0,
                "var_95": 3_000_000.0,
                "var_99": 4_000_000.0,
                "loss_event_frequency": 4.0,
                "loss_magnitude": 525_000.0,
                "n_simulations": 1000,
                "simulation_results": [1.0, 2.0, 3.0],  # will be stripped
            },
            "residual_risk": {
                "annualized_loss_expectancy": 1_240_000.0,
                "mean": 1_240_000.0,
                "median": 1_200_000.0,
                "std_deviation": 300_000.0,
                "var_95": 1_800_000.0,
                "var_99": 2_400_000.0,
                "loss_event_frequency": 2.0,
                "loss_magnitude": 620_000.0,
                "n_simulations": 1000,
                "simulation_results": [1.0, 2.0, 3.0],  # will be stripped
            },
            "control_adjustments": [
                {
                    "control_id": "c1",
                    "effectiveness": 0.85,
                    "tef_multiplier": 0.6,
                    "vulnerability_multiplier": 0.5,
                    "primary_loss_multiplier": 1.0,
                    "secondary_loss_multiplier": 1.0,
                },
            ],
            "confidence_intervals": {
                "lower_bound": 980_000.0,
                "upper_bound": 1_510_000.0,
                "interval_pct": 95,
                "sample_size": 1000,
            },
            "loss_exceedance_curve": [{"loss": 100.0, "probability": 0.5}],
        },
        controls_snapshot=[
            {
                "snapshot_version": 2,
                "control_id": "c1",
                "name": "MFA",
                "domain": "VMC",
                "assignments": [],
            },
        ],
    )
    vm = build_display_results(run)
    assert vm is not None
    assert vm["headline_ale"]["value"] == 1_240_000.0
    assert vm["headline_ale"]["has_ci_band"] is True
    assert vm["risk_comparison"]["base"] == 2_100_000.0
    assert vm["risk_comparison"]["reduction"] == 860_000.0
    assert vm["control_effectiveness_rows"][0]["name"] == "MFA"
    # Sample arrays stripped from base/residual; other keys preserved:
    assert "simulation_results" not in vm["base_risk"]
    assert "simulation_results" not in vm["residual_risk"]
    assert vm["base_risk"]["mean"] == 2_100_000.0
    assert vm["base_risk"]["std_deviation"] == 500_000.0
    assert vm["base_risk"]["var_95"] == 3_000_000.0
    assert vm["residual_risk"]["var_99"] == 2_400_000.0
    # Other top-level keys preserved:
    assert vm["loss_exceedance_curve"] == [{"loss": 100.0, "probability": 0.5}]
    assert vm["confidence_intervals"]["interval_pct"] == 95
    # control_adjustments is NOT carried at top level (consumed into
    # control_effectiveness_rows; verified zero callers via grep).
    assert "control_adjustments" not in vm


def test_build_display_results_when_control_adjustments_key_absent() -> None:
    """Very-old legacy: simulation_results dict has no control_adjustments
    key at all. Must not raise; treat as empty list (zero controls)."""
    run = SimpleNamespace(
        simulation_results={
            "base_risk": {"annualized_loss_expectancy": 1_000_000.0},
            "residual_risk": {"annualized_loss_expectancy": 1_000_000.0},
            "confidence_intervals": {"lower_bound": 800_000.0, "upper_bound": 1_200_000.0},
            "loss_exceedance_curve": [],
            # NOTE: control_adjustments key intentionally absent
        },
        controls_snapshot=[],
    )
    vm = build_display_results(run)
    assert vm is not None
    assert vm["control_effectiveness_rows"] == []  # empty list, not crash


def test_build_display_results_when_controls_snapshot_is_none() -> None:
    """Defensive: controls_snapshot can be None per the column default
    on RiskAnalysisRun. The view-model must coerce to [] (avoids
    TypeError in the dict-comprehension over None)."""
    run = SimpleNamespace(
        simulation_results={
            "base_risk": {"annualized_loss_expectancy": 1_000_000.0},
            "residual_risk": {"annualized_loss_expectancy": 800_000.0},
            "confidence_intervals": {"lower_bound": 0.0, "upper_bound": 0.0},
            "control_adjustments": [],
            "loss_exceedance_curve": [],
        },
        controls_snapshot=None,
    )
    vm = build_display_results(run)
    assert vm is not None
    assert vm["control_effectiveness_rows"] == []


def test_build_display_results_passes_through_exceedance_probability_curve() -> None:
    from idraa.services.run_view_model import build_display_results

    run = SimpleNamespace(
        simulation_results={
            "base_risk": {},
            "residual_risk": {},
            "confidence_intervals": {},
            "control_adjustments": [],
            "loss_exceedance_curve": [],
            "exceedance_probability_curve": [
                {"percentile": 0.5, "loss": 1234.0},
                {"percentile": 0.95, "loss": 9999.0},
            ],
        },
        controls_snapshot=[],
    )
    result = build_display_results(run)
    assert result is not None
    assert result["exceedance_probability_curve"] == [
        {"percentile": 0.5, "loss": 1234.0},
        {"percentile": 0.95, "loss": 9999.0},
    ]


# ---- #266 D1: tail-risk metrics pass-through + backward compat ---------


def test_build_display_results_surfaces_tail_metrics_on_risk_dicts() -> None:
    """var_90/var_999 + expected_shortfall persisted on each risk dict by the
    executor must survive _strip_samples into base_risk/residual_risk."""
    run = SimpleNamespace(
        simulation_results={
            "base_risk": {
                "annualized_loss_expectancy": 2_000_000.0,
                "var_90": 2_500_000.0,
                "var_95": 3_000_000.0,
                "var_99": 4_000_000.0,
                "var_999": 5_000_000.0,
                "expected_shortfall": {
                    "es_95": 3_500_000.0,
                    "es_99": 4_500_000.0,
                    "es_999": 5_200_000.0,
                },
                "simulation_results": [1.0, 2.0, 3.0],
            },
            "residual_risk": {
                "annualized_loss_expectancy": 1_000_000.0,
                "var_90": 1_200_000.0,
                "var_999": 2_400_000.0,
                "expected_shortfall": {
                    "es_95": 1_600_000.0,
                    "es_99": 2_000_000.0,
                    "es_999": 2_500_000.0,
                },
                "simulation_results": [1.0, 2.0, 3.0],
            },
            "confidence_intervals": {},
            "control_adjustments": [],
            "loss_exceedance_curve": [],
        },
        controls_snapshot=[],
    )
    vm = build_display_results(run)
    assert vm is not None
    assert vm["base_risk"]["var_90"] == 2_500_000.0
    assert vm["base_risk"]["var_999"] == 5_000_000.0
    assert vm["base_risk"]["expected_shortfall"]["es_999"] == 5_200_000.0
    assert vm["residual_risk"]["expected_shortfall"]["es_95"] == 1_600_000.0
    # Flat residual-side tail_risk block surfaced top-level for the template.
    assert vm["tail_risk"]["var_90"] == 1_200_000.0
    assert vm["tail_risk"]["var_999"] == 2_400_000.0
    assert vm["tail_risk"]["es_999"] == 2_500_000.0


def test_build_display_results_old_run_without_tail_metrics_does_not_break() -> None:
    """OLD persisted runs lack var_90/var_999/expected_shortfall entirely.
    The view-model must build without KeyError; callers use .get() defaults."""
    run = SimpleNamespace(
        simulation_results={
            "base_risk": {
                "annualized_loss_expectancy": 2_000_000.0,
                "var_95": 3_000_000.0,
                "var_99": 4_000_000.0,
                # NOTE: var_90 / var_999 / expected_shortfall absent
            },
            "residual_risk": {
                "annualized_loss_expectancy": 1_000_000.0,
                "var_95": 1_800_000.0,
                "var_99": 2_400_000.0,
            },
            "confidence_intervals": {},
            "control_adjustments": [],
            "loss_exceedance_curve": [],
        },
        controls_snapshot=[],
    )
    vm = build_display_results(run)
    assert vm is not None
    # Missing keys simply absent on the risk dicts; consumer uses .get().
    assert vm["base_risk"].get("var_999") is None
    assert vm["base_risk"].get("expected_shortfall") is None
    assert vm["residual_risk"]["var_95"] == 1_800_000.0
    # tail_risk block must not raise on old runs — zeros, not KeyError.
    assert vm["tail_risk"]["var_999"] == 0.0
    assert vm["tail_risk"]["es_999"] == 0.0
    # var_95/var_99 still flow through from the residual dict where present.
    assert vm["tail_risk"]["var_95"] == 1_800_000.0


# ---- build_dist_stats_rows + DIST_STATS_DEFINITIONAL_NOTE (#353 Task 1) ----


def _full_risk(scale: float) -> dict:
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
    }


def test_dist_stats_rows_full_ladder_and_delta_hand_math() -> None:
    """Full tail-capable run: 10 rows in ladder order, delta = base - residual."""
    out = build_dist_stats_rows(_full_risk(1.0), _full_risk(0.5))
    assert out["has_tail"] is True
    labels = [r["label"] for r in out["rows"]]
    assert labels == [
        "Mean",
        "Median",
        "Std dev",
        "VaR 90%",
        "VaR 95%",
        "VaR 99%",
        "VaR 99.9%",
        "ES 95%",
        "ES 99%",
        "ES 99.9%",
    ]
    # VaR 95%: base=4_500_000, residual=2_250_000 (0.5 scale), delta=2_250_000
    var95 = next(r for r in out["rows"] if r["label"] == "VaR 95%")
    assert var95["base"] == 4_500_000.0
    assert var95["residual"] == 2_250_000.0
    assert var95["delta"] == 2_250_000.0  # base - residual, positive = reduced

    # ES 99%: base=6_200_000, residual=3_100_000, delta=3_100_000
    es99 = next(r for r in out["rows"] if r["label"] == "ES 99%")
    assert es99["delta"] == 3_100_000.0

    # Gated rows: VaR 90%, VaR 99.9%, ES 95%, ES 99%, ES 99.9%
    assert [r["label"] for r in out["rows"] if r["gated"]] == [
        "VaR 90%",
        "VaR 99.9%",
        "ES 95%",
        "ES 99%",
        "ES 99.9%",
    ]


def test_dist_stats_rows_legacy_run_gates_tail() -> None:
    """Legacy run (no var_90 / expected_shortfall): has_tail=False, gated rows omitted."""
    legacy = {
        "mean": 1.0,
        "median": 1.0,
        "std_deviation": 1.0,
        "var_95": 1.0,
        "var_99": 1.0,
    }
    out = build_dist_stats_rows(legacy, legacy)
    assert out["has_tail"] is False
    assert [r["label"] for r in out["rows"]] == [
        "Mean",
        "Median",
        "Std dev",
        "VaR 95%",
        "VaR 99%",
    ]


def test_dist_stats_rows_one_side_legacy_gates_tail() -> None:
    """Mixed sides: base full, residual legacy → has_tail=False.

    Never render a ladder with one fabricated side — if either side lacks
    tail metrics the gated rows are omitted entirely.
    """
    full = _full_risk(1.0)
    legacy = {
        "mean": 1.0,
        "median": 1.0,
        "std_deviation": 1.0,
        "var_95": 1.0,
        "var_99": 1.0,
    }
    out = build_dist_stats_rows(full, legacy)
    assert out["has_tail"] is False
    labels = [r["label"] for r in out["rows"]]
    assert "VaR 90%" not in labels
    assert "ES 95%" not in labels


def test_display_results_expose_dist_stats() -> None:
    """build_display_results exposes dist_stats + dist_stats_note (SINGLE vm)."""
    run = SimpleNamespace(
        simulation_results={
            "base_risk": _full_risk(1.0),
            "residual_risk": _full_risk(0.5),
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
    vm = build_display_results(run)
    assert vm is not None
    assert "dist_stats" in vm
    assert vm["dist_stats"]["has_tail"] is True
    assert len(vm["dist_stats"]["rows"]) == 10
    assert "dist_stats_note" in vm
    assert vm["dist_stats_note"] == DIST_STATS_DEFINITIONAL_NOTE
