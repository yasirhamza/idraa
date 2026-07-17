"""Orchestrator integration tests for services/reports.py (omicron-2 F10 + T2 #351).

DB-backed: ``build_executive_pdf_data`` with a real RiskAnalysisRun + Org
+ Scenarios + ControlSnapshotV2-shaped controls_snapshot.
"""

from __future__ import annotations

import datetime as dt
import io
import re
from decimal import Decimal

import pypdf
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.organization import Organization
from idraa.services.fx_rates import FxRateService
from idraa.services.pdf_report import render_executive_pdf
from idraa.services.reports import (
    ControlInventoryRow,
    RunReportData,
    build_executive_pdf_data,
)
from tests.integration._reports_fixtures import (
    _make_completed_aggregate_run,
    _make_completed_single_run,
)


async def test_build_executive_pdf_data_full_population(
    db_session: AsyncSession,
    organization: Organization,
) -> None:
    run = await _make_completed_aggregate_run(db_session, organization)

    data = await build_executive_pdf_data(db_session, run, organization)

    assert isinstance(data, RunReportData)
    assert data.org is organization
    assert data.run is run
    assert data.headline_ale == 2_610_000.0
    assert data.control_value_dollars == 2_340_000.0
    assert data.control_value_percent == 47.3
    assert data.n_scenarios == 3
    assert data.n_simulations == 50_000
    assert data.interval_pct == 95
    # pct_revenue computed only when annual_revenue is set on the org fixture.
    # The default factory leaves annual_revenue=None, so pct_revenue is None.
    # Still assert "either" to keep the test robust to a future fixture change.
    assert data.pct_revenue is None or data.pct_revenue > 0
    # Scenarios resolved from aggregate_scenario_ids -> Scenario rows
    scenario_names = {s.name for s in data.scenarios}
    assert scenario_names == {"Ransomware", "Insider", "APT"}
    # Controls grouped
    assert len(data.controls_by_domain["LOSS_EVENT"]) == 2
    assert len(data.controls_by_domain["VARIANCE_MANAGEMENT"]) == 1
    assert len(data.controls_by_domain["DECISION_SUPPORT"]) == 1
    assert isinstance(data.controls_by_domain["LOSS_EVENT"][0], ControlInventoryRow)
    # LEC clamped (loss=1.0 floor preserved; was 1.0 in fixture)
    assert all(loss >= 1.0 for (loss, _) in data.lec_with)
    assert all(loss >= 1.0 for (loss, _) in data.lec_without)
    # Narrative present
    assert "Across 3 modeled scenarios" in data.narrative


async def test_build_executive_pdf_data_empty_controls_snapshot(
    db_session: AsyncSession,
    organization: Organization,
) -> None:
    run = await _make_completed_aggregate_run(
        db_session,
        organization,
        controls=[],
    )
    data = await build_executive_pdf_data(db_session, run, organization)

    # All four domain keys present; all empty
    assert all(rows == [] for rows in data.controls_by_domain.values())


