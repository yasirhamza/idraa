"""Equivalence: shapley_values_batched(...)[cid][k] must be BIT-IDENTICAL to
shapley_values(...) run with the draw-k scalar value function, for every k.

This is the correctness contract that lets the weight-robustness ensemble walk
the Shapley combinatorics ONCE with vector values instead of once per draw
(K=256). Covers the exact branch (n <= EXACT_MAX_N), the sampled branch
(n > EXACT_MAX_N, both the Maleki default and a sample_permutations override),
the n==1 shortcut, n==0, and the v(empty)!=0 guard.
"""

from __future__ import annotations

import numpy as np
import pytest

from idraa.services.shapley import (
    EXACT_MAX_N,
    shapley_values,
    shapley_values_batched,
)


def _random_game(k_draws: int) -> dict[frozenset[str], np.ndarray]:
    """Seed table for a random K-draw game: v(empty)=0 pinned; every non-empty
    coalition gets a lazily-generated random positive vector in `_make_fns`'
    ``vector_fn`` (values are per-coalition, not additive, so overlap/synergy
    is present and the marginal telescoping is genuinely exercised)."""
    return {frozenset(): np.zeros(k_draws)}


def _make_fns(table: dict[frozenset[str], np.ndarray], k_draws: int, rng: np.random.Generator):
    """(vector_fn, scalar_fn_for_draw) over a lazily-populated random table."""

    def vector_fn(s: frozenset[str]) -> np.ndarray:
        got = table.get(s)
        if got is None:
            got = np.asarray(rng.uniform(0.0, 1000.0, size=k_draws))
            table[s] = got
        return got

    def scalar_fn_for(k: int):
        def f(s: frozenset[str]) -> float:
            # the scalar path must see the SAME per-draw value the vector path
            # serves — mirror the production wiring (float(vec[k]))
            return float(vector_fn(s)[k])

        return f

    return vector_fn, scalar_fn_for


@pytest.mark.parametrize("n", [2, 3, 5, EXACT_MAX_N])
def test_exact_branch_bit_identical(n: int) -> None:
    rng = np.random.default_rng(n)
    ids = [f"c{j}" for j in range(n)]
    k_draws = 17
    table = _random_game(k_draws)
    vector_fn, scalar_fn_for = _make_fns(table, k_draws, rng)

    batched = shapley_values_batched(ids, vector_fn, k_draws)
    assert set(batched) == set(ids)
    for k in range(k_draws):
        scalar = shapley_values(ids, scalar_fn_for(k))
        for cid in ids:
            # bit-identical, not approx: same float64 ops in the same order
            assert batched[cid][k] == scalar[cid], (cid, k)


@pytest.mark.parametrize("m_override", [None, 40])
def test_sampled_branch_bit_identical(m_override: int | None) -> None:
    """n > EXACT_MAX_N -> permutation sampling. With the None (Maleki) default
    this is the production ensemble shape (fixed seed=0 walk); keep k_draws
    small so the full-Maleki case stays fast."""
    n = EXACT_MAX_N + 2
    rng = np.random.default_rng(99)
    ids = [f"c{j}" for j in range(n)]
    k_draws = 3 if m_override is None else 11
    table = _random_game(k_draws)
    vector_fn, scalar_fn_for = _make_fns(table, k_draws, rng)

    batched = shapley_values_batched(ids, vector_fn, k_draws, sample_permutations=m_override)
    for k in range(k_draws):
        scalar = shapley_values(ids, scalar_fn_for(k), sample_permutations=m_override)
        for cid in ids:
            assert batched[cid][k] == scalar[cid], (cid, k)


def test_n1_shortcut_and_n0() -> None:
    rng = np.random.default_rng(1)
    k_draws = 5
    vec = np.asarray(rng.uniform(0.0, 100.0, size=k_draws))

    def vfn(s: frozenset[str]) -> np.ndarray:
        return vec if s else np.zeros(k_draws)

    out = shapley_values_batched(["only"], vfn, k_draws)
    np.testing.assert_array_equal(out["only"], vec)
    assert shapley_values_batched([], vfn, k_draws) == {}


def test_v_empty_guard() -> None:
    k_draws = 4

    def bad(s: frozenset[str]) -> np.ndarray:
        return np.full(k_draws, 0.5)  # v(empty) != 0 in every draw

    with pytest.raises(ValueError, match="v\\(empty\\)==0"):
        shapley_values_batched(["a", "b"], bad, k_draws)


def test_shape_guard() -> None:
    def wrong_shape(s: frozenset[str]) -> np.ndarray:
        return np.zeros(7)

    with pytest.raises(ValueError, match="shape"):
        shapley_values_batched(["a", "b"], wrong_shape, 4)


