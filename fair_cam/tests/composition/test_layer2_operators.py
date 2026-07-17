"""Layer 2 - Boolean composition operators.

Spec §3.2.3:
- OR-style 1 - product(1 - x_i) for OR groups + within-sub-function across controls
- AND product(x_i) for AND groups
- weak-AND weighted arithmetic mean with equal weights for weak-AND groups

Backtest Scenario B (spec §9.2): closed-form references on canonical input
(0.9, 0.5, 0.0).
"""

import pytest
from fair_cam.composition import (
    and_compose,
    or_compose,
    weak_and_compose,
)


def test_or_compose_two_inputs():
    """1 - (1-0.6)(1-0.7) = 1 - 0.4 * 0.3 = 0.88"""
    assert or_compose([0.6, 0.7]) == pytest.approx(0.88, abs=1e-9)


def test_or_compose_with_zero():
    """OR with 0 is identity for the others: 1 - 1 * (1-0.7) = 0.7"""
    assert or_compose([0.0, 0.7]) == pytest.approx(0.7, abs=1e-9)


def test_or_compose_with_one():
    """OR with 1 saturates to 1."""
    assert or_compose([0.5, 1.0, 0.3]) == pytest.approx(1.0, abs=1e-9)


def test_or_compose_empty_returns_zero():
    """Empty operand list -> 0 (no contribution)."""
    assert or_compose([]) == 0.0


def test_and_compose_basic():
    """product(0.7, 0.8, 0.8) = 0.448"""
    assert and_compose([0.7, 0.8, 0.8]) == pytest.approx(0.448, abs=1e-9)


def test_and_compose_with_zero_collapses():
    """AND with 0 -> 0 (boundary correctness)."""
    assert and_compose([0.9, 0.5, 0.0]) == 0.0


def test_and_compose_empty_returns_one():
    """Empty operand list -> 1 (multiplicative identity); caller treats as
    'no constraint applied' for the group."""
    assert and_compose([]) == 1.0


def test_weak_and_compose_equal_weights_default():
    """Equal-weights weighted mean: (0.9 + 0.5 + 0.0) / 3 = 0.4666..."""
    assert weak_and_compose([0.9, 0.5, 0.0]) == pytest.approx(1.4 / 3, abs=1e-9)


def test_weak_and_compose_explicit_equal_weights():
    """Equivalent to default."""
    assert weak_and_compose([0.9, 0.5, 0.0], weights=[1 / 3, 1 / 3, 1 / 3]) == pytest.approx(
        1.4 / 3, abs=1e-9
    )


def test_weak_and_compose_zero_does_not_collapse():
    """Standard §3.3 weak-AND boundary: 0 in one element still gives partial output.
    This is the property that disqualifies Hamacher t-norm and weighted complement-product
    (audit §8.3 errors flagged in spec §8.1)."""
    result = weak_and_compose([0.0, 0.8, 0.5])
    assert result > 0
    assert result == pytest.approx((0.0 + 0.8 + 0.5) / 3, abs=1e-9)


def test_weak_and_compose_empty_returns_none():
    """Empty operand list -> None (per §3.2.4 'all operands time-unit excluded' rule).
    Distinguishes 'uncomputable yet' from '0 effectiveness'."""
    assert weak_and_compose([]) is None


def test_weak_and_compose_weights_must_sum_to_one():
    with pytest.raises(ValueError, match="weights must sum to 1"):
        weak_and_compose([0.5, 0.5], weights=[0.7, 0.7])


def test_weak_and_compose_weights_length_must_match():
    with pytest.raises(ValueError, match="weights length"):
        weak_and_compose([0.5, 0.5], weights=[1.0])


def test_weak_and_compose_all_zero_weights_rejected():
    """Paranoid-review fix C6: zero-weight tuple must raise (sum != 1.0).
    Without this guard, [0,0,0] would silently produce 0 regardless of inputs -
    indistinguishable from genuine zero-effectiveness."""
    with pytest.raises(ValueError, match="weights must sum to 1"):
        weak_and_compose([0.5, 0.5, 0.5], weights=[0.0, 0.0, 0.0])


def test_backtest_scenario_b_or_canonical():
    """Spec §9.2 Scenario B - OR-style on (0.9, 0.5, 0.0) -> 0.95."""
    assert or_compose([0.9, 0.5, 0.0]) == pytest.approx(0.95, abs=1e-9)


def test_backtest_scenario_b_and_canonical():
    """Spec §9.2 Scenario B - AND-product on (0.9, 0.5, 0.0) -> 0.0."""
    assert and_compose([0.9, 0.5, 0.0]) == 0.0


def test_backtest_scenario_b_weak_and_canonical():
    """Spec §9.2 Scenario B - weak-AND mean (equal weights) on (0.9, 0.5, 0.0) -> 0.4666..."""
    assert weak_and_compose([0.9, 0.5, 0.0]) == pytest.approx(1.4 / 3, abs=1e-9)
