"""Engine-level sampling for DistributionType.LOGNORMAL_MIXTURE (issue #27
Task 3). Pins the sampling contract for ``FAIRDistribution.sample`` and the
``_scale_distribution`` currency-scaling branch introduced alongside T1/T2's
pure ``LognormMixture`` pooling math.

Wire shape: ``parameters = {"components": [{"mean", "sigma", "weight"}, ...]}``
-- ``mean`` is the LOG-space meanlog (matching the existing plain-LOGNORMAL
convention, NOT ``mu``). This mirrors ``LognormMixture.components[i].meanlog``
/``.sdlog`` from ``fair_cam.quantile_pooling`` one-for-one; the engine layer
does not import that module (fair_core.py has no quantile_pooling
dependency), so the dict shape is duplicated by convention, not by import.

Per the verification-reporting collaboration convention, every numeric
assertion below prints an expected-vs-actual side-by-side pair.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from fair_cam.risk_engine.fair_core import (
    DistributionType,
    FAIRDistribution,
    _scale_distribution,
)


def _component_first_second_moment(mean: float, sigma: float) -> tuple[float, float]:
    """E[X] and E[X^2] for a single lognormal(mean, sigma) component (mean is
    log-space meanlog, numpy semantics)."""
    m1 = math.exp(mean + sigma**2 / 2.0)
    m2 = math.exp(2.0 * mean + 2.0 * sigma**2)
    return m1, m2


def _mixture_mean_variance(components: list[dict[str, float]]) -> tuple[float, float]:
    """Analytic mean/variance of a lognormal mixture via raw moments:
    E[X] = sum_i w_i * E[X_i]; E[X^2] = sum_i w_i * E[X_i^2] (X | component=i
    is distributed as X_i); Var[X] = E[X^2] - E[X]^2."""
    mean = 0.0
    second = 0.0
    for c in components:
        m1, m2 = _component_first_second_moment(c["mean"], c["sigma"])
        mean += c["weight"] * m1
        second += c["weight"] * m2
    return mean, second - mean**2


# Shared 3-component asymmetric-weight fixture. Weights sum EXACTLY to 1.0
# (no floating remainder) so rng.choice's internal p-sum tolerance is a
# non-issue. meanlog/sigma chosen so components overlap in real space (this
# fixture is for mean/variance pinning, NOT component-selection frequency --
# that test below uses a disjoint-range fixture instead).
_MIX_COMPONENTS = [
    {"mean": 8.0, "sigma": 0.5, "weight": 0.3},
    {"mean": 10.0, "sigma": 0.8, "weight": 0.5},
    {"mean": 12.0, "sigma": 0.3, "weight": 0.2},
]


def _mix_dist(components: list[dict[str, float]]) -> FAIRDistribution:
    return FAIRDistribution(
        DistributionType.LOGNORMAL_MIXTURE,
        {"components": components},
    )


def test_law_of_total_variance_formula_exact_arithmetic():
    """Pure analytic check (no sampling): the raw-second-moment variance
    formula (E[X^2] - E[X]^2) must equal the law-of-total-variance
    decomposition (E_i[Var(X|i)] + Var_i[E(X|i)]) EXACTLY (up to float
    rounding) for the shared fixture. This is the "verify the LOTV formula
    analytically" half of the binding amendment -- independent of any RNG
    draw."""
    components = _MIX_COMPONENTS

    mean_raw, var_raw = _mixture_mean_variance(components)

    # Law-of-total-variance decomposition, computed independently.
    within = 0.0  # E_i[Var(X|i)]
    means = []
    for c in components:
        m1, m2 = _component_first_second_moment(c["mean"], c["sigma"])
        var_i = m2 - m1**2
        within += c["weight"] * var_i
        means.append((c["weight"], m1))
    grand_mean = sum(w * m for w, m in means)
    between = sum(w * (m - grand_mean) ** 2 for w, m in means)  # Var_i[E(X|i)]
    var_lotv = within + between

    print(f"LOTV check: raw-moment var={var_raw!r} vs LOTV-decomposition var={var_lotv!r}")
    assert math.isclose(mean_raw, grand_mean, rel_tol=1e-12)
    assert math.isclose(var_raw, var_lotv, rel_tol=1e-9)


@pytest.mark.slow
def test_sampled_mean_and_variance_match_analytic_pinned_seed():
    """4e5 draws, fixed seed: sampled mean within 1% of analytic mean;
    sampled variance within 7% of analytic variance (binding amendment --
    1% is unrealistic for variance given 4th-moment domination on a
    lognormal mixture). Seed picked and pinned green at gate time."""
    n = 400_000
    dist = _mix_dist(_MIX_COMPONENTS)
    rng = np.random.default_rng(2027)
    samples = dist.sample(size=n, rng=rng)

    analytic_mean, analytic_var = _mixture_mean_variance(_MIX_COMPONENTS)
    sample_mean = float(np.mean(samples))
    sample_var = float(np.var(samples))

    mean_rel_err = abs(sample_mean - analytic_mean) / analytic_mean
    var_rel_err = abs(sample_var - analytic_var) / analytic_var

    print(
        f"mean: expected(analytic)={analytic_mean:.6f} vs actual(sampled)={sample_mean:.6f} "
        f"(rel_err={mean_rel_err:.4%})"
    )
    print(
        f"variance: expected(analytic)={analytic_var:.6f} vs actual(sampled)={sample_var:.6f} "
        f"(rel_err={var_rel_err:.4%})"
    )

    assert mean_rel_err < 0.01
    assert var_rel_err < 0.07


def test_component_selection_frequencies_within_3sigma_binomial():
    """Components chosen with disjoint real-space ranges (widely separated
    meanlog, tight sigma) so each drawn sample can be unambiguously
    attributed to its source component by value alone. Observed bucket
    counts must fall within +/-3 sigma of the binomial expectation
    n*w_i, sigma=sqrt(n*w_i*(1-w_i))."""
    n = 200_000
    weights = [0.3, 0.5, 0.2]
    components = [
        {"mean": 5.0, "sigma": 0.05, "weight": weights[0]},  # ~exp(5) ~= 148
        {"mean": 15.0, "sigma": 0.05, "weight": weights[1]},  # ~exp(15) ~= 3.3M
        {"mean": 25.0, "sigma": 0.05, "weight": weights[2]},  # ~exp(25) ~= 7.2e10
    ]
    dist = _mix_dist(components)
    rng = np.random.default_rng(3031)
    samples = dist.sample(size=n, rng=rng)

    thresholds = [1e5, 1e9]
    bucket0 = int(np.sum(samples < thresholds[0]))
    bucket1 = int(np.sum((samples >= thresholds[0]) & (samples < thresholds[1])))
    bucket2 = int(np.sum(samples >= thresholds[1]))
    observed = [bucket0, bucket1, bucket2]

    assert bucket0 + bucket1 + bucket2 == n  # every draw classified (disjoint ranges)

    for i, (w, obs) in enumerate(zip(weights, observed, strict=True)):
        expected = n * w
        sigma = math.sqrt(n * w * (1 - w))
        bound = 3 * sigma
        print(
            f"component[{i}] (weight={w}): expected(binomial)={expected:.1f} "
            f"+/-{bound:.1f} vs actual(observed)={obs}"
        )
        assert abs(obs - expected) <= bound


def test_single_component_stream_identical_to_plain_lognormal():
    """Single-component mixtures bypass rng.choice entirely (dedicated
    branch, binding amendment): rng.choice ALWAYS consumes randomness to
    pick an index -- even with only one possible outcome -- so routing a
    1-component mixture through it would desync the sample stream from a
    plain scalar DistributionType.LOGNORMAL draw at the same seed. Assert
    the two streams are allclose (in fact bit-identical) at a shared seed."""
    mean, sigma = 9.0, 0.6
    mix = _mix_dist([{"mean": mean, "sigma": sigma, "weight": 1.0}])
    plain = FAIRDistribution(DistributionType.LOGNORMAL, {"mean": mean, "sigma": sigma})

    mix_samples = mix.sample(size=10_000, rng=np.random.default_rng(4242))
    plain_samples = plain.sample(size=10_000, rng=np.random.default_rng(4242))

    print(
        f"single-component mixture[:5]={mix_samples[:5]!r} "
        f"vs plain-lognormal[:5]={plain_samples[:5]!r}"
    )
    np.testing.assert_allclose(mix_samples, plain_samples, rtol=0, atol=0)


def test_scale_distribution_shifts_every_component_mean_by_ln_multiplier():
    """_scale_distribution's LOGNORMAL_MIXTURE branch shifts EVERY
    component's meanlog by +ln(multiplier); sigma/weight untouched
    (mirrors the plain-LOGNORMAL log-space shift). The resulting mixture's
    analytic mean must equal multiplier * original analytic mean EXACTLY
    (log-space additive shift == real-space multiplicative scale)."""
    multiplier = 2.5
    original = _mix_dist(_MIX_COMPONENTS)
    scaled = _scale_distribution(original, multiplier)

    assert scaled.distribution_type is DistributionType.LOGNORMAL_MIXTURE
    scaled_components = scaled.parameters["components"]
    assert len(scaled_components) == len(_MIX_COMPONENTS)

    for orig_c, new_c in zip(_MIX_COMPONENTS, scaled_components, strict=True):
        assert math.isclose(new_c["mean"], orig_c["mean"] + math.log(multiplier), rel_tol=1e-12)
        assert math.isclose(new_c["sigma"], orig_c["sigma"], rel_tol=1e-12)
        assert math.isclose(new_c["weight"], orig_c["weight"], rel_tol=1e-12)

    # original must be untouched (no in-place mutation of the input dist)
    assert original.parameters["components"] == _MIX_COMPONENTS

    analytic_mean_orig, _ = _mixture_mean_variance(_MIX_COMPONENTS)
    analytic_mean_scaled, _ = _mixture_mean_variance(scaled_components)
    print(
        f"scaled analytic mean: expected={multiplier * analytic_mean_orig:.6f} "
        f"vs actual={analytic_mean_scaled:.6f}"
    )
    assert math.isclose(analytic_mean_scaled, multiplier * analytic_mean_orig, rel_tol=1e-9)


def test_lognormal_mixture_type_value():
    assert DistributionType.LOGNORMAL_MIXTURE.value == "lognormal_mixture"


def test_empty_components_raises():
    dist = _mix_dist([])
    with pytest.raises(ValueError):
        dist.sample(size=10, rng=np.random.default_rng(1))
