from __future__ import annotations

from idraa.currency import (
    SELECTABLE_CURRENCIES,
    currency_display_name,
    is_supported_code,
)


def test_selectable_set_includes_majors_and_all_gcc() -> None:
    for code in ("USD", "EUR", "GBP", "JPY", "CHF", "CNY"):
        assert code in SELECTABLE_CURRENCIES, code
    for code in ("SAR", "AED", "QAR", "KWD", "BHD", "OMR"):
        assert code in SELECTABLE_CURRENCIES, code


def test_usd_is_always_supported() -> None:
    assert is_supported_code("USD") is True


def test_unsupported_and_malformed_codes_rejected() -> None:
    assert is_supported_code("ZZZ") is False
    assert is_supported_code("us") is False
    assert is_supported_code("<b>") is False
    assert is_supported_code("") is False


def test_display_name_via_babel() -> None:
    assert currency_display_name("USD") == "US Dollar"
    assert currency_display_name("SAR") == "Saudi Riyal"
