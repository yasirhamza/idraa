"""Pure types for the quantile-pooling helpers. No imports from idraa, no pyfair."""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

# Z_0.95 from the native-lognormal module — _lognormal_native imports
# nothing from _types, so this is cycle-free (Arch re-gate corrected an
# earlier false cycle claim). Aliased so the divergence criterion reads
# as what it is. Pinned by test_pooling_divergence_warning.py.
from ._lognormal_native import Z_0_95 as _DIVERGENCE_Z

logger = logging.getLogger(__name__)


class ModeClampReason(StrEnum):
    """Meth-11 R3: typed enum for distribution_fit_metadata.mode_clamp_reason.
    String values are stable wire format; do not rename without a sidecar
    schema_version bump.

    Note on MODE_ABOVE_PERT_HIGH for lognormal fits: this enum value is
    UNREACHABLE for ``lognormal_to_pert_approx`` because the lognormal mode
    satisfies ``raw_mode <= median <= q95`` for any ``sdlog > 0`` -- so
    ``raw_mode > high`` implies ``max_support`` already clipped ``high`` below
    ``raw_mode`` and the precedence rule hands the event to
    ``UNTRUNCATED_MODE_ABOVE_MAX_SUPPORT`` first. The branch + enum value
    are retained for symmetry with ``normal_to_pert_approx`` (where the
    mode = mean and right-skewed PERT bounds can leave mode > high) and as
    a stable wire-format value."""

    UNTRUNCATED_MODE_BELOW_MIN_SUPPORT = "untruncated_mode_below_min_support"
    UNTRUNCATED_MODE_ABOVE_MAX_SUPPORT = "untruncated_mode_above_max_support"
    MODE_ABOVE_PERT_HIGH = "mode_above_pert_high"
    MODE_BELOW_PERT_LOW = "mode_below_pert_low"


@dataclass(frozen=True)
class LogNormalTruncFit:
    meanlog: float
    sdlog: float
    min_support: float
    max_support: float


@dataclass(frozen=True)
class NormalTruncFit:
    mean: float
    sd: float
    min_support: float
    max_support: float


def _validate_mixture_shape(
    components: Sequence[Any], weights: Sequence[float], cls_name: str
) -> None:
    """Structural invariant for LognormMixture/NormMixture, called from
    ``__post_init__`` so it holds for ANY construction path -- not just
    via ``combine_lognorm_trunc``/``combine_norm`` (issue #27).

    Non-empty; ``weights`` length matches ``components``; every weight is
    ``> 0`` (checked as ``not (w > 0)`` rather than ``w <= 0`` so a NaN
    weight is also rejected -- NaN satisfies neither comparison, the
    #306 corruption class); weights sum to 1 within 1e-9 (the combiners
    normalize before constructing, so this re-checks an already-
    established invariant rather than performing normalization -- see
    ``_normalize_weights``).
    """
    if not components:
        raise QuantilePoolingError(f"{cls_name} requires >=1 component")
    if len(weights) != len(components):
        raise QuantilePoolingError(
            f"{cls_name}: len(weights)={len(weights)} != len(components)={len(components)}"
        )
    for i, w in enumerate(weights):
        if not (w > 0):
            raise QuantilePoolingError(f"{cls_name}: weights[{i}]={w} must be > 0")
    total = sum(weights)
    if abs(total - 1.0) > 1e-9:
        raise QuantilePoolingError(f"{cls_name}: sum(weights)={total} must equal 1 (±1e-9)")


