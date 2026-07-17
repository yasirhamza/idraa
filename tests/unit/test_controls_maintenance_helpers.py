"""Unit tests for services.controls_maintenance._is_zero_cost helper.

These are pure unit tests (no DB) for the Decimal-based zero-cost predicate
introduced in issue #66 (cost_model JSON -> annual_cost Decimal column).
They live in tests/unit/ rather than tests/services/ because they have no
session/fixture dependency — kept out of tests/services/ to avoid inheriting
the file-level ``pytestmark = pytest.mark.asyncio`` there (which would emit
PytestWarning on sync functions).
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from idraa.services.controls_maintenance import _is_zero_cost


def test_needs_cost_zero_decimal_triggers_alert() -> None:
    """Issue #66: _is_zero_cost collapses to annual_cost == Decimal('0')."""
    c = SimpleNamespace(annual_cost=Decimal("0"))
    assert _is_zero_cost(c) is True


def test_needs_cost_nonzero_decimal_does_not_trigger_alert() -> None:
    """Issue #66: nonzero Decimal annual_cost does NOT trigger the maintenance alert."""
    c = SimpleNamespace(annual_cost=Decimal("0.01"))
    assert _is_zero_cost(c) is False