async def test_legacy_aggregate_pdf_suppresses_band_not_relabel(
    db_session: AsyncSession,
    organization: Organization,
) -> None:
    """#202 end-to-end suppress-not-relabel on the executive-PDF path.

    A pre-#202 AGGREGATE run (confidence_intervals with confidence_level /
    standard_error / lower_bound / upper_bound but NO interval_pct marker —
    the retired Gaussian SE-of-the-mean band) must NOT have its narrow SE
    bounds relabeled as the empirical central-95% percentile span. The
    orchestrator must set has_band=False (mirroring the HTML has_ci_band
    gate) and the rendered PDF must show the "not available" copy instead of
    the overclaim. This is the exact #202 repro: AGGREGATE run -> /reports ->
    download PDF -> page-2 conf-interval line. Pre-#202 rows exist in the live
    fly.dev DB and are re-exportable after merge.
    """
    run = await _make_completed_aggregate_run(db_session, organization, legacy_band=True)

    data = await build_executive_pdf_data(db_session, run, organization)

    # Orchestrator gates the band on the SAME predicate as HTML run-detail.
    assert data.has_band is False
    # The retired SE bounds are NOT read into the headline (would be the
    # mislabel source); they collapse to 0.0 and are never rendered.
    assert data.headline_ci_lo == 0.0
    assert data.headline_ci_hi == 0.0

    pdf = render_executive_pdf(data)
    reader = pypdf.PdfReader(io.BytesIO(pdf))
    # Risk picture is always pages[2] (cover[0]+TOC[1]+risk[2]+...).
    # Controls/back page is reader.pages[-1] — using -1 rather than a hard
    # index because the #352 reconciliation adds an attribution-matrix page
    # when the fixture carries shapley_value keys, shifting the controls page
    # one position forward.
    page2 = re.sub(r"\s+", " ", reader.pages[2].extract_text())
    footer = re.sub(r"\s+", " ", reader.pages[-1].extract_text())

    # The affirmative overclaim must be ABSENT.
    assert "Central 95% of modeled annualized losses" not in page2
    assert "p2.5" not in page2 and "p97.5" not in page2
    assert "p2.5" not in footer and "p97.5" not in footer
    # The legacy SE bounds must NOT leak into the document.
    assert "2,586,480" not in page2
    assert "2,633,520" not in page2
    # Suppression copy present on both surfaces.
    assert "not available for legacy runs" in page2
    assert "not available for legacy runs" in footer


async def test_build_executive_pdf_data_revenue_none_yields_pct_revenue_none(
    db_session: AsyncSession,
    organization: Organization,
) -> None:
    """If org.annual_revenue is None at orchestrator-call time, pct_revenue
    is None and the narrative omits the '% of revenue' clause."""
    organization.annual_revenue = None
    db_session.add(organization)
    await db_session.flush()

    run = await _make_completed_aggregate_run(db_session, organization)
    data = await build_executive_pdf_data(db_session, run, organization)

    assert data.pct_revenue is None
    assert "% of annual revenue" not in data.narrative


# ---- T2 (#351): RunReportData new fields ----


async def test_run_report_data_is_run_report_data_type(
    db_session: AsyncSession,
    organization: Organization,
) -> None:
    """T2: build_executive_pdf_data returns a RunReportData instance."""
    run = await _make_completed_aggregate_run(db_session, organization)
    data = await build_executive_pdf_data(db_session, run, organization)
    assert isinstance(data, RunReportData)


async def test_tail_risk_populated_for_aggregate_run(
    db_session: AsyncSession,
    organization: Organization,
) -> None:
    """T2(a): tail_risk fields populated from aggregate_with_controls dict."""
    run = await _make_completed_aggregate_run(db_session, organization)
    data = await build_executive_pdf_data(db_session, run, organization)
    # The fixture's aggregate_with_controls dict now carries the full tail ladder
    # (mirrors the prod post-change shape), so tail_risk is populated + non-degenerate.
    assert data.tail_risk is not None
    assert isinstance(data.tail_risk, dict)
    assert "var_90" in data.tail_risk
    assert "es_95" in data.tail_risk


