"""Pure-function tests for dashboard_view_model helpers (omicron-1 F8-F11).

No DB, no HTTP, no fair_cam imports. Mirrors tests/unit pattern for
run_view_model + aggregate_run_view_model.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pytest

from idraa.models.enums import ControlDomain
from idraa.models.risk_analysis_run import RunStatus, RunType
from idraa.services.coverage import CoverageResult
from idraa.services.dashboard_view_model import (
    TopScenarioRow,
    _interpolate_exceedance_probability,
    build_budget_card,
    build_control_coverage,
    build_posture_verdict,
    build_posture_verdict_from_lec,
    build_recent_run_row,
    build_residual_ale_card,
    build_scenario_coverage,
    build_top_scenarios,
    display_name_fallback,
    posture_appetite_detail,
)


@dataclass
class _StubRun:
    """Duck-types a RiskAnalysisRun for view-model tests."""

    id: uuid.UUID
    name: str | None
    run_type: RunType
    status: RunStatus
    scenario_id: uuid.UUID | None
    aggregate_scenario_ids: list[str] | None
    created_at: dt.datetime
    simulation_results: dict[str, Any] | None


def _make_aggregate_stub(
    *,
    n_scenarios: int = 2,
    name: str | None = None,
    per_scenario: list[dict[str, Any]] | None = None,
) -> _StubRun:
    return _StubRun(
        id=uuid.uuid4(),
        name=name,
        run_type=RunType.AGGREGATE,
        status=RunStatus.COMPLETED,
        scenario_id=None,
        aggregate_scenario_ids=[str(uuid.uuid4()) for _ in range(n_scenarios)],
        created_at=dt.datetime(2026, 5, 5, 12, 30, tzinfo=dt.UTC),
        simulation_results={
            "aggregate_with_controls": {"annualized_loss_expectancy": 100000.0},
            "aggregate_without_controls": {"annualized_loss_expectancy": 500000.0},
            "control_value": {"dollars": 400000.0, "percent": 80.0},
            "per_scenario": per_scenario or [],
            "n_scenarios": n_scenarios,
            "n_simulations": 1000,
        },
    )


def _make_single_stub(
    *,
    scenario_id: uuid.UUID,
    residual_ale: float = 1.0,
    name: str | None = None,
    base_ale: float | None = None,
) -> _StubRun:
    if base_ale is None:
        base_ale = residual_ale * 2
    return _StubRun(
        id=uuid.uuid4(),
        name=name,
        run_type=RunType.SINGLE,
        status=RunStatus.COMPLETED,
        scenario_id=scenario_id,
        aggregate_scenario_ids=None,
        created_at=dt.datetime(2026, 5, 5, 12, 30, tzinfo=dt.UTC),
        simulation_results={
            "base_risk": {"annualized_loss_expectancy": base_ale},
            "residual_risk": {"annualized_loss_expectancy": residual_ale},
        },
    )


_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def test_display_name_fallback_aggregate() -> None:
    run = _make_aggregate_stub(n_scenarios=3)
    label = display_name_fallback(run)
    assert label == "Aggregate · 3 scenarios"


def test_display_name_fallback_single_with_scenario_name() -> None:
    run = _make_single_stub(scenario_id=uuid.uuid4(), residual_ale=1.0)
    label = display_name_fallback(run, scenario_name="Ransomware Q2")
    assert label == "Ransomware Q2"


def test_display_name_fallback_single_without_scenario_name() -> None:
    sid = uuid.uuid4()
    run = _make_single_stub(scenario_id=sid, residual_ale=1.0)
    label = display_name_fallback(run, scenario_name=None)
    short = run.id.hex[:8]
    assert label == f"Run {short}"


@pytest.mark.parametrize("scenario_name", ["Ransomware Q2", None])
def test_display_name_fallback_bakes_no_utc_date_string(scenario_name: str | None) -> None:
    """Issue #263: the label must NOT bake a YYYY-MM-DD date via strftime.

    Raw strftime in the view-model bypasses the format_datetime/<time
    data-localize> localizer and renders off-by-one for users west of UTC.
    The localized date is rendered separately from run.created_at in the
    template's "Created" column.
    """
    single = _make_single_stub(scenario_id=uuid.uuid4(), residual_ale=1.0)
    aggregate = _make_aggregate_stub(n_scenarios=3)
    assert _DATE_RE.search(display_name_fallback(single, scenario_name=scenario_name)) is None
    assert _DATE_RE.search(display_name_fallback(aggregate)) is None


def test_build_top_scenarios_aggregate_path_sorts_by_residual_ale_desc() -> None:
    s1, s2, s3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    agg = _make_aggregate_stub(
        per_scenario=[
            {
                "scenario_id": str(s1),
                "scenario_name": "Low",
                "base_risk": {"annualized_loss_expectancy": 100.0},
                "residual_risk": {"annualized_loss_expectancy": 50.0},
            },
            {
                "scenario_id": str(s3),
                "scenario_name": "High",
                "base_risk": {"annualized_loss_expectancy": 1000.0},
                "residual_risk": {"annualized_loss_expectancy": 500.0},
            },
            {
                "scenario_id": str(s2),
                "scenario_name": "Mid",
                "base_risk": {"annualized_loss_expectancy": 500.0},
                "residual_risk": {"annualized_loss_expectancy": 250.0},
            },
        ],
        n_scenarios=3,
    )
    rows, total = build_top_scenarios(agg, fallback_singles_by_scenario={}, scenario_names_by_id={})
    assert total == 3
    assert [r.scenario_name for r in rows] == ["High", "Mid", "Low"]
    assert isinstance(rows[0], TopScenarioRow)
    assert rows[0].source == "aggregate"
    assert rows[0].base_ale == 1000.0
    assert rows[0].residual_ale == 500.0


def test_build_top_scenarios_aggregate_path_caps_at_top_n() -> None:
    agg = _make_aggregate_stub(
        per_scenario=[
            {
                "scenario_id": str(uuid.uuid4()),
                "scenario_name": f"S{i}",
                "base_risk": {"annualized_loss_expectancy": float(1000 - i)},
                "residual_risk": {"annualized_loss_expectancy": float(500 - i)},
            }
            for i in range(7)
        ],
        n_scenarios=7,
    )
    rows, total = build_top_scenarios(
        agg, fallback_singles_by_scenario={}, scenario_names_by_id={}, top_n=5
    )
    assert len(rows) == 5
    assert total == 7


def test_build_top_scenarios_aggregate_path_returns_all_when_under_top_n() -> None:
    agg = _make_aggregate_stub(
        per_scenario=[
            {
                "scenario_id": str(uuid.uuid4()),
                "scenario_name": f"S{i}",
                "base_risk": {"annualized_loss_expectancy": float(100 - i)},
                "residual_risk": {"annualized_loss_expectancy": float(50 - i)},
            }
            for i in range(3)
        ],
        n_scenarios=3,
    )
    rows, total = build_top_scenarios(
        agg, fallback_singles_by_scenario={}, scenario_names_by_id={}, top_n=5
    )
    assert len(rows) == 3
    assert total == 3


def test_build_top_scenarios_aggregate_path_skips_rows_missing_scenario_id() -> None:
    """Defensive: malformed per_scenario row without scenario_id is skipped."""
    s1 = uuid.uuid4()
    agg = _make_aggregate_stub(
        per_scenario=[
            {
                "scenario_name": "BrokenRow",  # no scenario_id
                "base_risk": {"annualized_loss_expectancy": 1000.0},
                "residual_risk": {"annualized_loss_expectancy": 500.0},
            },
            {
                "scenario_id": str(s1),
                "scenario_name": "Good",
                "base_risk": {"annualized_loss_expectancy": 100.0},
                "residual_risk": {"annualized_loss_expectancy": 50.0},
            },
        ],
        n_scenarios=2,
    )
    rows, total = build_top_scenarios(agg, fallback_singles_by_scenario={}, scenario_names_by_id={})
    assert total == 1
    assert [r.scenario_name for r in rows] == ["Good"]


def test_build_top_scenarios_fallback_path_uses_scenario_names_by_id() -> None:
    s1, s2, s3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    fallback = {
        s1: _make_single_stub(scenario_id=s1, residual_ale=50.0, name="r1"),
        s2: _make_single_stub(scenario_id=s2, residual_ale=500.0, name="r2"),
        s3: _make_single_stub(scenario_id=s3, residual_ale=200.0, name="r3"),
    }
    names = {s1: "Cyber", s2: "Insider", s3: "APT"}
    rows, total = build_top_scenarios(
        latest_aggregate=None,
        fallback_singles_by_scenario=fallback,
        scenario_names_by_id=names,
    )
    assert total == 3
    # Sorted desc by residual_ale: 500, 200, 50 -> Insider, APT, Cyber
    assert [r.scenario_name for r in rows] == ["Insider", "APT", "Cyber"]
    assert rows[0].source == "single"
    assert rows[0].base_ale is None  # SINGLE path does not surface base


def test_build_top_scenarios_fallback_path_unknown_scenario_id_marked_unknown() -> None:
    s1 = uuid.uuid4()
    fallback = {s1: _make_single_stub(scenario_id=s1, residual_ale=42.0)}
    rows, _total = build_top_scenarios(
        latest_aggregate=None,
        fallback_singles_by_scenario=fallback,
        scenario_names_by_id={},  # s1 not in map
    )
    assert rows[0].scenario_name == "(unknown)"


def test_build_top_scenarios_both_empty_returns_empty() -> None:
    rows, total = build_top_scenarios(
        latest_aggregate=None,
        fallback_singles_by_scenario={},
        scenario_names_by_id={},
    )
    assert rows == []
    assert total == 0


def test_build_top_scenarios_tied_ale_stable_secondary_sort_by_name() -> None:
    s1, s2 = uuid.uuid4(), uuid.uuid4()
    agg = _make_aggregate_stub(
        per_scenario=[
            {
                "scenario_id": str(s2),
                "scenario_name": "Bravo",
                "base_risk": {"annualized_loss_expectancy": 100.0},
                "residual_risk": {"annualized_loss_expectancy": 50.0},
            },
            {
                "scenario_id": str(s1),
                "scenario_name": "Alpha",
                "base_risk": {"annualized_loss_expectancy": 100.0},
                "residual_risk": {"annualized_loss_expectancy": 50.0},
            },
        ],
        n_scenarios=2,
    )
    rows, _total = build_top_scenarios(
        agg, fallback_singles_by_scenario={}, scenario_names_by_id={}
    )
    assert [r.scenario_name for r in rows] == ["Alpha", "Bravo"]


def test_build_recent_run_row_completed_aggregate_extracts_with_controls_ale() -> None:
    run = _make_aggregate_stub(name="My Portfolio")
    row = build_recent_run_row(run, scenario_name=None)
    assert row.headline_ale == 100000.0
    assert row.run_type == RunType.AGGREGATE
    assert row.status == RunStatus.COMPLETED
    assert row.display_name == "My Portfolio"  # uses run.name


def test_build_recent_run_row_uses_fallback_when_name_none() -> None:
    sid = uuid.uuid4()
    run = _make_single_stub(scenario_id=sid, residual_ale=42.0, name=None)
    row = build_recent_run_row(run, scenario_name="Insider")
    assert row.display_name == "Insider"


def test_build_recent_run_row_completed_single_extracts_residual_ale() -> None:
    run = _make_single_stub(scenario_id=uuid.uuid4(), residual_ale=42_000.0, name="r")
    row = build_recent_run_row(run, scenario_name=None)
    assert row.headline_ale == 42_000.0
    assert row.run_type == RunType.SINGLE


@pytest.mark.parametrize(
    "status",
    [
        RunStatus.QUEUED,
        RunStatus.RUNNING,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    ],
)
def test_build_recent_run_row_non_completed_returns_none_ale(status: RunStatus) -> None:
    run = _StubRun(
        id=uuid.uuid4(),
        name="r",
        run_type=RunType.SINGLE,
        status=status,
        scenario_id=uuid.uuid4(),
        aggregate_scenario_ids=None,
        created_at=dt.datetime.now(dt.UTC),
        simulation_results={"residual_risk": {"annualized_loss_expectancy": 999.0}},
    )
    row = build_recent_run_row(run, scenario_name=None)
    assert row.headline_ale is None
    assert row.status == status


def test_build_recent_run_row_simulation_results_none_returns_none_ale() -> None:
    run = _StubRun(
        id=uuid.uuid4(),
        name="r",
        run_type=RunType.SINGLE,
        status=RunStatus.COMPLETED,
        scenario_id=uuid.uuid4(),
        aggregate_scenario_ids=None,
        created_at=dt.datetime.now(dt.UTC),
        simulation_results=None,
    )
    row = build_recent_run_row(run, scenario_name=None)
    assert row.headline_ale is None


def test_build_recent_run_row_populates_non_headline_fields() -> None:
    run_id = uuid.uuid4()
    created = dt.datetime(2026, 5, 5, 12, 30, tzinfo=dt.UTC)
    run = _StubRun(
        id=run_id,
        name="My Run",
        run_type=RunType.SINGLE,
        status=RunStatus.COMPLETED,
        scenario_id=uuid.uuid4(),
        aggregate_scenario_ids=None,
        created_at=created,
        simulation_results={"residual_risk": {"annualized_loss_expectancy": 1.0}},
    )
    row = build_recent_run_row(run, scenario_name=None)
    assert row.id == run_id
    assert row.created_at == created
    assert row.display_name == "My Run"


@dataclass
class _StubOrg:
    """Duck-types Organization for revenue formatting (Protocol satisfied)."""

    annual_revenue: Decimal | None


def test_build_residual_ale_card_revenue_set_returns_value_and_pct() -> None:
    agg = _make_aggregate_stub()
    org = _StubOrg(annual_revenue=Decimal("100000000"))
    out = build_residual_ale_card(agg, org)
    assert out is not None
    assert out["value"] == 100000.0
    assert abs(out["pct_revenue"] - 0.10) < 1e-9


def test_build_residual_ale_card_revenue_none_returns_pct_none() -> None:
    agg = _make_aggregate_stub()
    org = _StubOrg(annual_revenue=None)
    out = build_residual_ale_card(agg, org)
    assert out is not None
    assert out["pct_revenue"] is None


def test_build_residual_ale_card_revenue_zero_returns_pct_none() -> None:
    agg = _make_aggregate_stub()
    org = _StubOrg(annual_revenue=Decimal("0"))
    out = build_residual_ale_card(agg, org)
    assert out is not None
    assert out["pct_revenue"] is None


def test_build_residual_ale_card_no_aggregate_returns_none() -> None:
    org = _StubOrg(annual_revenue=Decimal("100000000"))
    out = build_residual_ale_card(latest_aggregate=None, org=org)
    assert out is None


# ---------------------------------------------------------------------------
# Task 3 (#478): posture verdict, budget card, coverage aggregates.
# ---------------------------------------------------------------------------


def test_verdict_within_when_residual_tail_under_appetite() -> None:
    # tolerance $8M @ 5%; residual samples with P(loss>=8M)=2% -> within
    v = build_posture_verdict(
        residual_samples=[1e6] * 98 + [9e6] * 2,
        tolerance={"amount": 8_000_000, "probability": 0.05},
    )
    assert v == "within"


def test_verdict_exceeds_when_tail_over_appetite() -> None:
    v = build_posture_verdict(
        residual_samples=[1e6] * 90 + [9e6] * 10,  # P=10% > 5%
        tolerance={"amount": 8_000_000, "probability": 0.05},
    )
    assert v == "exceeds"


def test_verdict_none_when_no_tolerance() -> None:
    assert build_posture_verdict(residual_samples=[1e6], tolerance=None) is None


def test_verdict_empty_samples_with_tolerance_is_within() -> None:
    """No data to compute an exceedance probability from -> p=0.0 -> within."""
    assert (
        build_posture_verdict(
            residual_samples=[], tolerance={"amount": 8_000_000, "probability": 0.05}
        )
        == "within"
    )


def test_budget_card_ratio_and_headroom_when_budget_set() -> None:
    c = build_budget_card(control_spend=2_670_000, annual_security_budget=3_500_000)
    assert round(c["ratio"], 2) == 0.76
    assert c["headroom"] == 830_000


def test_budget_card_no_gauge_when_budget_unset() -> None:
    c = build_budget_card(control_spend=2_670_000, annual_security_budget=None)
    assert c["budget"] is None
    assert c["ratio"] is None  # panel falls back to cost+ROI


def test_budget_card_zero_budget_avoids_division_by_zero() -> None:
    c = build_budget_card(control_spend=100.0, annual_security_budget=0.0)
    assert c["ratio"] is None
    assert c["headroom"] == -100.0


def test_scenario_coverage_uses_library_reference_and_pins() -> None:
    # reference = 3 sector library ids; covered via 2 pinned scenarios
    r = build_scenario_coverage(
        sector_library_ids=["s1", "s2", "s3"], pinned_library_ids=["s1", "s3"]
    )
    assert r.reference_count == 3
    assert r.covered_count == 2
    assert r.missing == ["s2"]  # actionable gap
    assert isinstance(r, CoverageResult)


# ---------------------------------------------------------------------------
# build_posture_verdict_from_lec — dashboard-wiring variant (interpolates off
# the loss-exceedance curve instead of raw per-iteration samples; see the
# function's docstring for why raw samples aren't loaded on the dashboard path).
# ---------------------------------------------------------------------------


def test_posture_verdict_from_lec_within_when_curve_probability_under_appetite() -> None:
    # Curve says P(loss>=8M) ~= 0.02 (interpolated) which is < 5% tolerance.
    lec = [
        {"loss": 1_000_000.0, "probability": 0.5},
        {"loss": 8_000_000.0, "probability": 0.02},
        {"loss": 20_000_000.0, "probability": 0.0},
    ]
    v = build_posture_verdict_from_lec(lec, {"amount": 8_000_000, "probability": 0.05})
    assert v == "within"


def test_posture_verdict_from_lec_exceeds_when_curve_probability_over_appetite() -> None:
    lec = [
        {"loss": 1_000_000.0, "probability": 0.5},
        {"loss": 8_000_000.0, "probability": 0.10},
        {"loss": 20_000_000.0, "probability": 0.0},
    ]
    v = build_posture_verdict_from_lec(lec, {"amount": 8_000_000, "probability": 0.05})
    assert v == "exceeds"


def test_posture_verdict_from_lec_none_when_no_tolerance() -> None:
    lec = [{"loss": 1.0, "probability": 1.0}]
    assert build_posture_verdict_from_lec(lec, None) is None


def test_posture_verdict_from_lec_none_when_no_curve() -> None:
    assert build_posture_verdict_from_lec(None, {"amount": 8_000_000, "probability": 0.05}) is None


def test_posture_appetite_detail_flags_near_threshold_borderline() -> None:
    """N1 (methodology review 2026-07-04): a boundary-adjacent verdict is flagged
    ``near_threshold`` so the UI can soften the hard within/exceeds binary.
    P(loss>=8M)=0.055 vs a 5% appetite → verdict "exceeds", but |0.055-0.05|=0.005
    is within the band max(0.005, 0.05*0.15=0.0075) → near."""
    lec = [
        {"loss": 1_000_000.0, "probability": 0.5},
        {"loss": 8_000_000.0, "probability": 0.055},
        {"loss": 20_000_000.0, "probability": 0.0},
    ]
    assert posture_appetite_detail(lec, {"amount": 8_000_000, "probability": 0.05}) == {
        "verdict": "exceeds",
        "near_threshold": True,
    }


def test_posture_appetite_detail_clear_within_is_not_near() -> None:
    """A comfortably-within posture (P=1% vs 5%) is NOT flagged near-threshold."""
    lec = [
        {"loss": 1_000_000.0, "probability": 0.3},
        {"loss": 8_000_000.0, "probability": 0.01},
        {"loss": 20_000_000.0, "probability": 0.0},
    ]
    assert posture_appetite_detail(lec, {"amount": 8_000_000, "probability": 0.05}) == {
        "verdict": "within",
        "near_threshold": False,
    }


def test_posture_appetite_detail_none_without_tolerance_or_curve() -> None:
    assert posture_appetite_detail([{"loss": 1.0, "probability": 1.0}], None) is None
    assert posture_appetite_detail(None, {"amount": 8_000_000, "probability": 0.05}) is None
    assert build_posture_verdict_from_lec([], {"amount": 8_000_000, "probability": 0.05}) is None


def test_interpolate_exceedance_probability_mid_segment_between_grid_points() -> None:
    """Minor gap fix: the two tests above pin ``amount`` exactly AT a curve
    point (8_000_000), a degenerate t=1.0 interpolation that never exercises
    the ``0 < t < 1`` linear-blend branch. Here ``amount=4_500_000`` sits
    strictly between the 1M and 8M grid points, so the hand-computed
    interpolation fraction is genuinely fractional (t=0.5, not 0 or 1).

    Hand math: t = (4.5M - 1M) / (8M - 1M) = 3.5M / 7M = 0.5
    p = 0.5 + 0.5 * (0.02 - 0.5) = 0.5 - 0.24 = 0.26
    """
    lec = [
        {"loss": 1_000_000.0, "probability": 0.5},
        {"loss": 8_000_000.0, "probability": 0.02},
        {"loss": 20_000_000.0, "probability": 0.0},
    ]
    p = _interpolate_exceedance_probability(lec, 4_500_000.0)
    assert p == pytest.approx(0.26)


# ---------------------------------------------------------------------------
# build_control_coverage
# ---------------------------------------------------------------------------


@dataclass
class _StubControl:
    """Duck-types a Control for build_control_coverage tests."""

    domains: frozenset[ControlDomain]
    nist_csf_functions: list[str]
    compliance_mappings: dict[str, Any]


def test_control_coverage_fair_cam_counts_every_domain_present() -> None:
    controls = [
        _StubControl(
            domains=frozenset({ControlDomain.LOSS_EVENT}),
            nist_csf_functions=[],
            compliance_mappings={},
        ),
        _StubControl(
            domains=frozenset({ControlDomain.LOSS_EVENT, ControlDomain.VARIANCE_MANAGEMENT}),
            nist_csf_functions=[],
            compliance_mappings={},
        ),
    ]
    out = build_control_coverage(controls, seeded_frameworks=(), framework_totals={})
    assert out["fair_cam"][ControlDomain.LOSS_EVENT] == 2
    assert out["fair_cam"][ControlDomain.VARIANCE_MANAGEMENT] == 1
    # Every enum member present even at 0 (no hardcoded "the domains" list downstream).
    assert out["fair_cam"][ControlDomain.DECISION_SUPPORT] == 0
    assert set(out["fair_cam"]) == set(ControlDomain)


def test_control_coverage_per_framework_uses_control_tags() -> None:
    controls = [
        _StubControl(
            domains=frozenset(),
            nist_csf_functions=["PR.AC-7"],
            compliance_mappings={"cis_safeguards": ["6.3"]},
        ),
        _StubControl(
            domains=frozenset(),
            nist_csf_functions=["PR.AC-7"],  # dup tag across controls -> deduped by coverage()
            compliance_mappings={},
        ),
    ]
    framework_totals = {
        "nist_csf": ["PR.AC-7", "PR.AC-1"],
        "cis": ["6.3", "6.4"],
    }
    out = build_control_coverage(
        controls, seeded_frameworks=("nist_csf", "cis"), framework_totals=framework_totals
    )
    by_name = {fw["name"]: fw["coverage"] for fw in out["frameworks"]}
    assert by_name["nist_csf"].covered_count == 1
    assert by_name["nist_csf"].reference_count == 2
    assert by_name["nist_csf"].missing == ["PR.AC-1"]
    assert by_name["cis"].covered_count == 1
    assert by_name["cis"].missing == ["6.4"]


def test_control_coverage_no_controls_returns_zero_coverage() -> None:
    out = build_control_coverage(
        [], seeded_frameworks=("nist_csf",), framework_totals={"nist_csf": ["PR.AC-7"]}
    )
    fw = out["frameworks"][0]["coverage"]
    assert fw.covered_count == 0
    assert fw.reference_count == 1
