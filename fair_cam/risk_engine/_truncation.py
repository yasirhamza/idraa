"""Truncated-lognormal sampling via inverse-CDF (PR2 capacity bound).

Bounds a lognormal loss draw to support ``[0, max_value)`` -- closed at 0
(attainable at probability 2**-53, when the drawn uniform is exactly 0.0),
open at ``max_value`` (never attained, since ``rng.random()`` is half-open
``[0, 1)`` and the truncation multiplies by ``Phi(b) <= 1`` before inverting).

Formula (frozen -- methodology-owned, do not re-derive):
    b = (ln(max_value) - meanlog) / sigma
    u = rng.random(size) * Phi(b)
    x = exp(meanlog + sigma * Phi^-1(u))

where ``Phi``/``Phi^-1`` are the standard-normal CDF/inverse-CDF
(``scipy.special.ndtr``/``ndtri``). This is the standard inverse-CDF
(quantile) transform for a left-truncated-at-0 lognormal specialised to a
RIGHT truncation at ``max_value``: the untruncated quantile function is
``exp(meanlog + sigma*Phi^-1(p))``; restricting ``p`` to draw only from
``[0, Phi(b))`` restricts ``x`` to ``[0, max_value)``.

Verified three ways (2026-07-25, seed=20260725, n=5,000,000, SYNTHETIC
triple meanlog=ln(1e6)=13.815510557964274, sigma=1.7, max_value=1e9 --
`fair_cam/` is TRACKED and PUBLIC; this deployment's real (meanlog, sigma,
max) is never disclosed here, per the same rule Task 1 applies to Decimal
revenue test inputs):

1. Against ``fair_cam.quantile_pooling._lognormal._qlnormtrunc`` (mirrors
   ``EnvStats::qlnormTrunc``): empirical quantiles of a 5,000,000-draw
   sample vs. ``_qlnormtrunc(p, meanlog, sigma, 0.0, max_value)`` swept over
   p in {0.01, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99, 0.999,
   0.9999, 0.99999}. WORST relative error over the swept set (not the most
   favourable point): 2.21e-02, at p=0.9999 -- expected, not a
   discrepancy: only ~500 of 5,000,000 draws land beyond that quantile, so
   finite-sample quantile noise (~1/sqrt(500) ~= 4.5%) dominates the true
   agreement there. Restricting to the interior p in [0.05, 0.99], where
   sample size no longer starves the estimate, the worst error drops to
   2.50e-03.
2. Against rejection sampling (draw untruncated ``rng.lognormal(meanlog,
   sigma)``, discard >= max_value, mean of the rest): sample mean agrees to
   2.87e-03 relative, within Monte Carlo noise at n=5,000,000.
3. Against the closed-form conditional mean ``exp(meanlog + sigma**2/2) *
   Phi(b - sigma) / Phi(b)`` (b as above): sample mean agrees to 3.79e-04
   relative.

Per-component-vs-conditioned deviation (design's Bound rule, ``B-CAP-MIX``):
for ``LOGNORMAL_MIXTURE``, this module truncates EACH component at the
shared cap independently (the semantics ``FAIRDistribution.sample`` wires
up), retaining density ``sum(w_i f_i / F_i(M))``. The alternative --
conditioning the WHOLE mixture on ``X <= M`` -- would retain
``sum(w_i f_i) / sum(w_j F_j(M))`` instead, distorting component i's
effective weight by ``F_bar / F_i(M)`` where ``F_bar = sum(w_j F_j(M))``.
Given the D19 validator floor (``M`` > every component's p95, so
``F_i(M) > Phi(z95)`` for all i), the distortion between the two semantics
is bounded analytically in BOTH directions (``B-CAP-MIX``, suprema over
admissible configurations, approached but never attained): **+5.263%** up
(heavy components over-weighted, at ``F_i`` -> the floor with
``F_bar`` -> 1) and **-5.000%** down (light components under-weighted, at
``F_i`` -> 1 with ``F_bar`` at the floor). Per-component (this module's
choice) is "each regime capped at its own capacity", which is the design's
chosen semantics -- not an approximation of the conditioned alternative.

Draw-consumption note: exactly ONE ``rng.random(size)`` call per invocation
-- the same stream cost as ``rng.lognormal`` would have consumed via its
internal uniform draw, so switching a distribution from untruncated to
truncated at the same seed does not change how much of the shared stream
downstream fields consume (contrast with e.g. ``rng.choice``, which some
mixture code paths call BEFORE this).

Underflow note: ``ndtr(b)`` underflows to exactly ``0.0`` (not a small
positive float) for ``b <~ -38`` (float64 denormal floor). When that
happens every draw is silently exactly ``0.0`` -- finite, not NaN/inf, so
the engine's finite-output guard does not catch it. This occurs when
``max_value`` is far below ``exp(meanlog)`` (the cap sits many sigma below
the distribution's median); callers must keep ``max_value`` comfortably
above the median (see the D19 floor check, which additionally requires it
above the p95).

Saturation note (the mirror-image footgun, load-bearing for
``LOGNORMAL_MIXTURE`` per-component correctness): ``ndtr(b)`` saturates to
float-exactly ``1.0`` for ``b >~ 8.29``. At that point ``u * Phi(b) == u``
bit-for-bit, and an implementation that skips truncating a component
because "the cap can't bind way out there" is byte-identical to a correct
implementation -- until ``max_value`` is lowered. See
``fair_cam/tests/risk_engine/test_mixture_truncation_pin.py`` fixture 2's
D19-floor-to-saturation window, which pins the multi-component per-draw
truncation using this exact fact (``B-CAP-MIX``'s bounds are derived from
that floor).

Peak-allocation contract, measured with ``tracemalloc`` at the deployed
ceiling ``n = 10,000,000`` (numbers in this module's introducing commit
message; ``fair_cam/tests/risk_engine/test_truncation.py`` pins the
no-regression bound):

- Scalar branch (both ``meanlog`` and ``sigma`` are 0-d): exactly ONE
  size-``size`` float64 array (``u``) -- BIT-FOR-BIT parity with
  ``rng.lognormal``'s own peak (measured 1.00x baseline == 1.00x here).
- Array branch of THIS function (``truncated_lognormal`` called directly
  with pre-gathered arrays): adds one size-``size`` array (``b_arr``) on
  top of whatever the caller already holds -- ``b_arr`` is reduced in
  place to ``Phi(b)`` via ``ndtr(b_arr, out=b_arr)``, folded into ``u`` via
  ``u *= b_arr``, and released (``del b_arr``) before ``ndtri``/the affine
  step/``exp`` proceed, all in place on ``u``. Neither ``meanlog`` nor
  ``sigma`` is mutated here -- both are read-only (contrast: earlier drafts
  of this module mutated a caller's gathered array as scratch space; that
  contract was dropped because it is unnecessary and a footgun for future
  callers).
- ``truncated_lognormal_mixture_gather`` (what ``LOGNORMAL_MIXTURE``'s
  multi-component branch actually calls): stages the per-draw gathers so
  only ONE gathered size-``size`` array is alive alongside ``idx`` and
  ``u`` at any moment -- measured 3.00x the scalar baseline, BELOW the
  pre-PR2 uncapped mixture branch's own 4.00x (that branch's peak is
  ``idx`` + two full gathers + its output array).
"""