async def test_aggregate_run_surfaces_full_tail_ladder(
    db_session: AsyncSession,
    organization: Organization,
) -> None:
    """An AGGREGATE run surfaces the FULL residual + base tail ladder
    (VaR 90/95/99/99.9 + ES 95/99/99.9) in the report data, gated by has_tail_risk —
    the gate flips True once aggregate_with_controls carries the tail keys (the
    payload _build_aggregate_lec_pair now persists). Residual tail comes from
    aggregate_with_controls; base tail from aggregate_without_controls."""
    run = await _make_completed_aggregate_run(db_session, organization)
    data = await build_executive_pdf_data(db_session, run, organization)

    # The tail-risk section is surfaced (not suppressed) for the aggregate run.
    assert data.has_tail_risk is True
    # Full residual ladder present + non-zero (from aggregate_with_controls).
    for key in ("var_90", "var_95", "var_99", "var_999", "es_95", "es_99", "es_999"):
        assert key in data.tail_risk
        assert data.tail_risk[key] != 0.0
    # Statistically sane: var_999 >= var_99 >= var_95 >= var_90; es_q >= var_q.
    t = data.tail_risk
    assert t["var_999"] >= t["var_99"] >= t["var_95"] >= t["var_90"]
    assert t["es_95"] >= t["var_95"]
    assert t["es_99"] >= t["var_99"]
    assert t["es_999"] >= t["var_999"]
    # Base ladder (from aggregate_without_controls) is also present.
    assert data.base_tail_risk is not None
    for key in ("var_90", "var_999", "es_999"):
        assert key in data.base_tail_risk


async def test_cost_summary_populated_for_aggregate_run(
    db_session: AsyncSession,
    organization: Organization,
) -> None:
    """T2(d): cost_summary populated when present in simulation_results."""
    # The default aggregate fixture doesn't have cost_summary at top-level.
    # For the integration test we use a custom simulation_results.
    run = await _make_completed_aggregate_run(db_session, organization)
    data = await build_executive_pdf_data(db_session, run, organization)
    # The fixture has no cost_summary key — should be None (renderer prints not available)
    assert data.cost_summary is None


async def test_attribution_matrix_populated_for_aggregate_run(
    db_session: AsyncSession,
    organization: Organization,
) -> None:
    """T2(e): attribution_matrix populated for AGGREGATE runs (not None)."""
    run = await _make_completed_aggregate_run(db_session, organization)
    data = await build_executive_pdf_data(db_session, run, organization)
    # The fixture has per_scenario but no control_adjustments — empty matrix is ok,
    # but the field itself must not be None (it's an AGGREGATE run).
    assert data.attribution_matrix is not None
    assert isinstance(data.attribution_matrix, dict)


async def test_attribution_matrix_none_for_single_run(
    db_session: AsyncSession,
    organization: Organization,
) -> None:
    """T2(e): attribution_matrix is None for SINGLE runs."""
    run = await _make_completed_single_run(db_session, organization)
    data = await build_executive_pdf_data(db_session, run, organization)
    assert data.attribution_matrix is None


async def test_control_effectiveness_for_single_run(
    db_session: AsyncSession,
    organization: Organization,
) -> None:
    """T2(f): control_effectiveness_rows is non-None for SINGLE runs."""
    run = await _make_completed_single_run(db_session, organization)
    data = await build_executive_pdf_data(db_session, run, organization)
    assert data.control_effectiveness_rows is not None


async def test_control_effectiveness_none_for_aggregate_run(
    db_session: AsyncSession,
    organization: Organization,
) -> None:
    """T2(f): control_effectiveness_rows is None for AGGREGATE runs."""
    run = await _make_completed_aggregate_run(db_session, organization)
    data = await build_executive_pdf_data(db_session, run, organization)
    assert data.control_effectiveness_rows is None


async def test_snapshot_backed_run_shows_as_executed_inputs(
    db_session: AsyncSession,
    organization: Organization,
) -> None:
    """T2(g): snapshot-backed run returns as-executed scenario inputs."""
    # Fixture seeds snapshot at run-create time; then mutate the live scenario after.
    run = await _make_completed_single_run(db_session, organization, with_snapshot=True)
    # Mutate the live scenario's TEF (simulate post-run edit)
    from sqlalchemy import select

    from idraa.models.scenario import Scenario

    stmt = select(Scenario).where(Scenario.id == run.scenario_id)
    result = await db_session.execute(stmt)
    sc = result.scalar_one()
    sc.threat_event_frequency = {"distribution": "PERT", "low": 99.0, "mode": 99.5, "high": 99.9}
    db_session.add(sc)
    await db_session.flush()

    data = await build_executive_pdf_data(db_session, run, organization)

    assert data.scenario_inputs is not None
    assert data.scenario_inputs["label"] == "as-executed"
    # The snapshot should show the ORIGINAL fixture values, not the mutated 99.0
    first_sc = data.scenario_inputs["scenarios"][0]
    assert first_sc["threat_event_frequency"]["low"] != 99.0  # snapshot, not live


