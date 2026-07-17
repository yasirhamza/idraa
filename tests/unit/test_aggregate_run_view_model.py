"""Unit tests for build_aggregate_display_results (PR xi F7).

Includes a methodology-guard regression test asserting the view-model
dict has NO contribution_pct / diversification_benefit / top_contributors /
risk_concentration_index keys per the PR psi + PR phi cleanup.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from idraa.services.aggregate_run_view_model import (
    _build_per_scenario_control_matrix,
    build_aggregate_display_results,
)


def _aggregate_run(simulation_results: dict[str, Any] | None = None) -> SimpleNamespace:
    return SimpleNamespace(simulation_results=simulation_results)


def _full_aggregate_payload() -> dict[str, Any]:
    return {
        "per_scenario": [
            {
                "scenario_id": "sid_1",
                "scenario_name": "S1",
                "base_risk": {
                    "annualized_loss_expectancy": 1_000_000.0,
                    "simulation_results": [1.0] * 100,
                },
                "residual_risk": {
                    "annualized_loss_expectancy": 600_000.0,
                    "simulation_results": [0.6] * 100,
                },
                "control_adjustments": [],
                "confidence_intervals": {},
                "loss_exceedance_curve": [],
            },
            {
                "scenario_id": "sid_2",
                "scenario_name": "S2",
                "base_risk": {
                    "annualized_loss_expectancy": 500_000.0,
                    "simulation_results": [0.5] * 100,
                },
                "residual_risk": {
                    "annualized_loss_expectancy": 300_000.0,
                    "simulation_results": [0.3] * 100,
                },
                "control_adjustments": [],
                "confidence_intervals": {},
                "loss_exceedance_curve": [],
            },
        ],
        "aggregate_with_controls": {
            "annualized_loss_expectancy": 900_000.0,
            "simulation_results": [0.9] * 100,
            "loss_exceedance_curve": [{"loss": 900_000, "probability": 0.5}],
        },
        "aggregate_without_controls": {
            "annualized_loss_expectancy": 1_500_000.0,
            "simulation_results": [1.5] * 100,
            "loss_exceedance_curve": [{"loss": 1_500_000, "probability": 0.5}],
        },
        "control_value": {"dollars": 600_000.0, "percent": 40.0},
        "confidence_intervals": {
            "lower_bound": 850_000,
            "upper_bound": 950_000,
            "interval_pct": 95,
            "sample_size": 100,
        },
        "n_scenarios": 2,
        "n_simulations": 100,
    }


def test_returns_none_for_pending_run() -> None:
    assert build_aggregate_display_results(_aggregate_run(simulation_results=None)) is None


def test_strips_sample_arrays() -> None:
    result = build_aggregate_display_results(_aggregate_run(_full_aggregate_payload()))
    assert result is not None
    assert "simulation_results" not in result["aggregate_with_controls"]
    assert "simulation_results" not in result["aggregate_without_controls"]


def test_per_scenario_ale_rows_sorted_desc_by_base_ale() -> None:
    result = build_aggregate_display_results(_aggregate_run(_full_aggregate_payload()))
    assert result is not None
    rows = result["per_scenario_ale_rows"]
    assert rows[0]["scenario_id"] == "sid_1"  # base_ale 1M > 500k
    assert rows[1]["scenario_id"] == "sid_2"
    assert rows[0]["base_ale"] > rows[1]["base_ale"]


def test_no_contamination_keys_in_view_model() -> None:
    """METHODOLOGY GUARD (PR psi + PR phi): view-model must not surface
    contribution_pct / diversification_benefit / top_contributors / risk_concentration_index."""
    result = build_aggregate_display_results(_aggregate_run(_full_aggregate_payload()))
    assert result is not None
    forbidden = {
        "contribution_pct",
        "diversification_benefit",
        "top_contributors",
        "risk_concentration_index",
    }
    # Top-level
    assert not (set(result.keys()) & forbidden), (
        f"Top-level contamination: {set(result.keys()) & forbidden}"
    )
    # Per-scenario rows
    for row in result["per_scenario_ale_rows"]:
        assert not (set(row.keys()) & forbidden), (
            f"Per-scenario contamination: {set(row.keys()) & forbidden}"
        )


def test_confidence_intervals_shape_preserved() -> None:
    result = build_aggregate_display_results(_aggregate_run(_full_aggregate_payload()))
    assert result is not None
    ci = result["confidence_intervals"]
    assert ci["interval_pct"] == 95
    assert ci["lower_bound"] == 850_000
    assert ci["upper_bound"] == 950_000


def test_build_aggregate_display_results_includes_dual_epc() -> None:
    from idraa.services.aggregate_run_view_model import build_aggregate_display_results

    run = SimpleNamespace(
        simulation_results={
            "aggregate_with_controls": {
                "annualized_loss_expectancy": 800.0,
                "loss_exceedance_curve": [],
            },
            "aggregate_without_controls": {
                "annualized_loss_expectancy": 1500.0,
                "loss_exceedance_curve": [],
            },
            "control_value": {"dollars": 700.0, "percent": 46.6},
            "confidence_intervals": {},
            "dual_epc": {
                "with_controls": [{"percentile": 0.5, "loss": 100.0}],
                "without_controls": [{"percentile": 0.5, "loss": 200.0}],
            },
            "per_scenario": [],
            "n_scenarios": 2,
            "n_simulations": 1000,
        },
    )
    out = build_aggregate_display_results(run)
    assert out is not None
    assert "dual_epc" in out
    # Loss values are clamped to >= $1 by the macro-prep layer (mirrors dual_lec).
    assert out["dual_epc"]["with_controls"] == [{"percentile": 0.5, "loss": 100.0}]
    assert out["dual_epc"]["without_controls"] == [{"percentile": 0.5, "loss": 200.0}]


def test_build_aggregate_display_results_clamps_epc_zero_loss_to_one_dollar() -> None:
    from idraa.services.aggregate_run_view_model import build_aggregate_display_results

    run = SimpleNamespace(
        simulation_results={
            "aggregate_with_controls": {
                "annualized_loss_expectancy": 800.0,
                "loss_exceedance_curve": [],
            },
            "aggregate_without_controls": {
                "annualized_loss_expectancy": 1500.0,
                "loss_exceedance_curve": [],
            },
            "control_value": {"dollars": 700.0, "percent": 46.6},
            "confidence_intervals": {},
            "dual_epc": {
                "with_controls": [{"percentile": 0.01, "loss": 0.0}],
                "without_controls": [{"percentile": 0.01, "loss": -5.0}],
            },
            "per_scenario": [],
            "n_scenarios": 2,
            "n_simulations": 1000,
        },
    )
    out = build_aggregate_display_results(run)
    assert out is not None
    assert out["dual_epc"]["with_controls"][0]["loss"] == 1.0
    assert out["dual_epc"]["without_controls"][0]["loss"] == 1.0


def test_per_scenario_control_matrix_happy_path() -> None:
    """Asymmetric controls across scenarios: row/col order + sparse cells (Shapley reader)."""
    payload = {
        "per_scenario": [
            {
                "scenario_id": "sid_high",
                "scenario_name": "High ALE",
                "base_risk": {"annualized_loss_expectancy": 1_000_000.0},
                "residual_risk": {"annualized_loss_expectancy": 600_000.0},
                "control_adjustments": [
                    {"control_id": "c_x", "control_name": "X", "shapley_value": 50_000.0},
                ],
            },
            {
                "scenario_id": "sid_mid",
                "scenario_name": "Mid ALE",
                "base_risk": {"annualized_loss_expectancy": 500_000.0},
                "residual_risk": {"annualized_loss_expectancy": 300_000.0},
                "control_adjustments": [
                    {"control_id": "c_x", "control_name": "X", "shapley_value": 10_000.0},
                    {"control_id": "c_y", "control_name": "Y", "shapley_value": 30_000.0},
                ],
            },
            {
                "scenario_id": "sid_low",
                "scenario_name": "Low ALE",
                "base_risk": {"annualized_loss_expectancy": 100_000.0},
                "residual_risk": {"annualized_loss_expectancy": 80_000.0},
                "control_adjustments": [],
            },
        ],
        "aggregate_with_controls": {
            "annualized_loss_expectancy": 980_000.0,
            "simulation_results": [],
        },
        "aggregate_without_controls": {
            "annualized_loss_expectancy": 1_600_000.0,
            "simulation_results": [],
        },
        "control_value": {"dollars": 620_000.0, "percent": 38.75},
        "confidence_intervals": {},
        "n_scenarios": 3,
        "n_simulations": 100,
    }
    result = build_aggregate_display_results(_aggregate_run(simulation_results=payload))
    matrix = result["per_scenario_control_matrix"]

    # Column order: X first ($60k total) then Y ($30k total)
    assert [c["control_id"] for c in matrix["controls"]] == ["c_x", "c_y"]
    assert [c["control_name"] for c in matrix["controls"]] == ["X", "Y"]
    assert matrix["controls"][0]["total_reduction"] == 60_000.0
    assert matrix["controls"][1]["total_reduction"] == 30_000.0

    # Row order: desc by base_ale
    assert [r["scenario_id"] for r in matrix["rows"]] == ["sid_high", "sid_mid", "sid_low"]

    # Cells are self-describing dicts, positional parallel to controls column order.
    # 2026-07-04 mean+typical side-by-side: cells gain a paired "value_typical"
    # field (always the legacy shapley_value key). This fixture has no
    # shapley_value_mean at all (legacy shape), so the primary "value" falls
    # back to shapley_value too — value == value_typical on every cell here.
    # #100: touched cells carry a factors sub-dict (None values when the
    # persisted adjustment lacks multiplier keys, as this fixture's do);
    # untouched placeholder cells carry NO factors key at all.
    assert matrix["rows"][0]["cells"] == [
        {
            "control_id": "c_x",
            "value": 50_000.0,
            "value_typical": 50_000.0,
            "factors": {"tef": None, "vuln": None, "pl": None, "sl": None},
        },
        {"control_id": "c_y", "value": None, "value_typical": None},
    ]
    assert matrix["rows"][1]["cells"] == [
        {
            "control_id": "c_x",
            "value": 10_000.0,
            "value_typical": 10_000.0,
            "factors": {"tef": None, "vuln": None, "pl": None, "sl": None},
        },
        {
            "control_id": "c_y",
            "value": 30_000.0,
            "value_typical": 30_000.0,
            "factors": {"tef": None, "vuln": None, "pl": None, "sl": None},
        },
    ]
    assert matrix["rows"][2]["cells"] == [
        {"control_id": "c_x", "value": None, "value_typical": None},
        {"control_id": "c_y", "value": None, "value_typical": None},
    ]


def test_per_scenario_control_matrix_iteration_contract_N5_controls_N4_scenarios() -> None:
    """Iteration contract: N≥3 on BOTH axes (controls inner, scenarios outer).

    Catches `[0]` / `[-1]` slips on either iteration axis. Every scenario
    gets a NON-empty control_adjustments list of 5 distinct controls so the
    outer per_scenario loop and inner control_adjustments loop are both
    exercised at N≥3.
    """
    scenarios = []
    for i in range(4):
        scenarios.append(
            {
                "scenario_id": f"s_{i}",
                "scenario_name": f"Sc {i}",
                "base_risk": {"annualized_loss_expectancy": 1000.0 - i},
                "residual_risk": {"annualized_loss_expectancy": 500.0},
                "control_adjustments": [
                    # Each scenario has 5 distinct controls; the SAME 5 controls
                    # across scenarios so the column count stays at 5.
                    {
                        "control_id": f"c_{j}",
                        "control_name": f"Ctl{j}",
                        "shapley_value": 1000.0 * (5 - j) + i,  # vary per scenario
                    }
                    for j in range(5)
                ],
            }
        )

    result = build_aggregate_display_results(
        _aggregate_run(
            simulation_results={
                "per_scenario": scenarios,
                "aggregate_with_controls": {"simulation_results": []},
                "aggregate_without_controls": {"simulation_results": []},
                "control_value": {"dollars": 0, "percent": 0},
                "confidence_intervals": {},
                "n_scenarios": 4,
                "n_simulations": 1,
            }
        )
    )
    matrix = result["per_scenario_control_matrix"]
    assert len(matrix["controls"]) == 5, "All 5 controls must appear in columns"
    assert len(matrix["rows"]) == 4, "All 4 scenarios must appear in rows"
    # Each row's cells: all 5 non-None values (every scenario has every control)
    for row in matrix["rows"]:
        assert len(row["cells"]) == 5, "Every row's cells parallel to 5 columns"
        assert all(cell["value"] is not None for cell in row["cells"]), (
            f"Row {row['scenario_id']} missing cell values — inner-loop [0]/[-1] slip"
        )


def test_per_scenario_control_matrix_empty_everything() -> None:
    """Zero controls across all scenarios → empty controls AND empty rows.

    Empty-state short-circuit: builder returns {"controls": [], "rows": []}
    so the template's `{% if matrix.controls %}` gate cleanly suppresses the
    table and the `{% else %}` emits the info alert.
    """
    payload = {
        "per_scenario": [
            {
                "scenario_id": "s1",
                "scenario_name": "S1",
                "base_risk": {"annualized_loss_expectancy": 100.0},
                "residual_risk": {"annualized_loss_expectancy": 100.0},
                "control_adjustments": [],
            },
            {
                "scenario_id": "s2",
                "scenario_name": "S2",
                "base_risk": {"annualized_loss_expectancy": 50.0},
                "residual_risk": {"annualized_loss_expectancy": 50.0},
                "control_adjustments": [],
            },
        ],
        "aggregate_with_controls": {"simulation_results": []},
        "aggregate_without_controls": {"simulation_results": []},
        "control_value": {"dollars": 0, "percent": 0},
        "confidence_intervals": {},
        "n_scenarios": 2,
        "n_simulations": 1,
    }
    matrix = build_aggregate_display_results(_aggregate_run(simulation_results=payload))[
        "per_scenario_control_matrix"
    ]
    # 2026-07-04 mean+typical side-by-side: the empty-state dict gains a "basis"
    # field (defaults to "typical" — no data to derive a real basis from).
    assert matrix == {"controls": [], "rows": [], "basis": "typical"}


def test_per_scenario_control_matrix_tie_break_determinism() -> None:
    """Two controls with identical total_reduction → secondary sort by name asc."""
    payload = {
        "per_scenario": [
            {
                "scenario_id": "s1",
                "scenario_name": "S1",
                "base_risk": {"annualized_loss_expectancy": 1000.0},
                "residual_risk": {"annualized_loss_expectancy": 500.0},
                "control_adjustments": [
                    {"control_id": "cz", "control_name": "Zebra", "shapley_value": 500.0},
                    {"control_id": "ca", "control_name": "Alpha", "shapley_value": 500.0},
                ],
            },
        ],
        "aggregate_with_controls": {"simulation_results": []},
        "aggregate_without_controls": {"simulation_results": []},
        "control_value": {"dollars": 0, "percent": 0},
        "confidence_intervals": {},
        "n_scenarios": 1,
        "n_simulations": 1,
    }
    m1 = build_aggregate_display_results(_aggregate_run(simulation_results=payload))[
        "per_scenario_control_matrix"
    ]
    m2 = build_aggregate_display_results(_aggregate_run(simulation_results=payload))[
        "per_scenario_control_matrix"
    ]
    # Alpha before Zebra both times (stable, name asc on tie)
    assert [c["control_name"] for c in m1["controls"]] == ["Alpha", "Zebra"]
    assert [c["control_name"] for c in m2["controls"]] == ["Alpha", "Zebra"]


def test_per_scenario_control_matrix_full_coverage_shape() -> None:
    """AGGREGATE where every scenario has every control: all cells must be populated.

    Same control_adjustments list repeated on every scenario (mirrors the
    pre-#89 union model but also describes any full-coverage scenario set).
    Matrix must render all cells populated — that's the truthful representation.
    """
    union_adjustments = [
        {"control_id": "u1", "control_name": "U1", "shapley_value": 100.0},
        {"control_id": "u2", "control_name": "U2", "shapley_value": 200.0},
    ]
    payload = {
        "per_scenario": [
            {
                "scenario_id": "s1",
                "scenario_name": "S1",
                "base_risk": {"annualized_loss_expectancy": 1000.0},
                "residual_risk": {"annualized_loss_expectancy": 700.0},
                "control_adjustments": union_adjustments,
            },
            {
                "scenario_id": "s2",
                "scenario_name": "S2",
                "base_risk": {"annualized_loss_expectancy": 500.0},
                "residual_risk": {"annualized_loss_expectancy": 200.0},
                "control_adjustments": union_adjustments,
            },
        ],
        "aggregate_with_controls": {"simulation_results": []},
        "aggregate_without_controls": {"simulation_results": []},
        "control_value": {"dollars": 0, "percent": 0},
        "confidence_intervals": {},
        "n_scenarios": 2,
        "n_simulations": 1,
    }
    matrix = build_aggregate_display_results(_aggregate_run(simulation_results=payload))[
        "per_scenario_control_matrix"
    ]
    # Every cell populated (every scenario got the full UNION list)
    for row in matrix["rows"]:
        assert all(cell["value"] is not None for cell in row["cells"])


def test_per_scenario_control_matrix_all_N3_shapley_values_survive() -> None:
    """Iteration data-contract: N≥3 controls all keep their persisted shapley_value
    through the matrix (no [0]/[-1] slip drops a control to None or 0.0).
    """
    payload = {
        "per_scenario": [
            {
                "scenario_id": "s1",
                "scenario_name": "S1",
                "base_risk": {"annualized_loss_expectancy": 1_000_000.0},
                "residual_risk": {"annualized_loss_expectancy": 600_000.0},
                "control_adjustments": [
                    {"control_id": "mfa", "control_name": "MFA", "shapley_value": 80_000.0},
                    {
                        "control_id": "ins",
                        "control_name": "Cyber Insurance",
                        "shapley_value": 20_000.0,
                    },
                    {"control_id": "edr", "control_name": "EDR", "shapley_value": 22_000.0},
                ],
            },
        ],
        "aggregate_with_controls": {"simulation_results": []},
        "aggregate_without_controls": {"simulation_results": []},
        "control_value": {"dollars": 0, "percent": 0},
        "confidence_intervals": {},
        "n_scenarios": 1,
        "n_simulations": 1,
    }
    matrix = build_aggregate_display_results(_aggregate_run(simulation_results=payload))[
        "per_scenario_control_matrix"
    ]
    assert len(matrix["controls"]) == 3
    by_id = {c["control_id"]: c["total_reduction"] for c in matrix["controls"]}
    assert by_id["mfa"] == 80_000.0
    assert by_id["ins"] == 20_000.0
    assert by_id["edr"] == 22_000.0
    # All cells non-None — no control dropped.
    for row in matrix["rows"]:
        for cell in row["cells"]:
            assert cell["value"] is not None, (
                f"control {cell['control_id']} dropped to None — shapley_value not read"
            )


# ---------------------------------------------------------------------------
# Plan-specified new tests: persisted Shapley reader
# ---------------------------------------------------------------------------


def test_matrix_cells_read_persisted_shapley_value() -> None:
    """Cells render the persisted shapley_value; column totals == sum of cells."""
    per_scenario = [
        {
            "scenario_id": "s1",
            "scenario_name": "S1",
            "base_risk": {"annualized_loss_expectancy": 100.0, "loss_event_frequency": 1.0},
            "control_adjustments": [
                {"control_id": "a", "control_name": "A", "shapley_value": 30.0},
                {"control_id": "b", "control_name": "B", "shapley_value": 40.0},
            ],
        },
        {
            "scenario_id": "s2",
            "scenario_name": "S2",
            "base_risk": {"annualized_loss_expectancy": 50.0, "loss_event_frequency": 1.0},
            "control_adjustments": [
                {"control_id": "a", "control_name": "A", "shapley_value": 10.0},
            ],
        },
    ]
    matrix = _build_per_scenario_control_matrix(per_scenario)
    cols = {c["control_id"]: c for c in matrix["controls"]}
    assert cols["a"]["total_reduction"] == pytest.approx(40.0)  # 30 + 10
    assert cols["b"]["total_reduction"] == pytest.approx(40.0)
    s1 = next(r for r in matrix["rows"] if r["scenario_id"] == "s1")
    cell_by_cid = {c["control_id"]: c["value"] for c in s1["cells"]}
    assert cell_by_cid["a"] == pytest.approx(30.0)
    assert cell_by_cid["b"] == pytest.approx(40.0)


def test_matrix_basis_is_typical_when_no_shapley_value_mean_present() -> None:
    """A run with only the legacy shapley_value key (pre-mean-basis) -> basis
    'typical', and the primary cell falls back to shapley_value."""
    per_scenario = [
        {
            "scenario_id": "s1",
            "scenario_name": "S1",
            "base_risk": {"annualized_loss_expectancy": 100.0, "loss_event_frequency": 1.0},
            "control_adjustments": [
                {"control_id": "a", "control_name": "A", "shapley_value": 30.0},
            ],
        }
    ]
    matrix = _build_per_scenario_control_matrix(per_scenario)
    assert matrix["basis"] == "typical"
    cell = matrix["rows"][0]["cells"][0]
    assert cell["value"] == pytest.approx(30.0)
    assert cell["value_typical"] == pytest.approx(30.0)


def test_matrix_basis_is_mean_when_shapley_value_mean_present() -> None:
    """A run carrying shapley_value_mean (2026-07-04 side-by-side) -> basis
    'mean', and the primary cell reads shapley_value_mean, NOT shapley_value."""
    per_scenario = [
        {
            "scenario_id": "s1",
            "scenario_name": "S1",
            "base_risk": {"annualized_loss_expectancy": 100.0, "loss_event_frequency": 1.0},
            "control_adjustments": [
                {
                    "control_id": "a",
                    "control_name": "A",
                    "shapley_value": 30.0,
                    "shapley_value_mean": 450.0,
                },
            ],
        }
    ]
    matrix = _build_per_scenario_control_matrix(per_scenario)
    assert matrix["basis"] == "mean"
    cell = matrix["rows"][0]["cells"][0]
    assert cell["value"] == pytest.approx(450.0)  # primary: mean-basis
    assert cell["value_typical"] == pytest.approx(30.0)  # paired typical
    assert matrix["controls"][0]["total_reduction"] == pytest.approx(450.0)
    assert matrix["controls"][0]["total_reduction_typical"] == pytest.approx(30.0)


def test_matrix_primary_cell_falls_back_to_typical_when_mean_pass_dropped_this_cell() -> None:
    """A mean-basis run (some cells carry shapley_value_mean) where THIS cell's
    mean pass individually dropped (non-finite) but the typical pass survived —
    the primary falls back to shapley_value for that cell only (run_executor.py's
    *_dropped_mean_only audit trail documents this as the rare reachable path)."""
    per_scenario = [
        {
            "scenario_id": "s1",
            "scenario_name": "S1",
            "base_risk": {"annualized_loss_expectancy": 100.0, "loss_event_frequency": 1.0},
            "control_adjustments": [
                {
                    "control_id": "a",
                    "control_name": "A",
                    "shapley_value": 30.0,
                    "shapley_value_mean": 450.0,
                },
                # This control's mean pass dropped (no shapley_value_mean key),
                # but the run overall IS mean-basis (control "a" carries it).
                {"control_id": "b", "control_name": "B", "shapley_value": 20.0},
            ],
        }
    ]
    matrix = _build_per_scenario_control_matrix(per_scenario)
    assert matrix["basis"] == "mean"
    by_cid = {c["control_id"]: c["value"] for c in matrix["rows"][0]["cells"]}
    assert by_cid["a"] == pytest.approx(450.0)
    assert by_cid["b"] == pytest.approx(20.0)  # fallback to typical for this cell only


def test_matrix_paired_typical_absent_renders_none_not_zero_when_mean_present() -> None:
    """Methodology-review F7: the mean and typical Shapley passes are independent
    computations sharing only the composition cache — a cell can have
    shapley_value_mean present while shapley_value (typical) individually
    dropped as non-finite, or vice versa. The absent side of the pair must
    render None (-> '—'), NEVER a fabricated 0.0, on EITHER side."""
    per_scenario = [
        {
            "scenario_id": "s1",
            "scenario_name": "S1",
            "base_risk": {"annualized_loss_expectancy": 100.0, "loss_event_frequency": 1.0},
            "control_adjustments": [
                # Mean present, typical (shapley_value) absent.
                {"control_id": "a", "control_name": "A", "shapley_value_mean": 450.0},
                # Typical present, mean absent (falls back to typical for "value").
                {"control_id": "b", "control_name": "B", "shapley_value": 20.0},
            ],
        }
    ]
    matrix = _build_per_scenario_control_matrix(per_scenario)
    by_cid = {c["control_id"]: c for c in matrix["rows"][0]["cells"]}
    assert by_cid["a"]["value"] == pytest.approx(450.0)
    assert by_cid["a"]["value_typical"] is None  # absent, never 0.0
    assert by_cid["b"]["value"] == pytest.approx(20.0)  # fallback
    assert by_cid["b"]["value_typical"] == pytest.approx(20.0)  # same key backs both

    totals = {c["control_id"]: c for c in matrix["controls"]}
    assert totals["a"]["total_reduction_typical"] is None  # absent, never 0.0
    assert totals["a"]["total_reduction"] == pytest.approx(450.0)


# ---- Final display slice (2026-07-04): prefer_basis="typical" PDF pin (#467) ----


def test_matrix_prefer_basis_default_unchanged_from_mean_preferred_behavior() -> None:
    """Default prefer_basis="mean" (no kwarg passed) must be byte-identical to
    calling with prefer_basis="mean" explicitly — every existing (web) call site
    relies on this."""
    per_scenario = [
        {
            "scenario_id": "s1",
            "scenario_name": "S1",
            "base_risk": {"annualized_loss_expectancy": 100.0, "loss_event_frequency": 1.0},
            "control_adjustments": [
                {
                    "control_id": "a",
                    "control_name": "A",
                    "shapley_value": 30.0,
                    "shapley_value_mean": 450.0,
                },
            ],
        }
    ]
    default_matrix = _build_per_scenario_control_matrix(per_scenario)
    explicit_mean_matrix = _build_per_scenario_control_matrix(per_scenario, prefer_basis="mean")
    assert default_matrix == explicit_mean_matrix


def test_matrix_prefer_basis_typical_swaps_primary_and_secondary_fields() -> None:
    """Issue #467 PDF pin: prefer_basis="typical" swaps which field is primary —
    "value"/"total_reduction" become the legacy typical (shapley_value) figures,
    "value_typical"/"total_reduction_typical" become the mean figures — and the
    returned "basis" is pinned to "typical" even though this run carries mean
    data (has_mean True)."""
    per_scenario = [
        {
            "scenario_id": "s1",
            "scenario_name": "S1",
            "base_risk": {"annualized_loss_expectancy": 100.0, "loss_event_frequency": 1.0},
            "control_adjustments": [
                {
                    "control_id": "a",
                    "control_name": "A",
                    "shapley_value": 30.0,
                    "shapley_value_mean": 450.0,
                },
            ],
        }
    ]
    matrix = _build_per_scenario_control_matrix(per_scenario, prefer_basis="typical")
    assert matrix["basis"] == "typical"  # pinned, NOT "mean" despite has_mean=True
    cell = matrix["rows"][0]["cells"][0]
    assert cell["value"] == pytest.approx(30.0)  # primary: typical (swapped)
    assert cell["value_typical"] == pytest.approx(450.0)  # secondary: mean (swapped)
    assert matrix["controls"][0]["total_reduction"] == pytest.approx(30.0)
    assert matrix["controls"][0]["total_reduction_typical"] == pytest.approx(450.0)


def test_matrix_prefer_basis_typical_legacy_run_unaffected() -> None:
    """A legacy run (no shapley_value_mean anywhere) is identical under either
    prefer_basis — both the primary and secondary reads fall back to the same
    shapley_value key, so swapping them is a no-op on the numbers (only the
    "basis" label is pinned)."""
    per_scenario = [
        {
            "scenario_id": "s1",
            "scenario_name": "S1",
            "base_risk": {"annualized_loss_expectancy": 100.0, "loss_event_frequency": 1.0},
            "control_adjustments": [
                {"control_id": "a", "control_name": "A", "shapley_value": 30.0},
            ],
        }
    ]
    mean_pref = _build_per_scenario_control_matrix(per_scenario, prefer_basis="mean")
    typical_pref = _build_per_scenario_control_matrix(per_scenario, prefer_basis="typical")
    assert mean_pref["basis"] == "typical"  # no mean data either way
    assert typical_pref["basis"] == "typical"
    assert mean_pref["rows"][0]["cells"][0]["value"] == pytest.approx(30.0)
    assert typical_pref["rows"][0]["cells"][0]["value"] == pytest.approx(30.0)


def test_matrix_preserves_all_controls_iteration_contract() -> None:
    """Adapter iteration contract (CLAUDE.md): N>=3 controls all survive."""
    per_scenario = [
        {
            "scenario_id": "s1",
            "scenario_name": "S1",
            "base_risk": {"annualized_loss_expectancy": 100.0, "loss_event_frequency": 1.0},
            "control_adjustments": [
                {"control_id": f"c{i}", "control_name": f"C{i}", "shapley_value": float(i)}
                for i in range(1, 5)
            ],
        }
    ]
    matrix = _build_per_scenario_control_matrix(per_scenario)
    assert len(matrix["controls"]) == 4
    assert len(matrix["rows"][0]["cells"]) == 4


def test_legacy_run_without_shapley_renders_unavailable() -> None:
    """A pre-release run (no shapley_value key anywhere) -> 'unavailable', NOT all-$0
    (B-Arch-I1/B-Spec-I2). Absent must be distinguished from a genuine 0.0."""
    legacy = [
        {
            "scenario_id": "s1",
            "scenario_name": "S1",
            "base_risk": {"annualized_loss_expectancy": 100.0, "loss_event_frequency": 1.0},
            "control_adjustments": [
                {"control_id": "a", "control_name": "A", "risk_reduction_value": 40.0},
            ],
        }
    ]
    matrix = _build_per_scenario_control_matrix(legacy)
    assert matrix.get("unavailable") is True
    assert matrix["controls"] == [] and matrix["rows"] == []


def test_genuine_null_player_zero_is_not_unavailable() -> None:
    """A present shapley_value of 0.0 is a real null-player, NOT 'unavailable'."""
    per_scenario = [
        {
            "scenario_id": "s1",
            "scenario_name": "S1",
            "base_risk": {"annualized_loss_expectancy": 100.0, "loss_event_frequency": 1.0},
            "control_adjustments": [
                {"control_id": "a", "control_name": "A", "shapley_value": 70.0},
                {"control_id": "b", "control_name": "B", "shapley_value": 0.0},
            ],
        }
    ]
    matrix = _build_per_scenario_control_matrix(per_scenario)
    assert not matrix.get("unavailable")
    cols = {c["control_id"]: c["total_reduction"] for c in matrix["controls"]}
    assert cols["b"] == pytest.approx(0.0)  # rendered as $0, not dropped


def test_matrix_partial_availability_absent_cells_excluded_from_totals() -> None:
    """Mixed: one scenario has shapley_value, another does not.

    Matrix renders (not unavailable — at least one shapley key present).
    The absent scenario's cells are None and excluded from column totals.
    """
    per_scenario = [
        {
            "scenario_id": "s1",
            "scenario_name": "S1",
            "base_risk": {"annualized_loss_expectancy": 100.0, "loss_event_frequency": 1.0},
            "control_adjustments": [
                {"control_id": "a", "control_name": "A", "shapley_value": 50.0},
            ],
        },
        {
            "scenario_id": "s2",
            "scenario_name": "S2",
            "base_risk": {"annualized_loss_expectancy": 80.0, "loss_event_frequency": 1.0},
            "control_adjustments": [
                # No shapley_value key — attribution was skipped for this scenario
                {"control_id": "a", "control_name": "A", "risk_reduction_value": 99.0},
            ],
        },
    ]
    matrix = _build_per_scenario_control_matrix(per_scenario)
    # Not unavailable — at least one shapley_value present
    assert not matrix.get("unavailable")
    # Column total counts only the s1 cell (s2's absent cell excluded)
    cols = {c["control_id"]: c for c in matrix["controls"]}
    assert cols["a"]["total_reduction"] == pytest.approx(50.0)
    # s2's cell is None (absent, not 0.0)
    s2 = next(r for r in matrix["rows"] if r["scenario_id"] == "s2")
    assert s2["cells"][0]["value"] is None


# ---------------------------------------------------------------------------
# Finding 2: absent-only column total_reduction is None, not $0
# Finding 3: explicit shapley_value: None degrades to absent
# ---------------------------------------------------------------------------


def test_absent_only_control_column_total_is_none() -> None:
    """I1/I2 (Task-4 review): a control present ONLY in scenarios that lack
    shapley_value gets total_reduction=None, not $0.

    Specifically tests:
    - The absent-only control's column exists in the matrix.
    - Its total_reduction is None (not 0.0).
    - All its cells are None.
    - It sorts AFTER valued columns.
    - A genuine null-player (present 0.0) still totals 0.0 (not None).
    """
    per_scenario = [
        {
            # This scenario has a valued Shapley attribution → run is NOT unavailable.
            "scenario_id": "s_valued",
            "scenario_name": "Valued Scenario",
            "base_risk": {"annualized_loss_expectancy": 1_000_000.0},
            "control_adjustments": [
                {
                    "control_id": "ctrl_valued",
                    "control_name": "Valued Ctrl",
                    "shapley_value": 100_000.0,
                },
                # Genuine null-player: present with 0.0
                {
                    "control_id": "ctrl_null_player",
                    "control_name": "Null Player",
                    "shapley_value": 0.0,
                },
            ],
        },
        {
            # This scenario has ctrl_absent_only but WITHOUT a shapley_value key
            # (e.g. skipped over_cap/over_budget/error).  No shapley_value anywhere
            # for this control — it is absent-only.
            "scenario_id": "s_skipped",
            "scenario_name": "Skipped Scenario",
            "base_risk": {"annualized_loss_expectancy": 500_000.0},
            "control_adjustments": [
                {
                    "control_id": "ctrl_absent_only",
                    "control_name": "Absent Only Ctrl",
                    # Deliberately NO shapley_value key — attribution was skipped
                    "risk_reduction_value": 999.0,
                },
            ],
        },
    ]
    matrix = _build_per_scenario_control_matrix(per_scenario)

    # Run must be in normal state (has_shapley is True from s_valued)
    assert not matrix.get("unavailable")

    by_id = {c["control_id"]: c for c in matrix["controls"]}

    # The absent-only control must appear as a column.
    assert "ctrl_absent_only" in by_id, "absent-only control must appear in columns"

    # Its total_reduction must be None — not $0.
    assert by_id["ctrl_absent_only"]["total_reduction"] is None, (
        "absent-only column must have total_reduction=None, not 0.0"
    )

    # Genuine null-player still totals 0.0 (not None).
    assert by_id["ctrl_null_player"]["total_reduction"] == pytest.approx(0.0), (
        "genuine null-player (shapley_value=0.0) must total 0.0, not None"
    )

    # All cells for the absent-only control must be None.
    col_order = [c["control_id"] for c in matrix["controls"]]
    absent_idx = col_order.index("ctrl_absent_only")
    for row in matrix["rows"]:
        assert row["cells"][absent_idx]["value"] is None, (
            f"row {row['scenario_id']}: absent-only cell must be None"
        )

    # Sort order: absent-only columns come AFTER all valued columns.
    # ctrl_valued ($100k) and ctrl_null_player ($0) both have non-None totals;
    # ctrl_absent_only (None) must be last.
    valued_positions = [
        col_order.index("ctrl_valued"),
        col_order.index("ctrl_null_player"),
    ]
    absent_position = col_order.index("ctrl_absent_only")
    assert absent_position > max(valued_positions), (
        "absent-only column must sort after all valued columns"
    )


def test_explicit_null_shapley_value_degrades_to_absent() -> None:
    """N1 (Task-4 review): explicit shapley_value: None persisted in JSON degrades
    to absent (cell None, excluded from totals), NOT a fake $0 null-player.

    This is a latent JSON foot-gun: the writer never emits null today, but
    a future schema migration or hand-edit could introduce it.
    """
    per_scenario = [
        {
            "scenario_id": "s1",
            "scenario_name": "S1",
            "base_risk": {"annualized_loss_expectancy": 1_000.0},
            "control_adjustments": [
                # Real valued control (ensures has_shapley=True, run is normal)
                {"control_id": "ctrl_real", "control_name": "Real", "shapley_value": 500.0},
                # Explicit null — must degrade to absent, not $0
                {
                    "control_id": "ctrl_explicit_null",
                    "control_name": "Explicit Null",
                    "shapley_value": None,
                },
            ],
        },
    ]
    matrix = _build_per_scenario_control_matrix(per_scenario)

    assert not matrix.get("unavailable")

    by_id = {c["control_id"]: c for c in matrix["controls"]}

    # ctrl_explicit_null must appear as a column (control was registered)
    assert "ctrl_explicit_null" in by_id, "explicit-null control must appear in columns"

    # Its total_reduction must be None — same as absent, not $0
    assert by_id["ctrl_explicit_null"]["total_reduction"] is None, (
        "explicit shapley_value=None must yield total_reduction=None, not 0.0"
    )

    # Its cell must be None (not 0.0)
    col_order = [c["control_id"] for c in matrix["controls"]]
    null_idx = col_order.index("ctrl_explicit_null")
    s1_row = next(r for r in matrix["rows"] if r["scenario_id"] == "s1")
    assert s1_row["cells"][null_idx]["value"] is None, (
        "explicit shapley_value=None must render as absent cell (None), not $0"
    )

    # Real control still has its correct total
    assert by_id["ctrl_real"]["total_reduction"] == pytest.approx(500.0)


# ---- dist_stats + dist_stats_note exposure (#353 Task 1) ----


def _full_tail_aggregate_payload() -> dict[str, Any]:
    """Full aggregate payload with tail metrics on both agg sides."""
    return {
        **_full_aggregate_payload(),
        "aggregate_without_controls": {
            "annualized_loss_expectancy": 1_500_000.0,
            "mean": 1_500_000.0,
            "median": 1_300_000.0,
            "std_deviation": 400_000.0,
            "var_90": 1_800_000.0,
            "var_95": 2_000_000.0,
            "var_99": 2_500_000.0,
            "var_999": 3_200_000.0,
            "expected_shortfall": {
                "es_95": 2_200_000.0,
                "es_99": 2_700_000.0,
                "es_999": 3_500_000.0,
            },
            "loss_exceedance_curve": [],
        },
        "aggregate_with_controls": {
            "annualized_loss_expectancy": 900_000.0,
            "mean": 900_000.0,
            "median": 800_000.0,
            "std_deviation": 250_000.0,
            "var_90": 1_100_000.0,
            "var_95": 1_200_000.0,
            "var_99": 1_500_000.0,
            "var_999": 2_000_000.0,
            "expected_shortfall": {
                "es_95": 1_350_000.0,
                "es_99": 1_650_000.0,
                "es_999": 2_200_000.0,
            },
            "loss_exceedance_curve": [],
        },
    }


def test_aggregate_display_results_expose_dist_stats() -> None:
    """build_aggregate_display_results exposes dist_stats + dist_stats_note (#353 Task 1).

    AGGREGATE semantics: agg_without_controls is base, agg_with_controls is residual.
    Mirrors test_display_results_expose_dist_stats on the SINGLE side.
    """
    from idraa.services.reports import DIST_STATS_DEFINITIONAL_NOTE

    result = build_aggregate_display_results(_aggregate_run(_full_tail_aggregate_payload()))
    assert result is not None

    assert "dist_stats" in result, "dist_stats key must be present in aggregate vm"
    ds = result["dist_stats"]
    assert ds["has_tail"] is True
    assert len(ds["rows"]) == 10

    # Spot-check labels
    labels = [r["label"] for r in ds["rows"]]
    assert labels[0] == "Mean"
    assert "VaR 90%" in labels
    assert "ES 99.9%" in labels

    # Delta sign check: base (without controls) > residual (with controls) → positive Δ
    mean_row = next(r for r in ds["rows"] if r["label"] == "Mean")
    assert mean_row["delta"] == pytest.approx(600_000.0)  # 1_500_000 - 900_000

    assert "dist_stats_note" in result
    assert result["dist_stats_note"] == DIST_STATS_DEFINITIONAL_NOTE


def test_canon_order_contract_matrix_columns_equal_displayed_control_order():
    """#421 item 1: the weight-robustness ensemble's canonical reference ranking
    (run_executor.py canon_order = displayed_control_order(...)) and the
    displayed matrix's column order MUST be the same ordering. Both consume the
    shared helper / the shared sort key ((total is None, -total, name)); this
    contract test pins the equality on a payload with the edge cases that could
    desync a future sort-key drift: a near-tie broken by a second scenario's
    contribution, and a negative total.
    """
    from idraa.services.aggregate_run_view_model import displayed_control_order

    per_scenario = [
        {
            "scenario_id": "s1",
            "scenario_name": "S1",
            "control_adjustments": [
                {"control_id": "c_a", "control_name": "Alpha", "shapley_value": 40_000.0},
                {"control_id": "c_b", "control_name": "Beta", "shapley_value": 40_000.0},
                {"control_id": "c_neg", "control_name": "Neg", "shapley_value": -5_000.0},
            ],
        },
        {
            "scenario_id": "s2",
            "scenario_name": "S2",
            "control_adjustments": [
                {"control_id": "c_b", "control_name": "Beta", "shapley_value": 1_000.0},
            ],
        },
    ]
    matrix = _build_per_scenario_control_matrix(per_scenario)
    displayed = [c["control_id"] for c in matrix["controls"]]

    # The executor's canonical pass: totals summed from the SAME primary value
    # per (scenario, control), names from the same payload.
    totals: dict[str, float] = {}
    names: dict[str, str] = {}
    for ps in per_scenario:
        for adj in ps["control_adjustments"]:
            cid = adj["control_id"]
            totals[cid] = totals.get(cid, 0.0) + adj["shapley_value"]
            names[cid] = adj["control_name"]
    canon = displayed_control_order(totals, names)

    assert canon == displayed, (
        f"canonical ensemble order {canon} != displayed matrix column order "
        f"{displayed} — the shared-sort-key contract (Arch-I8) is broken"
    )
    # Sanity on the shared sort semantics themselves: Beta (41k) > Alpha (40k)
    # > Neg (-5k); the name tiebreak is exercised only if totals collide.
    assert displayed == ["c_b", "c_a", "c_neg"]
