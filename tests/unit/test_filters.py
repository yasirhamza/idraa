"""Unit tests for Jinja filters registered in src/idraa/app.py.

Task 8 (P3): ``abbreviate_money`` and ``currency_symbol`` retired in favour of
``money_format``/``safe_money_format``.  Their tests below were deleted:
  - ``test_abbreviate_money_*`` — tested a removed function
  - ``test_currency_symbol_single_source`` — tested that pdf_report imported the
    same currency_symbol; pdf_report.py already retired it in Task 7, and the
    shared function is gone entirely now.
The remaining tests cover the surviving filters.

Polish-1 (post-#454 SWE review): ``_humanize_slug`` gained an uppercase
acronym token set ({"lec", "vmc", "dsc", "sa", "id", "tef", "roi"}) applied
before the generic capitalize fallback, so FAIR-CAM sub-function slugs read
as acronyms rather than title-cased words (e.g. "sa" -> "SA", not "Sa").
The ``dsc_prev_sa_reporting`` case below is re-pinned to
``"DSC Prev SA Reporting"`` accordingly.
"""

from __future__ import annotations

import pytest

from idraa.app import (  # type: ignore[attr-defined]
    _format_dist_value,
    _format_money_input,
    _format_probability_input,
    _format_rate_input,
    _humanize_slug,
)

# ---------------------------------------------------------------------------
# humanize_slug — #454 item 3: DISPLAY-ONLY enum/slug humanizer.
# Underscores→spaces, title-case, acronym runs preserved, revenue-tier unit
# tokens (m/b) uppercased, small joiner words kept lowercase. Never mutates
# stored values.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        # FAIR-CAM domain slugs
        ("decision_support", "Decision Support"),
        ("loss_event", "Loss Event"),
        ("variance_management", "Variance Management"),
        # Revenue-tier: unit tokens uppercased, "to" stays lowercase
        ("100m_to_1b", "100M to 1B"),
        ("10m_to_100m", "10M to 100M"),
        ("1b_to_10b", "1B to 10B"),
        # Single-word industry
        ("other", "Other"),
        ("manufacturing", "Manufacturing"),
        # Hyphen treated like underscore
        ("threat-actor", "Threat Actor"),
        # Sub-function slug (known acronym tokens uppercased; raw kept in tooltip)
        ("dsc_prev_sa_reporting", "DSC Prev SA Reporting"),
        # Existing acronym runs preserved (already-uppercase words untouched)
        ("TPRM_control", "TPRM Control"),
        # Empty / None
        ("", ""),
        (None, ""),
        # Already-humanized-ish single token
        ("Active", "Active"),
    ],
)
def test_humanize_slug(value: object, expected: str) -> None:
    assert _humanize_slug(value) == expected


def test_humanize_slug_is_pure_and_leaves_value_untouched() -> None:
    """Idempotent on its own output for simple slugs; never mutates input."""
    src = "loss_event"
    assert _humanize_slug(src) == "Loss Event"
    # Original string object is not modified (strings are immutable, but this
    # documents the display-only contract — no side effects on stored values).
    assert src == "loss_event"


def test_format_dist_value_probability_renders_4dp() -> None:  # I-2
    """I-2 regression: the vuln distribution chart passes fmt="probability".
    A 0..1 probability must render at 4dp (matching format_probability_input),
    not money's 2dp — pre-fix the vuln chart used fmt="money" and showed 0.35
    instead of 0.3500, losing precision on low/mode/high.
    """
    assert _format_dist_value(0.35, "probability") == "0.3500"
    assert _format_dist_value(0.3500001, "probability") == "0.3500"
    assert _format_dist_value(0.05, "probability") == "0.0500"
    # Dispatch parity: the probability branch == the probability filter itself.
    assert _format_dist_value(0.6, "probability") == _format_probability_input(0.6)
    # And distinct from money (which would render "0.35" / "0.05" at 2dp).
    assert _format_dist_value(0.35, "money") != _format_dist_value(0.35, "probability")


