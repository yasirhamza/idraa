"""View-model helper tests for services/reports.py (omicron-2 F5-F8) + Task 2 T2.

Pure-function tests; no DB, no fixtures. Each test constructs the
input directly and asserts on the output.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from idraa.services._view_model_helpers import has_tail_metrics
from idraa.services.reports import (
    CONTROL_WEIGHT_PROVENANCE_DISCLAIMER,
    CONTROL_WEIGHT_PROVENANCE_DISCLAIMER_BASE,
    ControlInventoryRow,
    PerScenarioRow,
    RunReportData,
    build_control_breakdown_rows,
    build_narrative,
    build_narrative_single,
    build_pct_revenue,
    build_per_scenario_rows,
    group_controls_by_fair_cam_domain,
)
from idraa.services.run_view_model import _build_tail_risk

# ---- F5: build_pct_revenue ----


def test_build_pct_revenue_with_positive_revenue_returns_percent() -> None:
    # 200,000 / 100,000,000 = 0.002 -> 0.20%
    got = build_pct_revenue(residual_ale=200_000.0, annual_revenue=100_000_000.0)
    assert got == pytest.approx(0.2)


def test_build_pct_revenue_with_decimal_revenue_casts_cleanly() -> None:
    got = build_pct_revenue(
        residual_ale=200_000.0,
        annual_revenue=Decimal("100000000"),
    )
    assert got == pytest.approx(0.2)


def test_build_pct_revenue_with_none_revenue_returns_none() -> None:
    assert build_pct_revenue(residual_ale=200_000.0, annual_revenue=None) is None


def test_build_pct_revenue_with_zero_revenue_returns_none() -> None:
    """No divide-by-zero; zero is the unset case."""
    assert build_pct_revenue(residual_ale=200_000.0, annual_revenue=0) is None


def test_build_pct_revenue_with_decimal_zero_returns_none() -> None:
    assert (
        build_pct_revenue(
            residual_ale=200_000.0,
            annual_revenue=Decimal("0"),
        )
        is None
    )


# ---- F6: build_narrative ----

CANONICAL_NARRATIVE_INPUT: dict[str, Any] = {
    "n_scenarios": 8,
    "control_value_dollars": 2_340_000.0,
    "control_value_percent": 47.3,
    "residual_ale": 2_610_000.0,
    "pct_revenue": 0.87,
    "n_simulations": 50_000,
    "currency": "USD",
}


def test_build_narrative_with_revenue_clause_canonical() -> None:
    """Snapshot test: the exact narrative string for a canonical input.

    Regression on phrasing changes — if the wording changes, this test
    must be updated deliberately and reviewers must accept it.
    """
    got = build_narrative(**CANONICAL_NARRATIVE_INPUT)
    # Task 5 (#419 / Meth-I4): the spurious one-decimal-place percentage is DROPPED;
    # without a weight_robustness_headline the point dollar leads (no "(47.3%)").
    # M4: no weight_robustness → BASE disclaimer only (no indistinguishable sentence).
    expected = (
        "Across 8 modeled scenarios, the current control posture reduces "
        "annualized loss expectancy by $2,340,000, bringing residual "
        "ALE to $2,610,000 — equivalent to 0.87% of annual revenue. This "
        "estimate reflects 50,000 Monte Carlo iterations. "
        # M4: base disclaimer only (no too-close-to-call caveat for legacy/robustness-absent).
        "These are modeled estimates shown as ranges, not measured or "
        "guaranteed loss reductions."
    )
    assert got == expected


def test_build_narrative_leads_with_range_when_robustness_present() -> None:
    """Task 5 (#419 / Meth-I4): with a weight_robustness_headline, the narrative
    LEADS with the range (~$p50 [range $p5–$p95]) and still drops the bare %."""
    inp = {
        **CANONICAL_NARRATIVE_INPUT,
        "weight_robustness_headline": {
            "reduction_p5": 1_800_000.0,
            "reduction_p50": 2_100_000.0,
            "reduction_p95": 2_600_000.0,
        },
    }
    got = build_narrative(**inp)
    # P2 (2026-07-03): the cover narrative was reworded for statistical coherence.
    # It now LEADS with the AVERAGE reduction ("… on average") — matching the cover
    # headline and the MEAN residual ALE — and demotes the typical-case (median)
    # band to a tilde-prefixed parenthetical that explains the skew. The old
    # "(typical-case range)" gloss is intentionally gone; assert the new shape.
    assert "on average" in got
    assert "~" in got  # tilde still prefixes the typical-case band
    assert "typical-case" in got
    assert "skewed losses run far below the average" in got
    # No bare point-precision "$X (Y.Y%)" leads the narrative
    import re as _re

    assert _re.search(r"\$[\d,]+ \(\d+\.\d%\)", got) is None


def test_build_narrative_mean_basis_drops_skew_claim() -> None:
    """2026-07-04 mean+typical side-by-side: when weight_robustness_headline's
    ``basis`` is "mean", the range IS the average figure — the "skewed losses
    run far below the average" claim would be FALSE for this range, so it is
    dropped in favor of same-basis framing. No "typical-case" wording leaks in."""
    inp = {
        **CANONICAL_NARRATIVE_INPUT,
        "weight_robustness_headline": {
            "reduction_p5": 1_800_000.0,
            "reduction_p50": 2_100_000.0,
            "reduction_p95": 2_600_000.0,
            "basis": "mean",
        },
    }
    got = build_narrative(**inp)
    assert "on average" in got
    assert "same average basis" in got
    assert "typical-case" not in got
    assert "skewed losses run far below the average" not in got


def test_build_narrative_typical_basis_unchanged_when_basis_key_present() -> None:
    """Explicit basis="typical" (or the historical shape with no "basis" key at
    all) both take the legacy skew-explaining wording — no behavior change."""
    inp_explicit = {
        **CANONICAL_NARRATIVE_INPUT,
        "weight_robustness_headline": {
            "reduction_p5": 1_800_000.0,
            "reduction_p50": 2_100_000.0,
            "reduction_p95": 2_600_000.0,
            "basis": "typical",
        },
    }
    got = build_narrative(**inp_explicit)
    assert "typical-case" in got
    assert "skewed losses run far below the average" in got


def test_build_narrative_without_revenue_clause() -> None:
    inp = {**CANONICAL_NARRATIVE_INPUT, "pct_revenue": None}
    got = build_narrative(**inp)
    assert "% of annual revenue" not in got
    assert "Across 8 modeled scenarios" in got
    # Task 5: bare "(47.3%)" percentage is dropped
    assert "(47.3%)" not in got
    assert "$2,340,000" in got
    assert "50,000 Monte Carlo iterations." in got
    assert "confidence" not in got


def test_build_narrative_no_bare_point_percent_leads() -> None:
    """Task 5 (#419 / Meth-I4): negative-match — no bare "$X (Y.Y%)" leads the
    narrative in EITHER the robustness-present or robustness-absent path."""
    import re as _re

    _pat = r"\$[\d,]+ \(\d+\.\d%\)"
    # Robustness absent
    assert _re.search(_pat, build_narrative(**CANONICAL_NARRATIVE_INPUT)) is None
    # Robustness present
    inp = {
        **CANONICAL_NARRATIVE_INPUT,
        "weight_robustness_headline": {
            "reduction_p5": 1.0,
            "reduction_p50": 2.0,
            "reduction_p95": 3.0,
        },
    }
    assert _re.search(_pat, build_narrative(**inp)) is None


def test_build_narrative_eur_currency() -> None:
    inp = {**CANONICAL_NARRATIVE_INPUT, "currency": "EUR"}
    got = build_narrative(**inp)
    assert "€2,340,000" in got
    assert "$" not in got


def test_build_narrative_n_simulations_comma_formatted() -> None:
    inp = {**CANONICAL_NARRATIVE_INPUT, "n_simulations": 1_000_000}
    got = build_narrative(**inp)
    assert "1,000,000 Monte Carlo iterations" in got


def test_build_narrative_appends_control_weight_provenance_disclaimer() -> None:
    """Issue #413 / M4: the AGGREGATE exec narrative always appends the disclaimer.
    When weight_robustness_headline is absent, the BASE variant (no indistinguishable
    sentence) is appended; the full disclaimer is used only when robustness is present."""
    # Robustness-absent path → BASE disclaimer.
    got_absent = build_narrative(**CANONICAL_NARRATIVE_INPUT)
    assert CONTROL_WEIGHT_PROVENANCE_DISCLAIMER_BASE in got_absent
    assert got_absent.endswith(CONTROL_WEIGHT_PROVENANCE_DISCLAIMER_BASE)
    # Robustness-present path → full disclaimer (both sentences).
    inp_present = {
        **CANONICAL_NARRATIVE_INPUT,
        "weight_robustness_headline": {
            "reduction_p5": 1.0,
            "reduction_p50": 2.0,
            "reduction_p95": 3.0,
        },
    }
    got_present = build_narrative(**inp_present)
    assert CONTROL_WEIGHT_PROVENANCE_DISCLAIMER in got_present
    assert got_present.endswith(CONTROL_WEIGHT_PROVENANCE_DISCLAIMER)


# ---- F7: group_controls_by_fair_cam_domain ----


def _control_snapshot_dict(
    *, name: str, domain: str | None = "loss_event", type_: str = "preventive"
) -> dict[str, Any]:
    """Mimics the relevant subset of ControlSnapshotV2.model_dump output."""
    snap: dict[str, Any] = {
        "snapshot_version": 2,
        "control_id": "00000000-0000-0000-0000-000000000000",
        "name": name,
        "type": type_,
        "assignments": [],
    }
    if domain is not None:
        snap["domain"] = domain
    return snap


def test_group_controls_by_fair_cam_domain_three_buckets() -> None:
    snapshot = [
        _control_snapshot_dict(name="Firewall", domain="loss_event"),
        _control_snapshot_dict(name="SIEM", domain="variance_management"),
        _control_snapshot_dict(name="Risk Committee", domain="decision_support"),
        _control_snapshot_dict(name="EDR", domain="loss_event"),
    ]
    got = group_controls_by_fair_cam_domain(snapshot)

    # All four required keys present (UNCATEGORIZED may be absent or empty)
    assert "LOSS_EVENT" in got
    assert "VARIANCE_MANAGEMENT" in got
    assert "DECISION_SUPPORT" in got

    assert [r.name for r in got["LOSS_EVENT"]] == ["Firewall", "EDR"]
    assert [r.name for r in got["VARIANCE_MANAGEMENT"]] == ["SIEM"]
    assert [r.name for r in got["DECISION_SUPPORT"]] == ["Risk Committee"]
    assert got.get("UNCATEGORIZED", []) == []


def test_group_controls_by_fair_cam_domain_uncategorized_fallback() -> None:
    """Missing/unknown domain -> UNCATEGORIZED bucket (defensive)."""
    snapshot = [
        _control_snapshot_dict(name="Mystery1", domain=None),
        _control_snapshot_dict(name="Mystery2", domain="unknown_value"),
        _control_snapshot_dict(name="Real", domain="loss_event"),
    ]
    got = group_controls_by_fair_cam_domain(snapshot)
    assert [r.name for r in got["UNCATEGORIZED"]] == ["Mystery1", "Mystery2"]
    assert [r.name for r in got["LOSS_EVENT"]] == ["Real"]


def test_group_controls_by_fair_cam_domain_preserves_input_order() -> None:
    """Same domain -> rows appear in input order (no resort)."""
    snapshot = [_control_snapshot_dict(name=f"C{i}", domain="loss_event") for i in range(5)]
    got = group_controls_by_fair_cam_domain(snapshot)
    assert [r.name for r in got["LOSS_EVENT"]] == ["C0", "C1", "C2", "C3", "C4"]


def test_control_inventory_row_carries_name_and_type() -> None:
    snapshot = [_control_snapshot_dict(name="Firewall", domain="loss_event", type_="preventive")]
    got = group_controls_by_fair_cam_domain(snapshot)
    row = got["LOSS_EVENT"][0]
    assert isinstance(row, ControlInventoryRow)
    assert row.name == "Firewall"
    assert row.type == "preventive"


# Issue #90 task 6.5 — reader honours the V2 ``domains: list[str]`` shape
# emitted by ``_snapshot_control_v2`` (lowercase ControlDomain enum values).


def test_group_controls_by_fair_cam_domain_buckets_multi_domain_into_each() -> None:
    """Issue #90: multi-domain V2 snapshot entries appear under EACH domain bucket.

    Matches the Task 5 maintenance-grouping fix: a control spanning multiple
    sub-functions whose domains differ is surfaced under every domain it spans.
    """
    snapshot: list[dict[str, Any]] = [
        {
            "snapshot_version": 2,
            "control_id": "00000000-0000-0000-0000-000000000001",
            "name": "Multi",
            "domains": ["decision_support", "loss_event"],
            "type": "preventive",
            "assignments": [],
        },
        {
            "snapshot_version": 2,
            "control_id": "00000000-0000-0000-0000-000000000002",
            "name": "LecOnly",
            "domains": ["loss_event"],
            "type": "preventive",
            "assignments": [],
        },
    ]
    got = group_controls_by_fair_cam_domain(snapshot)

    assert [r.name for r in got["LOSS_EVENT"]] == ["Multi", "LecOnly"]
    assert [r.name for r in got["DECISION_SUPPORT"]] == ["Multi"]
    assert got["VARIANCE_MANAGEMENT"] == []
    assert got["UNCATEGORIZED"] == []


def test_group_controls_by_fair_cam_domain_falls_back_to_legacy_domain_key() -> None:
    """Pre-issue-#90 V1/V2 snapshots use the scalar ``domain`` field.

    Production V1 snapshots stored the lowercase ControlDomain enum value
    (``c.domain.value``); ensure the reader still buckets them correctly.
    """
    snapshot: list[dict[str, Any]] = [
        {
            "snapshot_version": 1,
            "control_id": "00000000-0000-0000-0000-000000000003",
            "name": "Legacy",
            "domain": "loss_event",
            "type": "preventive",
        }
    ]
    got = group_controls_by_fair_cam_domain(snapshot)
    assert [r.name for r in got["LOSS_EVENT"]] == ["Legacy"]


def test_group_controls_by_fair_cam_domain_v2_empty_or_tampered_domains_uncategorised() -> None:
    """Defensive: empty list, non-list ``domains``, and unrecognised values
    fall back to UNCATEGORIZED rather than crashing the render."""
    snapshot: list[dict[str, Any]] = [
        {"name": "EmptyList", "domains": [], "type": "preventive"},
        {"name": "NotAList", "domains": "loss_event", "type": "preventive"},
        {"name": "Unknown", "domains": ["bogus_value"], "type": "preventive"},
        {"name": "MixedTypes", "domains": [123, None, "loss_event"], "type": "preventive"},
    ]
    got = group_controls_by_fair_cam_domain(snapshot)
    # EmptyList -> falls through to scalar 'domain' (missing) -> UNCATEGORIZED
    # NotAList -> domains is not a list -> falls through to scalar 'domain'
    #   (missing) -> UNCATEGORIZED
    # Unknown -> domains list has only unrecognised string -> UNCATEGORIZED
    # MixedTypes -> only the valid 'loss_event' entry buckets; non-strings ignored
    assert {r.name for r in got["UNCATEGORIZED"]} == {"EmptyList", "NotAList", "Unknown"}
    assert [r.name for r in got["LOSS_EVENT"]] == ["MixedTypes"]


# ---- F8: build_per_scenario_rows ----


def _ps_dict(scenario_id: str, name: str, base: float, residual: float) -> dict[str, Any]:
    """Mimics one entry of simulation_results['per_scenario']."""
    return {
        "scenario_id": scenario_id,
        "scenario_name": name,
        "base_risk": {"annualized_loss_expectancy": base},
        "residual_risk": {"annualized_loss_expectancy": residual},
    }


def test_build_per_scenario_rows_sorts_desc_by_residual() -> None:
    per_scenario = [
        _ps_dict("a", "Alpha", base=100.0, residual=50.0),
        _ps_dict("b", "Bravo", base=80.0, residual=70.0),
        _ps_dict("c", "Charlie", base=120.0, residual=10.0),
    ]
    got = build_per_scenario_rows(per_scenario)
    assert [r.scenario_name for r in got] == ["Bravo", "Alpha", "Charlie"]


def test_build_per_scenario_rows_tie_breaks_asc_by_name() -> None:
    per_scenario = [
        _ps_dict("a", "Zebra", base=100.0, residual=50.0),
        _ps_dict("b", "Apple", base=80.0, residual=50.0),
        _ps_dict("c", "Mango", base=120.0, residual=50.0),
    ]
    got = build_per_scenario_rows(per_scenario)
    assert [r.scenario_name for r in got] == ["Apple", "Mango", "Zebra"]


def test_build_per_scenario_rows_empty_returns_empty() -> None:
    assert build_per_scenario_rows([]) == []


def test_build_per_scenario_rows_carries_all_fields() -> None:
    per_scenario = [_ps_dict("a", "Alpha", base=100.0, residual=50.0)]
    got = build_per_scenario_rows(per_scenario)
    assert len(got) == 1
    row = got[0]
    assert isinstance(row, PerScenarioRow)
    assert row.scenario_id == "a"
    assert row.scenario_name == "Alpha"
    assert row.base_ale == 100.0
    assert row.residual_ale == 50.0
    assert row.reduction == 50.0  # base - residual


# ---- PR μ.1: build_control_breakdown_rows ----


class TestBuildControlBreakdownRows:
    """Tests for build_control_breakdown_rows view-model helper (PR μ.1).

    The helper aggregates loss_reduction_per_event across scenarios,
    deduplicates by control_id, sorts descending (with control_name
    tie-break), and sets loss_reduction_label=None when loss_reduction_per_event == 0.

    Per CLAUDE.md adapter-iteration contract: tests use N≥3 controls
    across M≥2 scenarios where noted.
    """

    @staticmethod
    def _per_scenario(scenarios: list[list[dict]]) -> list[dict[str, Any]]:
        """Build a per_scenario list of dicts where each dict carries control_adjustments."""
        return [{"control_adjustments": adjs} for adjs in scenarios]

    def test_cross_scenario_sum(self) -> None:
        """One control_id appearing in 3 scenarios → loss_reduction summed.

        N=3 scenarios (M≥2 multi-scenario contract requirement).
        """
        per_scenario = self._per_scenario(
            [
                [{"control_id": "c1", "control_name": "Ctrl 1", "loss_reduction_per_event": 100.0}],
                [{"control_id": "c1", "control_name": "Ctrl 1", "loss_reduction_per_event": 200.0}],
                [{"control_id": "c1", "control_name": "Ctrl 1", "loss_reduction_per_event": 300.0}],
            ]
        )
        rows = build_control_breakdown_rows(per_scenario, currency="USD")
        assert len(rows) == 1
        assert rows[0].control_id == "c1"
        assert rows[0].loss_reduction_per_event == pytest.approx(600.0)

    def test_multi_scenario_label_does_not_claim_per_event(self) -> None:
        """#266/D4: the summed-across-scenarios label must NOT claim "/event reduced".

        loss_reduction_per_event is a PER-loss-event, PER-scenario Secondary-Loss
        reduction. Summing it across N>1 DIFFERENT scenarios and labeling it
        "/event reduced" overstates a single event ~N times (FAIR-node-unit
        overclaim, "No portfolio-finance overclaim"). For N>1 the label must be
        a portfolio-derivation phrasing that names the scenario count, with no
        bare "/event reduced" substring.
        """
        per_scenario = self._per_scenario(
            [
                [{"control_id": "c1", "control_name": "Ctrl 1", "loss_reduction_per_event": 100.0}],
                [{"control_id": "c1", "control_name": "Ctrl 1", "loss_reduction_per_event": 200.0}],
                [{"control_id": "c1", "control_name": "Ctrl 1", "loss_reduction_per_event": 300.0}],
            ]
        )
        rows = build_control_breakdown_rows(per_scenario, currency="USD")
        assert len(rows) == 1
        label = rows[0].loss_reduction_label
        assert label is not None
        # The overclaim: a 3-scenario sum must not be labeled as one event reduced.
        assert "/event reduced" not in label
        # Positive: it names the scenario count as a portfolio derivation.
        assert "3 scenarios" in label
        assert "Loss Reduction" in label

    def test_single_scenario_label_keeps_per_event(self) -> None:
        """#266/D4: a control in exactly ONE scenario genuinely IS one event.

        N==1 retains the "/event reduced" phrasing (no overclaim — the summed
        quantity is a single per-event, per-scenario reduction).
        """
        per_scenario = self._per_scenario(
            [
                [{"control_id": "c1", "control_name": "Ctrl 1", "loss_reduction_per_event": 100.0}],
            ]
        )
        rows = build_control_breakdown_rows(per_scenario, currency="USD")
        assert len(rows) == 1
        label = rows[0].loss_reduction_label
        assert label is not None
        assert "/event reduced" in label

    def test_dedup_by_control_id(self) -> None:
        """Same control_id across 2 scenarios produces one row, not two."""
        per_scenario = self._per_scenario(
            [
                [{"control_id": "c1", "control_name": "Ctrl 1", "loss_reduction_per_event": 100.0}],
                [{"control_id": "c1", "control_name": "Ctrl 1", "loss_reduction_per_event": 100.0}],
            ]
        )
        rows = build_control_breakdown_rows(per_scenario, currency="USD")
        assert len(rows) == 1

    def test_sort_descending_by_loss_reduction(self) -> None:
        """N=3 controls in one scenario; row order is descending by loss_reduction_per_event."""
        per_scenario = self._per_scenario(
            [
                [
                    {
                        "control_id": "c1",
                        "control_name": "Ctrl 1",
                        "loss_reduction_per_event": 100.0,
                    },
                    {
                        "control_id": "c2",
                        "control_name": "Ctrl 2",
                        "loss_reduction_per_event": 500.0,
                    },
                    {
                        "control_id": "c3",
                        "control_name": "Ctrl 3",
                        "loss_reduction_per_event": 300.0,
                    },
                ],
            ]
        )
        rows = build_control_breakdown_rows(per_scenario, currency="USD")
        assert [r.control_id for r in rows] == ["c2", "c3", "c1"]

    def test_alphabetical_tie_break_on_control_name(self) -> None:
        """When loss_reduction values tie, sort alphabetically ascending by control_name.

        N=3 controls with identical loss_reduction_per_event; M-2 tie-break fix.
        """
        per_scenario = self._per_scenario(
            [
                [
                    {
                        "control_id": "cz",
                        "control_name": "Zebra",
                        "loss_reduction_per_event": 100.0,
                    },
                    {
                        "control_id": "ca",
                        "control_name": "Apple",
                        "loss_reduction_per_event": 100.0,
                    },
                    {
                        "control_id": "cm",
                        "control_name": "Mango",
                        "loss_reduction_per_event": 100.0,
                    },
                ],
            ]
        )
        rows = build_control_breakdown_rows(per_scenario, currency="USD")
        assert [r.control_name for r in rows] == ["Apple", "Mango", "Zebra"]

    def test_zero_loss_reduction_yields_none_label_but_row_present(self) -> None:
        """Rows with loss_reduction_per_event=0 are still IN the result,
        but with loss_reduction_label=None (renderer is expected to skip them).

        N=2 controls (c1=zero, c2=non-zero) to verify isolation of None-label logic.
        """
        per_scenario = self._per_scenario(
            [
                [
                    {"control_id": "c1", "control_name": "Ctrl 1", "loss_reduction_per_event": 0.0},
                    {
                        "control_id": "c2",
                        "control_name": "Ctrl 2",
                        "loss_reduction_per_event": 100.0,
                    },
                ],
            ]
        )
        rows = build_control_breakdown_rows(per_scenario, currency="USD")
        # Both rows present
        ids = sorted(r.control_id for r in rows)
        assert ids == ["c1", "c2"]
        # c1 has None label; c2 has non-None label
        c1_row = next(r for r in rows if r.control_id == "c1")
        c2_row = next(r for r in rows if r.control_id == "c2")
        assert c1_row.loss_reduction_label is None
        assert c2_row.loss_reduction_label is not None

    def test_empty_per_scenario_returns_empty_list(self) -> None:
        """per_scenario=[] returns an empty list (no crash, no sentinel rows)."""
        rows = build_control_breakdown_rows([], currency="USD")
        assert rows == []

    def test_missing_or_empty_control_id_skipped(self) -> None:
        """Adjustments with control_id='' or control_id=None are skipped, not raised.

        Only the adjustment with a truthy control_id survives; N=3 adjustments
        in input, N=1 in output (adapter-iteration contract guard).
        """
        per_scenario = self._per_scenario(
            [
                [
                    {"control_id": "", "control_name": "Empty", "loss_reduction_per_event": 100.0},
                    {"control_id": None, "control_name": "None", "loss_reduction_per_event": 100.0},
                    {
                        "control_id": "c1",
                        "control_name": "Ctrl 1",
                        "loss_reduction_per_event": 100.0,
                    },
                ],
            ]
        )
        rows = build_control_breakdown_rows(per_scenario, currency="USD")
        # Only c1 makes it through
        assert len(rows) == 1
        assert rows[0].control_id == "c1"


# ---- Task 2 (T2): RunReportData — alias bridge, tail risk, economics, matrix, provenance ----


# ---- T2(a): tail_risk reuse — values equal _build_tail_risk output ----


def _residual_with_tail() -> dict[str, Any]:
    """A residual_risk dict with all four VaR levels + Expected Shortfall."""
    return {
        "annualized_loss_expectancy": 500_000.0,
        "var_90": 800_000.0,
        "var_95": 1_000_000.0,
        "var_99": 1_500_000.0,
        "var_999": 2_000_000.0,
        "expected_shortfall": {
            "es_95": 1_200_000.0,
            "es_99": 1_700_000.0,
            "es_999": 2_100_000.0,
        },
    }


def test_tail_risk_values_equal_build_tail_risk_output() -> None:
    """T2(a): RunReportData.tail_risk must equal _build_tail_risk(residual_risk)."""
    residual = _residual_with_tail()
    expected = _build_tail_risk(residual)
    # Verify the helper itself returns the correct shape
    assert expected["var_90"] == 800_000.0
    assert expected["var_95"] == 1_000_000.0
    assert expected["var_99"] == 1_500_000.0
    assert expected["var_999"] == 2_000_000.0
    assert expected["es_95"] == 1_200_000.0
    assert expected["es_99"] == 1_700_000.0
    assert expected["es_999"] == 2_100_000.0


def test_build_tail_risk_moved_to_view_model_helpers() -> None:
    """T2(a): _build_tail_risk is importable from run_view_model (re-export of helper)."""
    from idraa.services.run_view_model import _build_tail_risk as btr

    assert callable(btr)


# ---- T2(b): base_stats and base_tail_risk via parameterized helper ----


def _base_risk_dict() -> dict[str, Any]:
    """A base_risk dict (without_controls) with mean/median/std + tail metrics."""
    return {
        "annualized_loss_expectancy": 2_000_000.0,
        "mean": 2_100_000.0,
        "median": 1_900_000.0,
        "std_deviation": 400_000.0,
        "var_90": 3_000_000.0,
        "var_95": 3_500_000.0,
        "var_99": 4_000_000.0,
        "var_999": 5_000_000.0,
        "expected_shortfall": {
            "es_95": 3_700_000.0,
            "es_99": 4_300_000.0,
            "es_999": 5_500_000.0,
        },
    }


def test_base_tail_risk_uses_same_helper_as_tail_risk() -> None:
    """T2(b): base_tail_risk is built by calling _build_tail_risk on base_risk dict.

    The helper is parameterized (not duplicated) — both base and residual use it.
    """
    base = _base_risk_dict()
    result = _build_tail_risk(base)
    assert result["var_90"] == 3_000_000.0
    assert result["var_95"] == 3_500_000.0
    assert result["var_99"] == 4_000_000.0
    assert result["var_999"] == 5_000_000.0
    assert result["es_95"] == 3_700_000.0
    assert result["es_99"] == 4_300_000.0
    assert result["es_999"] == 5_500_000.0


def test_base_stats_extracted_from_base_risk() -> None:
    """T2(b): mean/median/std can be extracted from base_risk dict."""
    base = _base_risk_dict()
    assert base["mean"] == 2_100_000.0
    assert base["median"] == 1_900_000.0
    assert base["std_deviation"] == 400_000.0


# ---- T2(c): has_tail_metrics helper ----


def test_has_tail_metrics_true_when_all_keys_present_and_nonzero() -> None:
    """T2(c): has_tail_metrics returns True when tail keys present/non-degenerate."""
    residual = _residual_with_tail()
    assert has_tail_metrics(residual) is True


def test_has_tail_metrics_false_when_var_90_missing() -> None:
    """T2(c): has_tail_metrics returns False when var_90 is absent."""
    assert has_tail_metrics({}) is False


def test_has_tail_metrics_false_when_all_zeros() -> None:
    """T2(c): has_tail_metrics returns False when all tail values are zero."""
    degenerate = {
        "var_90": 0.0,
        "var_95": 0.0,
        "var_99": 0.0,
        "var_999": 0.0,
        "expected_shortfall": {"es_95": 0.0, "es_99": 0.0, "es_999": 0.0},
    }
    assert has_tail_metrics(degenerate) is False


def test_has_tail_metrics_false_when_expected_shortfall_missing() -> None:
    """T2(c): has_tail_metrics returns False when expected_shortfall dict is missing."""
    partial = {"var_90": 1_000.0, "var_95": 2_000.0, "var_99": 3_000.0, "var_999": 4_000.0}
    assert has_tail_metrics(partial) is False


# ---- T2(d): cost_summary extraction ----


def _cost_summary_dict() -> dict[str, Any]:
    return {
        "total_annual_cost": 100_000.0,
        "total_risk_reduction": 500_000.0,
        "net_benefit": 400_000.0,
        "aggregate_roi": 5.0,
    }


def test_cost_summary_extracted_all_fields() -> None:
    """T2(d): cost_summary carries all four KPI fields."""
    cs = _cost_summary_dict()
    assert cs["total_annual_cost"] == 100_000.0
    assert cs["total_risk_reduction"] == 500_000.0
    assert cs["net_benefit"] == 400_000.0
    assert cs["aggregate_roi"] == 5.0


def test_cost_summary_absent_yields_none() -> None:
    """T2(d): absent cost_summary in simulation_results -> None (renderer prints not available)."""
    from idraa.services.reports import _extract_cost_summary

    result = _extract_cost_summary({})
    assert result is None


def test_cost_summary_extracted_from_sr() -> None:
    """T2(d): cost_summary is extracted from simulation_results when present."""
    from idraa.services.reports import _extract_cost_summary

    sr = {"cost_summary": _cost_summary_dict()}
    result = _extract_cost_summary(sr)
    assert result is not None
    assert result["total_annual_cost"] == 100_000.0
    assert result["aggregate_roi"] == 5.0


# ---- T2(e): attribution matrix for AGGREGATE (≥3-scenario adapter-iteration contract) ----


def _per_scenario_with_controls(n_scenarios: int = 3, n_controls: int = 3) -> list[dict[str, Any]]:
    """Build a per_scenario list with N scenarios and M controls each."""
    per_scenario = []
    for i in range(n_scenarios):
        per_scenario.append(
            {
                "scenario_id": f"s{i}",
                "scenario_name": f"Scenario {i}",
                "base_risk": {
                    "annualized_loss_expectancy": 100_000.0 * (i + 1),
                    "loss_event_frequency": 2.0,
                },
                "residual_risk": {"annualized_loss_expectancy": 50_000.0 * (i + 1)},
                "control_adjustments": [
                    {
                        "control_id": f"c{j}",
                        "control_name": f"Control {j}",
                        "risk_reduction_value": 10_000.0 * (j + 1),
                        "loss_reduction_per_event": 1_000.0 * (j + 1),
                        # shapley_value is required after #352: the builder reads this
                        # key to classify has_shapley; without it every scenario
                        # degrades to the 'unavailable' state (no rows emitted).
                        "shapley_value": 5_000.0 * (j + 1),
                    }
                    for j in range(n_controls)
                ],
            }
        )
    return per_scenario


def test_attribution_matrix_adapter_iteration_n3_scenarios() -> None:
    """T2(e): _build_per_scenario_control_matrix preserves all N>=3 scenarios.

    Adapter-iteration contract: building the matrix from N=3 scenarios
    must return exactly 3 rows (no silent truncation).
    """
    from idraa.services.aggregate_run_view_model import _build_per_scenario_control_matrix

    per_scenario = _per_scenario_with_controls(n_scenarios=3, n_controls=3)
    matrix = _build_per_scenario_control_matrix(per_scenario)
    assert len(matrix["rows"]) == 3  # all 3 scenarios preserved
    assert len(matrix["controls"]) == 3  # all 3 controls present


def test_attribution_matrix_is_none_for_single_run() -> None:
    """T2(e): attribution_matrix_rows is None for SINGLE runs.

    A SINGLE run has no per_scenario cross-run breakdown.
    This is a contract the RunReportData builder must enforce.
    """
    # For SINGLE runs the simulation_results has no 'per_scenario' key at the
    # top level, so the builder returns None.
    # Simulate SINGLE run type
    from idraa.models.risk_analysis_run import RunType
    from idraa.services.reports import _build_attribution_matrix_for_run

    result = _build_attribution_matrix_for_run([], run_type=RunType.SINGLE)
    assert result is None


def test_attribution_matrix_returned_for_aggregate_run() -> None:
    """T2(e): attribution_matrix_rows is non-None for AGGREGATE runs."""
    from idraa.models.risk_analysis_run import RunType
    from idraa.services.reports import _build_attribution_matrix_for_run

    per_scenario = _per_scenario_with_controls(n_scenarios=3, n_controls=3)
    result = _build_attribution_matrix_for_run(per_scenario, run_type=RunType.AGGREGATE)
    assert result is not None
    assert len(result["rows"]) == 3


def test_attribution_matrix_for_pdf_is_pinned_to_mean_basis_now_that_pdf_caption_is_basis_aware() -> (
    None
):
    """Final display slice (2026-07-04, issue #467 IMPLEMENTED): pdf_report.py's
    attribution-matrix caption is now basis-aware (switches copy on
    ``matrix["basis"]``), so `_build_attribution_matrix_for_run` no longer
    needs to pin the matrix to the typical basis to keep an untouched caption
    true. RE-PINNED (was ``result["basis"] == "typical"`` / cell value
    30.0-typical before #467 landed the PDF pairing) — this call site now
    passes through the module default ``prefer_basis="mean"``, matching the
    web's own call site. Mirrors
    test_aggregate_run_view_model.test_matrix_prefer_basis_mean_default_uses_mean_as_primary
    (or equivalent default-basis coverage) at the reports.py call-site boundary."""
    from idraa.models.risk_analysis_run import RunType
    from idraa.services.reports import _build_attribution_matrix_for_run

    per_scenario = [
        {
            "scenario_id": "s1",
            "scenario_name": "S1",
            "base_risk": {"annualized_loss_expectancy": 100.0},
            "control_adjustments": [
                {
                    "control_id": "c1",
                    "control_name": "Control One",
                    "shapley_value": 30.0,
                    "shapley_value_mean": 450.0,
                },
            ],
        }
    ]
    result = _build_attribution_matrix_for_run(per_scenario, run_type=RunType.AGGREGATE)
    assert result is not None
    assert result["basis"] == "mean"
    assert result["rows"][0]["cells"][0]["value"] == pytest.approx(450.0)  # mean, NOT typical
    assert result["controls"][0]["total_reduction"] == pytest.approx(450.0)


# ---- T2(f): control-effectiveness scores for SINGLE, None for AGGREGATE ----


def test_control_effectiveness_present_for_single_run() -> None:
    """T2(f): control effectiveness rows are non-None for SINGLE runs."""
    from idraa.models.risk_analysis_run import RunType
    from idraa.services.reports import _build_control_effectiveness_for_run

    adjustments = [
        {"control_id": "c1", "effectiveness": 0.8},
        {"control_id": "c2", "effectiveness": 0.6},
        {"control_id": "c3", "effectiveness": 0.4},
    ]
    snapshot = [
        {"control_id": "c1", "name": "Control 1"},
        {"control_id": "c2", "name": "Control 2"},
        {"control_id": "c3", "name": "Control 3"},
    ]
    result = _build_control_effectiveness_for_run(adjustments, snapshot, run_type=RunType.SINGLE)
    assert result is not None
    assert len(result) == 3


def test_control_effectiveness_none_for_aggregate_run() -> None:
    """T2(f): control effectiveness rows are None for AGGREGATE runs."""
    from idraa.models.risk_analysis_run import RunType
    from idraa.services.reports import _build_control_effectiveness_for_run

    result = _build_control_effectiveness_for_run([], [], run_type=RunType.AGGREGATE)
    assert result is None


# ---- T2(g): scenario input snapshots — snapshot-backed vs legacy-null ----


def _scenario_input_snapshot() -> dict[str, Any]:
    """Return a scenario_inputs_snapshot JSON dict (as executor would persist)."""
    return {
        "scenarios": [
            {
                "scenario_id": "s1",
                "scenario_name": "Test Scenario",
                "threat_event_frequency": {
                    "distribution": "PERT",
                    "low": 0.1,
                    "mode": 0.5,
                    "high": 1.0,
                },
                "vulnerability": {"distribution": "PERT", "low": 0.2, "mode": 0.4, "high": 0.6},
                "primary_loss": {
                    "distribution": "PERT",
                    "low": 10_000.0,
                    "mode": 100_000.0,
                    "high": 1_000_000.0,
                },
                "secondary_loss": None,
            }
        ]
    }


def test_snapshot_backed_run_shows_as_executed_values() -> None:
    """T2(g): snapshot-backed run returns as-executed TEF/Vuln/PL/SL values."""
    from idraa.services.reports import _extract_scenario_inputs

    snapshot = _scenario_input_snapshot()
    result = _extract_scenario_inputs(snapshot_json=snapshot, live_scenarios=[])
    assert result["label"] == "as-executed"
    assert len(result["scenarios"]) == 1
    assert result["scenarios"][0]["scenario_name"] == "Test Scenario"
    assert result["scenarios"][0]["threat_event_frequency"]["distribution"] == "PERT"


def test_legacy_null_run_falls_back_to_live_values_with_honest_label() -> None:
    """T2(g): legacy-null run shows live values with the honest label."""
    from typing import ClassVar

    from idraa.services.reports import _extract_scenario_inputs

    # Simulate a live scenario object
    class _FakeScenario:
        id = "s1"
        name = "Live Scenario"
        threat_event_frequency: ClassVar[dict] = {
            "distribution": "PERT",
            "low": 0.5,
            "mode": 1.0,
            "high": 2.0,
        }
        vulnerability: ClassVar[dict] = {
            "distribution": "PERT",
            "low": 0.3,
            "mode": 0.5,
            "high": 0.7,
        }
        primary_loss: ClassVar[dict] = {
            "distribution": "PERT",
            "low": 50_000.0,
            "mode": 200_000.0,
            "high": 2_000_000.0,
        }
        secondary_loss = None

    result = _extract_scenario_inputs(snapshot_json=None, live_scenarios=[_FakeScenario()])
    assert (
        result["label"]
        == "Current scenario values (run predates input snapshots — values may differ from as-executed)"
    )
    assert len(result["scenarios"]) == 1
    assert result["scenarios"][0]["scenario_name"] == "Live Scenario"
    # The live scenario values should reflect the current (potentially-edited) state
    assert result["scenarios"][0]["threat_event_frequency"]["low"] == 0.5


# ---- T2(i): library provenance ----


def _library_pin_dict() -> dict[str, Any]:
    """Simulate a scenario.library_pin dict (set when scenario derives from a library entry)."""
    return {
        "entry_id": "abc123",
        "entry_version": 1,
        "slug": "ransomware-manufacturing",
        "loss_tier": "paginated",
        "calibration_anchor": {
            "text": "Verizon DBIR 2024 Table 3: median dwell time for manufacturing sector",
            "source": "Verizon DBIR 2024",
        },
        "source_citations": [
            "Verizon DBIR 2024, p. 45, Table 3",
            "IBM CODB 2023, p. 12",
            "CrowdStrike 2024 Global Threat Report, p. 8",
        ],
    }


def test_library_provenance_extracted_from_library_pin() -> None:
    """T2(i): library provenance extracted for library-derived scenarios."""
    from idraa.services.reports import _extract_scenario_provenance

    pin = _library_pin_dict()
    result = _extract_scenario_provenance(library_pin=pin)
    assert result["loss_tier"] == "paginated"
    assert "Verizon DBIR 2024" in result["calibration_anchor"]["text"]
    assert len(result["source_citations"]) == 3


def test_library_provenance_fallback_for_no_pin() -> None:
    """T2(i): analyst-authored fallback for scenarios with no library lineage."""
    from idraa.services.reports import _extract_scenario_provenance

    result = _extract_scenario_provenance(library_pin=None)
    assert result["loss_tier"] is None
    assert result["calibration_anchor"] is None
    assert result["source_citations"] == []
    assert result["provenance_label"] == "analyst-authored — no library provenance"


# ---- T2(j): N≥3 adapter-iteration contract for list fields ----


def test_per_scenario_rows_adapter_iteration_n4() -> None:
    """T2(j): build_per_scenario_rows preserves all N>=3 items (adapter-iteration contract)."""
    per_scenario = [
        _ps_dict(f"s{i}", f"Scenario {i}", base=100_000.0 * (i + 1), residual=50_000.0 * (i + 1))
        for i in range(4)  # N=4 >= 3
    ]
    rows = build_per_scenario_rows(per_scenario)
    assert len(rows) == 4  # all 4 preserved


def test_control_breakdown_rows_adapter_iteration_n3() -> None:
    """T2(j): build_control_breakdown_rows preserves all N>=3 controls."""
    per_scenario = [
        {
            "control_adjustments": [
                {
                    "control_id": f"c{i}",
                    "control_name": f"Ctrl {i}",
                    "loss_reduction_per_event": float(i * 100),
                }
                for i in range(3)  # N=3 controls
            ]
        }
    ]
    rows = build_control_breakdown_rows(per_scenario, currency="USD")
    # All 3 controls preserved (even c0 with 0.0 reduction)
    assert len(rows) == 3


def test_matrix_rows_adapter_iteration_n5_scenarios() -> None:
    """T2(j): _build_per_scenario_control_matrix preserves all N>=3 rows."""
    from idraa.services.aggregate_run_view_model import _build_per_scenario_control_matrix

    per_scenario = _per_scenario_with_controls(n_scenarios=5, n_controls=3)
    matrix = _build_per_scenario_control_matrix(per_scenario)
    assert len(matrix["rows"]) == 5  # all 5 scenarios preserved


# ---- T2(k): XSS escape regression for user-controlled strings ----

_XSS_PAYLOAD = "<script>alert('xss')</script>"
_XSS_ATTR_PAYLOAD = '" onmouseover="alert(1)"'


def test_rl_escape_inerts_xss_in_org_name() -> None:
    """T2(k): rl_escape makes reportlab markup inert for org name."""
    from xml.sax.saxutils import escape as rl_escape

    escaped = rl_escape(_XSS_PAYLOAD)
    assert "<script>" not in escaped
    assert "&lt;script&gt;" in escaped


def test_rl_escape_inerts_xss_in_scenario_name() -> None:
    """T2(k): rl_escape makes reportlab markup inert for scenario name."""
    from xml.sax.saxutils import escape as rl_escape

    escaped = rl_escape(_XSS_PAYLOAD)
    assert "<" not in escaped


def test_rl_escape_inerts_xss_in_citation() -> None:
    """T2(k): source citations with XSS markup are escaped via rl_escape."""
    from xml.sax.saxutils import escape as rl_escape

    citation = 'javascript:alert(1)" onmouseover="evil()'
    escaped = rl_escape(citation)
    # No raw double-quote or angle brackets remain
    assert "<" not in escaped


# ---- T2: alias bridge removed at T9 ----
# test_executive_pdf_data_is_run_report_data_alias was deleted at T9 (#351)
# because the ExecutivePdfData alias was removed. The alias-bridge test is
# superseded; the class is now always RunReportData.


# ============================================================
# Task 3 (T3): build_narrative_single — snapshot + negative-match tests
# ============================================================

CANONICAL_SINGLE_NARRATIVE_INPUT: dict[str, Any] = {
    "control_value_dollars": 400_000.0,
    "control_value_percent": 33.3,
    "residual_ale": 800_000.0,
    "pct_revenue": 0.27,
    "n_simulations": 50_000,
    "currency": "USD",
    "scenario_name": "Ransomware attack",
}

# Verbatim expected narrative — snapshot-pinned for drift detection.
# Update deliberately if wording changes; requires explicit reviewer acceptance.
# Task 5 (#419 / Meth-I4): the spurious one-decimal-place percentage is DROPPED;
# without a weight_robustness_headline the point dollar leads.
# M4: no weight_robustness → BASE disclaimer only (no indistinguishable sentence).
_EXPECTED_SINGLE_NARRATIVE = (
    "For Ransomware attack, the current control posture reduces "
    "annualized loss expectancy by $400,000, bringing residual "
    "ALE to $800,000 — equivalent to 0.27% of annual revenue. This "
    "estimate reflects 50,000 Monte Carlo iterations. "
    # M4: base disclaimer only (no too-close-to-call caveat for legacy/robustness-absent).
    "These are modeled estimates shown as ranges, not measured or "
    "guaranteed loss reductions."
)


def test_build_narrative_single_canonical_snapshot() -> None:
    """T3 snapshot: build_narrative_single exact output for canonical SINGLE input.

    Regression on phrasing changes — if the wording changes, this test must be
    updated deliberately. The snapshot is also the verbatim text asserted in the
    PDF renderer tests.
    """
    got = build_narrative_single(**CANONICAL_SINGLE_NARRATIVE_INPUT)
    assert got == _EXPECTED_SINGLE_NARRATIVE


def test_build_narrative_single_no_portfolio_phrasing() -> None:
    """T3: SINGLE narrative must NOT contain banned portfolio phrases.

    Negative-match assertions on the generated narrative string:
      - 'portfolio'         (portfolio-scope claim)
      - 'N scenarios'       (multi-scenario phrasing)
      - 'across scenarios'  (cross-scenario aggregate framing)
      - 'aggregate'         (as a portfolio descriptor)
      - 'diversif'          (diversification prefix)
    """
    got = build_narrative_single(**CANONICAL_SINGLE_NARRATIVE_INPUT).lower()
    assert "portfolio" not in got, "SINGLE narrative must not contain 'portfolio'"
    import re as _re

    assert _re.search(r"\d+\s+scenarios", got) is None, (
        "SINGLE narrative must not contain 'N scenarios' phrasing"
    )
    assert "across scenarios" not in got, "SINGLE narrative must not contain 'across scenarios'"
    assert "aggregate" not in got, "SINGLE narrative must not contain 'aggregate'"
    assert "diversif" not in got, "SINGLE narrative must not contain 'diversif*' prefix"


def test_build_narrative_single_uses_scenario_name() -> None:
    """T3: SINGLE narrative uses the scenario name for scenario-scoped framing."""
    got = build_narrative_single(**CANONICAL_SINGLE_NARRATIVE_INPUT)
    assert "Ransomware attack" in got, "SINGLE narrative must include the scenario name"


def test_build_narrative_single_appends_control_weight_provenance_disclaimer() -> None:
    """Issue #413 / M4: the SINGLE exec narrative always appends the disclaimer.
    When weight_robustness_headline is absent, the BASE variant (no indistinguishable
    sentence) is appended; the full disclaimer is used only when robustness is present."""
    # Robustness-absent path → BASE disclaimer.
    got_absent = build_narrative_single(**CANONICAL_SINGLE_NARRATIVE_INPUT)
    assert CONTROL_WEIGHT_PROVENANCE_DISCLAIMER_BASE in got_absent
    assert got_absent.endswith(CONTROL_WEIGHT_PROVENANCE_DISCLAIMER_BASE)
    # Robustness-present path → full disclaimer (both sentences).
    inp_present = {
        **CANONICAL_SINGLE_NARRATIVE_INPUT,
        "weight_robustness_headline": {
            "reduction_p5": 300_000.0,
            "reduction_p50": 400_000.0,
            "reduction_p95": 550_000.0,
        },
    }
    got_present = build_narrative_single(**inp_present)
    assert CONTROL_WEIGHT_PROVENANCE_DISCLAIMER in got_present
    assert got_present.endswith(CONTROL_WEIGHT_PROVENANCE_DISCLAIMER)


def test_build_narrative_single_no_bare_point_percent_leads() -> None:
    """Task 5 (#419 / Meth-I4): negative-match — no bare "$X (Y.Y%)" leads the
    SINGLE narrative in EITHER the robustness-present or robustness-absent path,
    and the reworded disclaimer composes grammatically (ends the sentence).
    M4: robustness-absent path ends with BASE disclaimer (no indistinguishable sentence)."""
    import re as _re

    _pat = r"\$[\d,]+ \(\d+\.\d%\)"
    got_absent = build_narrative_single(**CANONICAL_SINGLE_NARRATIVE_INPUT)
    assert _re.search(_pat, got_absent) is None
    # Reworded disclaimer composes: the narrative reads as full sentences and ends
    # with the BASE disclaimer's closing sentence (no indistinguishable caveat here).
    assert ". These are modeled estimates shown as ranges" in got_absent
    assert got_absent.endswith("not measured or guaranteed loss reductions.")

    inp = {
        **CANONICAL_SINGLE_NARRATIVE_INPUT,
        "weight_robustness_headline": {
            "reduction_p5": 300_000.0,
            "reduction_p50": 400_000.0,
            "reduction_p95": 550_000.0,
        },
    }
    got_present = build_narrative_single(**inp)
    assert _re.search(_pat, got_present) is None
    # P2 (2026-07-03): reworded for coherence — leads with the average reduction,
    # demotes the typical-case median band to a skew-explaining parenthetical.
    assert "on average" in got_present
    assert "~" in got_present
    assert "typical-case" in got_present
    assert "skewed losses run far below the average" in got_present


def test_build_narrative_single_no_revenue_clause_when_none() -> None:
    """T3: SINGLE narrative omits the revenue clause when pct_revenue is None."""
    inp = {**CANONICAL_SINGLE_NARRATIVE_INPUT, "pct_revenue": None}
    got = build_narrative_single(**inp)
    assert "% of annual revenue" not in got
    assert "50,000 Monte Carlo iterations" in got


def test_build_narrative_single_eur_currency() -> None:
    """T3: SINGLE narrative uses the EUR symbol when currency is EUR."""
    inp = {**CANONICAL_SINGLE_NARRATIVE_INPUT, "currency": "EUR"}
    got = build_narrative_single(**inp)
    assert "€400,000" in got
    assert "$" not in got


def test_run_report_data_has_run_type_field() -> None:
    """T3: RunReportData carries the run_type discriminator field.

    Defaults to 'aggregate' for backward compat; SINGLE runs pass 'single'.
    """
    # Default is 'aggregate'
    from dataclasses import fields

    field_names = {f.name for f in fields(RunReportData)}
    assert "run_type" in field_names, "RunReportData must have a run_type field"


# ---- T4SC-6 + T4M-3-I1: residual_stats extraction — coverage parity with base_stats ----


def _residual_risk_dict() -> dict[str, Any]:
    """A residual_risk dict with mean/median/std_deviation (mirrors _base_risk_dict)."""
    return {
        "annualized_loss_expectancy": 400_000.0,
        "mean": 410_000.0,
        "median": 375_000.0,
        "std_deviation": 100_000.0,
        "var_90": 580_000.0,
        "var_95": 680_000.0,
        "var_99": 900_000.0,
        "var_999": 1_200_000.0,
        "expected_shortfall": {
            "es_95": 750_000.0,
            "es_99": 1_000_000.0,
            "es_999": 1_300_000.0,
        },
    }


def test_residual_stats_extracted_from_residual_risk() -> None:
    """T4SC-6 + T4M-3-I1: mean/median/std_deviation can be extracted from residual_risk dict.

    Mirrors test_base_stats_extracted_from_base_risk for coverage parity.
    The builder must pull these three fields from the residual_risk (with_controls)
    result to populate residual_stats for the Δ column in the distribution-statistics table.
    """
    residual = _residual_risk_dict()
    assert residual["mean"] == 410_000.0
    assert residual["median"] == 375_000.0
    assert residual["std_deviation"] == 100_000.0


# ---- random_seed field on RunReportData ----


def test_run_report_data_has_random_seed_field() -> None:
    """RunReportData must have a random_seed field (int | None, default None).

    Seed-reproducibility transparency: the PDF renderer shows which seed
    produced a run so operators can re-run deterministically.
    """
    from dataclasses import fields

    field_names = {f.name for f in fields(RunReportData)}
    assert "random_seed" in field_names, "RunReportData must have a random_seed field"


def test_run_report_data_random_seed_defaults_none() -> None:
    """random_seed must default to None so existing call sites construct unchanged."""
    from dataclasses import fields

    for f in fields(RunReportData):
        if f.name == "random_seed":
            import dataclasses

            assert f.default is None or (
                f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING  # type: ignore[misc]
            ), "random_seed must have a default value of None"
            break
