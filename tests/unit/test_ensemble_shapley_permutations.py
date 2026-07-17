"""Ensemble per-draw Shapley eval-cost accounting + the opt-in permutation throttle.

The weight-robustness ensemble runs sampled Shapley once per weight draw. The real
speedup for many-control single scenarios is the cross-draw composition cache
(compose_groups is weight-invariant -> computed once per subset, reused every draw),
so the ensemble is fast at FULL Maleki precision and the default no longer cuts
permutations (Settings.weight_ensemble_shapley_permutations defaults to None = full).
The reduced-perm path remains as an OPT-IN emergency throttle. These tests pin the
eval-cost accounting that the throttle feeds into the K-degrade budget: the override
yields permutations*n in the sampled branch and is a no-op on the exact (<=12) branch.
"""

from __future__ import annotations

from idraa.services.run_executor import _scenario_eval_cost
from idraa.services.shapley import EXACT_MAX_N, maleki_sample_count


def test_exact_branch_unaffected_by_permutations() -> None:
    """n <= EXACT_MAX_N uses exact 2^n; sample_permutations is irrelevant there."""
    assert _scenario_eval_cost(EXACT_MAX_N) == 2**EXACT_MAX_N
    assert _scenario_eval_cost(EXACT_MAX_N, sample_permutations=256) == 2**EXACT_MAX_N


def test_sampled_branch_full_maleki_when_unset() -> None:
    """Above the exact threshold and with no override -> full Maleki count * n."""
    n = EXACT_MAX_N + 2
    assert _scenario_eval_cost(n) == maleki_sample_count(0.02, 0.05) * n


def test_sampled_branch_reduced_permutations() -> None:
    """The ensemble per-draw override yields permutations * n, far below Maleki."""
    n = EXACT_MAX_N + 2
    reduced = _scenario_eval_cost(n, sample_permutations=256)
    assert reduced == 256 * n
    assert reduced < _scenario_eval_cost(n)  # the speedup the ensemble relies on
