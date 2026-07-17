"""Contract: `_fair_risk_to_dict` keeps the raw numpy sample array (no
`.tolist()` materialization), and the binary codec round-trips split arrays.

Regression guard for the write-path change that drops the O(M·N) Python-list
materialization of `simulation_results` at persist time (the driver of the
100k-iteration AGGREGATE OOM, issue #211/#294).
"""

from __future__ import annotations

import numpy as np

from idraa.services.run_executor import _build_tail_metrics, _fair_risk_to_dict
from idraa.services.sample_codec import decode_sample_arrays, encode_sample_arrays


class _FR:
    def __init__(self, samples):
        self.simulation_results = samples
        self.annualized_loss_expectancy = 100.0
        self.mean = 100.0
        self.median = 90.0
        self.std_deviation = 10.0
        self.var_95 = 150.0
        self.var_99 = 200.0
        self.loss_event_frequency = 1.0
        self.loss_magnitude = 100.0
        self.n_simulations = 3


def test_fair_risk_to_dict_keeps_numpy_not_list():
    fr = _FR(np.array([1.0, 2.0, 3.0]))
    d = _fair_risk_to_dict(fr)
    assert isinstance(d["simulation_results"], np.ndarray)
    assert "expected_shortfall" in d
    # d["var_95"] is fr.var_95 straight off the fair_cam dataclass (fixture
    # 150.0 here) — NOT sample-derived (see the _NEW_VAR_KEYS comment in
    # run_executor.py: only var_90/var_999/expected_shortfall are merged from
    # _build_tail_metrics; var_95/var_99 stay dataclass-sourced so they are
    # never silently swapped for sample-derived percentiles). So d["var_999"]
    # and d["var_95"] are independent quantities and cannot be compared
    # directly against this fixture's tiny 3-sample array. Assert the actual
    # invariant instead: p99.9 >= p95 computed from the SAME sample array.
    tail = _build_tail_metrics(fr)
    assert tail["var_999"] >= tail["var_95"]


def test_none_samples_yields_empty_array():
    d = _fair_risk_to_dict(_FR(None))
    assert isinstance(d["simulation_results"], np.ndarray)
    assert d["simulation_results"].size == 0


def test_codec_round_trips_split_arrays():
    arrays = {"base_risk": np.array([5.0, 6.0]), "residual_risk": np.array([1.0])}
    out = decode_sample_arrays(encode_sample_arrays(arrays))
    assert out["base_risk"] == [5.0, 6.0]
    assert out["residual_risk"] == [1.0]