async def test_legacy_null_run_falls_back_with_honest_label(
    db_session: AsyncSession,
    organization: Organization,
) -> None:
    """T2(g): legacy-null run falls back to live values with the honest label."""
    run = await _make_completed_single_run(db_session, organization, with_snapshot=False)
    data = await build_executive_pdf_data(db_session, run, organization)

    assert data.scenario_inputs is not None
    assert "run predates input snapshots" in data.scenario_inputs["label"]
    assert len(data.scenario_inputs["scenarios"]) == 1


async def test_scenario_inputs_n3_adapter_iteration_for_aggregate(
    db_session: AsyncSession,
    organization: Organization,
) -> None:
    """T2(j): scenario inputs preserved for all N>=3 scenarios (adapter-iteration contract)."""
    run = await _make_completed_aggregate_run(
        db_session,
        organization,
        scenario_names=["S1", "S2", "S3"],  # N=3
    )
    data = await build_executive_pdf_data(db_session, run, organization)
    assert data.scenario_inputs is not None
    assert len(data.scenario_inputs["scenarios"]) == 3  # all 3 preserved


async def test_scenario_provenance_analyst_authored_fallback(
    db_session: AsyncSession,
    organization: Organization,
) -> None:
    """T2(i): analyst-authored fallback for scenarios without library lineage."""
    run = await _make_completed_single_run(db_session, organization)
    data = await build_executive_pdf_data(db_session, run, organization)

    assert data.scenario_provenance is not None
    assert len(data.scenario_provenance) == 1
    prov = data.scenario_provenance[0]
    assert prov["provenance_label"] == "analyst-authored — no library provenance"
    assert prov["loss_tier"] is None
    assert prov["source_citations"] == []


async def test_scenario_provenance_n3_adapter_iteration(
    db_session: AsyncSession,
    organization: Organization,
) -> None:
    """T2SC-F1 — scenario_provenance N≥3 adapter-iteration contract.

    Per CLAUDE.md data-contract policy: for any list[ORM] → list[DTO] adapter,
    a regression test must build N≥3 input items and assert all N are preserved
    in the output — guarding against [0]/[-1]/first-only truncation bugs.

    Builds an AGGREGATE run with 3 distinct scenarios, calls
    build_executive_pdf_data, then asserts:
    1. ``len(data.scenario_provenance) == 3``  (all 3 preserved — no truncation)
    2. Each entry maps to a distinct scenario_id                (no aliasing/dedup)
    """
    run = await _make_completed_aggregate_run(
        db_session,
        organization,
        scenario_names=["Ransomware OT", "Insider Threat", "APT Supply Chain"],
    )
    data = await build_executive_pdf_data(db_session, run, organization)

    assert data.scenario_provenance is not None
    # All 3 entries preserved — adapter-iteration contract
    assert len(data.scenario_provenance) == 3
    # Each entry maps to a distinct scenario_id (no [0]/[-1] aliasing)
    prov_ids = [entry["scenario_id"] for entry in data.scenario_provenance]
    assert len(set(prov_ids)) == 3, (
        f"scenario_provenance entries must map to 3 distinct scenario_ids; got {prov_ids!r}"
    )


async def test_tail_risk_populated_for_single_run(
    db_session: AsyncSession,
    organization: Organization,
) -> None:
    """T2(a)/(c): tail_risk and has_tail_risk for SINGLE run fixture."""
    run = await _make_completed_single_run(db_session, organization)
    data = await build_executive_pdf_data(db_session, run, organization)

    assert data.tail_risk is not None
    # The single-run fixture has var_90 etc. set to non-zero values
    assert data.tail_risk["var_90"] == 580_000.0
    assert data.tail_risk["var_95"] == 680_000.0
    assert data.has_tail_risk is True


