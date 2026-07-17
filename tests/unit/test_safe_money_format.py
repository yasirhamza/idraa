from __future__ import annotations

from idraa.formatting import safe_money_format


def test_supported_code_formats_normally() -> None:
    assert safe_money_format(2_100_000, "EUR", compact=True) == "€2.10M"


def test_unsupported_alpha_code_falls_back_to_literal() -> None:
    # Stale/corrupt but well-formed code must NOT raise — fall back to "<CODE> <amount>".
    assert safe_money_format(1_000_000, "ZZZ") == "ZZZ 1,000,000"


def test_markup_code_sanitized_no_injection() -> None:
    # SECURITY (re-establish the retired currency_symbol ^[A-Z]{3}$ guard): a
    # malformed/markup code must NEVER reach the literal — it feeds reportlab
    # Paragraph markup downstream. Non-[A-Z]{3} collapses to "?".
    out = safe_money_format(1_000_000, "<i>")
    assert "<" not in out and ">" not in out
    assert out == "? 1,000,000"


def test_none_nonfinite_dash() -> None:
    import math

    assert safe_money_format(None, "ZZZ") == "—"
    assert safe_money_format(math.inf, "ZZZ") == "—"
