"""Unit tests for the EPC view-model builders in services/run_executor.py.

Mirrors the LEC test pattern in tests/unit/test_run_executor.py.
Closes acceptance criterion from issue #72.
"""

from itertools import pairwise
from types import SimpleNamespace

import numpy as np
import pytest

from idraa.services.run_executor import (
    _build_aggregate_epc_pair,
    _build_exceedance_probability_curve,
)


def _fr(samples: list[float]) -> SimpleNamespace:
    """Stub a FairRisk-shaped object exposing simulation_results."""
    return SimpleNamespace(simulation_results=np.asarray(samples) if samples else None)


def test_epc_returns_100_points_for_nonempty_samples() -> None:
    rng = np.random.default_rng(seed=42)
    samples = rng.lognormal(mean=10, sigma=2, size=10_000).tolist()
    result = _build_exceedance_probability_curve(_fr(samples))
    assert len(result) == 100


def test_epc_percentiles_strictly_increasing_one_to_one_hundred_pct() -> None:
    rng = np.random.default_rng(seed=42)
    samples = rng.lognormal(mean=10, sigma=2, size=10_000).tolist()
    result = _build_exceedance_probability_curve(_fr(samples))
    pcts = [p["percentile"] for p in result]
    assert pcts[0] == pytest.approx(0.01)
    assert pcts[-1] == pytest.approx(1.00)
    assert all(b > a for a, b in pairwise(pcts))


def test_epc_losses_monotonic_non_decreasing() -> None:
    rng = np.random.default_rng(seed=42)
    samples = rng.lognormal(mean=10, sigma=2, size=10_000).tolist()
    result = _build_exceedance_probability_curve(_fr(samples))
    losses = [p["loss"] for p in result]
    assert all(b >= a for a, b in pairwise(losses))


def test_epc_empty_samples_returns_empty_list() -> None:
    assert _build_exceedance_probability_curve(_fr([])) == []


def test_epc_none_samples_returns_empty_list() -> None:
    assert _build_exceedance_probability_curve(SimpleNamespace(simulation_results=None)) == []


def test_epc_clamps_non_positive_samples_to_one_dollar() -> None:
    samples = [-100.0, 0.0, 0.5, 10.0, 100.0]
    result = _build_exceedance_probability_curve(_fr(samples))
    assert all(p["loss"] >= 1.0 for p in result)


def test_epc_var_95_parity_with_numpy_percentile() -> None:
    # Issue #72 acceptance criterion: payload[94].loss == np.percentile(samples, 95)
    rng = np.random.default_rng(seed=42)
    samples = rng.lognormal(mean=10, sigma=2, size=10_000).tolist()
    result = _build_exceedance_probability_curve(_fr(samples))
    expected_p95 = float(np.percentile(np.asarray(samples), 95))
    assert result[94]["percentile"] == pytest.approx(0.95)
    assert result[94]["loss"] == pytest.approx(expected_p95, rel=1e-6)


def _aggregate_stub(with_samples: list[float], without_samples: list[float]) -> SimpleNamespace:
    """Stub an AggregateEnhancedRisk-shaped object with the two FairRisk halves."""
    return SimpleNamespace(
        aggregate_with_controls=_fr(with_samples),
        aggregate_without_controls=_fr(without_samples),
    )


def test_aggregate_epc_returns_both_curves_each_100_long() -> None:
    rng = np.random.default_rng(seed=7)
    with_s = rng.lognormal(mean=8, sigma=1.5, size=5000).tolist()
    without_s = rng.lognormal(mean=10, sigma=2.0, size=5000).tolist()
    out = _build_aggregate_epc_pair(_aggregate_stub(with_s, without_s))
    assert set(out.keys()) == {"with_controls", "without_controls"}
    assert len(out["with_controls"]) == 100
    assert len(out["without_controls"]) == 100


def test_aggregate_epc_curves_use_their_own_samples_not_a_shared_grid() -> None:
    # Unlike LEC (which shares a union grid), each EPC curve is computed
    # from its own samples — percentile mapping is intrinsic, no x-domain
    # alignment is needed.
    rng = np.random.default_rng(seed=7)
    with_s = rng.lognormal(mean=8, sigma=1.5, size=5000).tolist()
    without_s = rng.lognormal(mean=10, sigma=2.0, size=5000).tolist()
    out = _build_aggregate_epc_pair(_aggregate_stub(with_s, without_s))
    assert out["with_controls"][94]["loss"] == pytest.approx(
        float(np.percentile(np.asarray(with_s), 95)), rel=1e-6
    )
    assert out["without_controls"][94]["loss"] == pytest.approx(
        float(np.percentile(np.asarray(without_s), 95)), rel=1e-6
    )