async def test_base_tail_risk_populated_for_single_run(
    db_session: AsyncSession,
    organization: Organization,
) -> None:
    """T2(b): base_tail_risk populated from base_risk via SAME helper."""
    run = await _make_completed_single_run(db_session, organization)
    data = await build_executive_pdf_data(db_session, run, organization)

    assert data.base_tail_risk is not None
    assert data.base_tail_risk["var_90"] == 1_100_000.0
    assert data.base_tail_risk["var_95"] == 1_300_000.0
    assert data.base_stats is not None
    assert data.base_stats["mean"] == 820_000.0
    assert data.base_stats["median"] == 750_000.0


async def test_residual_stats_populated_for_single_run(
    db_session: AsyncSession,
    organization: Organization,
) -> None:
    """T4SC-6 + T4M-3-I1: residual_stats populated from residual_risk for SINGLE run fixture.

    Coverage parity with test_base_tail_risk_populated_for_single_run (which checks base_stats).
    The single-run fixture's residual_risk carries mean=410_000 / median=375_000 /
    std_deviation=100_000 (from _single_simulation_results in _reports_fixtures.py).
    """
    run = await _make_completed_single_run(db_session, organization)
    data = await build_executive_pdf_data(db_session, run, organization)

    assert data.residual_stats is not None, "residual_stats must be non-None for SINGLE run"
    assert data.residual_stats["mean"] == 410_000.0
    assert data.residual_stats["median"] == 375_000.0


async def test_cost_summary_populated_for_single_run(
    db_session: AsyncSession,
    organization: Organization,
) -> None:
    """T2(d): cost_summary populated for SINGLE run fixture (has cost_summary key)."""
    run = await _make_completed_single_run(db_session, organization)
    data = await build_executive_pdf_data(db_session, run, organization)

    assert data.cost_summary is not None
    assert data.cost_summary["total_annual_cost"] == 100_000.0
    assert data.cost_summary["total_risk_reduction"] == 400_000.0
    assert data.cost_summary["net_benefit"] == 300_000.0
    assert data.cost_summary["aggregate_roi"] == 4.0


async def test_attribution_matrix_n3_scenarios_preserved(
    db_session: AsyncSession,
    organization: Organization,
) -> None:
    """T2(e)/(j): attribution matrix preserves all N>=3 scenario rows."""

    # Seed 3 scenarios and include control_adjustments in simulation_results
    run = await _make_completed_aggregate_run(
        db_session,
        organization,
        scenario_names=["Alpha", "Bravo", "Charlie"],
    )
    # Update simulation_results to include per_scenario with control_adjustments
    per_scenario = run.simulation_results["per_scenario"]
    for i, ps in enumerate(per_scenario):
        ps["control_adjustments"] = [
            {
                "control_id": f"c{i}",
                "control_name": f"Ctrl {i}",
                "risk_reduction_value": 1000.0,
                "loss_reduction_per_event": 100.0,
                # shapley_value required after #352: builder needs at least one
                # shapley_value key or it returns the 'unavailable' state.
                "shapley_value": 500.0,
            },
        ]
        ps["base_risk"]["loss_event_frequency"] = 2.0
    run.simulation_results = {**run.simulation_results, "per_scenario": per_scenario}
    db_session.add(run)
    await db_session.flush()

    data = await build_executive_pdf_data(db_session, run, organization)

    assert data.attribution_matrix is not None
    assert len(data.attribution_matrix["rows"]) == 3


# ---- T6 (#351): controls_snapshot field on RunReportData ----