from __future__ import annotations

import math

import numpy as np
from scipy.special import ndtr, ndtri


def truncated_lognormal(
    rng: np.random.Generator,
    meanlog: float | np.ndarray,
    sigma: float | np.ndarray,
    size: int,
    max_value: float,
) -> np.ndarray:
    """Draw ``size`` samples from a lognormal(meanlog, sigma) truncated to
    support ``[0, max_value)`` via the inverse-CDF transform (module
    docstring has the formula, verification, and footguns).

    ``meanlog``/``sigma`` may each be a Python/numpy scalar OR a
    ``size``-length ndarray (the ``LOGNORMAL_MIXTURE`` engine branch passes
    per-draw gathered arrays, e.g. ``mean_arr[idx]``). Scalar and
    equivalent-array inputs (an array whose every element equals the
    scalar) produce BIT-IDENTICAL output at the same seed -- the same
    arithmetic is applied elementwise either way.

    Raises ``ValueError`` if ``max_value`` is non-finite or <= 0, or if any
    element of ``sigma`` is non-finite or <= 0 (silent-but-finite failure
    modes the design forbids: an invalid ``max_value``/``sigma`` must fail
    loud here rather than degrade to the ``ndtr`` underflow footgun above).
    """
    if not math.isfinite(max_value) or max_value <= 0:
        raise ValueError(
            f"truncated_lognormal: max_value must be finite and > 0, got {max_value!r}"
        )

    sigma_is_scalar = np.ndim(sigma) == 0
    if sigma_is_scalar:
        sigma_f = float(sigma)
        if not math.isfinite(sigma_f) or sigma_f <= 0:
            raise ValueError(f"truncated_lognormal: sigma must be finite and > 0, got {sigma!r}")
    else:
        sigma_check = np.asarray(sigma, dtype=float)
        if not np.all(np.isfinite(sigma_check)) or np.any(sigma_check <= 0):
            raise ValueError(
                f"truncated_lognormal: sigma must be finite and > 0 elementwise, "
                f"got array with min={np.min(sigma_check)!r}"
            )

    log_max = math.log(max_value)
    meanlog_is_scalar = np.ndim(meanlog) == 0

    if sigma_is_scalar and meanlog_is_scalar:
        # Scalar fast path (plain LOGNORMAL branch): the ONLY size-`size`
        # allocation is `u`. b is a plain float -- no array ever touches it.
        meanlog_f = float(meanlog)
        b = (log_max - meanlog_f) / sigma_f
        u = rng.random(size)
        u *= ndtr(b)
        ndtri(u, out=u)
        u *= sigma_f
        u += meanlog_f
        np.exp(u, out=u)
        return np.asarray(u)

    # Array path: `meanlog`/`sigma` are already-resolved per-draw arrays (or
    # a scalar broadcast against an array partner). `np.broadcast_to` on a
    # scalar side is a zero-copy VIEW (no size-`size` allocation); an
    # already-array side is used as-is (also zero-copy -- `np.asarray` on
    # an existing float64 ndarray returns it unchanged). This function adds
    # exactly ONE size-`size` allocation beyond whatever the caller already
    # holds (`b`), released immediately after Phi(b) is folded into `u`.
    # NOTE: `FAIRDistribution.sample`'s multi-component LOGNORMAL_MIXTURE
    # branch does NOT call this array path in production -- pre-gathering
    # both `mean_arr[idx]` and `sigma_arr[idx]` before calling in would
    # require both to be held alive for this function's whole body (needed
    # twice: once for `b`, once for the final affine step), landing at 4x
    # peak. It calls `truncated_lognormal_mixture_gather` below instead,
    # which stages the gathers to reach ~3x. This path exists for the
    # single-component mixture bypass (scalar meanlog/sigma -> the fast
    # path above, not this one) and for direct callers/tests that already
    # have full-size arrays in hand.
    meanlog_arr = (
        np.broadcast_to(meanlog, (size,)) if meanlog_is_scalar else np.asarray(meanlog, dtype=float)
    )
    sigma_arr = (
        np.broadcast_to(sigma, (size,)) if sigma_is_scalar else np.asarray(sigma, dtype=float)
    )

    b_arr = log_max - meanlog_arr
    b_arr /= sigma_arr
    ndtr(b_arr, out=b_arr)
    u = rng.random(size)
    u *= b_arr
    del b_arr
    ndtri(u, out=u)
    u *= sigma_arr
    u += meanlog_arr
    np.exp(u, out=u)
    return np.asarray(u)


