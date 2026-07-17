"""Shapley-value attribution (cooperative game theory) — v3 view-model derivation.

Domain-agnostic: given player ids and a value function ``v: frozenset[str] -> float``
with ``v(frozenset()) == 0``, return each player's Shapley value — the average
marginal contribution over all join orders. Used to attribute a scenario's modeled
risk reduction among its controls so the credits sum to the true total (efficiency
axiom) instead of double-counting overlap.

This module contains NO FAIR math; the value function is supplied by the caller
(the executor wires fair_cam's closed-form v(S) evaluator). Cooperative-game-theory
aggregation is a v3 derivation, not FAIR-grounded (CLAUDE.md "No portfolio-finance
overclaim"), so it lives here in v3's service layer, not in fair_cam.

Refs: Shapley (1953) — uniqueness under efficiency/symmetry/null/linearity;
Castro et al. (2009) / Maleki et al. (2013) — the permutation-sampling estimator.
Both exact and sampled paths are EXACTLY efficient (each join order telescopes to
v(N)); sampling only approximates the per-player split.
"""

from __future__ import annotations

import itertools
import logging
import math
import random
from collections.abc import Callable, Sequence

import numpy as np

logger = logging.getLogger(__name__)

# 2^12 = 4,096 cheap closed-form v(S) evals per scenario — bounded for the
# 2 GB / 1-vCPU VM at portfolio scale (B-Arch-I2/B-Sec-B1). Above this, sample.
EXACT_MAX_N = 12


def maleki_sample_count(rel_eps: float, delta: float) -> int:
    """Finite-sample permutation count (Maleki et al. 2013): m = ceil(ln(2/δ)·r²/(2ε²)).

    With ε = rel_eps·r the range r cancels, giving m = ceil(ln(2/δ)/(2·rel_eps²)).
    Defaults rel_eps=0.02, delta=0.05 -> m ~ 4,612.  Pr(|phi_hat - phi| >= rel_eps*r) <= delta.
    """
    return math.ceil(math.log(2.0 / delta) / (2.0 * rel_eps**2))


def shapley_values(
    players: Sequence[str],
    value_fn: Callable[[frozenset[str]], float],
    *,
    exact_max_n: int = EXACT_MAX_N,
    sample_permutations: int | None = None,
    rel_eps: float = 0.02,
    delta: float = 0.05,
    seed: int = 0,
) -> dict[str, float]:
    """Return ``{player_id: shapley_value}``.

    Contract: ``value_fn(frozenset()) == 0``. ``value_fn`` must be cheap
    (closed-form), not a Monte-Carlo run. Exact subset enumeration for
    ``len(players) <= exact_max_n``; permutation sampling above, where the
    sample count comes from the Maleki bound (NOT a silent fixed cap — B-Arch-I5/
    B-Sec-I2/B-Meth-I3) unless ``sample_permutations`` overrides it. The override
    is a first-class production path (the weight-robustness ensemble passes a
    reduced count for its per-draw Shapley — sound under common random numbers),
    as well as a test hook; do NOT treat it as test-only scaffolding.
    Both paths are exactly efficient (Σφ = v(N)); sampling only approximates the
    per-player split.
    """
    ids = list(players)
    n = len(ids)
    if n == 0:
        return {}
    v_empty = float(value_fn(frozenset()))
    if not math.isclose(v_empty, 0.0, abs_tol=1e-9):
        raise ValueError(f"value_fn must satisfy v(empty)==0 (got {v_empty!r})")
    if n == 1:
        return {ids[0]: float(value_fn(frozenset(ids)))}
    if n <= exact_max_n:
        return _shapley_exact(ids, value_fn)
    m = (
        sample_permutations
        if sample_permutations is not None
        else maleki_sample_count(rel_eps, delta)
    )
    logger.info(
        "shapley sampling: n=%d rel_eps=%.3g delta=%.3g m=%d (Maleki bound)",
        n,
        rel_eps,
        delta,
        m,
    )
    return _shapley_sampled(ids, value_fn, m, seed)


def _shapley_exact(ids: list[str], value_fn: Callable[[frozenset[str]], float]) -> dict[str, float]:
    n = len(ids)
    fact = math.factorial
    # weight for a coalition S of size k that excludes player i
    weight = [fact(k) * fact(n - k - 1) / fact(n) for k in range(n)]
    phi = {i: 0.0 for i in ids}  # noqa: C420 — dict.fromkeys gives None, not 0.0
    cache: dict[frozenset[str], float] = {}

    def v(s: frozenset[str]) -> float:
        if s not in cache:
            cache[s] = float(value_fn(s))
        return cache[s]

    for i in ids:
        others = [p for p in ids if p != i]
        for k in range(len(others) + 1):
            w = weight[k]
            for combo in itertools.combinations(others, k):
                s = frozenset(combo)
                phi[i] += w * (v(s | {i}) - v(s))
    return phi


def _shapley_sampled(
    ids: list[str],
    value_fn: Callable[[frozenset[str]], float],
    m: int,
    seed: int,
) -> dict[str, float]:
    rng = random.Random(seed)  # noqa: S311 — statistical sampling, not cryptography
    phi = {i: 0.0 for i in ids}  # noqa: C420 — dict.fromkeys gives None, not 0.0
    for _ in range(m):
        order = ids[:]
        rng.shuffle(order)
        pre: set[str] = set()
        v_pre = 0.0  # value_fn(frozenset()) == 0 by contract
        for p in order:
            v_with = float(value_fn(frozenset(pre | {p})))
            phi[p] += v_with - v_pre
            pre.add(p)
            v_pre = v_with
    return {i: phi[i] / m for i in ids}


