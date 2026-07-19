"""Quantile-fitted distribution helpers for the wizard-step-3 elicitation flow.

Port of evaluator/collector R package (MIT, pinned commit per
tests/quantile_pooling/fixtures/evaluator_commit_pinned.txt). Pure functions;
zero coupling to idraa or pyfair. See spec §7.1 + §10 (MD-1 through MD-8)."""

from ._cleaning import clean_quantile_pair
from ._lognormal import (
    combine_lognorm_trunc,
    fit_lognorm_trunc,
    lognormal_mixture_to_pert_approx,
    lognormal_to_pert_approx,
    mixture_quantile_lognorm,
)
from ._lognormal_native import (
    Z_0_95,
    lognormal_from_median_mean,
    lognormal_from_quantiles,
    lognormal_mean,
    lognormal_quantiles,
)
from ._normal import (
    combine_norm,
    fit_norm_trunc,
    mixture_quantile_norm,
    normal_mixture_to_pert_approx,
    normal_to_pert_approx,
)
from ._types import (
    ClampEvent,
    DeadlineCallback,
    LogNormalTruncFit,
    LognormMixture,
    ModeClampReason,
    NormalTruncFit,
    NormMixture,
    PertTriple,
    QuantilePoolingError,
)

__all__ = [
    "Z_0_95",
    "ClampEvent",
    "DeadlineCallback",
    "LogNormalTruncFit",
    "LognormMixture",
    "ModeClampReason",
    "NormMixture",
    "NormalTruncFit",
    "PertTriple",
    "QuantilePoolingError",
    "clean_quantile_pair",
    "combine_lognorm_trunc",
    "combine_norm",
    "fit_lognorm_trunc",
    "fit_norm_trunc",
    "lognormal_from_median_mean",
    "lognormal_from_quantiles",
    "lognormal_mean",
    "lognormal_mixture_to_pert_approx",
    "lognormal_quantiles",
    "lognormal_to_pert_approx",
    "mixture_quantile_lognorm",
    "mixture_quantile_norm",
    "normal_mixture_to_pert_approx",
    "normal_to_pert_approx",
]