# ---------------------------------------------------------------------------
# format_*_input filters — PR #247 UAT bug fix.
# The T1 quantile-pooling pipeline produces honest tiny-float low quantiles
# (e.g. 1.5e-06 dollars). These filters render them as readable fixed-point
# strings for ``<input type="number">``, never scientific notation.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        # The literal screenshot bugs from the user report
        (1.5146025633444114e-06, "0.00"),  # tiny float collapses to "0.00", not "1.5e-0..."
        (69999999.98617376, "69999999.99"),  # 6+ decimals trimmed to 2dp
        # None → "" so optional SL fields render blank
        (None, ""),
        # Zero
        (0, "0.00"),
        (0.0, "0.00"),
        # Round numbers stay clean
        (1000, "1000.00"),
        (1_000_000.5, "1000000.50"),
        # NaN / inf collapse to "" (never break input parsing)
        (float("nan"), ""),
        (float("inf"), ""),
        (float("-inf"), ""),
        # String input that can be parsed to float
        ("12.345", "12.35"),
        # Garbage input collapses safely
        ("garbage", ""),
    ],
)
def test_format_money_input(value: object, expected: str) -> None:
    assert _format_money_input(value) == expected  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "value,expected",
    [
        # Common TEF prefill values from the pooling pipeline
        (0.00200000144326, "0.0020"),
        (0.060000000777520664, "0.0600"),
        # None → ""
        (None, ""),
        # Zero and integer rates
        (0, "0.0000"),
        (1, "1.0000"),
        # Extremely tiny rate (below 4dp precision) collapses to "0.0000"
        (1e-09, "0.0000"),
        # Above 1 (rare but legal for high-frequency threats)
        (12.3456789, "12.3457"),
        # NaN / inf safe
        (float("nan"), ""),
        (float("inf"), ""),
        # Garbage safe
        ("garbage", ""),
    ],
)
def test_format_rate_input(value: object, expected: str) -> None:
    assert _format_rate_input(value) == expected  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "value,expected",
    [
        # Common Vuln prefill values
        (0.2999998117810301, "0.3000"),
        (0.551088591189831, "0.5511"),
        # None → ""
        (None, ""),
        # Boundary values
        (0, "0.0000"),
        (1, "1.0000"),
        (0.5, "0.5000"),
        # Sub-4dp precision collapses
        (1e-09, "0.0000"),
        # NaN / inf safe
        (float("nan"), ""),
        (float("inf"), ""),
        # Garbage safe
        ("garbage", ""),
    ],
)
def test_format_probability_input(value: object, expected: str) -> None:
    assert _format_probability_input(value) == expected  # type: ignore[arg-type]


def test_input_filters_never_emit_scientific_notation() -> None:
    """Regression test for the literal user-reported bug shape.

    The bug was that ``1.5146025633444114e-06`` rendered into an
    ``<input type="number">`` as ``"1.5146025633444114e-0"`` (truncated
    mid-exponent by the input width), which is both unreadable AND
    invalid for the input's number parser. Guard the format string.
    """
    tiny = 1.5146025633444114e-06
    for f in (_format_money_input, _format_rate_input, _format_probability_input):
        out = f(tiny)
        assert "e" not in out.lower(), f"{f.__name__} emitted sci notation: {out!r}"
        assert "E" not in out, f"{f.__name__} emitted sci notation: {out!r}"


def test_input_filters_none_yields_empty_string_for_optional_fields() -> None:
    """Optional fields (e.g. Secondary Loss) submit empty when blank.

    The form's Pydantic validator coerces empty string → None on
    submit, so the round-trip None → "" → None is the contract.
    """
    assert _format_money_input(None) == ""
    assert _format_rate_input(None) == ""
    assert _format_probability_input(None) == ""


# Task 8 (P3): test_currency_symbol_single_source and test_abbreviate_money_currency_code
# were deleted — they tested currency_symbol / abbreviate_money which are retired.
# The security guard (markup rejection) is now enforced by safe_money_format,
# tested in tests/unit/test_safe_money_format.py::test_markup_code_sanitized_no_injection.