@dataclass(frozen=True)
class LognormMixture:
    """A linear-opinion-pool mixture of truncated-lognormal SME fits
    (issue #27 via #25). Each ``components[i]`` keeps its ORIGINAL fit
    verbatim -- pooling no longer averages (meanlog, sdlog, min_support,
    max_support) into a single distribution. ``weights`` are normalized to
    sum to 1 by the combiner and re-validated (not re-normalized) here in
    ``__post_init__``.

    Methodology: the linear opinion pool is the standard combination rule
    for expert probability distributions -- Clemen, R.T. & Winkler, R.L.
    (1999), "Combining Probability Distributions From Experts in Risk
    Analysis", Risk Analysis 19(2), pp. 187-203 (lineage to Stone, M.
    (1961), "The Opinion Pool", Annals of Mathematical Statistics 32(4)).

    R-oracle departure (explicit, not silent): the evaluator/collector R
    port this module used to mirror (MD-1, R/fit_distributions.R:67-79)
    parameter-AVERAGES divergent fits into one distribution covering
    neither expert's stated range (issue #343's worked example: $1k-$10k
    pooled with $1M-$50M gives a 90% range of ~$31k-$710k, covering
    neither). This mixture type is an intentional, methodology-justified
    break from that R behavior for multi-component pooling -- see
    docs/superpowers/specs/2026-07-19-mixture-pooling-design.md
    "Decision record" (2026-07-19) for the full rationale. Construct via
    ``combine_lognorm_trunc``, not directly, in production code.
    """

    components: tuple[LogNormalTruncFit, ...]
    weights: tuple[float, ...]

    def __post_init__(self) -> None:
        _validate_mixture_shape(self.components, self.weights, type(self).__name__)


@dataclass(frozen=True)
class NormMixture:
    """Normal-fit counterpart to ``LognormMixture`` (used for vuln, MD-4a).
    Same linear-opinion-pool semantics and R-oracle-departure rationale --
    see ``LognormMixture`` for the full docstring. Construct via
    ``combine_norm``, not directly, in production code.
    """

    components: tuple[NormalTruncFit, ...]
    weights: tuple[float, ...]

    def __post_init__(self) -> None:
        _validate_mixture_shape(self.components, self.weights, type(self).__name__)


@dataclass(frozen=True)
class PertTriple:
    low: float
    mode: float
    high: float


@dataclass(frozen=True)
class ClampEvent:
    """Narrow event from clean_quantile_pair. v3 service layer wraps this
    into AuditClampEvent with scenario context."""

    rule: str
    before: tuple[float, float]
    after: tuple[float, float]


class QuantilePoolingError(RuntimeError):
    """Raised on optimizer divergence, non-finite fit params, or DeadlineCallback timeout."""


class DeadlineCallback:
    """Arch-16/Sec-11 R2 + Arch-29 R3 signature-compat. Cooperative timeout
    for scipy.optimize.minimize. Signature `(*args, **kwargs)` accepts both
    scipy <1.11 positional-x-vector callbacks AND scipy >=1.11
    `intermediate_result=` keyword-argument callbacks."""

    def __init__(self, wall_clock_ms: int) -> None:
        self._budget_s = wall_clock_ms / 1000.0
        self._start = time.monotonic()

    def __call__(self, *args: Any, **kwargs: Any) -> None:
        if time.monotonic() - self._start > self._budget_s:
            raise QuantilePoolingError(
                f"scipy.optimize exceeded wall_clock_ms budget ({self._budget_s * 1000:.0f}ms)"
            )


