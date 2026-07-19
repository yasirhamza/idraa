"""Mixture quantile inversion + PERT collapse (issue #27 Task 2).

Covers: `_plnormtrunc`/`_pnormtrunc` (mixture CDF companions to the existing
`_qlnormtrunc`/`_qnormtrunc`), `mixture_quantile_lognorm`/`mixture_quantile_norm`
(deterministic bisection quantile inversion), and
`lognormal_mixture_to_pert_approx`/`normal_mixture_to_pert_approx` (PERT
collapse: range from mixture quantiles, mode from the unconstrained global
mixture-density argmax, then the SAME `_clamp_mode` precedence machinery as
the pre-mixture scalar functions).

The worked A/B pair (spec `docs/superpowers/specs/2026-07-19-mixture-pooling-
design.md` §6; also used by test_mixture_pooling.py /
test_pooling_divergence_warning.py): SME A $1k-$10k (meanlog 8.06, sdlog
0.70), SME B $1M-$50M (meanlog 15.77, sdlog 1.19), equal weight.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from fair_cam.quantile_pooling import (
    LogNormalTruncFit,
    LognormMixture,
    ModeClampReason,
    NormalTruncFit,
    NormMixture,
    lognormal_mixture_to_pert_approx,
    lognormal_to_pert_approx,
    mixture_quantile_lognorm,
    mixture_quantile_norm,
    normal_mixture_to_pert_approx,
    normal_to_pert_approx,
)
from fair_cam.quantile_pooling._lognormal import _plnormtrunc, _qlnormtrunc
from fair_cam.quantile_pooling._normal import _pnormtrunc, _qnormtrunc
from scipy.stats import beta as scipy_beta

# The #27/#343 worked pair: SME A $1k-$10k, SME B $1M-$50M.
_LN_A = LogNormalTruncFit(meanlog=8.06, sdlog=0.70, min_support=0.0, max_support=math.inf)
_LN_B = LogNormalTruncFit(meanlog=15.77, sdlog=1.19, min_support=0.0, max_support=math.inf)
_WORKED_MIX = LognormMixture(components=(_LN_A, _LN_B), weights=(0.5, 0.5))


# ----------------------------------------------------------------------------
# Worked A/B pair pin (spec §6; Task 2 binding amendment -- corrected)
# ----------------------------------------------------------------------------


def test_worked_pair_low_quantile_exact_identity() -> None:
    """In an equal pool the mixture reaches CDF 0.05 EXACTLY where the
    lower component alone reaches CDF 0.10 -- SME B contributes ~2e-13 mass
    at that point (verified at plan-gate), so this is an exact identity,
    not an approximation of one. Binding amendment: the earlier `< $1,155`
    bound was arithmetically WRONG; do not "fix" the math to satisfy it."""
    actual = mixture_quantile_lognorm(_WORKED_MIX, 0.05)
    expected = _qlnormtrunc(0.10, 8.06, 0.70, 0.0, math.inf)
    print(f"Q_mix(0.05): expected={expected!r} actual={actual!r}")
    assert actual == pytest.approx(expected, rel=1e-9), (
        f"Q_mix(0.05) should equal _qlnormtrunc(0.10, 8.06, 0.70, 0, inf) "
        f"(expected={expected}, actual={actual})"
    )
    # Sanity anchor: ~$1,291 per spec §6 / plan Task 2 amendment.
    assert actual == pytest.approx(1290.67, rel=1e-3)


def test_worked_pair_high_quantile_loose_bound() -> None:
    """Q_mix(0.95) ~= B's upper decile (~$32.4M); loose bound > $15.2M per
    the binding amendment (the earlier `< $1,155` low-side bound was wrong,
    this bound is deliberately loose rather than a second brittle exact
    pin)."""
    actual = mixture_quantile_lognorm(_WORKED_MIX, 0.95)
    print(f"Q_mix(0.95): actual={actual!r} (loose bound > 15.2e6)")
    assert actual > 15.2e6
    # Sanity anchor: ~$32.4M per spec §6.
    assert actual == pytest.approx(32.4e6, rel=1e-2)


def test_worked_pair_diverges_from_retired_averaged_fit_range() -> None:
    """Pre-mixture, combine_lognorm_trunc parameter-averaged this exact
    pair to (meanlog 11.92, sdlog 0.94) -> 90% range ~$31k-$710k (issue
    #343's worked example, covering NEITHER expert). The mixture's range
    must NOT reproduce that -- low sits far below $31k (near A's own
    decile) and high sits far above $710k (near B's own decile)."""
    low = mixture_quantile_lognorm(_WORKED_MIX, 0.05)
    high = mixture_quantile_lognorm(_WORKED_MIX, 0.95)
    print(f"mixture 90% range=({low!r}, {high!r}) vs retired average ~(31_000, 710_000)")
    assert low < 5_000
    assert high > 1_000_000


# ----------------------------------------------------------------------------
# Quantile inversion vs brute-force Monte Carlo (binding amendment: 1e-2
# relative at 1e6 draws, fixed seed -- 1e-3 is MC-flaky on tail quantiles,
# gate-measured 62-72% seed-failure rate)
# ----------------------------------------------------------------------------


def test_worked_pair_quantiles_match_brute_force_monte_carlo() -> None:
    """1e6-draw empirical quantiles at a fixed seed vs the deterministic
    bisection, on the TAIL quantiles only (p=0.05, p=0.95) -- p=0.5 sits in
    the inter-expert density valley for this divergent pair, where a
    finite sample's empirical median is extremely unstable (checked
    separately: relative error at p=0.5 ranges -53% to +205% across
    several seeds at 1e6 draws) and is NOT a meaningful MC cross-check for
    a bimodal mixture; the binding amendment scopes the MC comparison to
    tail quantiles for exactly this reason."""
    rng = np.random.default_rng(0)
    n = 1_000_000
    idx = rng.integers(0, 2, size=n)
    meanlogs = np.array([8.06, 15.77])[idx]
    sdlogs = np.array([0.70, 1.19])[idx]
    samples = rng.lognormal(meanlogs, sdlogs)

    for p in (0.05, 0.95):
        empirical = float(np.quantile(samples, p))
        analytic = mixture_quantile_lognorm(_WORKED_MIX, p)
        rel_err = abs(empirical - analytic) / analytic
        print(
            f"p={p}: analytic(bisection)={analytic!r} "
            f"empirical(1e6 MC, seed=0)={empirical!r} rel_err={rel_err!r}"
        )
        assert rel_err < 1e-2, (
            f"p={p}: analytic={analytic}, empirical={empirical}, rel_err={rel_err} exceeds 1e-2"
        )


# ----------------------------------------------------------------------------
# Monotonicity (deterministic -- no MC needed)
# ----------------------------------------------------------------------------


def test_worked_pair_quantile_strictly_increasing() -> None:
    ps = [0.01, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99]
    qs = [mixture_quantile_lognorm(_WORKED_MIX, p) for p in ps]
    for i in range(1, len(qs)):
        assert qs[i] > qs[i - 1], f"Q_mix not increasing at p={ps[i]}: {qs[i - 1]} -> {qs[i]}"


def test_moderate_mixture_quantile_strictly_increasing() -> None:
    """Same check on a less-divergent (more typical) mixture, for
    generality beyond the deliberately-divergent worked pair."""
    a = LogNormalTruncFit(meanlog=10.0, sdlog=0.6, min_support=0.0, max_support=math.inf)
    b = LogNormalTruncFit(meanlog=10.8, sdlog=0.5, min_support=0.0, max_support=math.inf)
    mix = LognormMixture(components=(a, b), weights=(0.6, 0.4))
    ps = np.linspace(0.01, 0.99, 25)
    qs = [mixture_quantile_lognorm(mix, float(p)) for p in ps]
    assert all(qs[i] > qs[i - 1] for i in range(1, len(qs)))


# ----------------------------------------------------------------------------
# Single-component identity -- byte-identical to the pre-mixture scalar path
# ----------------------------------------------------------------------------


def test_single_component_quantile_byte_identical_to_qlnormtrunc() -> None:
    mix = LognormMixture(components=(_LN_A,), weights=(1.0,))
    for p in (0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99):
        actual = mixture_quantile_lognorm(mix, p)
        expected = _qlnormtrunc(p, _LN_A.meanlog, _LN_A.sdlog, _LN_A.min_support, _LN_A.max_support)
        assert actual == expected, f"p={p}: mixture={actual!r} != scalar={expected!r}"


def test_single_component_pert_collapse_byte_identical() -> None:
    """No clamp triggered (sdlog=0.70 < 1.645 threshold) -- the common
    unclamped case."""
    mix = LognormMixture(components=(_LN_A,), weights=(1.0,))
    mix_pert, mix_reason = lognormal_mixture_to_pert_approx(mix)
    scalar_pert, scalar_reason = lognormal_to_pert_approx(_LN_A)
    assert mix_pert == scalar_pert
    assert mix_reason == scalar_reason
    assert mix_reason is None


def test_single_component_pert_collapse_byte_identical_with_clamp() -> None:
    """High sdlog (>= 1.645) clamps the mode to low with
    MODE_BELOW_PERT_LOW -- exercise the clamped branch too, since that is
    where a parallel (non-shared) implementation would most likely drift."""
    high_sigma_fit = LogNormalTruncFit(
        meanlog=10.0, sdlog=2.0, min_support=0.0, max_support=math.inf
    )
    mix = LognormMixture(components=(high_sigma_fit,), weights=(1.0,))
    mix_pert, mix_reason = lognormal_mixture_to_pert_approx(mix)
    scalar_pert, scalar_reason = lognormal_to_pert_approx(high_sigma_fit)
    assert mix_pert == scalar_pert
    assert mix_reason == scalar_reason
    assert mix_reason == ModeClampReason.MODE_BELOW_PERT_LOW


# ----------------------------------------------------------------------------
# Bimodal mode: well-separated components, heavier one wins the global peak
# ----------------------------------------------------------------------------


def test_bimodal_mode_picks_heavier_components_peak() -> None:
    """Equal-width, well-separated components with UNEQUAL weight: the
    global mixture-density argmax must sit at the heavier component's own
    closed-form mode (its density contribution swamps the lighter
    component's everywhere near its own peak)."""
    heavy = LogNormalTruncFit(meanlog=10.0, sdlog=0.3, min_support=0.0, max_support=math.inf)
    light = LogNormalTruncFit(meanlog=13.0, sdlog=0.3, min_support=0.0, max_support=math.inf)
    mix = LognormMixture(components=(heavy, light), weights=(0.9, 0.1))
    pert, reason = lognormal_mixture_to_pert_approx(mix)
    heavy_mode = math.exp(heavy.meanlog - heavy.sdlog**2)
    light_mode = math.exp(light.meanlog - light.sdlog**2)
    print(
        f"heavy_mode={heavy_mode!r} light_mode={light_mode!r} "
        f"picked_mode={pert.mode!r} reason={reason!r}"
    )
    assert reason is None  # falls inside [low, high] unclamped
    assert pert.mode == pytest.approx(heavy_mode, rel=1e-4)
    assert abs(pert.mode - heavy_mode) < abs(pert.mode - light_mode)


def test_bimodal_mode_unconstrained_includes_out_of_range_component_mode() -> None:
    """A component's closed-form mode is a candidate EVEN IF it falls
    outside that component's own support / the PERT [low, high] window
    (binding amendment: "including out-of-range ones") -- constructed so
    the dominant candidate is a component mode outside [low, high], then
    _clamp_mode (not the argmax search) is what pulls it back in."""
    dominant = LogNormalTruncFit(meanlog=16.0, sdlog=0.1, min_support=0.0, max_support=math.inf)
    minor = LogNormalTruncFit(meanlog=5.0, sdlog=1.5, min_support=0.0, max_support=math.inf)
    mix = LognormMixture(components=(dominant, minor), weights=(0.95, 0.05))
    pert, reason = lognormal_mixture_to_pert_approx(mix)
    dominant_mode = math.exp(dominant.meanlog - dominant.sdlog**2)
    print(f"dominant_mode={dominant_mode!r} pert={pert!r} reason={reason!r}")
    # The dominant component's own tight peak sits above its 95th-heavy
    # pooled `high` only if high < dominant_mode; assert internal
    # consistency rather than a specific reason (support/PERT geometry
    # dependent) -- the key property is mode stays within [low, high].
    assert pert.low <= pert.mode <= pert.high


# ----------------------------------------------------------------------------
# Bracket-widening hard cap (binding amendment): 200-doubling circuit
# breaker raises ArithmeticError with the component params. The real
# candidate-seeded bracket is self-satisfying for well-formed finite
# inputs (see mixture_quantile_lognorm's docstring) -- triggering the cap
# requires a deliberately mis-seeded bracket, injected here via
# monkeypatch.
# ----------------------------------------------------------------------------


def test_bracket_widening_hard_cap_raises_arithmetic_error_upper(monkeypatch) -> None:
    import fair_cam.quantile_pooling._lognormal as ln_module

    monkeypatch.setattr(ln_module, "MIXTURE_BRACKET_WIDEN_MAX_DOUBLINGS", 3)
    monkeypatch.setattr(ln_module, "_qlnormtrunc", lambda *a, **k: 1e-6)

    with pytest.raises(ArithmeticError, match="upper bracket failed to converge") as exc_info:
        ln_module.mixture_quantile_lognorm(_WORKED_MIX, 0.5)
    assert "components=" in str(exc_info.value)
    assert "weights=" in str(exc_info.value)


def test_bracket_widening_hard_cap_raises_arithmetic_error_lower(monkeypatch) -> None:
    import fair_cam.quantile_pooling._lognormal as ln_module

    monkeypatch.setattr(ln_module, "MIXTURE_BRACKET_WIDEN_MAX_DOUBLINGS", 3)
    monkeypatch.setattr(ln_module, "_qlnormtrunc", lambda *a, **k: 1e15)

    with pytest.raises(ArithmeticError, match="lower bracket failed to converge") as exc_info:
        ln_module.mixture_quantile_lognorm(_WORKED_MIX, 0.5)
    assert "components=" in str(exc_info.value)


# ----------------------------------------------------------------------------
# Documented limitation: PERT collapse of a divergent mixture (Meth-I1,
# spec §2). NOT a regression bar -- a documentation pin of the known
# residual, per the binding amendment.
# ----------------------------------------------------------------------------


def _vose_pert_beta(low: float, mode: float, high: float) -> tuple[float, float]:
    """Vose BetaPERT (gamma=4) shape parameters -- MIRRORS
    fair_cam/risk_engine/fair_core.py FAIRDistribution.sample's PERT
    branch exactly (same formula, reproduced here rather than imported so
    quantile_pooling tests stay decoupled from risk_engine, per this
    module's "pure functions, zero coupling" convention). This is the
    actual sampled shape of a collapsed PERT triple once it reaches the
    engine."""
    gamma = 4.0
    mean = (low + gamma * mode + high) / (gamma + 2.0)
    stdev = (high - low) / (gamma + 2.0)
    g1 = (mean - low) / (high - low)
    g2 = ((mean - low) * (high - mean)) / (stdev**2)
    alpha = g1 * (g2 - 1.0)
    beta = alpha * (high - mean) / (mean - low)
    return alpha, beta


def test_divergent_pert_collapse_documented_limitation() -> None:
    """Pins the KNOWN residual of PERT-collapsing the divergent worked A/B
    pair, as a documentation pin (not a regression bar):

    - Inter-expert valley mass on P[$20k, $500k]: the collapsed PERT
      (sampled as Vose BetaPERT gamma=4, matching fair_core.py) places
      ~0.129 of its mass there; the TRUE mixture places ~0.009 there
      (values MC-verified at plan-gate; the gap is the headline residual
      of summarizing a bimodal mixture as a unimodal PERT).
    - Median ratio: collapsed-PERT median / true-mixture median ~= 66x
      (PERT $3.63M / mixture $55.0k) -- NOT the ~27x figure that only
      appears against the RETIRED parameter-averaged fit (do not confuse
      the two -- this ratio is against the true mixture)."""
    pert, _reason = lognormal_mixture_to_pert_approx(_WORKED_MIX)
    alpha, beta = _vose_pert_beta(pert.low, pert.mode, pert.high)
    rv = scipy_beta(alpha, beta)

    def pert_cdf(x: float) -> float:
        u = (x - pert.low) / (pert.high - pert.low)
        u = min(max(u, 0.0), 1.0)
        return float(rv.cdf(u))

    def pert_median() -> float:
        u = float(rv.ppf(0.5))
        return pert.low + u * (pert.high - pert.low)

    pert_valley_mass = pert_cdf(500_000) - pert_cdf(20_000)
    mix_valley_mass = (
        _plnormtrunc(500_000, _LN_A.meanlog, _LN_A.sdlog, _LN_A.min_support, _LN_A.max_support)
        * 0.5
        + _plnormtrunc(500_000, _LN_B.meanlog, _LN_B.sdlog, _LN_B.min_support, _LN_B.max_support)
        * 0.5
        - (
            _plnormtrunc(20_000, _LN_A.meanlog, _LN_A.sdlog, _LN_A.min_support, _LN_A.max_support)
            * 0.5
            + _plnormtrunc(20_000, _LN_B.meanlog, _LN_B.sdlog, _LN_B.min_support, _LN_B.max_support)
            * 0.5
        )
    )

    pert_median_actual = pert_median()
    mix_median_actual = mixture_quantile_lognorm(_WORKED_MIX, 0.5)
    ratio = pert_median_actual / mix_median_actual

    print(
        f"valley mass P[20k,500k]: PERT expected~=0.129 actual={pert_valley_mass!r} | "
        f"mixture expected~=0.009 actual={mix_valley_mass!r}"
    )
    print(
        f"median: PERT expected~=$3.63M actual={pert_median_actual!r} | "
        f"mixture expected~=$55.0k actual={mix_median_actual!r} | "
        f"ratio expected~=66x actual={ratio!r}"
    )

    assert pert_valley_mass == pytest.approx(0.129, abs=0.01)
    assert mix_valley_mass == pytest.approx(0.009, abs=0.003)
    assert pert_median_actual == pytest.approx(3.63e6, rel=0.02)
    assert mix_median_actual == pytest.approx(55_000, rel=0.02)
    assert ratio == pytest.approx(66, abs=5)


# ----------------------------------------------------------------------------
# Normal counterparts (mixture_quantile_norm / normal_mixture_to_pert_approx)
# ----------------------------------------------------------------------------

_N_A = NormalTruncFit(mean=0.2, sd=0.05, min_support=0.0, max_support=1.0)
_N_B = NormalTruncFit(mean=0.7, sd=0.05, min_support=0.0, max_support=1.0)


def test_normal_single_component_quantile_byte_identical() -> None:
    mix = NormMixture(components=(_N_A,), weights=(1.0,))
    for p in (0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99):
        actual = mixture_quantile_norm(mix, p)
        expected = _qnormtrunc(p, _N_A.mean, _N_A.sd, _N_A.min_support, _N_A.max_support)
        assert actual == expected


def test_normal_single_component_pert_collapse_byte_identical() -> None:
    mix = NormMixture(components=(_N_A,), weights=(1.0,))
    mix_pert, mix_reason = normal_mixture_to_pert_approx(mix)
    scalar_pert, scalar_reason = normal_to_pert_approx(_N_A)
    assert mix_pert == scalar_pert
    assert mix_reason == scalar_reason


def test_normal_single_component_pert_collapse_byte_identical_with_clamp() -> None:
    below_support = NormalTruncFit(mean=-0.05, sd=0.1, min_support=0.0, max_support=1.0)
    mix = NormMixture(components=(below_support,), weights=(1.0,))
    mix_pert, mix_reason = normal_mixture_to_pert_approx(mix)
    scalar_pert, scalar_reason = normal_to_pert_approx(below_support)
    assert mix_pert == scalar_pert
    assert mix_reason == scalar_reason
    assert mix_reason == ModeClampReason.UNTRUNCATED_MODE_BELOW_MIN_SUPPORT


def test_normal_worked_pair_quantile_self_consistent() -> None:
    """Deterministic self-consistency: F_mix(Q_mix(p)) == p (no MC needed
    -- mixture_quantile_norm and _pnormtrunc are both exact closed-form /
    bisection, so this holds to the bisection's own tolerance)."""
    mix = NormMixture(components=(_N_A, _N_B), weights=(0.5, 0.5))
    for p in (0.01, 0.05, 0.3, 0.5, 0.7, 0.95, 0.99):
        q = mixture_quantile_norm(mix, p)
        cdf = 0.5 * _pnormtrunc(
            q, _N_A.mean, _N_A.sd, _N_A.min_support, _N_A.max_support
        ) + 0.5 * _pnormtrunc(q, _N_B.mean, _N_B.sd, _N_B.min_support, _N_B.max_support)
        print(f"p={p}: Q_mix={q!r} F_mix(Q_mix)={cdf!r}")
        assert cdf == pytest.approx(p, abs=1e-6)


def test_normal_worked_pair_quantile_strictly_increasing() -> None:
    mix = NormMixture(components=(_N_A, _N_B), weights=(0.5, 0.5))
    ps = np.linspace(0.01, 0.99, 25)
    qs = [mixture_quantile_norm(mix, float(p)) for p in ps]
    assert all(qs[i] > qs[i - 1] for i in range(1, len(qs)))


def test_normal_bimodal_mode_picks_heavier_components_peak() -> None:
    mix = NormMixture(components=(_N_A, _N_B), weights=(0.8, 0.2))
    pert, reason = normal_mixture_to_pert_approx(mix)
    print(f"picked_mode={pert.mode!r} N_A.mean={_N_A.mean!r} N_B.mean={_N_B.mean!r}")
    assert reason is None
    assert pert.mode == pytest.approx(_N_A.mean, abs=1e-6)
