"""MD-6 sanity-floor pinning tests for clean_quantile_pair.
Mirrors R clean_answers.R:30-52 cases."""

from __future__ import annotations

import pytest
from fair_cam.quantile_pooling import clean_quantile_pair


def test_tef_zero_low_floors_to_0_1() -> None:
    (low, high), event = clean_quantile_pair(0.0, 5.0, "tef")
    assert low == 0.1 and high == 5.0
    assert event is not None and "tef_zero_low_floor" in event.rule


def test_tef_zero_high_floors_to_1_0() -> None:
    (low, high), event = clean_quantile_pair(0.0, 0.0, "tef")
    assert (low, high) == (0.1, 1.0)
    assert event is not None


def test_vuln_below_0_05_clamped() -> None:
    (low, high), event = clean_quantile_pair(0.02, 0.5, "vuln")
    assert low == pytest.approx(0.05)
    assert event is not None and "vuln_low_floor_0.05" in event.rule


def test_vuln_above_0_95_clamped() -> None:
    (low, high), event = clean_quantile_pair(0.1, 0.99, "vuln")
    assert high == pytest.approx(0.95)
    assert event is not None


def test_pl_below_1000_floored() -> None:
    (low, high), event = clean_quantile_pair(500, 10000, "pl")
    assert (low, high) == (1000, 10000)
    assert event is not None and "pl_min_loss_floor_1000" in event.rule


def test_sl_below_1000_floored() -> None:
    (low, high), event = clean_quantile_pair(500, 800, "sl")
    assert (low, high) == (1000, 1000)


def test_no_clamp_returns_none_event() -> None:
    (low, high), event = clean_quantile_pair(10.0, 20.0, "tef")
    assert event is None
    assert (low, high) == (10.0, 20.0)


def test_invalid_fieldset_raises() -> None:
    with pytest.raises(ValueError, match="Unknown fieldset"):
        clean_quantile_pair(1, 2, "invalid")  # type: ignore[arg-type]


def test_vuln_high_eq_low_rebroadcast() -> None:
    """Meth-4 PR1: when high<low after individual clamps, rebroadcast must
    never permit final high > 0.95."""
    # low=0.98 (above 0.95), high=0.99 -> after low-floor doesn't fire, high
    # capped to 0.95, but now high<low. Rebroadcast must give low<=0.95.
    (low, high), event = clean_quantile_pair(0.98, 0.99, "vuln")
    assert high <= 0.95
    assert low <= high
    assert event is not None


def test_vuln_low_above_0_95_clamps_both_to_0_95() -> None:
    """Meth-1 T1 review: DEPARTURE FROM R. R's clean_answers.R:34 applies
    pmax(high, low) AFTER the 0.95 cap, so (low=0.98, high=0.99) would yield
    (0.98, 0.98) in R. Python deliberately clamps BOTH ends to 0.95 to
    preserve the [0.05, 0.95] vuln support invariant. This test pins the
    deliberate departure to prevent regression toward R's permissive
    rebroadcast semantics."""
    (low, high), event = clean_quantile_pair(0.98, 0.99, "vuln")
    assert (low, high) == (0.95, 0.95), (
        "Vuln must clamp to [0.05, 0.95]: even when input low > 0.95, "
        "final (low, high) must both equal 0.95 — NOT R's (0.98, 0.98) "
        "pmax-after-cap behavior."
    )
    assert event is not None
    assert "vuln_high_cap_0.95" in event.rule
    assert "vuln_high_eq_low_rebroadcast" in event.rule


def test_vuln_already_in_range_no_event() -> None:
    (low, high), event = clean_quantile_pair(0.2, 0.7, "vuln")
    assert (low, high) == (0.2, 0.7)
    assert event is None