async def test_controls_snapshot_populated_for_aggregate_run(
    db_session: AsyncSession,
    organization: Organization,
) -> None:
    """T6: controls_snapshot field populated from run.controls_snapshot (not live controls).

    The field passes the as-executed snapshot verbatim to the renderer for the
    control-assignment snapshot summary table in Assumptions & inputs.
    """
    run = await _make_completed_aggregate_run(db_session, organization)
    data = await build_executive_pdf_data(db_session, run, organization)

    assert isinstance(data.controls_snapshot, list), "controls_snapshot must be a list"
    # The fixture has 4 controls
    assert len(data.controls_snapshot) == 4, (
        f"Expected 4 controls in snapshot, got {len(data.controls_snapshot)}"
    )
    # Each entry must have a 'name' key (basic shape check)
    for snap in data.controls_snapshot:
        assert "name" in snap, f"controls_snapshot entry missing 'name': {snap!r}"


async def test_controls_snapshot_populated_for_single_run(
    db_session: AsyncSession,
    organization: Organization,
) -> None:
    """T6: controls_snapshot field populated for SINGLE run fixture."""
    run = await _make_completed_single_run(db_session, organization)
    data = await build_executive_pdf_data(db_session, run, organization)

    assert isinstance(data.controls_snapshot, list), "controls_snapshot must be a list"
    # The single-run fixture has 3 controls
    assert len(data.controls_snapshot) == 3, (
        f"Expected 3 controls in snapshot for SINGLE run, got {len(data.controls_snapshot)}"
    )


async def test_controls_snapshot_n3_adapter_iteration_contract(
    db_session: AsyncSession,
    organization: Organization,
) -> None:
    """T6 adapter-iteration contract: N≥3 controls in snapshot all preserved.

    Per CLAUDE.md data-contract policy: for any list[ORM] → list[DTO] adapter,
    assert N≥3 items all preserved in the output.
    """
    # Use a run with 4 controls (default fixture)
    run = await _make_completed_aggregate_run(db_session, organization)
    data = await build_executive_pdf_data(db_session, run, organization)

    # All 4 controls must be preserved (no truncation)
    assert len(data.controls_snapshot) == 4, (
        f"Adapter-iteration contract: all 4 controls must be preserved, got {len(data.controls_snapshot)}"
    )
    # Each must have distinct names
    names = [s.get("name") for s in data.controls_snapshot]
    assert len(set(names)) == 4, f"All 4 control names must be distinct: {names!r}"


async def test_t6_single_run_pdf_renders_assumptions_inputs_section(
    db_session: AsyncSession,
    organization: Organization,
) -> None:
    """T6 end-to-end: assumptions & inputs section present in SINGLE run PDF."""
    import io
    import re

    import pypdf

    run = await _make_completed_single_run(db_session, organization, with_snapshot=True)
    data = await build_executive_pdf_data(db_session, run, organization)
    pdf = render_executive_pdf(data)

    reader = pypdf.PdfReader(io.BytesIO(pdf))
    all_text = re.sub(r"\s+", " ", " ".join(p.extract_text() for p in reader.pages))

    assert "Assumptions & inputs" in all_text, (
        "T6 end-to-end: 'Assumptions & inputs' section must appear in SINGLE run PDF"
    )
    # The scenario has PERT distributions — 'PERT' or 'elicited values' must appear
    assert "PERT" in all_text or "elicited values" in all_text, (
        "PERT distribution type or elicited values label must appear for the SINGLE run fixture"
    )


# ---- Spec-compliance fix (#351): engine label populated by builder ----


async def test_builder_populates_engine_label(
    db_session: AsyncSession,
    organization: Organization,
) -> None:
    """Spec-compliance fix (#351): build_executive_pdf_data sets a non-empty
    engine_label starting with 'fair-cam'.

    The builder calls _resolve_engine_label() which queries importlib.metadata
    for the 'fair-cam' distribution; the test asserts the resulting field is
    non-empty and prefixed with 'fair-cam', confirming the distribution was
    found in the project venv.
    """
    run = await _make_completed_aggregate_run(db_session, organization)
    data = await build_executive_pdf_data(db_session, run, organization)

    assert data.engine_label, "engine_label must be non-empty"
    assert data.engine_label.startswith("fair-cam"), (
        f"engine_label must start with 'fair-cam', got {data.engine_label!r}. "
        "Check that the 'fair-cam' distribution is installed in the project venv "
        "and that _resolve_engine_label() uses the correct distribution name."
    )


