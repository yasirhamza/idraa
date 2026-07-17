"""Shared formatting helpers used across web templates AND PDF reports.

UAT 2026-05-21: the LEC/EPC charts in the executive PDF report
(``services/pdf_report.py``) rendered axis labels with the raw
``f"${v:,.0f}"`` format, producing strings like ``$31,622,777`` that
overlapped each other on the log-x axis.  P3 (Task 8) retired the hand-rolled
``currency_symbol``/``abbreviate_money`` pair in favor of Babel-backed
``money_format``/``safe_money_format`` so all currency rendering goes through
a single CLDR-accurate path.  ``money_format`` and ``safe_money_format`` are
the canonical money-formatting API for this module.
"""

from __future__ import annotations

import datetime
import math
import re
from urllib.parse import urlsplit

from babel.numbers import format_currency, get_currency_symbol
from markupsafe import Markup, escape

from idraa.currency import APP_LOCALE, is_supported_code

_HTTPS_URL_RE = re.compile(r"https://[^\s<>\"'\)\]]+")
_TRAILING_PUNCT = ".,;:!?"


def linkify_https(text: str) -> Markup:
    """Render *text* escaped, with https:// URLs as clickable links.

    Sec-I1 gate (issue #349): every candidate href passes an explicit
    https-only scheme allowlist (scheme == "https" and non-empty netloc)
    before any href use — the regex match alone is NOT trusted. Anything
    else (javascript:, data:, http:, malformed) renders as inert escaped
    text. Jinja autoescape covers text nodes but not href values; this
    helper is the single gate for citation-derived hrefs.

    Applied in ``templates/library/entry_detail.html`` citations block.
    Regression tests: ``tests/unit/test_formatting_linkify.py`` +
    ``tests/integration/test_library_routes.py``.
    """
    parts: list[str] = []
    last = 0
    for m in _HTTPS_URL_RE.finditer(text):
        url = m.group(0).rstrip(_TRAILING_PUNCT)
        end = m.start() + len(url)
        split = urlsplit(url)
        parts.append(str(escape(text[last : m.start()])))
        if split.scheme == "https" and split.netloc:
            parts.append(
                f'<a href="{escape(url)}" class="link" target="_blank" '
                f'rel="noopener noreferrer">{escape(url)}</a>'
            )
        else:
            parts.append(str(escape(url)))
        last = end
    parts.append(str(escape(text[last:])))
    return Markup("".join(parts))  # noqa: S704 — all text nodes escaped via markupsafe.escape above; Markup wraps pre-escaped parts only


def utc_isoformat(value: datetime.datetime | None) -> str:
    """Render a datetime as a UTC-aware ISO-8601 string for CSV exports.

    Issue #266 (D3): CSV exports call ``.isoformat()`` directly on
    ``TimestampMixin`` columns, which use raw ``DateTime(timezone=True)``
    rather than the ``UtcDateTime`` TypeDecorator. On SQLite those columns
    read back NAIVE, so a bare ``.isoformat()`` omits the ``+00:00`` offset
    and exported timestamps lose their timezone. This export-side helper
    normalises to UTC-aware before formatting so the offset always renders,
    regardless of dialect.

    None collapses to ``""`` (matching the existing ``... if x else ""``
    guards at the call sites). A naive value is treated as UTC (the
    application stores all timestamps as UTC via ``now_utc``); an aware
    value is converted to UTC.

    >>> utc_isoformat(None)
    ''
    >>> utc_isoformat(datetime.datetime(2026, 5, 29, 12, 0, 0))
    '2026-05-29T12:00:00+00:00'
    """
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.UTC)
    else:
        value = value.astimezone(datetime.UTC)
    return value.isoformat()


def money_format(
    value: float | int | None,
    code: str = "USD",
    *,
    compact: bool = False,
    locale: str = APP_LOCALE,
) -> str:
    """Locale-aware currency formatting via Babel/CLDR.

    Full form (default) delegates to ``babel.numbers.format_currency`` with
    ``currency_digits=True`` so per-currency minor units are correct (USD=2,
    JPY=0, KWD=3) — never hardcode 2 decimals.

    Compact form keeps our M/k abbreviation (Babel has no portable compact-
    currency) but takes the symbol from Babel's CLDR data: ``$2.10M``, ``€860k``.

    None / NaN / ±inf collapse to ``—`` so corrupted data cannot crash a render.
    An unsupported ``code`` (not in the offered set) raises ``KeyError`` —
    formatting validates offered-set membership via ``is_supported_code``; the
    rated-currency gate (``is_selectable_currency``) lives at the write paths.
    The render boundary (later phase) catches this KeyError and falls back to the
    literal code rather than 500-ing a report.
    """
    if value is None or not math.isfinite(value):
        return "—"
    if not is_supported_code(code):
        raise KeyError(code)
    if compact:
        symbol = get_currency_symbol(code, locale=locale)
        sign = "-" if value < 0 else ""
        n = abs(value)
        if n >= 999_500_000:
            return _compact(sign, n / 1_000_000_000, "B", symbol)
        if n >= 999_500:
            return _compact(sign, n / 1_000_000, "M", symbol)
        if n >= 1_000:
            return _compact(sign, n / 1_000, "k", symbol)
        return f"{sign}{symbol}{round(n)}"
    return str(format_currency(value, code, locale=locale, currency_digits=True))


def safe_money_format(
    value: float | int | None,
    code: str = "USD",
    *,
    compact: bool = False,
    locale: str = APP_LOCALE,
) -> str:
    """Render-boundary wrapper: a stale/unsupported currency code must never
    500 a report. Falls back to "<CODE> <amount>" instead of raising.

    SECURITY: the fallback string is interpolated into reportlab Paragraph markup
    downstream (pdf_report.py) which has NO autoescape. The retired
    ``currency_symbol`` carried a ^[A-Z]{3}$ -> "$" guard so markup could not
    enter via the currency channel; re-establish it here — a non-[A-Z]{3} code
    collapses to "?" so no markup reaches Paragraph. (Code-reviewer M-1.)"""
    try:
        return money_format(value, code, compact=compact, locale=locale)
    except KeyError:
        if value is None or not math.isfinite(value):
            return "—"
        safe = code.upper() if (len(code) == 3 and code.isascii() and code.isalpha()) else "?"
        return f"{safe} {value:,.0f}"


def _compact(sign: str, val: float, suffix: str, symbol: str = "$") -> str:
    """Format ``val`` with two decimals, but drop them when the value is a
    clean integer multiple of the suffix's unit at 2-decimal precision
    (so ``860.0`` → ``"860"`` and ``0.9995`` → ``"1"`` because both
    produce ``"X.00"`` from `:.2f`).

    String-based detection (``.endswith(".00")``) is used instead of a
    tolerance-based int comparison because boundary values like ``0.9995``
    differ from their rounded integer by 5e-4 — far above any practical
    FP-drift tolerance — yet ``f"{0.9995:.2f}"`` correctly produces
    ``"1.00"`` (CPython's dtoa). String detection sidesteps the picking
    of an arbitrary tolerance.
    """
    formatted = f"{val:.2f}"
    if formatted.endswith(".00"):
        return f"{sign}{symbol}{formatted[:-3]}{suffix}"
    return f"{sign}{symbol}{formatted}{suffix}"