def _warn_if_divergent_fits(
    fits: Sequence[Any],
    loc_field: str,
    scale_field: str,
    combine_name: str,
) -> None:
    """Issue #27: INFO-level observability when pooling divergent fits.

    Divergence criterion (unchanged from the #343 interim guard): any PAIR
    of fits whose central 90% intervals on the location scale are
    disjoint, i.e.::

        |loc_i - loc_j| > Z_0.95 * (scale_i + scale_j)

    For lognormal fits the location scale is the log scale (meanlog/sdlog),
    so disjointness here means the experts' central 90% ranges do not
    overlap in dollars either.

    Historical note: before the linear-opinion-pool mixture landed (#27),
    ``combine_lognorm_trunc``/``combine_norm`` parameter-AVERAGED fits, and
    a divergent pair produced a pooled distribution covering NEITHER
    expert's stated range -- a real defect, hence the original WARNING
    (see the #343 worked example: $1k-$10k pooled with $1M-$50M gave a 90%
    range of ~$31k-$710k, covering neither). Parameter averaging has been
    replaced by an explicit mixture: each expert's fit survives as its own
    component, so divergence is now REPRESENTED, not distorted. The log
    demotes to INFO accordingly -- it is observability (a caller may want
    to know experts disagreed sharply), not a signal of understated risk.
    Single-fit pooling never logs (identity under any scheme).
    """
    if len(fits) < 2:
        return
    for i in range(len(fits)):
        for j in range(i + 1, len(fits)):
            loc_i, loc_j = getattr(fits[i], loc_field), getattr(fits[j], loc_field)
            sc_i, sc_j = getattr(fits[i], scale_field), getattr(fits[j], scale_field)
            if abs(loc_i - loc_j) > _DIVERGENCE_Z * (sc_i + sc_j):
                logger.info(
                    "%s: pooling divergent fits (pair %d/%d: %s=%.4g vs %.4g, "
                    "central 90%% intervals disjoint). Divergence is represented "
                    "by the mixture -- each expert's fit is kept as its own "
                    "component rather than averaged away (linear opinion pool, "
                    "Clemen & Winkler 1999; issue #27). Informational only.",
                    combine_name,
                    i,
                    j,
                    loc_field,
                    loc_i,
                    loc_j,
                )
                return  # one log record per pooling call is enough


# ----------------------------------------------------------------------------
# Mixture quantile / PERT-collapse shared math (issue #27 Task 2). Family-
# agnostic helpers reused by BOTH _lognormal.py and _normal.py so a
# single-component mixture is byte-identical to the pre-mixture scalar
# functions by construction (same code path, not a parallel
# reimplementation) -- see the Task 2 binding amendment in
# docs/superpowers/plans/2026-07-19-mixture-pooling.md.
# ----------------------------------------------------------------------------

#: HARD CAP on geometric bracket-widening doublings in mixture_quantile_*
#: (binding amendment). A finite-but-huge meanlog/mean must not spin the
#: render path -- non-convergence raises ArithmeticError with the component
#: params rather than looping indefinitely. In practice the initial bracket
#: (built from each component's own quantile function) is self-satisfying
#: for any finite, well-formed component set -- see mixture_quantile_
#: lognorm's docstring for the argument -- so this cap is a defensive
#: circuit breaker, not something well-formed production inputs hit.
MIXTURE_BRACKET_WIDEN_MAX_DOUBLINGS = 200

#: Bisection iteration cap once a valid bracket is established. 200
#: iterations comfortably reaches MIXTURE_BISECT_REL_TOL even across the
#: multi-order-of-magnitude ranges lognormal mixtures span.
MIXTURE_BISECT_MAX_ITER = 200

#: Relative convergence tolerance for the mixture-quantile bisection
#: (binding amendment: "tolerance 1e-10 relative, deterministic").
MIXTURE_BISECT_REL_TOL = 1e-10

#: Grid resolution for the unconstrained mixture-density mode search
#: (binding amendment: "256-point log grid").
MIXTURE_MODE_GRID_POINTS = 256


def _clamp_mode(
    raw_mode: float,
    min_support: float,
    max_support: float,
    low: float,
    high: float,
) -> tuple[float, ModeClampReason | None]:
    """Shared 4-branch clamp-precedence machinery for PERT-collapse mode
    computation. Used by ``lognormal_to_pert_approx``, ``normal_to_pert_
    approx``, and their mixture counterparts (``lognormal_mixture_to_pert_
    approx`` / ``normal_mixture_to_pert_approx``, issue #27 Task 2) -- this
    is the SAME code path for all four call sites, not a parallel
    reimplementation, so a single-component mixture's mode (and
    ``mode_clamp_reason``) is byte-identical to the pre-mixture scalar
    function by construction.

    Precedence (support-boundary wins over PERT-boundary; see
    ``lognormal_to_pert_approx``'s module docstring for the full
    rationale, including why ``MODE_ABOVE_PERT_HIGH`` is unreachable for
    lognormal fits):
      1. raw_mode < min_support -> UNTRUNCATED_MODE_BELOW_MIN_SUPPORT
      2. raw_mode > max_support -> UNTRUNCATED_MODE_ABOVE_MAX_SUPPORT
      3. ELSE raw_mode > high   -> MODE_ABOVE_PERT_HIGH
      4. ELSE raw_mode < low    -> MODE_BELOW_PERT_LOW
    """
    reason: ModeClampReason | None = None
    lo_bound = max(min_support, low)
    hi_bound = min(max_support, high)
    if raw_mode < min_support:
        mode, reason = lo_bound, ModeClampReason.UNTRUNCATED_MODE_BELOW_MIN_SUPPORT
    elif raw_mode > max_support:
        mode, reason = hi_bound, ModeClampReason.UNTRUNCATED_MODE_ABOVE_MAX_SUPPORT
    elif raw_mode > high:
        mode, reason = high, ModeClampReason.MODE_ABOVE_PERT_HIGH
    elif raw_mode < low:
        mode, reason = low, ModeClampReason.MODE_BELOW_PERT_LOW
    else:
        mode = raw_mode
    return mode, reason