def test_efficiency_axiom_batched() -> None:
    """Σφ == v(N) per draw (telescoping) — the axiom both paths guarantee."""
    rng = np.random.default_rng(7)
    ids = [f"c{j}" for j in range(EXACT_MAX_N + 2)]  # sampled branch
    k_draws = 6
    table = _random_game(k_draws)
    vector_fn, _ = _make_fns(table, k_draws, rng)
    batched = shapley_values_batched(ids, vector_fn, k_draws, sample_permutations=25)
    total = np.zeros(k_draws)
    for cid in ids:
        total = total + batched[cid]
    np.testing.assert_allclose(total, vector_fn(frozenset(ids)), rtol=1e-12)


def test_executor_skeleton_equivalence_scalar_vs_batched() -> None:
    """SWE-review I1: the executor-level skip skeleton is DUPLICATED between
    `_compute_shapley_by_scenario` (scalar) and `_compute_shapley_by_scenario_
    batched` — this fast-suite test pins their equivalence (values AND the
    skipped list) so skeleton drift is caught in routine CI, not only by the
    heavy end-to-end ensemble golden gate.

    Exercises every skip path in one pass: a normal scenario, an empty-cids
    scenario ({} in out), an over_cap scenario, and an over_budget scenario
    (budget sized so the normal scenario consumes it first). The scalar side
    uses the production `_make_subset_value_fn` value machinery; the batched
    side wraps THE SAME scalar fn replicated across K draws, so per draw k the
    batched output must equal the scalar output bit-for-bit.
    """
    from fair_cam.models.composition_topology import KAPPA_META_RELIABILITY
    from fair_cam.risk_engine.fair_core import (
        DistributionType,
        FAIRDistribution,
        FAIRParameters,
    )
    from fair_cam.risk_engine.native_control_aware import NativeControlAwareRiskCalculator
    from fair_cam.tests.risk_engine._helpers import make_control

    from idraa.services.run_executor import (
        _compute_shapley_by_scenario,
        _compute_shapley_by_scenario_batched,
        _make_subset_value_fn,
    )

    controls = [
        make_control(
            control_id=f"ctl{j}",
            assignments=[("lec_prev_resistance", "probability", 0.3 + 0.2 * j)],
        )
        for j in range(3)
    ]
    universe = [c.control_id for c in controls]
    calculator = NativeControlAwareRiskCalculator(controls=controls, n_simulations=100)

    def _params() -> FAIRParameters:
        return FAIRParameters(
            threat_event_frequency=FAIRDistribution(
                DistributionType.PERT, {"low": 1.0, "mode": 4.0, "high": 9.0}
            ),
            vulnerability=FAIRDistribution(
                DistributionType.PERT, {"low": 0.05, "mode": 0.2, "high": 0.4}
            ),
            primary_loss=FAIRDistribution(DistributionType.LOGNORMAL, {"mean": 11.0, "sigma": 0.7}),
            secondary_loss=FAIRDistribution(
                DistributionType.LOGNORMAL, {"mean": 10.0, "sigma": 0.9}
            ),
        )

    per_scenario_inputs = [
        ("s_ok", "normal", _params()),
        ("s_empty", "no controls", _params()),
        ("s_cap", "over cap", _params()),
        ("s_budget", "over budget", _params()),
    ]
    per_scenario_dict = {
        "s_ok": universe[:2],  # cost 2^2 = 4
        "s_empty": [],
        "s_cap": universe,  # 3 > max_controls=2 -> over_cap
        "s_budget": universe[1:],  # cost 4, but budget exhausted by s_ok
    }
    max_controls = 2
    total_eval_budget = 5  # s_ok spends 4; s_budget's +4 exceeds -> over_budget

    scalar_out, scalar_skipped = _compute_shapley_by_scenario(
        calculator,
        per_scenario_inputs,
        per_scenario_dict,
        universe,
        max_controls=max_controls,
        total_eval_budget=total_eval_budget,
    )

    # Batched side: wrap the SAME production value machinery, replicated per draw.
    k_draws = 3
    scalar_value_fn = _make_subset_value_fn(
        calculator.control_registry,
        {},
        {},
        None,
        KAPPA_META_RELIABILITY,
        None,
        statistic="typical",
    )

    def vec_fn(subset: frozenset[str], sid: str, rp: FAIRParameters) -> np.ndarray:
        return np.full(k_draws, scalar_value_fn(subset, sid, rp))

    batched_out, batched_skipped = _compute_shapley_by_scenario_batched(
        per_scenario_inputs,
        per_scenario_dict,
        universe,
        vec_fn,
        k_draws,
        max_controls=max_controls,
        total_eval_budget=total_eval_budget,
    )

    # Skip skeleton: identical skip SET and reasons, identical out keys.
    assert batched_skipped == scalar_skipped
    assert set(scalar_skipped) == {("s_cap", "over_cap"), ("s_budget", "over_budget")}
    assert set(batched_out) == set(scalar_out) == {"s_ok", "s_empty"}
    assert batched_out["s_empty"] == scalar_out["s_empty"] == {}

    # Values: per draw k, batched slices equal the scalar pass bit-for-bit.
    for cid, vec in batched_out["s_ok"].items():
        for k in range(k_draws):
            assert vec[k] == scalar_out["s_ok"][cid], (cid, k)
