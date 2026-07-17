"""Unit tests for the leave-one-out "if removed" display-plumbing helpers
(engine core landed in 62cf4dc; this is the view-model wiring only).

Covers:
- if_removed_by_control_single: SINGLE flat control_adjustments passthrough
  (present, absent key, explicit null -> absent, control_id-less entries skipped).
- if_removed_by_control_aggregate: AGGREGATE per-scenario summation (linearity
  of expectation -> sum is exact), a control absent from one scenario's map
  still sums the present scenarios AND is flagged partial (LOO-Meth-3), and an
  all-absent control -> None (not partial — it renders '—' outright).
- process_weight_robustness_for_display: if_removed merged into per_control
  cells, converted via the caller's convert callable, defaulting to None when
  if_removed_by_control is not passed at all (backward compat with existing
  callers/tests that predate this feature).
- 2026-07-04 mean+typical side-by-side: if_removed_by_control_{single,aggregate}
  ``key=`` parameterization (mean vs typical lookups built from twin keys);
  process_weight_robustness_for_display's ``basis`` exposure (defaulting missing
  -> "typical"), the paired ``typical_value`` cell field (from
  canonical_value_typical), and the paired ``if_removed_typical`` secondary figure.
"""

from __future__ import annotations

from typing import Any

from idraa.services._view_model_helpers import (
    if_removed_by_control_aggregate,
    if_removed_by_control_single,
    process_weight_robustness_for_display,
)

# ---------------------------------------------------------------------------
# if_removed_by_control_single
# ---------------------------------------------------------------------------


def test_single_passthrough_present_value() -> None:
    adjustments = [{"control_id": "c1", "if_removed_value": 12_345.0}]
    assert if_removed_by_control_single(adjustments) == {"c1": 12_345.0}


def test_single_passthrough_negative_value_not_clamped() -> None:
    """Weak-AND dilution can make a control's removal REDUCE modeled loss —
    the negative figure must pass through as-is, never clamped to 0."""
    adjustments = [{"control_id": "c1", "if_removed_value": -500.0}]
    assert if_removed_by_control_single(adjustments) == {"c1": -500.0}


def test_single_absent_key_maps_to_none() -> None:
    """No if_removed_value key at all (legacy run, or LOO skipped/degraded
    this run's one scenario) -> None, not a fabricated 0.0."""
    adjustments = [{"control_id": "c1", "effectiveness": 0.5}]
    assert if_removed_by_control_single(adjustments) == {"c1": None}


def test_single_explicit_null_degrades_to_absent() -> None:
    """An explicit if_removed_value: null in persisted JSON is treated
    identically to a missing key (same convention as shapley_value)."""
    adjustments = [{"control_id": "c1", "if_removed_value": None}]
    assert if_removed_by_control_single(adjustments) == {"c1": None}


def test_single_entries_without_control_id_are_skipped() -> None:
    adjustments: list[dict[str, Any]] = [
        {"if_removed_value": 100.0},
        {"control_id": "c1", "if_removed_value": 50.0},
    ]
    assert if_removed_by_control_single(adjustments) == {"c1": 50.0}


def test_single_multiple_controls_all_present() -> None:
    adjustments = [
        {"control_id": "c1", "if_removed_value": 10.0},
        {"control_id": "c2", "if_removed_value": 0.0},  # genuine null-player, not absent
        {"control_id": "c3", "if_removed_value": 30.0},
    ]
    assert if_removed_by_control_single(adjustments) == {"c1": 10.0, "c2": 0.0, "c3": 30.0}


# ---------------------------------------------------------------------------
# if_removed_by_control_aggregate
# ---------------------------------------------------------------------------


def test_aggregate_sums_across_scenarios_when_all_present() -> None:
    """Scenario independence makes the sum exact: a control appearing on 3
    scenarios sums all 3 if_removed_value figures."""
    per_scenario = [
        {
            "scenario_id": "s1",
            "control_adjustments": [{"control_id": "c1", "if_removed_value": 100.0}],
        },
        {
            "scenario_id": "s2",
            "control_adjustments": [{"control_id": "c1", "if_removed_value": 200.0}],
        },
        {
            "scenario_id": "s3",
            "control_adjustments": [{"control_id": "c1", "if_removed_value": 50.0}],
        },
    ]
    assert if_removed_by_control_aggregate(per_scenario) == ({"c1": 350.0}, set())


