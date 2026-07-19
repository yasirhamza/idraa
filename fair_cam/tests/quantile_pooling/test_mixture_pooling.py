"""New surface for issue #27 (via #25): LognormMixture/NormMixture + the
linear-opinion-pool combiners.

Complements ``test_combine_lognorm_trunc.py`` and ``test_combine_norm.py``,
which each pin the per-distribution R-oracle-departure test and the
retired-arithmetic-mean history for their own combiner; this file pins the
shared construction/validation contract (component identity, weight
normalization, ``__post_init__`` invariants) directly against both mixture
types.

Methodology: the linear opinion pool is the standard combination rule for
expert probability distributions -- Clemen, R.T. & Winkler, R.L. (1999),
"Combining Probability Distributions From Experts in Risk Analysis", Risk
Analysis 19(2), pp. 187-203 (lineage to Stone, M. (1961), "The Opinion
Pool", Annals of Mathematical Statistics 32(4)). See
docs/superpowers/specs/2026-07-19-mixture-pooling-design.md for the full
decision record.
"""

from __future__ import annotations

import math

import pytest
from fair_cam.quantile_pooling import (
    LogNormalTruncFit,
    LognormMixture,
    NormalTruncFit,
    NormMixture,
    QuantilePoolingError,
    combine_lognorm_trunc,
    combine_norm,
)

# The #27/#343 worked pair: SME A $1k-$10k, SME B $1M-$50M.
_LN_A = LogNormalTruncFit(meanlog=8.06, sdlog=0.70, min_support=0.0, max_support=math.inf)
_LN_B = LogNormalTruncFit(meanlog=15.77, sdlog=1.19, min_support=0.0, max_support=math.inf)
_N_A = NormalTruncFit(mean=0.2, sd=0.1, min_support=0.0, max_support=1.0)
_N_B = NormalTruncFit(mean=0.6, sd=0.2, min_support=0.0, max_support=1.0)


# ----------------------------------------------------------------------------
# combine_lognorm_trunc / combine_norm -- equal + explicit weight normalization
# ----------------------------------------------------------------------------


def test_combine_lognorm_trunc_returns_mixture_with_normalized_equal_weights() -> None:
    pooled = combine_lognorm_trunc([_LN_A, _LN_B])
    assert isinstance(pooled, LognormMixture)
    assert pooled.components == (_LN_A, _LN_B)
    assert pooled.weights == pytest.approx((0.5, 0.5))
    assert sum(pooled.weights) == pytest.approx(1.0, abs=1e-9)


def test_combine_lognorm_trunc_normalizes_explicit_weights() -> None:
    pooled = combine_lognorm_trunc([_LN_A, _LN_B], weights=[3.0, 1.0])
    assert pooled.components == (_LN_A, _LN_B)
    assert pooled.weights == pytest.approx((0.75, 0.25))
    assert sum(pooled.weights) == pytest.approx(1.0, abs=1e-9)


def test_combine_norm_returns_mixture_with_normalized_equal_weights() -> None:
    pooled = combine_norm([_N_A, _N_B])
    assert isinstance(pooled, NormMixture)
    assert pooled.components == (_N_A, _N_B)
    assert pooled.weights == pytest.approx((0.5, 0.5))


def test_combine_norm_normalizes_explicit_weights() -> None:
    pooled = combine_norm([_N_A, _N_B], weights=[1.0, 3.0])
    assert pooled.components == (_N_A, _N_B)
    assert pooled.weights == pytest.approx((0.25, 0.75))


# ----------------------------------------------------------------------------
# Single-fit identity -- exact, by construction
# ----------------------------------------------------------------------------


def test_combine_lognorm_trunc_single_fit_is_identity() -> None:
    pooled = combine_lognorm_trunc([_LN_A])
    assert pooled.components == (_LN_A,)
    assert pooled.weights == (1.0,)


def test_combine_norm_single_fit_is_identity() -> None:
    pooled = combine_norm([_N_A])
    assert pooled.components == (_N_A,)
    assert pooled.weights == (1.0,)


# ----------------------------------------------------------------------------
# Weight validation at the combiner entry point (zeros/negatives/len-mismatch)
# ----------------------------------------------------------------------------


def test_combine_lognorm_trunc_rejects_zero_weight_among_many() -> None:
    with pytest.raises(QuantilePoolingError, match=r"weights\[1\]"):
        combine_lognorm_trunc([_LN_A, _LN_B], weights=[1.0, 0.0])


def test_combine_lognorm_trunc_rejects_negative_weight() -> None:
    # sum([-1.0, 2.0]) = 1.0 > 0, so the total-weight guard alone would
    # miss this -- the per-element check must run too (see _normalize_weights).
    with pytest.raises(QuantilePoolingError, match=r"weights\[0\]"):
        combine_lognorm_trunc([_LN_A, _LN_B], weights=[-1.0, 2.0])