def _golden_section_max(
    f: Any,
    a: float,
    b: float,
    *,
    rel_tol: float = 1e-9,
    max_iter: int = 200,
) -> float:
    """Maximize a (locally) unimodal callable ``f`` over ``[a, b]`` via
    golden-section search (issue #27 Task 2: refines the coarse-grid
    mixture-density mode candidate to ``rel_tol`` relative precision).
    Family-agnostic -- shared by the lognormal and normal mixture mode
    searches (``_mixture_mode_lognorm`` / ``_mixture_mode_norm``)."""
    if a > b:
        a, b = b, a
    if a == b:
        return a
    invphi = (math.sqrt(5.0) - 1.0) / 2.0  # 1 / golden ratio
    c = b - invphi * (b - a)
    d = a + invphi * (b - a)
    fc, fd = f(c), f(d)
    for _ in range(max_iter):
        if abs(b - a) <= rel_tol * max(abs(a), abs(b), 1.0):
            break
        if fc > fd:
            b, d, fd = d, c, fc
            c = b - invphi * (b - a)
            fc = f(c)
        else:
            a, c, fc = c, d, fd
            d = a + invphi * (b - a)
            fd = f(d)
    return (a + b) / 2.0


def _normalize_weights(
    fits: Sequence[Any],
    weights: Sequence[float] | None,
    cls_name: str,
) -> tuple[float, ...]:
    """Validate raw pooling weights and normalize them to sum to 1.

    Shared by ``combine_lognorm_trunc``/``combine_norm`` (issue #27):
    ``weights=None`` means EQUAL weights (1.0 each) -- a calibrated expert
    and an anecdotal estimate pool with equal epistemic authority unless
    the caller supplies weights. Validates ``fits`` non-empty, ``weights``
    length matches, ``sum(weights) > 0`` (guards the division below and
    gives a clean message for the all-zero case), then each raw weight is
    ``> 0`` individually (checked as ``not (w > 0)`` so NaN is rejected
    too) -- this catches e.g. ``weights=[-1, -1]`` which sums positive-safe
    but would silently normalize to ``(0.5, 0.5)`` if only the total were
    checked. ``LognormMixture``/``NormMixture.__post_init__`` re-validates
    the returned tuple as a construction-path-independent invariant
    (``_validate_mixture_shape``) -- that is a second, deliberately
    redundant guard, not dead duplication: this function guards the RAW
    caller input (and prevents a ZeroDivisionError), ``__post_init__``
    guards the DATACLASS invariant regardless of how it was built.
    """
    if not fits:
        raise QuantilePoolingError(f"{cls_name} pooling requires >=1 fit")
    if weights is None:
        weights = [1.0] * len(fits)
    if len(weights) != len(fits):
        raise QuantilePoolingError(f"len(weights)={len(weights)} != len(fits)={len(fits)}")
    total_w = sum(weights)
    if total_w <= 0:
        raise QuantilePoolingError(f"sum(weights)={total_w} must be > 0")
    for i, w in enumerate(weights):
        if not (w > 0):
            raise QuantilePoolingError(f"weights[{i}]={w} must be > 0")
    return tuple(w / total_w for w in weights)