def test_aggregate_control_absent_from_one_scenario_sums_present_only() -> None:
    """A control present on 2 of 3 scenarios sums only the scenarios that
    carry the key — the absent scenario contributes nothing, not a fabricated
    0.0 that would understate the drop-cost."""
    per_scenario = [
        {
            "scenario_id": "s1",
            "control_adjustments": [{"control_id": "c1", "if_removed_value": 100.0}],
        },
        {
            "scenario_id": "s2",
            # LOO skipped/degraded for this scenario — no if_removed_value key.
            "control_adjustments": [{"control_id": "c1", "effectiveness": 0.3}],
        },
        {
            "scenario_id": "s3",
            "control_adjustments": [{"control_id": "c1", "if_removed_value": 75.0}],
        },
    ]
    assert if_removed_by_control_aggregate(per_scenario) == ({"c1": 175.0}, {"c1"})


def test_aggregate_all_absent_control_maps_to_none() -> None:
    """A control that never carries the key on any scenario it appears in
    (attribution unavailable everywhere) -> None, renders '—'."""
    per_scenario = [
        {
            "scenario_id": "s1",
            "control_adjustments": [{"control_id": "c1", "effectiveness": 0.5}],
        },
        {
            "scenario_id": "s2",
            "control_adjustments": [{"control_id": "c1", "shapley_value": 10.0}],
        },
    ]
    assert if_removed_by_control_aggregate(per_scenario) == ({"c1": None}, set())


def test_aggregate_mixed_controls_independent_totals() -> None:
    """Two controls with independent presence patterns each get their own
    correct total / None."""
    per_scenario = [
        {
            "scenario_id": "s1",
            "control_adjustments": [
                {"control_id": "c1", "if_removed_value": 10.0},
                {"control_id": "c2", "effectiveness": 0.1},  # c2 absent here
            ],
        },
        {
            "scenario_id": "s2",
            "control_adjustments": [
                {"control_id": "c1", "if_removed_value": 20.0},
                {"control_id": "c2", "effectiveness": 0.2},  # c2 absent here too -> None
            ],
        },
    ]
    result = if_removed_by_control_aggregate(per_scenario)
    assert result == ({"c1": 30.0, "c2": None}, set())


def test_aggregate_explicit_null_degrades_to_absent() -> None:
    per_scenario = [
        {
            "scenario_id": "s1",
            "control_adjustments": [{"control_id": "c1", "if_removed_value": None}],
        },
        {
            "scenario_id": "s2",
            "control_adjustments": [{"control_id": "c1", "if_removed_value": 40.0}],
        },
    ]
    assert if_removed_by_control_aggregate(per_scenario) == ({"c1": 40.0}, {"c1"})


def test_aggregate_empty_per_scenario_returns_empty_dict() -> None:
    assert if_removed_by_control_aggregate([]) == ({}, set())


# ---------------------------------------------------------------------------
# process_weight_robustness_for_display: if_removed wiring
# ---------------------------------------------------------------------------

_IDENTITY = lambda x: x  # noqa: E731  # USD identity convert


def _make_wr(cid: str, p50: float = 100_000.0) -> dict[str, Any]:
    return {
        "headline": {"reduction_p5": 0.0, "reduction_p50": p50, "reduction_p95": 0.0},
        "per_control": {
            cid: {
                "reduction_p5": 0.0,
                "reduction_p50": p50,
                "reduction_p95": 0.0,
                "rank_p50": 0,
                "rank_min": 0,
                "rank_max": 0,
                "stability_class": "not_applicable",
            }
        },
        "indistinguishable_pairs": [],
        "rank_stability_available": False,
        "draws_used": 0,
        "degraded": False,
        "state": "ok",
    }


def test_if_removed_merged_into_per_control_cell() -> None:
    wr = _make_wr("c1")
    result = process_weight_robustness_for_display(
        wr, _IDENTITY, "USD", if_removed_by_control={"c1": 55_000.0}
    )
    assert result is not None
    assert result["per_control"]["c1"]["if_removed"] == 55_000.0


def test_if_removed_defaults_to_none_when_lookup_not_passed() -> None:
    """Backward compat: existing callers that don't thread if_removed_by_control
    (e.g. pre-existing PDF/HTML fixtures) get None -> '—', not a KeyError."""
    wr = _make_wr("c1")
    result = process_weight_robustness_for_display(wr, _IDENTITY, "USD")
    assert result is not None
    assert result["per_control"]["c1"]["if_removed"] is None


def test_if_removed_none_when_cid_absent_from_lookup() -> None:
    wr = _make_wr("c1")
    result = process_weight_robustness_for_display(
        wr, _IDENTITY, "USD", if_removed_by_control={"other_control": 10.0}
    )
    assert result is not None
    assert result["per_control"]["c1"]["if_removed"] is None


def test_if_removed_none_when_lookup_value_is_none() -> None:
    wr = _make_wr("c1")
    result = process_weight_robustness_for_display(
        wr, _IDENTITY, "USD", if_removed_by_control={"c1": None}
    )
    assert result is not None
    assert result["per_control"]["c1"]["if_removed"] is None


