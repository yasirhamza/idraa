"""Unit tests for control_value_range / stability_badge helpers and the reworded
disclaimer (Issue #419 Task 5).

Written BEFORE implementation (TDD red-phase). These must fail on the current
codebase (old disclaimer, no helper functions) and pass once _view_model_helpers.py
is updated.
"""

from __future__ import annotations

from idraa.services._view_model_helpers import (
    CONTROL_WEIGHT_PROVENANCE_DISCLAIMER,
    control_value_range,
    stability_badge,
)

# ---- Disclaimer wording ----


def test_disclaimer_is_robustness_wording() -> None:
    """Issue #419: disclaimer must use plain-English robustness-framed language."""
    assert "modeled estimates shown as ranges" in CONTROL_WEIGHT_PROVENANCE_DISCLAIMER
    assert "not measured or" in CONTROL_WEIGHT_PROVENANCE_DISCLAIMER
    # Old wording (pending calibration) must be gone
    assert "pending calibration" not in CONTROL_WEIGHT_PROVENANCE_DISCLAIMER


def test_disclaimer_mentions_indistinguishable() -> None:
    """The reworded disclaimer calls out the too-close-to-call implication."""
    assert "too close to call" in CONTROL_WEIGHT_PROVENANCE_DISCLAIMER


# ---- control_value_range ----


def test_range_formatting_three_values() -> None:
    """Full range cell: p50 center + [p5–p95] bracket."""
    cell = {"reduction_p5": 80_000.0, "reduction_p50": 120_000.0, "reduction_p95": 190_000.0}
    s = control_value_range(cell)
    # All three magnitudes must appear somewhere in the string
    assert "120" in s
    assert "80" in s
    assert "190" in s


def test_range_formatting_shows_bracket() -> None:
    """The output must contain the '[…–…]' interval notation."""
    cell = {"reduction_p5": 80_000.0, "reduction_p50": 120_000.0, "reduction_p95": 190_000.0}
    s = control_value_range(cell)
    assert "[" in s and "–" in s and "]" in s


def test_range_formatting_missing_p50_returns_dash() -> None:
    """Absent p50 (no robustness data) returns '—'."""
    s = control_value_range({})
    assert s == "—"


def test_range_formatting_missing_bounds_returns_point() -> None:
    """Only p50 present (deterministic run) returns the p50 alone (no bracket)."""
    cell = {"reduction_p50": 120_000.0}
    s = control_value_range(cell)
    assert "120" in s
    assert "[" not in s


def test_range_formatting_respects_currency_code() -> None:
    """Non-USD code is reflected in the formatted output (symbol/code)."""
    cell = {"reduction_p5": 80_000.0, "reduction_p50": 120_000.0, "reduction_p95": 190_000.0}
    usd = control_value_range(cell, code="USD")
    eur = control_value_range(cell, code="EUR")
    # The two should differ (different symbols)
    assert usd != eur


# ---- stability_badge ----


def test_stability_badge_unstable_paired_vs_unpaired() -> None:
    """#421 item 2 tri-state: an unstable control CAPTURED by a pair badges
    'stable' (the pair-set 'too close to call' marker supersedes it in every
    consumer, so this value never displays); an unstable control NOT captured
    by any pair (instability spread thinly across many pairs) badges the
    non-committal 'rank sensitive' instead of overclaiming 'stable'."""
    assert (
        stability_badge({"stability_class": "unstable"}, in_indistinguishable_pair=True) == "stable"
    )
    assert (
        stability_badge({"stability_class": "unstable"}, in_indistinguishable_pair=False)
        == "rank sensitive"
    )
    # default is the unpaired (honest) reading
    assert stability_badge({"stability_class": "unstable"}) == "rank sensitive"


def test_stability_badge_unstable_never_indistinguishable() -> None:
    """stability_badge must NOT return 'indistinguishable' for any per-control
    stability_class (M3).  The 'indistinguishable' display is driven exclusively
    from weight_robustness.indistinguishable_control_ids (Spec-I1)."""
    for in_pair in (True, False):
        assert (
            stability_badge({"stability_class": "unstable"}, in_indistinguishable_pair=in_pair)
            != "indistinguishable"
        )
    assert stability_badge({"stability_class": "stable"}) != "indistinguishable"
    assert stability_badge({}) != "indistinguishable"


def test_stability_badge_stable() -> None:
    assert stability_badge({"stability_class": "stable"}) == "stable"


def test_stability_badge_not_assessed() -> None:
    """not_assessed → 'not assessed'.

    #454 item 6 reworded the display string from 'not checked' to
    'not assessed' (SINGLE-run template pairs it with a tooltip explaining
    rank stability is a multi-scenario measure). Assertion updated to the
    new copy — the mapping itself is unchanged.
    """
    assert stability_badge({"stability_class": "not_assessed"}) == "not assessed"


def test_stability_badge_not_applicable() -> None:
    """not_applicable → 'not assessed' (per brief: same as not_assessed).

    #454 item 6 reword: was 'not checked'.
    """
    assert stability_badge({"stability_class": "not_applicable"}) == "not assessed"


def test_stability_badge_missing_key() -> None:
    """Absent stability_class (e.g. legacy cell) → 'stable' (safe default)."""
    assert stability_badge({}) == "stable"
