"""Worked examples + axioms for the domain-agnostic Shapley service.

The OR value function below mirrors the single-node FAIR-CAM OR-composition
(math deck Part VI): base reduction B, controls with effectiveness e_i,
v(S) = B * (1 - prod_i (1 - e_i)). Numbers re-derived by hand in the deck.
"""

import logging

import pytest

from idraa.services.shapley import shapley_values


def or_value_fn(base, eff):
    def v(s):
        prod = 1.0
        for i in s:
            prod *= 1.0 - eff[i]
        return base * (1.0 - prod)

    return v


def test_two_control_worked_example():
    v = or_value_fn(100.0, {"A": 0.4, "B": 0.5})
    phi = shapley_values(["A", "B"], v)
    assert phi["A"] == pytest.approx(30.0)
    assert phi["B"] == pytest.approx(40.0)
    assert phi["A"] + phi["B"] == pytest.approx(70.0)  # efficiency == v(N)


def test_three_control_worked_example():
    v = or_value_fn(100.0, {"A": 0.4, "B": 0.5, "C": 0.3})
    phi = shapley_values(["A", "B", "C"], v)
    assert phi["A"] == pytest.approx(26.0)
    assert phi["B"] == pytest.approx(34.5)
    assert phi["C"] == pytest.approx(18.5)
    assert sum(phi.values()) == pytest.approx(79.0)  # == v(A,B,C)


def test_efficiency_property_random():
    eff = {"A": 0.2, "B": 0.7, "C": 0.45, "D": 0.9}
    v = or_value_fn(250.0, eff)
    phi = shapley_values(list(eff), v)
    assert sum(phi.values()) == pytest.approx(v(frozenset(eff)))


def test_null_player_gets_zero():
    v = or_value_fn(100.0, {"A": 0.5, "B": 0.0})
    phi = shapley_values(["A", "B"], v)
    assert phi["B"] == pytest.approx(0.0)


def test_symmetry_equal_controls_equal_credit():
    v = or_value_fn(100.0, {"A": 0.5, "B": 0.5})
    phi = shapley_values(["A", "B"], v)
    assert phi["A"] == pytest.approx(phi["B"])


def test_sampling_matches_exact_and_stays_efficient():
    eff = {"A": 0.4, "B": 0.5, "C": 0.3}
    v = or_value_fn(100.0, eff)
    exact = shapley_values(list(eff), v)
    # force the sampling branch by setting exact_max_n below n
    sampled = shapley_values(list(eff), v, exact_max_n=1, sample_permutations=20000, seed=7)
    assert sum(sampled.values()) == pytest.approx(v(frozenset(eff)))  # efficiency exact
    for k in eff:
        assert sampled[k] == pytest.approx(exact[k], abs=1.5)  # within MC tolerance


def test_empty_and_singleton():
    assert shapley_values([], or_value_fn(100.0, {})) == {}
    v = or_value_fn(100.0, {"A": 0.4})
    assert shapley_values(["A"], v)["A"] == pytest.approx(40.0)


def test_non_negativity_property():
    """For a monotone OR value function every Shapley share is >= 0 (B-Meth-N2)."""
    eff = {"A": 0.6, "B": 0.6, "C": 0.6, "D": 0.6, "E": 0.1}
    phi = shapley_values(list(eff), or_value_fn(500.0, eff))
    for k, val in phi.items():
        assert val >= 0.0, f"{k} got negative share {val}"


def test_sampling_count_uses_maleki_bound_not_silent_cap(caplog):
    """The sampling path derives m from the Maleki bound, not a hidden constant."""
    from idraa.services.shapley import maleki_sample_count

    assert maleki_sample_count(rel_eps=0.02, delta=0.05) == pytest.approx(4612, abs=2)
    # default-path sampling (no explicit sample_permutations) stays efficient
    eff = {f"c{i}": 0.3 for i in range(14)}  # n=14 > EXACT_MAX_N=12 -> samples
    v = or_value_fn(1000.0, eff)
    with caplog.at_level(logging.INFO, logger="idraa.services.shapley"):
        phi = shapley_values(list(eff), v, seed=3)
    assert sum(phi.values()) == pytest.approx(v(frozenset(eff)))
    # B-Arch-I5: sampling log must fire and name the Maleki bound with m=4612
    maleki_records = [r for r in caplog.records if "Maleki bound" in r.message]
    assert maleki_records, "Expected a log record containing 'Maleki bound'"
    assert "m=4612" in maleki_records[0].message


def test_nonzero_empty_coalition_rejected():
    """v(empty) != 0 must raise ValueError, not silently produce wrong values."""

    def bad_value_fn(s):
        if not s:
            return 5.0  # v(∅) = 5 violates the contract
        prod = 1.0
        for _i in s:
            prod *= 0.6
        return 100.0 * (1.0 - prod)

    with pytest.raises(ValueError, match="v\\(empty\\)==0"):
        shapley_values(["A", "B"], bad_value_fn)
