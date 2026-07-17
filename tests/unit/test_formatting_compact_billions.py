"""Compact money gains a $B tier (shared web+PDF helper)."""

from idraa.formatting import money_format


def test_compact_billions_tier():
    assert money_format(1_500_000_000.0, "USD", compact=True) == "$1.50B"
    assert money_format(2_118_358_713.48, "USD", compact=True) == "$2.12B"


def test_compact_millions_unchanged():
    assert money_format(39_590_000.0, "USD", compact=True) == "$39.59M"
    assert money_format(2_610_000.0, "USD", compact=True) == "$2.61M"


def test_compact_billions_boundary():
    # Just under the B threshold stays in M.
    assert money_format(999_400_000.0, "USD", compact=True).endswith("M")
    assert money_format(1_000_000_000.0, "USD", compact=True).endswith("B")