def test_if_removed_converted_via_convert_callable() -> None:
    """Convert-once invariant: if_removed passes through the same `convert`
    callable as the other money fields (e.g. a reporting-currency rate)."""
    wr = _make_wr("c1")
    convert = lambda v: v * 0.9 if v is not None else None  # noqa: E731
    result = process_weight_robustness_for_display(
        wr, convert, "EUR", if_removed_by_control={"c1": 1_000.0}
    )
    assert result is not None
    assert result["per_control"]["c1"]["if_removed"] == 900.0


def test_if_removed_negative_value_passes_through_converted() -> None:
    wr = _make_wr("c1")
    result = process_weight_robustness_for_display(
        wr, _IDENTITY, "USD", if_removed_by_control={"c1": -250.0}
    )
    assert result is not None
    assert result["per_control"]["c1"]["if_removed"] == -250.0


def test_if_removed_partial_flag_set_from_partial_ids() -> None:
    """LOO-Meth-3: a control whose aggregate sum covers only part of its
    scenarios gets if_removed_partial=True so templates/PDF append the
    '(partial)' marker (not '>=' — a missing scenario's LOO can be negative,
    so the partial sum is not a lower bound)."""
    wr = _make_wr("c1")
    result = process_weight_robustness_for_display(
        wr,
        _IDENTITY,
        "USD",
        if_removed_by_control={"c1": 55_000.0},
        if_removed_partial_ids={"c1"},
    )
    assert result is not None
    assert result["per_control"]["c1"]["if_removed"] == 55_000.0
    assert result["per_control"]["c1"]["if_removed_partial"] is True


def test_if_removed_partial_flag_defaults_false() -> None:
    """No partial ids passed (SINGLE callers, legacy callers) -> False; and a
    None value is never marked partial even if listed (nothing to mark)."""
    wr = _make_wr("c1")
    result = process_weight_robustness_for_display(
        wr, _IDENTITY, "USD", if_removed_by_control={"c1": 10.0}
    )
    assert result is not None
    assert result["per_control"]["c1"]["if_removed_partial"] is False

    wr2 = _make_wr("c1")
    result2 = process_weight_robustness_for_display(
        wr2,
        _IDENTITY,
        "USD",
        if_removed_by_control={"c1": None},
        if_removed_partial_ids={"c1"},
    )
    assert result2 is not None
    assert result2["per_control"]["c1"]["if_removed"] is None
    assert result2["per_control"]["c1"]["if_removed_partial"] is False


# ---------------------------------------------------------------------------
# 2026-07-04 mean+typical side-by-side: key= parameterization
# ---------------------------------------------------------------------------


def test_single_key_param_reads_mean_key_when_requested() -> None:
    """key="if_removed_value_mean" reads the mean-basis twin key, ignoring the
    typical-basis default key even when both are present on the same adjustment."""
    adjustments = [{"control_id": "c1", "if_removed_value": 10.0, "if_removed_value_mean": 99.0}]
    assert if_removed_by_control_single(adjustments, key="if_removed_value_mean") == {"c1": 99.0}
    # Default key is unchanged (still reads the typical-basis key).
    assert if_removed_by_control_single(adjustments) == {"c1": 10.0}


def test_single_key_param_absent_mean_key_maps_to_none() -> None:
    """A run with only the typical key (pre-mean-basis) -> None under the mean key,
    even though the typical key is present — the two lookups are independent."""
    adjustments = [{"control_id": "c1", "if_removed_value": 10.0}]
    assert if_removed_by_control_single(adjustments, key="if_removed_value_mean") == {"c1": None}


def test_aggregate_key_param_sums_mean_key_independently_of_typical() -> None:
    """The mean-key sum and the typical-key sum are computed independently — a
    scenario missing one key but carrying the other contributes to only one sum."""
    per_scenario = [
        {
            "scenario_id": "s1",
            "control_adjustments": [
                {"control_id": "c1", "if_removed_value": 100.0, "if_removed_value_mean": 500.0}
            ],
        },
        {
            "scenario_id": "s2",
            # Mean pass dropped this scenario (non-finite) but typical succeeded.
            "control_adjustments": [{"control_id": "c1", "if_removed_value": 50.0}],
        },
    ]
    typical_lookup, typical_partial = if_removed_by_control_aggregate(
        per_scenario, key="if_removed_value"
    )
    mean_lookup, mean_partial = if_removed_by_control_aggregate(
        per_scenario, key="if_removed_value_mean"
    )
    assert typical_lookup == {"c1": 150.0}
    assert typical_partial == set()
    assert mean_lookup == {"c1": 500.0}
    assert mean_partial == {"c1"}  # only s1 carries the mean key -> partial


# ---------------------------------------------------------------------------
# 2026-07-04 mean+typical side-by-side: basis exposure
# ---------------------------------------------------------------------------