def test_aggregate_epc_both_empty_returns_empty_lists() -> None:
    out = _build_aggregate_epc_pair(_aggregate_stub([], []))
    assert out == {"with_controls": [], "without_controls": []}


def test_aggregate_epc_one_side_empty_returns_one_curve_only() -> None:
    rng = np.random.default_rng(seed=7)
    out = _build_aggregate_epc_pair(_aggregate_stub([], rng.lognormal(size=5000).tolist()))
    assert out["with_controls"] == []
    assert len(out["without_controls"]) == 100


def _fr_full(samples: list[float], ale: float = 1000.0) -> SimpleNamespace:
    """Stub a FairRisk-shaped object with all fields _fair_risk_to_dict reads."""
    return SimpleNamespace(
        simulation_results=np.asarray(samples) if samples else None,
        annualized_loss_expectancy=ale,
        mean=ale,
        median=ale,
        std_deviation=ale * 0.1,
        var_95=ale * 1.5,
        var_99=ale * 2.0,
        loss_event_frequency=0.5,
        loss_magnitude=ale,
        n_simulations=len(samples),
        threat_event_frequency=1.0,
    )


def test_single_results_payload_includes_exceedance_probability_curve() -> None:
    """SINGLE _build_results_payload includes 'exceedance_probability_curve' key."""
    from idraa.services.run_executor import _build_results_payload

    rng = np.random.default_rng(seed=42)
    samples = rng.lognormal(mean=10, sigma=2, size=1000).tolist()
    enhanced = SimpleNamespace(
        residual_risk=_fr_full(samples),
        base_risk=_fr_full(samples),
        annualized_loss_expectancy=1000.0,
        threat_event_frequency=1.0,
        loss_event_frequency=0.5,
        confidence_intervals=SimpleNamespace(
            confidence_level=0.95,
            lower_bound=900.0,
            upper_bound=1100.0,
            standard_error=50.0,
            sample_size=1000,
        ),
        control_adjustments=[],
    )
    payload = _build_results_payload(enhanced)
    assert "exceedance_probability_curve" in payload
    assert len(payload["exceedance_probability_curve"]) == 100
    assert payload["exceedance_probability_curve"][94]["percentile"] == pytest.approx(0.95)


def test_aggregate_results_payload_includes_dual_epc() -> None:
    """AGGREGATE _build_aggregate_results_payload includes 'dual_epc' key."""
    from idraa.services.run_executor import _build_aggregate_results_payload

    rng = np.random.default_rng(seed=7)
    samples_with = rng.lognormal(mean=8, sigma=1.5, size=1000).tolist()
    samples_without = rng.lognormal(mean=10, sigma=2.0, size=1000).tolist()
    fake_fr = lambda s, ale: SimpleNamespace(  # noqa: E731
        simulation_results=np.asarray(s),
        annualized_loss_expectancy=ale,
        mean=ale,
        median=ale,
        std_deviation=ale * 0.1,
        var_95=ale * 1.5,
        var_99=ale * 2.0,
        threat_event_frequency=1.0,
        loss_event_frequency=0.5,
        loss_magnitude=ale,
        n_simulations=len(s),
    )
    aggregate = SimpleNamespace(
        aggregate_with_controls=fake_fr(samples_with, 800.0),
        aggregate_without_controls=fake_fr(samples_without, 1500.0),
        per_scenario=[],
        control_value_dollars=700.0,
        control_value_percent=46.6,
        confidence_intervals=SimpleNamespace(
            confidence_level=0.95,
            lower_bound=750.0,
            upper_bound=850.0,
            standard_error=25.0,
            sample_size=1000,
        ),
        n_scenarios=2,
        n_simulations=1000,
    )
    payload = _build_aggregate_results_payload(aggregate)
    assert "dual_epc" in payload
    assert "with_controls" in payload["dual_epc"]
    assert "without_controls" in payload["dual_epc"]
    assert len(payload["dual_epc"]["with_controls"]) == 100
    assert len(payload["dual_epc"]["without_controls"]) == 100
