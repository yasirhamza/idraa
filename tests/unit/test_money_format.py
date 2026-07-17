from __future__ import annotations

import math

import pytest

from idraa.formatting import money_format


def test_full_form_uses_per_currency_decimals() -> None:
    # CLDR decimals: USD=2, JPY=0, KWD=3. Hardcoding 2 would be a real bug
    # (a JPY 100x overstate / a KWD 1000x understate).
    # Values are chosen to avoid binary-float tie ambiguity: 1099.984 is below
    # the .985 boundary, 1099.986 above — no half-even tie-break dependency.
    assert money_format(1099.50, "USD") == "$1,099.50"
    assert money_format(1099.984, "USD") == "$1,099.98"
    assert money_format(1099.986, "USD") == "$1,099.99"
    assert money_format(1100.0, "JPY") == "¥1,100"  # 0 decimals
    assert money_format(1099.985, "KWD") == "KWD1,099.985"  # 3 decimals (exact)


def test_full_form_gcc_renders_latin_code() -> None:
    assert money_format(2_000_000, "SAR") == "SAR2,000,000.00"


def test_compact_form_matches_abbreviation_with_symbol() -> None:
    assert money_format(2_100_000, "USD", compact=True) == "$2.10M"
    assert money_format(860_000, "EUR", compact=True) == "€860k"
    assert money_format(2_100_000, "JPY", compact=True) == "¥2.10M"


def test_compact_million_scale_always_keeps_magnitude_suffix() -> None:
    """P6 (2026-07-03) regression: a million-scale compact amount must NEVER lose
    its 'M' suffix and render as a bare '$237'.

    The PDF distribution-stats delta column surfaced a VaR-99 delta of ~$237.01M
    rendering as '$237' in the 2026-07-03 report review. Root cause was suspected
    to be a round-integer edge in the compact formatter. The formatter is in fact
    correct — the ".00-drop" path drops only the DECIMALS, never the magnitude
    suffix — so this test pins that invariant so a future refactor can't regress
    it. The clean-integer case ('$237M') and the two-decimal case ('$237.01M')
    are BOTH acceptable per the review; the bare-'$237' (no suffix) form is not.
    """
    # 237.005e6 rounds to a clean integer at 2dp → "$237M" (decimals dropped, M kept).
    assert money_format(237_005_000, "USD", compact=True) == "$237M"
    # 237.010e6 keeps the cents → "$237.01M".
    assert money_format(237_010_000, "USD", compact=True) == "$237.01M"
    # Invariant: every million-scale value ends with the magnitude suffix, never
    # a bare integer. Sweep a range spanning clean-integer and fractional millions.
    for v in (237_000_000, 237_004_000, 237_005_000, 237_010_000):
        out = money_format(v, "USD", compact=True)
        assert out.endswith("M"), f"{v} → {out!r} dropped the 'M' suffix"
        assert out != "$237", "bare '$237' (no suffix) must never be produced"
    # The invariant holds ACROSS the $B tier boundary: 999.5M+ rounds into the
    # billions tier (added for the design-system PDF alignment), so 999,999,999
    # renders "$1B" — still a magnitude suffix, never a bare integer.
    assert money_format(999_999_999, "USD", compact=True).endswith("B")


def test_none_and_nonfinite_collapse_to_dash() -> None:
    assert money_format(None, "USD") == "—"
    assert money_format(math.nan, "USD") == "—"
    assert money_format(math.inf, "EUR", compact=True) == "—"


def test_unsupported_code_raises() -> None:
    with pytest.raises(KeyError):
        money_format(100, "ZZZ")
