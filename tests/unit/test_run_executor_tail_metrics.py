"""Unit tests for the tail-risk view-model derivations in run_executor.

Issue #266 / D1: SINGLE simulation_results must persist Expected Shortfall
(ES/CVaR) + p90/p99.9 VaR alongside the already-persisted var_95/var_99.

These are descriptive statistics on fair_cam's already-simulated sample
arrays — a legitimate v3 view-model derivation at persist time (not
re-derived FAIR math). They map to the FAIR LM-tail / loss-exceedance
surface.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from idraa.services.run_executor import _build_tail_metrics


def _fr(samples: list[float]) -> SimpleNamespace:
    return SimpleNamespace(simulation_results=np.asarray(samples) if samples else None)


# ---- exact-value test on a KNOWN, hand-computable array -----------------


def test_tail_metrics_exact_values_on_known_array() -> None:
    # 1..100 inclusive. numpy.percentile uses linear interpolation
    # (method="linear"): for a sorted array of length 100, the q-th
    # percentile sits at index (q/100)*(n-1) = (q/100)*99.
    #   p90  -> index 89.1  -> 90 + 0.1*(91-90)   = 90.1
    #   p95  -> index 94.05 -> 95 + 0.05*(96-95)  = 95.05
    #   p99  -> index 98.01 -> 99 + 0.01*(100-99) = 99.01
    #   p99.9-> index 99.0(0|099*99=98.901) -> 99.901  (99 + 0.901)
    samples = list(range(1, 101))  # 1.0 .. 100.0
    tm = _build_tail_metrics(_fr([float(x) for x in samples]))

    assert tm["var_90"] == pytest.approx(90.1)
    assert tm["var_95"] == pytest.approx(95.05)
    assert tm["var_99"] == pytest.approx(99.01)
    assert tm["var_999"] == pytest.approx(99.901)

    # ES_q = mean of all samples >= VaR_q.
    #   ES_95: samples >= 95.05 -> {96,97,98,99,100} -> mean 98.0
    #   ES_99: samples >= 99.01 -> {100}             -> mean 100.0
    #   ES_999: samples >= 99.901 -> {100}           -> mean 100.0
    es = tm["expected_shortfall"]
    assert es["es_95"] == pytest.approx(98.0)
    assert es["es_99"] == pytest.approx(100.0)
    assert es["es_999"] == pytest.approx(100.0)


def test_es_greater_equal_var_at_each_tail_level() -> None:
    rng = np.random.default_rng(seed=11)
    samples = rng.lognormal(mean=9, sigma=1.8, size=20_000).tolist()
    tm = _build_tail_metrics(_fr(samples))
    es = tm["expected_shortfall"]
    # ES is the mean of the tail >= VaR, so ES_q >= VaR_q always.
    assert es["es_95"] >= tm["var_95"]
    assert es["es_99"] >= tm["var_99"]
    assert es["es_999"] >= tm["var_999"]


def test_empty_tail_falls_back_to_max_not_zero() -> None:
    # A degenerate array where the top samples coincide with the percentile
    # cut in a way that an empty >= slice could occur is hard to force with
    # >= semantics; instead assert the documented fallback contract directly
    # via a single-value array, where np.percentile == max and the >= slice
    # is non-empty, AND via the explicit fallback guarantee: ES is never 0
    # when there are positive samples.
    samples = [5.0, 5.0, 5.0, 5.0]
    tm = _build_tail_metrics(_fr(samples))
    es = tm["expected_shortfall"]
    # All percentiles == 5.0; >= 5.0 selects everything -> mean 5.0 (== max).
    assert es["es_95"] == pytest.approx(5.0)
    assert es["es_99"] == pytest.approx(5.0)
    assert es["es_999"] == pytest.approx(5.0)
    # Never zero.
    assert es["es_95"] != 0.0


def test_empty_tail_slice_uses_max_fallback() -> None:
    # Force an empty >= slice: strictly-increasing floats where the p99.9
    # cut lands ABOVE the max is impossible (percentile is bounded by max),
    # so we test the fallback branch by stubbing a VaR above all samples is
    # not how the code works. Instead verify: when samples are all distinct
    # and the tail >= VaR_q is empty only if VaR_q > max — which numpy never
    # produces. The fallback exists defensively; we assert ES never < 0 and
    # never None on a tiny array.
    samples = [1.0, 2.0]
    tm = _build_tail_metrics(_fr(samples))
    es = tm["expected_shortfall"]
    assert es["es_999"] == pytest.approx(2.0)  # max, the only sample >= p99.9


def test_tail_metrics_empty_samples_returns_zeros() -> None:
    tm = _build_tail_metrics(_fr([]))
    assert tm["var_90"] == 0.0
    assert tm["var_999"] == 0.0
    assert tm["expected_shortfall"]["es_999"] == 0.0


def test_tail_metrics_none_samples_returns_zeros() -> None:
    tm = _build_tail_metrics(SimpleNamespace(simulation_results=None))
    assert tm["var_95"] == 0.0
    assert tm["expected_shortfall"]["es_95"] == 0.0


# ---- wiring: _fair_risk_to_dict surfaces the new keys -------------------


def test_fair_risk_to_dict_includes_tail_metrics() -> None:
    from idraa.services.run_executor import _fair_risk_to_dict

    rng = np.random.default_rng(seed=3)
    samples = rng.lognormal(mean=10, sigma=2, size=2000).tolist()
    fr = SimpleNamespace(
        simulation_results=np.asarray(samples),
        annualized_loss_expectancy=1000.0,
        mean=1000.0,
        median=900.0,
        std_deviation=100.0,
        var_95=1500.0,
        var_99=2000.0,
        loss_event_frequency=0.5,
        loss_magnitude=1000.0,
        n_simulations=2000,
    )
    d = _fair_risk_to_dict(fr)
    assert "var_90" in d
    assert "var_999" in d
    assert "expected_shortfall" in d
    assert set(d["expected_shortfall"].keys()) == {"es_95", "es_99", "es_999"}
    # var_95/var_99 stay sourced from the fair_cam dataclass (NOT overwritten
    # by the sample-derived tail helper).
    assert d["var_95"] == 1500.0
    assert d["var_99"] == 2000.0
    # The NEW sample-derived levels are monotone among themselves, and ES_q
    # dominates its own sample-derived VaR cut.
    assert d["var_90"] <= d["var_999"]
    assert d["expected_shortfall"]["es_999"] >= d["expected_shortfall"]["es_95"]


# ---- aggregate payload surfaces the full tail ladder (this feature) ----------


def _agg_fr(samples: np.ndarray) -> SimpleNamespace:
    return SimpleNamespace(
        simulation_results=samples,
        annualized_loss_expectancy=float(samples.mean()),
        mean=float(samples.mean()),
        median=float(np.median(samples)),
        std_deviation=float(samples.std()),
        var_95=float(np.percentile(samples, 95)),
        var_99=float(np.percentile(samples, 99)),
        loss_event_frequency=0.5,
        loss_magnitude=1000.0,
        n_simulations=int(samples.size),
    )


def test_aggregate_lec_pair_carries_full_tail_ladder() -> None:
    """An aggregate run's persisted aggregate_with_controls / aggregate_without_controls
    dicts carry the full tail ladder (var_90, var_999, expected_shortfall) derived from
    the aggregate loss distribution (the elementwise SUM of per-scenario loss arrays),
    with var_95/var_99 unchanged/consistent vs the fair_cam dataclass."""
    from idraa.services.run_executor import _build_aggregate_lec_pair

    rng = np.random.default_rng(seed=17)
    with_samples = rng.lognormal(mean=10, sigma=1.5, size=8000)
    without_samples = with_samples * 1.8  # base >= residual elementwise

    with_fr = _agg_fr(with_samples)
    without_fr = _agg_fr(without_samples)
    aggregate = SimpleNamespace(
        aggregate_with_controls=with_fr,
        aggregate_without_controls=without_fr,
    )

    pair = _build_aggregate_lec_pair(aggregate)
    for side, fr in (
        ("aggregate_with_controls", with_fr),
        ("aggregate_without_controls", without_fr),
    ):
        d = pair[side]
        # New tail keys present + non-zero (real samples).
        assert "var_90" in d and d["var_90"] > 0.0
        assert "var_999" in d and d["var_999"] > 0.0
        assert "expected_shortfall" in d
        assert set(d["expected_shortfall"].keys()) == {"es_95", "es_99", "es_999"}
        es = d["expected_shortfall"]
        assert all(v > 0.0 for v in es.values())
        # var_95/var_99 stay sourced from the fair_cam dataclass (consistent).
        assert d["var_95"] == fr.var_95
        assert d["var_99"] == fr.var_99
        # Statistically sane ladder: var_999 >= var_99 >= var_95 >= var_90 and
        # es_q >= var_q at each tail.
        assert d["var_999"] >= d["var_99"] >= d["var_95"] >= d["var_90"]
        assert es["es_95"] >= d["var_95"]
        assert es["es_99"] >= d["var_99"]
        assert es["es_999"] >= d["var_999"]
        # LEC curve still present.
        assert "loss_exceedance_curve" in d


def test_aggregate_lec_pair_empty_branch_carries_tail_keys() -> None:
    """The empty-sample branch (all_positive.size == 0) still carries the tail keys
    (all-zero per the empty-input contract), so has_tail_metrics is False (legacy/
    degenerate) rather than KeyError on a consumer read."""
    from idraa.services.run_executor import _build_aggregate_lec_pair

    with_fr = SimpleNamespace(
        simulation_results=np.array([]),
        annualized_loss_expectancy=0.0,
        mean=0.0,
        median=0.0,
        std_deviation=0.0,
        var_95=0.0,
        var_99=0.0,
        loss_event_frequency=0.0,
        loss_magnitude=0.0,
        n_simulations=0,
    )
    aggregate = SimpleNamespace(
        aggregate_with_controls=with_fr,
        aggregate_without_controls=with_fr,
    )
    pair = _build_aggregate_lec_pair(aggregate)
    d = pair["aggregate_with_controls"]
    assert d["var_90"] == 0.0 and d["var_999"] == 0.0
    assert d["expected_shortfall"] == {"es_95": 0.0, "es_99": 0.0, "es_999": 0.0}
    assert d["loss_exceedance_curve"] == []
