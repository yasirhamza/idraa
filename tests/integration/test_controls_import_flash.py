"""Tests for the post-import flash formatter (issue #87, #133).

The integration test that exercised the retired one-click
``POST /controls/import/library`` route (P2b Task 10) was removed when that
flat library import was retired in favour of browse+adopt. The
``_format_import_flash`` unit tests below remain — the formatter is still used
by the surviving arbitrary-CSV import (``POST /controls/import``).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


# ---- Issue #133: zero-count clause suppression in the flash formatter ----


async def test_format_import_flash_all_zero_drops_both_clauses() -> None:
    """Both counts zero → base message only, no maintenance clauses."""
    from idraa.routes.controls import _format_import_flash

    msg = _format_import_flash(imported=61, skipped=0, zero_cost_count=0, unconfirmed_count=0)
    assert msg == "Imported 61 controls (61 created, 0 skipped)."
    assert "need annual cost set" not in msg
    assert "need confirmation" not in msg


async def test_format_import_flash_only_zero_cost_zero_drops_only_that_clause() -> None:
    """zero_cost == 0 but unconfirmed > 0 → only the unconfirmed clause."""
    from idraa.routes.controls import _format_import_flash

    msg = _format_import_flash(imported=61, skipped=0, zero_cost_count=0, unconfirmed_count=12)
    assert "need annual cost set" not in msg
    assert "12 assignments need confirmation" in msg


async def test_format_import_flash_only_unconfirmed_zero_drops_only_that_clause() -> None:
    """unconfirmed == 0 but zero_cost > 0 → only the zero-cost clause."""
    from idraa.routes.controls import _format_import_flash

    msg = _format_import_flash(imported=61, skipped=0, zero_cost_count=5, unconfirmed_count=0)
    assert "5 controls need annual cost set" in msg
    assert "need confirmation" not in msg


async def test_format_import_flash_both_nonzero_joins_with_and() -> None:
    """Both counts > 0 → both clauses joined with ' and '."""
    from idraa.routes.controls import _format_import_flash

    msg = _format_import_flash(imported=61, skipped=0, zero_cost_count=5, unconfirmed_count=12)
    assert "5 controls need annual cost set" in msg
    assert "12 assignments need confirmation" in msg
    assert " and " in msg