def test_combine_lognorm_trunc_rejects_len_mismatch() -> None:
    with pytest.raises(QuantilePoolingError, match=r"len\(weights\)"):
        combine_lognorm_trunc([_LN_A, _LN_B], weights=[1.0, 1.0, 1.0])


def test_combine_norm_rejects_negative_weight() -> None:
    with pytest.raises(QuantilePoolingError, match=r"weights\[1\]"):
        combine_norm([_N_A, _N_B], weights=[1.0, -0.5])


def test_combine_norm_rejects_len_mismatch() -> None:
    with pytest.raises(QuantilePoolingError, match=r"len\(weights\)"):
        combine_norm([_N_A], weights=[1.0, 1.0])


# ----------------------------------------------------------------------------
# LognormMixture / NormMixture __post_init__ invariants -- construction-path
# independent: these must hold no matter how the mixture is built, not just
# via combine_lognorm_trunc/combine_norm.
# ----------------------------------------------------------------------------


def test_lognorm_mixture_rejects_empty_components() -> None:
    with pytest.raises(QuantilePoolingError, match=">=1 component"):
        LognormMixture(components=(), weights=())


def test_lognorm_mixture_rejects_weights_length_mismatch() -> None:
    with pytest.raises(QuantilePoolingError, match=r"len\(weights\)"):
        LognormMixture(components=(_LN_A, _LN_B), weights=(1.0,))


def test_lognorm_mixture_rejects_nonpositive_weight() -> None:
    with pytest.raises(QuantilePoolingError, match=r"weights\[0\]"):
        LognormMixture(components=(_LN_A, _LN_B), weights=(0.0, 1.0))


def test_lognorm_mixture_rejects_nan_weight() -> None:
    """NaN satisfies neither `> 0` nor `<= 0` -- __post_init__ must check
    `not (w > 0)` so a NaN weight is still caught (the #306 corruption
    class: a non-finite value silently slipping past a range check)."""
    with pytest.raises(QuantilePoolingError, match=r"weights\[1\]"):
        LognormMixture(components=(_LN_A, _LN_B), weights=(0.5, math.nan))


def test_lognorm_mixture_rejects_weights_not_summing_to_one() -> None:
    with pytest.raises(QuantilePoolingError, match=r"sum\(weights\)"):
        LognormMixture(components=(_LN_A, _LN_B), weights=(0.3, 0.3))


def test_lognorm_mixture_accepts_weights_within_tolerance() -> None:
    # sum = 1 + 1e-10 -- inside the documented ±1e-9 tolerance.
    mix = LognormMixture(components=(_LN_A, _LN_B), weights=(0.5, 0.5 + 1e-10))
    assert mix.weights == (0.5, 0.5 + 1e-10)


def test_norm_mixture_rejects_empty_components() -> None:
    with pytest.raises(QuantilePoolingError, match=">=1 component"):
        NormMixture(components=(), weights=())


def test_norm_mixture_rejects_weights_length_mismatch() -> None:
    with pytest.raises(QuantilePoolingError, match=r"len\(weights\)"):
        NormMixture(components=(_N_A, _N_B, _N_A), weights=(1.0, 1.0))


def test_norm_mixture_rejects_nonpositive_weight() -> None:
    with pytest.raises(QuantilePoolingError, match=r"weights\[1\]"):
        NormMixture(components=(_N_A, _N_B), weights=(1.0, 0.0))


def test_norm_mixture_rejects_weights_not_summing_to_one() -> None:
    with pytest.raises(QuantilePoolingError, match=r"sum\(weights\)"):
        NormMixture(components=(_N_A, _N_B), weights=(0.4, 0.4))


def test_lognorm_mixture_single_component_is_valid() -> None:
    mix = LognormMixture(components=(_LN_A,), weights=(1.0,))
    assert mix.components == (_LN_A,)


# ----------------------------------------------------------------------------
# N>=3 components -- adapter-iteration-contract style coverage: pooling many
# components must not silently drop any of them.
# ----------------------------------------------------------------------------


def test_combine_lognorm_trunc_preserves_all_components_n3() -> None:
    third = LogNormalTruncFit(meanlog=10.0, sdlog=0.5, min_support=0.0, max_support=math.inf)
    pooled = combine_lognorm_trunc([_LN_A, _LN_B, third], weights=[1.0, 1.0, 2.0])
    assert pooled.components == (_LN_A, _LN_B, third)
    assert pooled.weights == pytest.approx((0.25, 0.25, 0.5))
    assert sum(pooled.weights) == pytest.approx(1.0, abs=1e-9)