# ---------------------------------------------------------------------------
# Batched (vector-valued) variant — weight-robustness ensemble (#419/#439).
#
# The ensemble evaluates the SAME game K times, differing only in the value
# function's parameters (kappa, node-mapping weights). Because the coalition
# walk is deterministic (exact enumeration, or permutation sampling from a
# fixed ``random.Random(seed)``), the K scalar walks visit identical coalitions
# in identical order — so ONE walk with a vector-valued ``v: frozenset ->
# ndarray(K,)`` computes all K games at once. Per draw k the accumulation
# ``phi[p][k] += v_with[k] - v_pre[k]`` performs the same float64 operations in
# the same order as the scalar walk for that draw, so the result is
# BIT-IDENTICAL per element (pinned by tests/services/test_shapley_batched.py).
# The scalar functions above remain the canonical single-game path (display
# attribution + LOO); this variant exists so the ensemble does not re-walk the
# combinatorics once per draw.
# ---------------------------------------------------------------------------


def shapley_values_batched(
    players: Sequence[str],
    value_fn: Callable[[frozenset[str]], np.ndarray],
    k_draws: int,
    *,
    exact_max_n: int = EXACT_MAX_N,
    sample_permutations: int | None = None,
    rel_eps: float = 0.02,
    delta: float = 0.05,
    seed: int = 0,
) -> dict[str, np.ndarray]:
    """Vector analogue of :func:`shapley_values`: ``{player_id: ndarray(K,)}``.

    ``value_fn(S)`` returns the K per-draw values of coalition ``S`` as a
    float64 ``(k_draws,)`` array. Contract mirrors the scalar fn:
    ``value_fn(frozenset())`` must be ~0 in EVERY draw — the same 1e-9 abs
    tolerance, but enforced PER DRAW (strictly stronger than the scalar fn's
    single-value check; a value fn violating the contract in any one draw
    fails loud here). Branch selection (n==0 / n==1 / exact / Maleki-sampled)
    and the sampled branch's rng consumption are identical to the scalar path,
    so ``shapley_values_batched(...)[cid][k]`` is bit-identical to
    ``shapley_values(...)`` run with the draw-k scalar value function.
    """
    ids = list(players)
    n = len(ids)
    if n == 0:
        return {}
    v_empty = value_fn(frozenset())
    if v_empty.shape != (k_draws,):
        raise ValueError(f"value_fn must return shape ({k_draws},); got {v_empty.shape}")
    if not np.all(np.abs(v_empty) <= 1e-9):  # same abs_tol as the scalar math.isclose
        raise ValueError(
            f"value_fn must satisfy v(empty)==0 in every draw "
            f"(max |v|={float(np.max(np.abs(v_empty)))!r})"
        )
    if n == 1:
        # .copy(): the value fn may serve a cached array (the ensemble's
        # per-coalition value cache) — never hand a caller an alias into it.
        return {ids[0]: value_fn(frozenset(ids)).copy()}
    if n <= exact_max_n:
        return _shapley_exact_batched(ids, value_fn, k_draws)
    m = (
        sample_permutations
        if sample_permutations is not None
        else maleki_sample_count(rel_eps, delta)
    )
    logger.info(
        "shapley sampling (batched over %d draws): n=%d rel_eps=%.3g delta=%.3g m=%d",
        k_draws,
        n,
        rel_eps,
        delta,
        m,
    )
    return _shapley_sampled_batched(ids, value_fn, k_draws, m, seed)


def _shapley_exact_batched(
    ids: list[str], value_fn: Callable[[frozenset[str]], np.ndarray], k_draws: int
) -> dict[str, np.ndarray]:
    """Vector mirror of :func:`_shapley_exact` — same enumeration order, same
    memo cache; ``w * (v(S|i) - v(S))`` runs per element in float64."""
    n = len(ids)
    fact = math.factorial
    weight = [fact(k) * fact(n - k - 1) / fact(n) for k in range(n)]
    phi = {i: np.zeros(k_draws) for i in ids}
    cache: dict[frozenset[str], np.ndarray] = {}

    def v(s: frozenset[str]) -> np.ndarray:
        got = cache.get(s)
        if got is None:
            got = value_fn(s)
            cache[s] = got
        return got

    for i in ids:
        others = [p for p in ids if p != i]
        for k in range(len(others) + 1):
            w = weight[k]
            for combo in itertools.combinations(others, k):
                s = frozenset(combo)
                phi[i] = phi[i] + w * (v(s | {i}) - v(s))
    return phi


def _shapley_sampled_batched(
    ids: list[str],
    value_fn: Callable[[frozenset[str]], np.ndarray],
    k_draws: int,
    m: int,
    seed: int,
) -> dict[str, np.ndarray]:
    """Vector mirror of :func:`_shapley_sampled` — the SAME ``random.Random
    (seed)`` permutation stream (so the coalition walk matches the scalar path
    exactly); the prefix telescoping runs per element."""
    rng = random.Random(seed)  # noqa: S311 — statistical sampling, not cryptography
    phi = {i: np.zeros(k_draws) for i in ids}
    for _ in range(m):
        order = ids[:]
        rng.shuffle(order)
        pre: set[str] = set()
        v_pre: np.ndarray = np.zeros(k_draws)  # value_fn(frozenset()) == 0 by contract
        for p in order:
            v_with = value_fn(frozenset(pre | {p}))
            phi[p] = phi[p] + (v_with - v_pre)
            pre.add(p)
            v_pre = v_with
    return {i: phi[i] / m for i in ids}
