"""Tests for ``services.loss_capacity.capacity_max_for_org`` (PR2 D13/D14).

D13: cap = k * annual_revenue. D14: no quantile-anchored fallback — when
revenue is missing or non-positive the minter returns ``None`` rather than
inventing a number. Deliberately NOT tested here: any ``> p95`` floor — the
minter has none by design (see the module docstring); that check lives at
the validator (Task 3b), not at the minter.
"""

from __future__ import annotations

from decimal import Decimal

from idraa.services.loss_capacity import capacity_max_for_org


def test_decimal_revenue_converted_before_multiplying() -> None:
    """``float_k * Decimal`` raises TypeError — the helper must float() first.

    Synthetic revenue only (this is a tracked, public repo): the assertion
    is value-independent, so any plausible number proves the TypeError is
    avoided and the arithmetic is correct.
    """
    result = capacity_max_for_org(Decimal("123450000"), 1.0)
    assert result == 123450000.0
    assert isinstance(result, float)


def test_decimal_revenue_scaled_by_k() -> None:
    result = capacity_max_for_org(Decimal("123450000"), 0.5)
    assert result == 61725000.0


def test_none_revenue_returns_none() -> None:
    assert capacity_max_for_org(None, 1.0) is None


def test_zero_decimal_revenue_returns_none() -> None:
    assert capacity_max_for_org(Decimal("0"), 1.0) is None


def test_negative_decimal_revenue_returns_none() -> None:
    assert capacity_max_for_org(Decimal("-1"), 1.0) is None


def test_negative_float_revenue_returns_none() -> None:
    assert capacity_max_for_org(-1.0, 1.0) is None


def test_plain_float_revenue_accepted() -> None:
    result = capacity_max_for_org(2_000_000.0, 1.5)
    assert result == 3_000_000.0
    assert isinstance(result, float)


def test_no_floor_applied_to_a_tiny_result() -> None:
    """D19's ``> p95`` floor does NOT live here (module docstring).

    A minter that silently bumped a too-small cap up to some floor would
    invent capacity the org does not have. Prove the minter returns the
    raw ``k * revenue`` product even when it is tiny — nothing clamps it
    up. (The floor itself is enforced elsewhere — Task 3b's validator —
    which has no presence in this module to assert against here.)
    """
    result = capacity_max_for_org(1.0, 1.0)
    assert result == 1.0