def test_basis_defaults_to_typical_when_key_absent() -> None:
    """A legacy blob (persisted before the mean-basis chain landed) has no
    "basis" key at all -> the processed result normalizes it to "typical"."""
    wr = _make_wr("c1")
    assert "basis" not in wr
    result = process_weight_robustness_for_display(wr, _IDENTITY, "USD")
    assert result is not None
    assert result["basis"] == "typical"


def test_basis_passes_through_mean_value() -> None:
    wr = {**_make_wr("c1"), "basis": "mean"}
    result = process_weight_robustness_for_display(wr, _IDENTITY, "USD")
    assert result is not None
    assert result["basis"] == "mean"


# ---------------------------------------------------------------------------
# 2026-07-04 mean+typical side-by-side: paired typical_value cell field
# ---------------------------------------------------------------------------


def test_typical_value_populated_from_canonical_value_typical() -> None:
    wr = {**_make_wr("c1"), "basis": "mean", "canonical_value_typical": {"c1": 42_000.0}}
    result = process_weight_robustness_for_display(wr, _IDENTITY, "USD")
    assert result is not None
    assert result["per_control"]["c1"]["typical_value"] == 42_000.0


def test_typical_value_none_when_canonical_value_typical_absent() -> None:
    """Legacy blob (no canonical_value_typical key at all) -> None, never a
    fabricated $0 (absent≠0.0 convention, same as if_removed/shapley_value)."""
    wr = _make_wr("c1")
    assert "canonical_value_typical" not in wr
    result = process_weight_robustness_for_display(wr, _IDENTITY, "USD")
    assert result is not None
    assert result["per_control"]["c1"]["typical_value"] is None


def test_typical_value_none_when_cid_absent_from_canonical_value_typical() -> None:
    wr = {**_make_wr("c1"), "basis": "mean", "canonical_value_typical": {"other": 1.0}}
    result = process_weight_robustness_for_display(wr, _IDENTITY, "USD")
    assert result is not None
    assert result["per_control"]["c1"]["typical_value"] is None


def test_typical_value_converted_via_convert_callable() -> None:
    """Convert-once invariant: typical_value passes through the same `convert`
    callable as the other money fields."""
    wr = {**_make_wr("c1"), "basis": "mean", "canonical_value_typical": {"c1": 1_000.0}}
    convert = lambda v: v * 0.9 if v is not None else None  # noqa: E731
    result = process_weight_robustness_for_display(wr, convert, "EUR")
    assert result is not None
    assert result["per_control"]["c1"]["typical_value"] == 900.0


# ---------------------------------------------------------------------------
# 2026-07-04 mean+typical side-by-side: paired if_removed_typical secondary
# ---------------------------------------------------------------------------


def test_if_removed_typical_populated_when_secondary_lookup_supplied() -> None:
    wr = _make_wr("c1")
    result = process_weight_robustness_for_display(
        wr,
        _IDENTITY,
        "USD",
        if_removed_by_control={"c1": 500.0},  # primary (e.g. mean-basis)
        if_removed_by_control_typical={"c1": 300.0},  # secondary (typical-basis)
    )
    assert result is not None
    assert result["per_control"]["c1"]["if_removed"] == 500.0
    assert result["per_control"]["c1"]["if_removed_typical"] == 300.0


def test_if_removed_typical_none_when_secondary_lookup_not_supplied() -> None:
    """Legacy/typical-basis callers omit the secondary lookup entirely -> None,
    no sub-line rendered (matches "legacy runs render exactly as today")."""
    wr = _make_wr("c1")
    result = process_weight_robustness_for_display(
        wr, _IDENTITY, "USD", if_removed_by_control={"c1": 500.0}
    )
    assert result is not None
    assert result["per_control"]["c1"]["if_removed_typical"] is None


def test_if_removed_typical_none_when_cid_absent_from_secondary_lookup() -> None:
    wr = _make_wr("c1")
    result = process_weight_robustness_for_display(
        wr,
        _IDENTITY,
        "USD",
        if_removed_by_control={"c1": 500.0},
        if_removed_by_control_typical={"other": 1.0},
    )
    assert result is not None
    assert result["per_control"]["c1"]["if_removed_typical"] is None


def test_if_removed_typical_converted_via_convert_callable() -> None:
    wr = _make_wr("c1")
    convert = lambda v: v * 0.9 if v is not None else None  # noqa: E731
    result = process_weight_robustness_for_display(
        wr,
        convert,
        "EUR",
        if_removed_by_control={"c1": 500.0},
        if_removed_by_control_typical={"c1": 1_000.0},
    )
    assert result is not None
    assert result["per_control"]["c1"]["if_removed"] == 450.0
    assert result["per_control"]["c1"]["if_removed_typical"] == 900.0
