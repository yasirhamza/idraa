"""Offered reporting/entry currencies + CLDR-backed metadata (Babel).

Names, symbols, and per-currency decimal digits come from Babel/CLDR — we do
NOT hardcode them (a JPY-100x / KWD-1000x decimal bug is exactly what hardcoding
2 decimals causes). This module owns only the *set of selectable codes*; every
other currency fact is delegated to Babel. See the 2026-06-14 design doc.
"""

from __future__ import annotations

from babel import Locale

APP_LOCALE = "en_US"

SELECTABLE_CURRENCIES: frozenset[str] = frozenset(
    {
        "USD",
        "EUR",
        "GBP",
        "JPY",
        "CHF",
        "CNY",
        "INR",
        "AUD",
        "CAD",
        "SGD",
        "HKD",
        "SEK",
        "NOK",
        "DKK",
        "PLN",
        "TRY",
        "KRW",
        "BRL",
        "MXN",
        "ZAR",
        "SAR",
        "AED",
        "QAR",
        "KWD",
        "BHD",
        "OMR",
    }
)


def is_supported_code(code: str) -> bool:
    """True iff ``code`` is one we offer (exact, case-sensitive ISO 4217)."""
    return code in SELECTABLE_CURRENCIES


def currency_display_name(code: str, *, locale: str = APP_LOCALE) -> str:
    """Human currency name from CLDR, e.g. 'US Dollar' / 'Saudi Riyal'."""
    return str(Locale.parse(locale).currencies[code])