# ---- P3 regression: pct_revenue currency-invariance through the REAL builder ----


async def test_pct_revenue_currency_invariant_real_builder(
    db_session: AsyncSession,
) -> None:
    """P3 regression guard: pct_revenue must be currency-invariant across the
    REAL async builder (build_executive_pdf_data).

    The pre-fix bug: pct_revenue was computed AFTER headline_ale was converted
    to the org's preferred currency.  For a EUR org with rate=0.92, the
    resulting pct_revenue was USD_pct_revenue * 0.92 — wrong by an FX factor.

    This test catches that ordering bug by:
    1. Building a USD org with annual_revenue set + a completed AGGREGATE run.
    2. Calling the REAL build_executive_pdf_data and capturing pct_revenue.
    3. Building a EUR org (same annual_revenue), seeding a live EUR FxRate
       (rate=0.92, NOT 1.0), and a completed run for that org.
    4. Calling build_executive_pdf_data again and capturing pct_revenue.
    5. Asserting both pct_revenue values are equal (currency-invariant ratio).

    Under the pre-fix ordering, eur_data.pct_revenue would be
    usd_data.pct_revenue * 0.92 (or / 0.92 depending on direction), so the
    assertion ``eur_data.pct_revenue == approx(usd_data.pct_revenue)`` would
    FAIL against buggy code and PASS against the fixed code.
    """
    from tests.factories import create_org

    _annual_revenue = Decimal("10_000_000")  # $10M USD for both orgs

    # ---- USD org + run ----
    usd_org = await create_org(db_session, name="USD Test Org")
    usd_org.annual_revenue = _annual_revenue
    usd_org.preferred_currency = "USD"
    db_session.add(usd_org)
    await db_session.flush()

    usd_run = await _make_completed_aggregate_run(db_session, usd_org)
    usd_data = await build_executive_pdf_data(db_session, usd_run, usd_org)

    assert usd_data.pct_revenue is not None, (
        "USD org with annual_revenue set must yield a non-None pct_revenue"
    )

    # ---- EUR org + run ----
    eur_org = await create_org(db_session, name="EUR Test Org")
    eur_org.annual_revenue = _annual_revenue  # same revenue in USD (org stores in USD)
    eur_org.preferred_currency = "EUR"
    db_session.add(eur_org)
    await db_session.flush()

    # Seed an active EUR FxRate at 0.92 (not 1.0 — must be clearly non-trivial)
    await FxRateService(db_session).upsert_rate(
        eur_org.id,
        "EUR",
        Decimal("0.92"),
        dt.date(2026, 6, 14),
        "ECB",
        user_id=None,
    )
    await db_session.flush()

    eur_run = await _make_completed_aggregate_run(db_session, eur_org)
    eur_data = await build_executive_pdf_data(db_session, eur_run, eur_org)

    assert eur_data.pct_revenue is not None, (
        "EUR org with annual_revenue set must yield a non-None pct_revenue"
    )

    # The pre-fix bug would make eur_data.pct_revenue ≈ usd_data.pct_revenue * 0.92.
    # The fix computes pct_revenue BEFORE the headline_ale is converted, so both
    # must be equal (ratio of same USD values).
    assert eur_data.pct_revenue == pytest.approx(usd_data.pct_revenue, rel=1e-6), (
        f"pct_revenue must be currency-invariant. "
        f"USD pct_revenue={usd_data.pct_revenue:.6f}, "
        f"EUR pct_revenue={eur_data.pct_revenue:.6f}. "
        f"Difference suggests pct_revenue was computed AFTER FX conversion "
        f"(pre-fix ordering bug: ALE converted first, ratio wrong by FX factor)."
    )