def truncated_lognormal_mixture_gather(
    rng: np.random.Generator,
    mean_components: np.ndarray,
    sigma_components: np.ndarray,
    idx: np.ndarray,
    size: int,
    max_value: float,
) -> np.ndarray:
    """Multi-component ``LOGNORMAL_MIXTURE`` variant of ``truncated_lognormal``,
    specialised for the engine's per-draw component gather.

    Mathematically IDENTICAL to ``truncated_lognormal(rng,
    mean_components[idx], sigma_components[idx], size, max_value)`` --
    verified bit-for-bit equal, not just close: gathering (fancy indexing)
    is pure data movement and changes no float value that ``ndtr``/``ndtri``
    ever see, so ``ndtr(b_components)[idx] == ndtr(b_components[idx])``
    elementwise, and likewise for every other step. It is structured
    differently ONLY for peak allocation: ``mean_components``/
    ``sigma_components`` have only ``n_components`` distinct values (tiny,
    negligible cost) even though ``idx`` selects among them ``size`` times,
    so ``Phi(b)`` is computed ONCE at the tiny scale and gathered a single
    time, and the per-draw ``sigma``/``mean`` gathers needed for the final
    affine step are staged one at a time (computed, consumed, released)
    rather than held simultaneously. This keeps at most ONE gathered
    size-``size`` array alive alongside ``idx`` and ``u`` at any moment
    (measured peak ~3x a size-``size`` float64 array -- see this module's
    introducing commit message for the tracemalloc numbers), versus ~4x for
    calling the generic ``truncated_lognormal`` with both components
    pre-gathered.

    ``idx`` must be the array produced by the CALLER's ``rng.choice`` (this
    function does not call ``rng.choice`` itself) -- the "choice FIRST,
    then exactly one ``rng.random(size)``" stream-ordering contract is the
    caller's responsibility, pinned end-to-end by
    ``fair_cam/tests/risk_engine/test_mixture_truncation_pin.py`` Layer B.
    """
    if not math.isfinite(max_value) or max_value <= 0:
        raise ValueError(
            f"truncated_lognormal: max_value must be finite and > 0, got {max_value!r}"
        )
    sigma_components = np.asarray(sigma_components, dtype=float)
    mean_components = np.asarray(mean_components, dtype=float)
    if not np.all(np.isfinite(sigma_components)) or np.any(sigma_components <= 0):
        raise ValueError(
            f"truncated_lognormal: sigma must be finite and > 0 elementwise, "
            f"got array with min={np.min(sigma_components)!r}"
        )

    log_max = math.log(max_value)
    # Tiny (n_components-scale) work -- Phi(b) per COMPONENT, not per draw.
    b_components = log_max - mean_components
    b_components /= sigma_components
    phi_components = ndtr(b_components)

    phi_gathered = phi_components[idx]  # size-`size` gather #1 (of 3, staged)
    u = rng.random(size)
    u *= phi_gathered
    del phi_gathered
    ndtri(u, out=u)

    sigma_gathered = sigma_components[idx]  # size-`size` gather #2 (staged)
    u *= sigma_gathered
    del sigma_gathered

    mean_gathered = mean_components[idx]  # size-`size` gather #3 (staged)
    u += mean_gathered
    del mean_gathered

    np.exp(u, out=u)
    return np.asarray(u)
