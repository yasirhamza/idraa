"""Pure types for the quantile-pooling helpers. No imports from idraa, no pyfair."""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, TypeVar

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


_FitT = TypeVar("_FitT")


def _warn_if_divergent_fits(
    fits: Sequence[Any],
    loc_field: str,
    scale_field: str,
    combine_name: str,
) -> None:
    """#343 interim guard: WARN when parameter-averaging pools divergent fits.

    Divergence criterion: any PAIR of fits whose central 90% intervals on
    the location scale are disjoint, i.e.::

        |loc_i - loc_j| > Z_0.95 * (scale_i + scale_j)

    For lognormal fits the location scale is the log scale (meanlog/sdlog),
    so disjointness here means the experts' central 90% ranges do not
    overlap in dollars either. When that holds, the arithmetic parameter
    average concentrates mass BETWEEN the experts — a distribution covering
    neither stated range, understating total uncertainty (risk-understating
    bias). See the #343 worked example: $1k-$10k pooled with $1M-$50M gives
    a 90% range of ~$31k-$710k.

    Interim only: logs and proceeds (the pooled value is unchanged). The
    real fix — a linear opinion pool / true mixture — is tracked at #243.
    Single-fit pooling never warns (identity under both schemes).
    """
    if len(fits) < 2:
        return
    for i in range(len(fits)):
        for j in range(i + 1, len(fits)):
            loc_i, loc_j = getattr(fits[i], loc_field), getattr(fits[j], loc_field)
            sc_i, sc_j = getattr(fits[i], scale_field), getattr(fits[j], scale_field)
            if abs(loc_i - loc_j) > _DIVERGENCE_Z * (sc_i + sc_j):
                logger.warning(
                    "%s: pooling DIVERGENT fits (pair %d/%d: %s=%.4g vs %.4g, "
                    "central 90%% intervals disjoint). Parameter averaging is "
                    "NOT a mixture — the pooled distribution concentrates mass "
                    "between the experts and can cover neither stated range "
                    "(understated tails / risk-understating bias, issue #343). "
                    "True mixture pooling is tracked at #243.",
                    combine_name,
                    i,
                    j,
                    loc_field,
                    loc_i,
                    loc_j,
                )
                return  # one warning per pooling call is enough


def _weighted_mean_fields(
    fits: Sequence[_FitT],
    weights: Sequence[float] | None,
    fields: Sequence[str],
    cls: type[_FitT],
) -> _FitT:
    """Weighted arithmetic mean of named fields across N fits.

    Used by both ``combine_lognorm_trunc`` and ``combine_norm`` per MD-1
    convenience-port semantics (engineering approximation, NOT a true
    distributional mixture — for divergent fits the average concentrates
    mass between the experts; see ``_warn_if_divergent_fits`` and issue
    #343). ``weights=None`` means EQUAL weights (1.0 each): a calibrated
    expert and an anecdotal estimate pool with equal epistemic authority
    unless the caller supplies weights. Validates: ``fits`` non-empty,
    ``weights`` length matches, ``sum(weights) > 0``. Builds the result via
    ``cls(**{field: weighted_mean, ...})`` -- caller is responsible for
    passing a list of fields the dataclass actually accepts.
    """
    if not fits:
        raise QuantilePoolingError(f"{cls.__name__} pooling requires >=1 fit")
    if weights is None:
        weights = [1.0] * len(fits)
    if len(weights) != len(fits):
        raise QuantilePoolingError(f"len(weights)={len(weights)} != len(fits)={len(fits)}")
    total_w = sum(weights)
    if total_w <= 0:
        raise QuantilePoolingError(f"sum(weights)={total_w} must be > 0")
    kwargs = {
        field: sum(getattr(f, field) * w for f, w in zip(fits, weights, strict=True)) / total_w
        for field in fields
    }
    return cls(**kwargs)
