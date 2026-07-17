"""ES Monte Carlo standard-error estimator — validation against an independent
nonparametric bootstrap oracle and a closed-form exponential hand-math anchor.

The analytic estimator is the first-order influence-function SE of the sample
upper-tail Expected Shortfall (Scaillet 2004, Math. Finance 14(1); Manistre &
Hancock 2005, NAAJ 9(2)):

    IF(x) = (1/p)*(x - VaR)_+ - (ES - VaR),   p = 1 - alpha
    Var(ES_hat) ~= E[IF^2]/n = sigma^2_tail/(n p) + (1-p)(ES-VaR)^2/(n p)

Both terms matter — the bootstrap oracle catches a one-term implementation
(which understates the SE by ~sqrt(2) at small p).
"""

from __future__ import annotations

import numpy as np

from idraa.services.run_executor import _es_standard_error


def _bootstrap_es_se(samples: np.ndarray, alpha: float, b: int = 400, seed: int = 0) -> float:
    """Independent oracle: SD of the sample-ES over B nonparametric resamples."""
    rng = np.random.default_rng(seed)
    n = samples.size
    est = np.empty(b)
    for i in range(b):
        r = samples[rng.integers(0, n, n)]
        v = np.percentile(r, alpha * 100)
        tail = r[r >= v]
        est[i] = tail.mean() if tail.size else r.max()
    return float(est.std(ddof=1))


def _sample_es_var(samples: np.ndarray, alpha: float) -> tuple[float, float]:
    v = float(np.percentile(samples, alpha * 100))
    tail = samples[samples >= v]
    return (float(tail.mean()) if tail.size else float(samples.max()), v)


def test_analytic_matches_bootstrap_lognormal():
    rng = np.random.default_rng(42)
    s = rng.lognormal(mean=12.0, sigma=1.2, size=200_000)
    for alpha in (0.95, 0.99):
        es, v = _sample_es_var(s, alpha)
        analytic = _es_standard_error(s, alpha, es, v)
        boot = _bootstrap_es_se(s, alpha)
        assert abs(analytic - boot) / boot < 0.15, (alpha, analytic, boot)


def test_exponential_hand_math_anchor():
    """Closed form: SE = (1/lambda) * sqrt((2 - p) / (n p)),  p = 1 - alpha.

    Exp(lambda) is memoryless, so the tail-excess above any VaR is again
    Exp(lambda): ES - VaR = 1/lambda and sigma^2_tail = 1/lambda^2, giving
    Var = (1/lambda^2)(2 - p)/(n p). This is the two-term result; a one-term
    (dispersion-only) estimator would give (1/lambda)/sqrt(n p), ~sqrt(2) low.
    """
    rng = np.random.default_rng(3)
    lam = 1 / 5000.0
    n, alpha = 500_000, 0.99
    s = rng.exponential(1 / lam, n)
    es, v = _sample_es_var(s, alpha)
    p = 1 - alpha
    expected = (1 / lam) * np.sqrt((2 - p) / (n * p))
    actual = _es_standard_error(s, alpha, es, v)
    assert abs(actual - expected) / expected < 0.10, (expected, actual)


def test_se_shrinks_as_sqrt_n():
    # SE ~ 1/sqrt(N): 4x the samples -> ~2x smaller SE.
    rng = np.random.default_rng(7)
    a = rng.lognormal(12.0, 1.0, 50_000)
    b = rng.lognormal(12.0, 1.0, 200_000)

    def se(x: np.ndarray) -> float:
        es, v = _sample_es_var(x, 0.99)
        return _es_standard_error(x, 0.99, es, v)

    ratio = se(a) / se(b)
    assert 1.6 < ratio < 2.5, ratio


def test_too_few_tail_samples_returns_nan():
    s = np.array([1.0, 2.0, 3.0])
    v = float(np.percentile(s, 99.9))
    assert np.isnan(_es_standard_error(s, 0.999, 3.0, v))


def test_build_tail_metrics_stores_json_safe_none_not_nan():
    """The persisted es_*_se must be None (JSON null), never NaN — the summary
    payload is serialized with allow_nan=False, so a NaN would fail the run."""
    import json

    from idraa.services.run_executor import _build_tail_metrics

    class _FR:
        # A tiny run: the p99.9 tail has < 2 samples, so es_999_se is undefined.
        simulation_results = np.arange(1.0, 51.0)  # 50 samples
        n_simulations = 50

    metrics = _build_tail_metrics(_FR())
    se = metrics["expected_shortfall_se"]
    assert se["es_999"] is None  # undefined deep tail -> None, not NaN
    assert se["es_95"] is not None and se["es_95"] > 0  # well-populated tail
    # Must be JSON-serializable under the strict (allow_nan=False) serializer.
    json.dumps(metrics, allow_nan=False)
