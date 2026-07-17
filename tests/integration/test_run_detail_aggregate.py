"""Integration tests for /runs/{id} AGGREGATE detail rendering (PR xi F9).

Includes a methodology-guard regression test asserting rendered HTML
contains zero contamination terms per PR psi + PR phi cleanup.

Fixture topology: ``authed_analyst`` (via ``analyst_client`` + org_id) creates
the analyst's org. Aggregate runs must be seeded in THAT org so the org-scoped
``RunService.get_for_org`` lookup finds them. ``seed_aggregate_run_factory``
ties to ``seed_organization`` (a different org), so we seed runs inline here
in the analyst's org using ``RunService.create_and_dispatch`` with
``mc_iterations_override`` below the 1000-iteration inline threshold.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus


@pytest_asyncio.fixture
async def analyst_org_aggregate_run(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_user: Any,
    seed_scenario_factory: Callable[..., Awaitable[Any]],
    wire_executor_to_test_db: None,
) -> RiskAnalysisRun:
    """A COMPLETED AGGREGATE RiskAnalysisRun in the analyst's org.

    Seeds 2 scenarios in the analyst's org, then calls create_and_dispatch
    with mc_iterations_override=200 (below inline sync threshold) so the
    executor runs inline and the run is COMPLETED before the fixture returns.
    """
    from fastapi import BackgroundTasks

    from idraa.services.runs import RunService

    _, org_id = authed_analyst
    s1 = await seed_scenario_factory(name="agg-s1", organization_id=org_id, created_by=seed_user.id)
    s2 = await seed_scenario_factory(name="agg-s2", organization_id=org_id, created_by=seed_user.id)

    bg = BackgroundTasks()
    service = RunService(db_session)
    run = await service.create_and_dispatch(
        organization_id=org_id,
        scenario_ids=[s1.id, s2.id],
        mc_iterations_override=200,
        created_by=seed_user.id,
        background_tasks=bg,
    )
    await db_session.refresh(run)
    return run


@pytest_asyncio.fixture
async def analyst_org_single_run(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_user: Any,
    seed_scenario_factory: Callable[..., Awaitable[Any]],
    wire_executor_to_test_db: None,
) -> RiskAnalysisRun:
    """A COMPLETED SINGLE RiskAnalysisRun in the analyst's org (PR nu path)."""
    from fastapi import BackgroundTasks

    from idraa.services.runs import RunService

    _, org_id = authed_analyst
    scenario = await seed_scenario_factory(
        name="single-test-scenario", organization_id=org_id, created_by=seed_user.id
    )

    bg = BackgroundTasks()
    service = RunService(db_session)
    run = await service.create_and_dispatch(
        organization_id=org_id,
        scenario_ids=[scenario.id],
        mc_iterations_override=200,
        created_by=seed_user.id,
        background_tasks=bg,
    )
    await db_session.refresh(run)
    return run


@pytest_asyncio.fixture
async def analyst_org_aggregate_run_with_controls(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_user: Any,
    seed_scenario_factory: Callable[..., Awaitable[Any]],
    seed_control_factory: Callable[..., Awaitable[Any]],
    wire_executor_to_test_db: None,
) -> RiskAnalysisRun:
    """A COMPLETED AGGREGATE RiskAnalysisRun with mitigating controls on each scenario.

    Seeds 2 scenarios + 2 distinct controls in the analyst's org. Scenario 1 →
    [control A]; scenario 2 → [control A, control B]. Links via ScenarioControl
    M2M rows. RunService.create_and_dispatch runs the executor inline
    (mc_iterations_override=200 < inline threshold) so the run is COMPLETED
    with persisted control_adjustments before the fixture returns.
    """
    from fastapi import BackgroundTasks

    from idraa.models.scenario_control import ScenarioControl
    from idraa.services.runs import RunService

    _, org_id = authed_analyst
    s1 = await seed_scenario_factory(
        name="agg-ctrl-s1", organization_id=org_id, created_by=seed_user.id
    )
    s2 = await seed_scenario_factory(
        name="agg-ctrl-s2", organization_id=org_id, created_by=seed_user.id
    )
    ctrl_a = await seed_control_factory(
        name="Control Alpha", organization_id=org_id, created_by=seed_user.id
    )
    ctrl_b = await seed_control_factory(
        name="Control Beta", organization_id=org_id, created_by=seed_user.id
    )

    # M2M link via ScenarioControl rows (mitigating_controls is a relationship
    # through `scenario_controls`, not a JSON list of UUID strings).
    db_session.add_all(
        [
            ScenarioControl(scenario_id=s1.id, control_id=ctrl_a.id),
            ScenarioControl(scenario_id=s2.id, control_id=ctrl_a.id),
            ScenarioControl(scenario_id=s2.id, control_id=ctrl_b.id),
        ]
    )
    await db_session.commit()

    bg = BackgroundTasks()
    service = RunService(db_session)
    run = await service.create_and_dispatch(
        organization_id=org_id,
        scenario_ids=[s1.id, s2.id],
        mc_iterations_override=200,
        created_by=seed_user.id,
        background_tasks=bg,
    )
    await db_session.refresh(run)
    return run


@pytest.mark.asyncio
async def test_aggregate_run_detail_renders_aggregate_partial(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    analyst_org_aggregate_run: RiskAnalysisRun,
) -> None:
    """GET /runs/{id} for AGGREGATE renders the new aggregate partial."""
    client, _ = authed_analyst
    run = analyst_org_aggregate_run
    response = await client.get(f"/runs/{run.id}")
    assert response.status_code == 200
    body = response.text
    # Aggregate-specific markers (T7 redesign: verdict strip + renamed sections
    # replace the old "Control Value" / "Aggregate risk distribution" /
    # "Per-scenario annualized loss expectancy" headings).
    assert "Aggregate" in body
    assert 'id="verdict-strip"' in body
    assert "Loss distribution" in body  # was "Aggregate risk distribution"
    assert "Where the risk lives" in body  # was "Per-scenario annualized loss expectancy"
    # The loss-distribution money table must still be scroll-wrapped so it
    # doesn't clip its right column on a phone-width card (mobile-overflow fix).
    assert re.search(r'overflow-x-auto[^>]*>\s*<table class="table table-sm">', body), (
        "loss-distribution table is not wrapped in overflow-x-auto (clips on mobile)"
    )
    # Scenario-independence caveat on the aggregate VaR/ES (this freshly-executed run
    # has tail metrics): positively-correlated scenarios understate the tail.
    assert "lower bound" in body
    assert "scenarios are independent" in body


@pytest.mark.asyncio
async def test_aggregate_run_detail_includes_4_chart_macros(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    analyst_org_aggregate_run: RiskAnalysisRun,
) -> None:
    """The 4 expected chart macros are present in rendered HTML."""
    client, _ = authed_analyst
    run = analyst_org_aggregate_run
    response = await client.get(f"/runs/{run.id}")
    assert response.status_code == 200
    body = response.text
    # T7 redesign: verdict strip replaces the control_value_headline +
    # headline_ale_with_ci_band macros; the per-scenario bar becomes a dumbbell,
    # and the LEC curve lives inside the exceedance toggle.
    assert 'id="verdict-strip"' in body
    assert "Residual ALE" in body  # verdict-strip headline (mean)
    # dual_lec_curve — first-party SVG now (epic #547 P1 Task 3, inside the
    # exceedance toggle); the prior chart vendor's container retired for this card.
    assert 'data-chart="dual-lec"' in body
    # per-scenario dumbbell — CSS dumbbell (the prior chart vendor's per-scenario
    # chart retired; see runs/components/scenario_dumbbell.html)
    assert 'id="scenario-dumbbell"' in body
    assert "dumbbell-track" in body


@pytest.mark.asyncio
async def test_single_run_detail_still_renders_pr_nu_partial_unchanged(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    analyst_org_single_run: RiskAnalysisRun,
) -> None:
    """SINGLE detail page still renders PR nu partial — no AGGREGATE-specific markers."""
    client, _ = authed_analyst
    run = analyst_org_single_run
    response = await client.get(f"/runs/{run.id}")
    assert response.status_code == 200
    body = response.text
    # PR nu headline still present
    assert (
        "Residual annualized loss expectancy" in body
        or "annualized loss expectancy" in body.lower()
    )
    # AGGREGATE-specific containers should NOT appear. The old "container not
    # in body" check went VACUOUS the moment the prior chart vendor's container
    # name was retired (epic #547 P1 Task 3) — rewritten to the SVG contract so
    # it still bites if the LEC card ever leaks onto a SINGLE-run page.
    assert 'data-chart="dual-lec"' not in body
    assert "per-scenario-ale-bar-container" not in body


@pytest.mark.asyncio
async def test_aggregate_html_no_contamination_terms(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    analyst_org_aggregate_run: RiskAnalysisRun,
) -> None:
    """METHODOLOGY GUARD (PR psi + PR phi): rendered AGGREGATE HTML must
    contain ZERO of the LLM-hallucinated portfolio-finance terms."""
    client, _ = authed_analyst
    run = analyst_org_aggregate_run
    response = await client.get(f"/runs/{run.id}")
    assert response.status_code == 200
    body = response.text.lower()
    forbidden = [
        "diversification",
        "herfindahl",
        "% contribution to aggregate",
        "concentration index",
        "sum-of-parts",
    ]
    for term in forbidden:
        assert term not in body, f"Contamination leaked into HTML: {term!r}"


@pytest.mark.asyncio
async def test_failed_aggregate_run_does_not_render_invalid_rerun_url(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    analyst_org_aggregate_run: RiskAnalysisRun,
) -> None:
    """FAILED AGGREGATE re-run UI must not target /scenarios/None/run.

    AGGREGATE runs have scenario_id=None; the SINGLE-only inline re-run form
    in _status_poll.html previously rendered hx-post="/scenarios/None/run"
    for AGGREGATE FAILED/CANCELLED states (whole-branch reviewer M1, PR xi).
    The fix branches on run.run_type and links AGGREGATE to /analyses/new.
    """
    client, _ = authed_analyst
    run = analyst_org_aggregate_run
    run.status = RunStatus.FAILED
    run.error_message = "synthetic failure for regression test"
    await db_session.commit()
    response = await client.get(f"/runs/{run.id}")
    assert response.status_code == 200
    body = response.text
    assert "/scenarios/None/run" not in body, (
        "AGGREGATE FAILED rendered SINGLE-style re-run form with None scenario_id"
    )
    assert "/analyses/new" in body, "AGGREGATE FAILED should link to /analyses/new for re-trigger"


# ---- Issue #89: legacy-AGGREGATE banner + re-run form sanitization -------


@pytest.mark.asyncio
async def test_status_poll_failed_single_rerun_form_omits_control_ids_hidden_input(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    analyst_org_single_run: RiskAnalysisRun,
) -> None:
    """Issue #89 B2: FAILED SINGLE re-run form must NOT emit control_ids hidden inputs.

    The prior form mirrored controls_snapshot into hidden inputs and re-POSTed
    them to /scenarios/.../run. After issue #89 the legacy adapter ignores
    control_ids; re-run replays the scenario's CURRENT mitigating_controls.
    """
    client, _ = authed_analyst
    run = analyst_org_single_run
    run.status = RunStatus.FAILED
    run.error_message = "synthetic failure for re-run sanitization test"
    await db_session.commit()
    response = await client.get(f"/runs/{run.id}")
    assert response.status_code == 200
    body = response.text
    assert 'name="control_ids"' not in body, (
        "Re-run form leaked control_ids hidden inputs — issue #89 strict-coupling regression"
    )
    # Form still has the mc_iterations + Re-run button.
    assert "Re-run with same inputs" in body
    assert 'name="mc_iterations"' in body


@pytest.mark.asyncio
async def test_legacy_aggregate_run_renders_issue_89_banner(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    analyst_org_aggregate_run: RiskAnalysisRun,
) -> None:
    """Issue #89 M6: AGGREGATE rows with NULL aggregate_control_ids_per_scenario
    (pre-issue-89 legacy semantics) render a banner explaining the prior
    union-controls model."""
    import json

    from sqlalchemy import text

    client, _ = authed_analyst
    run = analyst_org_aggregate_run
    # Simulate a legacy row by raw-clearing the new column. The ORM @validates
    # check tolerates None, so this also works via direct assignment, but raw
    # SQL matches a true legacy-row state.
    await db_session.execute(
        text(
            "UPDATE risk_analysis_runs SET aggregate_control_ids_per_scenario = NULL WHERE id = :id"
        ),
        {"id": run.id.hex},
    )
    await db_session.commit()
    response = await client.get(f"/runs/{run.id}")
    assert response.status_code == 200
    body = response.text
    # Banner sentinel — copy can change but must reference the semantic shift.
    assert "issue #89" in body.lower() or "strict scenario-coupling" in body.lower()
    assert "before strict scenario-coupling" in body.lower() or "applied the union" in body.lower()
    # Quick: make sure new-AGGREGATE-runs do NOT show the banner.
    await db_session.execute(
        text(
            "UPDATE risk_analysis_runs SET aggregate_control_ids_per_scenario = :v WHERE id = :id"
        ),
        {"v": json.dumps({}), "id": run.id.hex},
    )
    # Note: we don't actually need to satisfy the cross-field invariant for this
    # smoke (it's checked by @validates which raw SQL bypasses); the row state
    # is fine for testing banner absence.
    await db_session.commit()
    response2 = await client.get(f"/runs/{run.id}")
    body2 = response2.text
    assert "before strict scenario-coupling" not in body2.lower()


# ---- PR omega T6: AGGREGATE dual_lec + dual_epc side-by-side -------


@pytest.mark.asyncio
async def test_run_detail_aggregate_renders_dual_epc(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    analyst_org_aggregate_run: RiskAnalysisRun,
) -> None:
    """AGGREGATE /runs/{id} renders dual_lec + dual_epc side-by-side."""
    client, _ = authed_analyst
    run = analyst_org_aggregate_run
    response = await client.get(f"/runs/{run.id}")
    assert response.status_code == 200
    body = response.text
    # T7 redesign: LEC + EPC now share ONE card behind an Alpine tab toggle
    # (id="exceedance") instead of a side-by-side lg:grid-cols-2 grid. Both
    # cards are first-party SVG now (not the prior chart vendor): LEC flipped
    # in epic #547 P1 Task 3, EPC flipped in Task 4.
    assert 'data-chart="dual-epc"' in body
    assert 'data-chart="dual-lec"' in body
    assert 'id="exceedance"' in body
    assert "Loss exceedance" in body and "Probability curve" in body


# ---- Issue #96: per-scenario control attribution matrix -------


@pytest.mark.asyncio
async def test_aggregate_run_detail_renders_per_scenario_control_matrix(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    analyst_org_aggregate_run_with_controls: RiskAnalysisRun,
) -> None:
    """AGGREGATE run with controls renders matrix section with names + $ figures."""
    client, _ = authed_analyst
    resp = await client.get(f"/runs/{analyst_org_aggregate_run_with_controls.id}")
    assert resp.status_code == 200
    html = resp.text
    # T7 redesign: the attribution matrix moved behind the control-ledger's
    # "Per-scenario breakdown (Shapley matrix)" disclosure.
    assert "What each control is worth" in html
    assert "Per-scenario breakdown (Shapley matrix)" in html
    # Shapley framing IS present in non-empty case; old multiplicative caveat is gone
    assert "Shapley" in html
    assert "compose multiplicatively" not in html
    # Both control names appear as headers/tooltips
    assert "Control Alpha" in html
    assert "Control Beta" in html
    # Per-column "(Shapley $)" suffix appears (Shapley semantics disclosure)
    assert "(Shapley $)" in html
    assert "(isol. $)" not in html
    # SWE-review negative pin (2026-07-04): this fixture is a LEGACY run (no
    # *_mean keys, no blob basis), where value_typical/total_reduction_typical
    # are populated from the SAME shapley_value the legacy primary falls back
    # to — a naive is-not-none gate on the paired sub-line would double-print
    # "typical $X" on every legacy run. The basis=="mean" gate must hold.

    # Both scenario names appear as row headers
    assert "agg-ctrl-s1" in html
    assert "agg-ctrl-s2" in html
    # At least one dollar figure rendered (cell content).
    # F18: the matrix now renders via data_grid macro which uses abbreviate_money
    # (e.g. "$640", "$1k", "$2.10M"). The sticky-grid td class differs from the
    # old plain-table class — assert on content, not the exact CSS class string.
    import re

    assert re.search(r">\$([\d,]+|[\d.]+[kM])<", html), (
        "Expected at least one $ cell in the data_grid matrix"
    )


@pytest.mark.asyncio
async def test_aggregate_run_detail_renders_weight_provenance_disclaimer(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    analyst_org_aggregate_run_with_controls: RiskAnalysisRun,
) -> None:
    """Issue #413: the control-value $ surfaces carry the implementation-
    calibrated-weights disclaimer, anchored to fair_cam's weights_provenance
    label. T7/T3: now single-source in the caveat panel (was duplicated)."""
    client, _ = authed_analyst
    resp = await client.get(f"/runs/{analyst_org_aggregate_run_with_controls.id}")
    assert resp.status_code == 200
    html = resp.text
    # T3 single-source (T7): the provenance disclosure is no longer duplicated
    # across the cost section + Shapley note — it lives ONCE in the caveat panel
    # (weight-provenance entry). Assert the base sentence (common to all three
    # adjudicated variants) renders exactly once.
    from idraa.services._view_model_helpers import (
        CONTROL_WEIGHT_PROVENANCE_DISCLAIMER_BASE,
    )

    assert CONTROL_WEIGHT_PROVENANCE_DISCLAIMER_BASE in html, (
        "control-weight provenance disclaimer missing from the caveat panel"
    )
    assert html.count(CONTROL_WEIGHT_PROVENANCE_DISCLAIMER_BASE) == 1, (
        "provenance disclaimer must be single-source (caveat registry), not duplicated"
    )


@pytest.mark.asyncio
async def test_aggregate_run_detail_renders_empty_state_when_no_controls(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    analyst_org_aggregate_run: RiskAnalysisRun,
) -> None:
    """AGGREGATE run with zero controls anywhere renders empty-state alert."""
    client, _ = authed_analyst
    resp = await client.get(f"/runs/{analyst_org_aggregate_run.id}")
    assert resp.status_code == 200
    html = resp.text
    # T7 redesign: the control-ledger heading is always rendered so users know
    # the section exists (was "Per-scenario control attribution").
    assert "What each control is worth" in html
    # Empty-state copy
    assert "No controls applied to any scenario in this run." in html
    # Matrix table NOT rendered
    assert "table-pin-rows" not in html
    # FAIR-CAM caveat is NOT rendered in the empty case (it lives inside
    # the matrix.controls if-block; would be confusing to talk about row
    # totals when there are no rows).
    assert "compose multiplicatively" not in html


@pytest.mark.asyncio
async def test_single_run_detail_does_not_render_aggregate_control_matrix(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    analyst_org_single_run: RiskAnalysisRun,
) -> None:
    """SINGLE run-detail must NOT leak the AGGREGATE matrix section."""
    client, _ = authed_analyst
    resp = await client.get(f"/runs/{analyst_org_single_run.id}")
    assert resp.status_code == 200
    html = resp.text
    assert "Per-scenario control attribution" not in html, (
        "SINGLE templates must not include the AGGREGATE matrix section"
    )


# ---- Task 2 (#353): 10-row tail ladder + Δ column ----------------------


_DIST_LABELS_10 = [
    "Mean",
    "Median",
    "Std dev",
    "VaR 90%",
    "VaR 95%",
    "VaR 99%",
    "VaR 99.9%",
    "ES 95%",
    "ES 99%",
    "ES 99.9%",
]


@pytest.mark.asyncio
async def test_aggregate_run_detail_renders_full_tail_ladder(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    analyst_org_aggregate_run: RiskAnalysisRun,
) -> None:
    """AGGREGATE detail page renders all 10 dist-stats rows + Δ column header.

    The live-executor fixture (mc_iterations_override=200) writes the full
    tail-metrics block (var_90/var_999/expected_shortfall), so has_tail=True
    and all 10 labels are present.
    """
    client, _ = authed_analyst
    resp = await client.get(f"/runs/{analyst_org_aggregate_run.id}")
    assert resp.status_code == 200
    html = resp.text
    # T7 redesign: the aggregate dist table maps canonical labels through
    # tail_ladder_display_labels (the SINGLE-run panel still uses raw labels,
    # see test_single_run_detail_renders_full_tail_ladder). Header is "Δ reduction".
    _display_labels_10 = [
        "Mean (average)",
        "Typical case (median)",
        "Std deviation",
        "1-in-10 year (VaR 90%)",
        "1-in-20 year (VaR 95%)",
        "1-in-100 year (VaR 99%)",
        "1-in-1000 year (VaR 99.9%)",
        "Expected shortfall (95%)",
        "Expected shortfall (99%)",
        "Expected shortfall (99.9%)",
    ]
    for label in _display_labels_10:
        assert label in html, f"Missing dist-stats display label: {label!r}"
    assert "Δ reduction" in html, "Δ reduction column header missing"


@pytest.mark.asyncio
async def test_aggregate_run_detail_with_controls_renders_numeric_pos(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    analyst_org_aggregate_run_with_controls: RiskAnalysisRun,
) -> None:
    """AGGREGATE run with controls shows text-numeric-pos for risk-reducing Δ."""
    client, _ = authed_analyst
    resp = await client.get(f"/runs/{analyst_org_aggregate_run_with_controls.id}")
    assert resp.status_code == 200
    html = resp.text
    assert "text-numeric-pos" in html, "No text-numeric-pos cell found (risk-reducing fixture)"


@pytest.mark.asyncio
async def test_single_run_detail_renders_full_tail_ladder(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    analyst_org_single_run: RiskAnalysisRun,
) -> None:
    """SINGLE detail page renders all 10 dist-stats rows + Δ column header.

    The live-executor fixture writes the full tail-metrics block.
    """
    client, _ = authed_analyst
    resp = await client.get(f"/runs/{analyst_org_single_run.id}")
    assert resp.status_code == 200
    html = resp.text
    for label in _DIST_LABELS_10:
        assert label in html, f"Missing dist-stats label: {label!r}"
    assert "Δ (reduction)" in html, "Δ (reduction) column header missing"


@pytest.mark.asyncio
async def test_aggregate_legacy_run_omits_tail_rows_shows_note(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_user: Any,
    seed_scenario_factory: Callable[..., Awaitable[Any]],
) -> None:
    """AGGREGATE legacy run (no var_90/expected_shortfall) suppresses tail rows
    and shows the 'not available for this run' note.

    Seeded with hand-built simulation_results lacking var_90/var_999/expected_shortfall
    to simulate a run persisted before the tail-metrics release.
    """

    _, org_id = authed_analyst
    s1 = await seed_scenario_factory(
        name="legacy-agg-s1", organization_id=org_id, created_by=seed_user.id
    )
    s2 = await seed_scenario_factory(
        name="legacy-agg-s2", organization_id=org_id, created_by=seed_user.id
    )

    # Build a minimal AGGREGATE simulation_results WITHOUT tail metrics
    legacy_sr = {
        "aggregate_without_controls": {
            "annualized_loss_expectancy": 2_000_000.0,
            "mean": 2_000_000.0,
            "median": 1_800_000.0,
            "std_deviation": 400_000.0,
            "var_95": 2_800_000.0,
            "var_99": 3_200_000.0,
            # no var_90, var_999, expected_shortfall
            "loss_exceedance_curve": [],
            "n_simulations": 200,
        },
        "aggregate_with_controls": {
            "annualized_loss_expectancy": 1_200_000.0,
            "mean": 1_200_000.0,
            "median": 1_050_000.0,
            "std_deviation": 250_000.0,
            "var_95": 1_700_000.0,
            "var_99": 2_000_000.0,
            # no var_90, var_999, expected_shortfall
            "loss_exceedance_curve": [],
            "n_simulations": 200,
        },
        "control_value": {"dollars": 800_000.0, "percent": 40.0},
        "confidence_intervals": {
            "lower_bound": 1_000_000.0,
            "upper_bound": 1_400_000.0,
            "interval_pct": 95,
            "sample_size": 200,
        },
        "n_scenarios": 2,
        "n_simulations": 200,
        "per_scenario": [],
        "dual_epc": {"with_controls": [], "without_controls": []},
    }

    from datetime import UTC, datetime

    from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType

    s1_id = str(s1.id)
    s2_id = str(s2.id)
    run = RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=org_id,
        mc_iterations=200,
        inputs_hash="legacy-agg-test-" + uuid.uuid4().hex[:8],
        controls_snapshot=[],
        control_ids_used=[],
        status=RunStatus.COMPLETED,
        run_type=RunType.AGGREGATE,
        created_by=seed_user.id,
        simulation_results=legacy_sr,
        completed_at=datetime.now(UTC),
        aggregate_scenario_ids=[s1_id, s2_id],
        aggregate_control_ids_per_scenario={s1_id: [], s2_id: []},
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)

    client, _ = authed_analyst
    resp = await client.get(f"/runs/{run.id}")
    assert resp.status_code == 200
    html = resp.text
    # Note is present
    assert "not available for this run" in html, "Legacy gating note missing"
    # Tail-only rows are absent
    assert "VaR 90%" not in html, "VaR 90% must be suppressed for legacy run"
    assert "ES 95%" not in html, "ES 95% must be suppressed for legacy run"


# ---- Task 5 (#419): weight-robustness ranges + explained secondary headline ----


def _agg_sr_with_tail(scenario_ids: tuple[str, str] | None = None) -> dict[str, Any]:
    """AGGREGATE simulation_results with tail metrics + a populated Shapley matrix.

    Includes per_scenario control_adjustments for controls c1/c2 (matching the
    weight_robustness per_control keys) so the per-control range table renders.
    """
    side = {
        "annualized_loss_expectancy": 2_000_000.0,
        "mean": 2_000_000.0,
        "median": 1_800_000.0,
        "std_deviation": 400_000.0,
        "var_90": 2_500_000.0,
        "var_95": 2_800_000.0,
        "var_99": 3_200_000.0,
        "var_999": 4_000_000.0,
        "expected_shortfall": {"es_95": 3_000_000.0, "es_99": 3_500_000.0, "es_999": 4_200_000.0},
        "loss_exceedance_curve": [],
        "n_simulations": 200,
    }
    with_side = {**side, "annualized_loss_expectancy": 1_200_000.0, "mean": 1_200_000.0}
    s1_id, s2_id = scenario_ids or ("s1", "s2")
    per_scenario = [
        {
            "scenario_id": s1_id,
            "scenario_name": "wr-agg-s1",
            "base_risk": {"annualized_loss_expectancy": 2_000_000.0},
            "residual_risk": {"annualized_loss_expectancy": 1_200_000.0},
            "control_adjustments": [
                {"control_id": "c1", "control_name": "Control One", "shapley_value": 120_000.0},
                {"control_id": "c2", "control_name": "Control Two", "shapley_value": 110_000.0},
            ],
        },
        {
            "scenario_id": s2_id,
            "scenario_name": "wr-agg-s2",
            "base_risk": {"annualized_loss_expectancy": 1_500_000.0},
            "residual_risk": {"annualized_loss_expectancy": 900_000.0},
            "control_adjustments": [
                {"control_id": "c1", "control_name": "Control One", "shapley_value": 100_000.0},
            ],
        },
    ]
    return {
        "aggregate_without_controls": side,
        "aggregate_with_controls": with_side,
        "control_value": {"dollars": 800_000.0, "percent": 40.0},
        "confidence_intervals": {
            "lower_bound": 1_000_000.0,
            "upper_bound": 1_400_000.0,
            "interval_pct": 95,
            "sample_size": 200,
        },
        "n_scenarios": 2,
        "n_simulations": 200,
        "per_scenario": per_scenario,
        "dual_epc": {"with_controls": [], "without_controls": []},
    }


def _weight_robustness_blob_with_flipped_pair() -> dict[str, Any]:
    """A weight_robustness blob with a banded headline + an indistinguishable pair."""
    return {
        "band": None,
        "canonical_value": None,
        "headline": {
            "reduction_p5": 550_000.0,
            "reduction_p50": 700_000.0,
            "reduction_p95": 900_000.0,
        },
        "per_control": {
            "c1": {
                "reduction_p5": 80_000.0,
                "reduction_p50": 120_000.0,
                "reduction_p95": 190_000.0,
                "rank_p50": 0,
                "rank_min": 0,
                "rank_max": 1,
                "stability_class": "unstable",
            },
            "c2": {
                "reduction_p5": 70_000.0,
                "reduction_p50": 110_000.0,
                "reduction_p95": 180_000.0,
                "rank_p50": 1,
                "rank_min": 0,
                "rank_max": 1,
                "stability_class": "unstable",
            },
        },
        "kendall_tau_p50": 0.5,
        "topk_preservation_k": 1,
        "topk_preservation_prob": 0.5,
        "indistinguishable_pairs": [["c1", "c2"]],
        "rank_stability_available": True,
        "draws_used": 64,
        "degraded": False,
        "state": "ok",
    }


@pytest.mark.asyncio
async def test_aggregate_run_detail_renders_weight_robustness_range_and_marker(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_user: Any,
    seed_scenario_factory: Callable[..., Awaitable[Any]],
) -> None:
    """Task 5 (#419): AGGREGATE run with weight_robustness renders the banded
    headline RANGE, the EXPLAINED secondary headline (median-below-mean), and an
    'indistinguishable' marker driven from indistinguishable_pairs — while keeping
    the MC-mean primary headline labeled as the point estimate (no bare unexplained
    representative-value dollar)."""
    from datetime import UTC, datetime

    from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType

    _, org_id = authed_analyst
    s1 = await seed_scenario_factory(
        name="wr-agg-s1", organization_id=org_id, created_by=seed_user.id
    )
    s2 = await seed_scenario_factory(
        name="wr-agg-s2", organization_id=org_id, created_by=seed_user.id
    )
    s1_id, s2_id = str(s1.id), str(s2.id)

    run = RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=org_id,
        mc_iterations=200,
        inputs_hash="wr-agg-" + uuid.uuid4().hex[:8],
        controls_snapshot=[],
        control_ids_used=[],
        status=RunStatus.COMPLETED,
        run_type=RunType.AGGREGATE,
        created_by=seed_user.id,
        simulation_results=_agg_sr_with_tail((s1_id, s2_id)),
        weight_robustness=_weight_robustness_blob_with_flipped_pair(),
        completed_at=datetime.now(UTC),
        aggregate_scenario_ids=[s1_id, s2_id],
        aggregate_control_ids_per_scenario={s1_id: [], s2_id: []},
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)

    client, _ = authed_analyst
    resp = await client.get(f"/runs/{run.id}")
    assert resp.status_code == 200
    html = resp.text

    # Banded headline range: center + [p5–p95] bracket.
    assert "[" in html and "–" in html and "]" in html, "headline range bracket missing"
    assert "$700k" in html or "$700,000" in html, "headline p50 range value missing"

    # EXPLAINED secondary headline (Meth-I3, plain-English): typical-case-below-average
    # wording must be present (not just two bare dollars).
    low = html.lower()
    assert "typical-case" in low and "average" in low, (
        "explained typical-case-vs-average wording missing"
    )
    assert "skew" in low, "skew explanation missing from secondary headline"

    # M2 (T7): the verdict-strip labels its headline "(mean)" (was "(average)").
    assert "(mean)" in low, "AGGREGATE robustness: verdict-strip headline not labeled '(mean)' (M2)"

    # Too-close-to-call marker driven from the PAIR set (Spec-I1).
    assert "too close to call" in low, "too-close-to-call pair marker missing"

    # Per-control value ranges now live in the control ledger (was "Estimated
    # value per control").
    assert "What each control is worth" in html, "control-ledger heading missing"
    assert "$120k" in html or "$120,000" in html, "per-control c1 p50 range value missing"

    # Reworded (plain-English) robustness disclaimer present.
    assert "modeled estimates shown as ranges" in low, "reworded robustness disclaimer missing"

    # M1 negative-match: no bare 1-decimal "$X (Y.Y%)" precision anywhere in the HTML.
    assert re.search(r"\$[\d,]+ \(\d+\.\d%\)", html) is None, (
        "1-decimal percentage '$X (Y.Y%)' found in AGGREGATE robustness HTML — M1 violation"
    )


@pytest.mark.asyncio
async def test_single_run_detail_renders_weight_robustness_range(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_user: Any,
    seed_scenario_factory: Callable[..., Awaitable[Any]],
) -> None:
    """Task 5 (#419): SINGLE run with weight_robustness renders the control-value
    RANGE with the median-below-mean explanation (ranges-only path, Meth-B6)."""
    from datetime import UTC, datetime

    from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType

    _, org_id = authed_analyst
    scenario = await seed_scenario_factory(
        name="wr-single", organization_id=org_id, created_by=seed_user.id
    )

    single_sr = {
        "base_risk": {
            "annualized_loss_expectancy": 1_500_000.0,
            "mean": 1_500_000.0,
            "median": 1_350_000.0,
            "std_deviation": 300_000.0,
            "var_90": 1_900_000.0,
            "var_95": 2_100_000.0,
            "var_99": 2_500_000.0,
            "var_999": 3_000_000.0,
            "expected_shortfall": {
                "es_95": 2_300_000.0,
                "es_99": 2_700_000.0,
                "es_999": 3_100_000.0,
            },
            "loss_event_frequency": 3.0,
            "loss_magnitude": 500_000.0,
            "n_simulations": 200,
            "simulation_results": [],
        },
        "residual_risk": {
            "annualized_loss_expectancy": 900_000.0,
            "mean": 900_000.0,
            "median": 810_000.0,
            "std_deviation": 180_000.0,
            "var_90": 1_100_000.0,
            "var_95": 1_260_000.0,
            "var_99": 1_500_000.0,
            "var_999": 1_900_000.0,
            "expected_shortfall": {
                "es_95": 1_400_000.0,
                "es_99": 1_700_000.0,
                "es_999": 2_000_000.0,
            },
            "loss_event_frequency": 1.5,
            "loss_magnitude": 600_000.0,
            "n_simulations": 200,
            "simulation_results": [],
        },
        "control_adjustments": [{"control_id": "c1", "effectiveness": 0.6}],
        "confidence_intervals": {
            "lower_bound": 750_000.0,
            "upper_bound": 1_050_000.0,
            "interval_pct": 95,
            "sample_size": 200,
        },
        "loss_exceedance_curve": [],
        "exceedance_probability_curve": [],
    }
    wr = {
        "band": None,
        "canonical_value": None,
        "headline": {
            "reduction_p5": 450_000.0,
            "reduction_p50": 600_000.0,
            "reduction_p95": 800_000.0,
        },
        # SINGLE ranges-only (Meth-B6): per_control carries dollar ranges but
        # stability is not computed → stability_class "not_applicable".
        "per_control": {
            "c1": {
                "reduction_p5": 90_000.0,
                "reduction_p50": 140_000.0,
                "reduction_p95": 210_000.0,
                "rank_p50": 0,
                "rank_min": 0,
                "rank_max": 0,
                "stability_class": "not_applicable",
            }
        },
        "kendall_tau_p50": None,
        "topk_preservation_k": None,
        "topk_preservation_prob": None,
        "indistinguishable_pairs": [],
        "rank_stability_available": False,
        "draws_used": 64,
        "degraded": False,
        "state": "ok",
    }

    run = RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=org_id,
        scenario_id=scenario.id,
        mc_iterations=200,
        inputs_hash="wr-single-" + uuid.uuid4().hex[:8],
        controls_snapshot=[
            {"snapshot_version": 2, "control_id": "c1", "name": "Control One", "assignments": []}
        ],
        control_ids_used=[],
        status=RunStatus.COMPLETED,
        run_type=RunType.SINGLE,
        created_by=seed_user.id,
        simulation_results=single_sr,
        weight_robustness=wr,
        completed_at=datetime.now(UTC),
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)

    client, _ = authed_analyst
    resp = await client.get(f"/runs/{run.id}")
    assert resp.status_code == 200
    html = resp.text
    low = html.lower()

    # Range bracket + p50 value.
    assert "[" in html and "–" in html and "]" in html, "SINGLE headline range bracket missing"
    assert "$600k" in html or "$600,000" in html, "SINGLE headline p50 range value missing"
    # Typical-case-below-average explanation present (no bare unexplained representative dollar).
    assert "typical-case" in low and "average" in low, (
        "SINGLE typical-case-vs-average explanation missing"
    )
    # M2 + #454 item 5: the orphaned floating "(average)" caption was folded
    # into the explainer-box heading ("the bars above show the average"). The
    # average label must still be present and tied to the bar chart, but no
    # longer as a dangling line. Assertion updated to the new wording.
    assert "bars above show the average" in low, (
        "SINGLE robustness: average label missing from explainer heading (M2 / #454)"
    )
    # Per-control range table renders with the control name + range; ranking is
    # "not assessed" (ranges-only SINGLE basis, Meth-B6). #454 item 6 reworded
    # the badge from "not checked" → "not assessed" (+ tooltip). Assertion
    # updated to the new copy.
    assert "Estimated value per control" in html, "SINGLE per-control range table heading missing"
    assert "Control One" in html, "SINGLE per-control name missing"
    assert "$140k" in html or "$140,000" in html, "SINGLE per-control p50 range value missing"
    assert "not assessed" in low, "SINGLE per-control ranking badge should read 'not assessed'"
    assert "Rank stability is assessed on multi-scenario runs" in html, (
        "SINGLE per-control ranking badge missing the #454 explanatory tooltip"
    )

    # M1 negative-match: no bare 1-decimal "$X (Y.Y%)" precision anywhere in the HTML.
    assert re.search(r"\$[\d,]+ \(\d+\.\d%\)", html) is None, (
        "1-decimal percentage '$X (Y.Y%)' found in SINGLE robustness HTML — M1 violation"
    )


# ---- Leave-one-out "if removed" column (display plumbing, 2026-07-03) ----


@pytest.mark.asyncio
async def test_aggregate_run_detail_renders_if_removed_column(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_user: Any,
    seed_scenario_factory: Callable[..., Awaitable[Any]],
) -> None:
    """AGGREGATE per-control value-range table gains an 'If removed' column:
    c1 carries if_removed_value on both scenarios (sums to $150k); c2 carries
    it on NEITHER scenario (renders '—', absent≠0.0). The extended legend
    distinguishing fair-share range from drop-cost also renders."""
    from datetime import UTC, datetime

    from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType

    _, org_id = authed_analyst
    s1 = await seed_scenario_factory(
        name="ir-agg-s1", organization_id=org_id, created_by=seed_user.id
    )
    s2 = await seed_scenario_factory(
        name="ir-agg-s2", organization_id=org_id, created_by=seed_user.id
    )
    s1_id, s2_id = str(s1.id), str(s2.id)

    sr = _agg_sr_with_tail((s1_id, s2_id))
    # c1: if_removed_value on BOTH scenarios -> sums to $150k.
    # c2: NO if_removed_value key anywhere -> None -> renders '—'.
    sr["per_scenario"][0]["control_adjustments"][0]["if_removed_value"] = 90_000.0  # c1 @ s1
    sr["per_scenario"][1]["control_adjustments"][0]["if_removed_value"] = 60_000.0  # c1 @ s2

    run = RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=org_id,
        mc_iterations=200,
        inputs_hash="ir-agg-" + uuid.uuid4().hex[:8],
        controls_snapshot=[],
        control_ids_used=[],
        status=RunStatus.COMPLETED,
        run_type=RunType.AGGREGATE,
        created_by=seed_user.id,
        simulation_results=sr,
        weight_robustness=_weight_robustness_blob_with_flipped_pair(),
        completed_at=datetime.now(UTC),
        aggregate_scenario_ids=[s1_id, s2_id],
        aggregate_control_ids_per_scenario={s1_id: [], s2_id: []},
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)

    client, _ = authed_analyst
    resp = await client.get(f"/runs/{run.id}")
    assert resp.status_code == 200
    html = resp.text

    assert "If removed" in html, "AGGREGATE 'If removed' column header missing"
    assert "$150k" in html or "$150,000" in html, "AGGREGATE if_removed summed value (c1) missing"

    # c2's row has NO if_removed_value on any scenario -> its If-removed cell is '—'.
    # T7: the control-ledger row markup carries CSS classes on the <tr>/<td>.
    c2_row = re.search(r"<tr[^>]*>\s*<td[^>]*>Control Two</td>.*?</tr>", html, re.DOTALL)
    assert c2_row is not None, "Control Two row not found in the control ledger"
    assert "—" in c2_row.group(0), "AGGREGATE absent if_removed (c2) should render '—'"

    # Extended legend (fair-share range vs drop-cost) renders alongside the
    # existing modeled-estimates disclaimer sentence.
    assert "modeled estimates shown as ranges" in html, "base disclaimer sentence missing"
    assert "fair share of the combined reduction" in html, "if-removed legend missing"
    assert "not the cost of removing the control" in html, "if-removed legend missing"
    assert (
        "increase in modeled annual loss if this control were dropped from the current portfolio"
        in html
    ), "if-removed legend missing the drop-cost sentence"


@pytest.mark.asyncio
async def test_single_run_detail_renders_if_removed_column(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_user: Any,
    seed_scenario_factory: Callable[..., Awaitable[Any]],
) -> None:
    """SINGLE per-control value-range table gains an 'If removed' column,
    reading the flat control_adjustments passthrough, and the extended legend
    renders alongside the base disclaimer."""
    from datetime import UTC, datetime

    from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType

    _, org_id = authed_analyst
    scenario = await seed_scenario_factory(
        name="ir-single", organization_id=org_id, created_by=seed_user.id
    )

    single_sr = {
        "base_risk": {
            "annualized_loss_expectancy": 1_500_000.0,
            "mean": 1_500_000.0,
            "median": 1_350_000.0,
            "std_deviation": 300_000.0,
            "var_90": 1_900_000.0,
            "var_95": 2_100_000.0,
            "var_99": 2_500_000.0,
            "var_999": 3_000_000.0,
            "expected_shortfall": {
                "es_95": 2_300_000.0,
                "es_99": 2_700_000.0,
                "es_999": 3_100_000.0,
            },
            "loss_event_frequency": 3.0,
            "loss_magnitude": 500_000.0,
            "n_simulations": 200,
            "simulation_results": [],
        },
        "residual_risk": {
            "annualized_loss_expectancy": 900_000.0,
            "mean": 900_000.0,
            "median": 810_000.0,
            "std_deviation": 180_000.0,
            "var_90": 1_100_000.0,
            "var_95": 1_260_000.0,
            "var_99": 1_500_000.0,
            "var_999": 1_900_000.0,
            "expected_shortfall": {
                "es_95": 1_400_000.0,
                "es_99": 1_700_000.0,
                "es_999": 2_000_000.0,
            },
            "loss_event_frequency": 1.5,
            "loss_magnitude": 600_000.0,
            "n_simulations": 200,
            "simulation_results": [],
        },
        # if_removed_value: negative case (weak-AND dilution, #453 policy) — must
        # render as-is with a minus sign, never clamped to $0.
        "control_adjustments": [
            {"control_id": "c1", "effectiveness": 0.6, "if_removed_value": -25_000.0}
        ],
        "confidence_intervals": {
            "lower_bound": 750_000.0,
            "upper_bound": 1_050_000.0,
            "interval_pct": 95,
            "sample_size": 200,
        },
        "loss_exceedance_curve": [],
        "exceedance_probability_curve": [],
    }
    wr: dict[str, Any] = {
        "band": None,
        "canonical_value": None,
        "headline": {
            "reduction_p5": 450_000.0,
            "reduction_p50": 600_000.0,
            "reduction_p95": 800_000.0,
        },
        "per_control": {
            "c1": {
                "reduction_p5": 90_000.0,
                "reduction_p50": 140_000.0,
                "reduction_p95": 210_000.0,
                "rank_p50": 0,
                "rank_min": 0,
                "rank_max": 0,
                "stability_class": "not_applicable",
            }
        },
        "kendall_tau_p50": None,
        "topk_preservation_k": None,
        "topk_preservation_prob": None,
        "indistinguishable_pairs": [],
        "rank_stability_available": False,
        "draws_used": 64,
        "degraded": False,
        "state": "ok",
    }

    run = RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=org_id,
        scenario_id=scenario.id,
        mc_iterations=200,
        inputs_hash="ir-single-" + uuid.uuid4().hex[:8],
        controls_snapshot=[
            {"snapshot_version": 2, "control_id": "c1", "name": "Control One", "assignments": []}
        ],
        control_ids_used=[],
        status=RunStatus.COMPLETED,
        run_type=RunType.SINGLE,
        created_by=seed_user.id,
        simulation_results=single_sr,
        weight_robustness=wr,
        completed_at=datetime.now(UTC),
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)

    client, _ = authed_analyst
    resp = await client.get(f"/runs/{run.id}")
    assert resp.status_code == 200
    html = resp.text

    assert "If removed" in html, "SINGLE 'If removed' column header missing"
    # Negative if_removed_value renders with a minus sign, never clamped to $0.
    assert "-$25k" in html or "-$25,000" in html or "−$25k" in html, (
        "SINGLE negative if_removed value not rendered as-is"
    )

    assert "fair share of the combined reduction" in html, "if-removed legend missing"
    assert "not the cost of removing the control" in html, "if-removed legend missing"
    assert (
        "increase in modeled annual loss if this control were dropped from the current portfolio"
        in html
    ), "if-removed legend missing the drop-cost sentence"


# ---- Final display slice (2026-07-04): mean+typical side-by-side rendering ----


@pytest.mark.asyncio
async def test_aggregate_run_detail_mean_basis_renders_value_range_and_typical_pairing(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_user: Any,
    seed_scenario_factory: Callable[..., Awaitable[Any]],
) -> None:
    """A mean-basis run (weight_robustness basis=="mean", matrix cells carrying
    shapley_value_mean) must NOT call its ranges/attribution "typical case" —
    they are on the same average basis as the headline. The verdict strip says
    "value range", the control ledger intro says "average case", both surface a
    muted "typical $X" pairing sub-line, and the run-level pairing note
    (MEAN_BASIS_PAIRING_NOTE) renders somewhere on the page."""
    from datetime import UTC, datetime

    from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType
    from idraa.services._view_model_helpers import MEAN_BASIS_PAIRING_NOTE

    _, org_id = authed_analyst
    s1 = await seed_scenario_factory(
        name="wr-mean-agg-s1", organization_id=org_id, created_by=seed_user.id
    )
    s2 = await seed_scenario_factory(
        name="wr-mean-agg-s2", organization_id=org_id, created_by=seed_user.id
    )
    s1_id, s2_id = str(s1.id), str(s2.id)

    sr = _agg_sr_with_tail((s1_id, s2_id))
    # Add shapley_value_mean (-> matrix.basis == "mean") + a paired typical
    # if_removed figure alongside the mean-basis primary.
    sr["per_scenario"][0]["control_adjustments"][0].update(
        {
            "shapley_value_mean": 150_000.0,
            "if_removed_value_mean": 130_000.0,
            "if_removed_value": 90_000.0,
        }
    )
    sr["per_scenario"][0]["control_adjustments"][1]["shapley_value_mean"] = 140_000.0
    sr["per_scenario"][1]["control_adjustments"][0].update(
        {
            "shapley_value_mean": 130_000.0,
            "if_removed_value_mean": 70_000.0,
            "if_removed_value": 60_000.0,
        }
    )

    wr = _weight_robustness_blob_with_flipped_pair()
    wr["basis"] = "mean"
    # Paired typical-basis canonical point per control (2026-07-04 side-by-side).
    wr["canonical_value_typical"] = {"c1": 95_000.0, "c2": 85_000.0}

    run = RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=org_id,
        mc_iterations=200,
        inputs_hash="wr-mean-agg-" + uuid.uuid4().hex[:8],
        controls_snapshot=[],
        control_ids_used=[],
        status=RunStatus.COMPLETED,
        run_type=RunType.AGGREGATE,
        created_by=seed_user.id,
        simulation_results=sr,
        weight_robustness=wr,
        completed_at=datetime.now(UTC),
        aggregate_scenario_ids=[s1_id, s2_id],
        aggregate_control_ids_per_scenario={s1_id: [], s2_id: []},
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)

    client, _ = authed_analyst
    resp = await client.get(f"/runs/{run.id}")
    assert resp.status_code == 200
    html = resp.text
    low = html.lower()

    # Verdict strip sub-line: "value range {range}", NOT "typical case {range}".
    assert "value range" in low, "mean-basis verdict strip should say 'value range'"

    # Control ledger intro: "average case", NOT "(typical case)".
    assert "average case" in low, "mean-basis control ledger intro should say 'average case'"

    # Mean-basis pairing note (embedded by identity, per repo convention).
    assert MEAN_BASIS_PAIRING_NOTE in html, "mean-basis pairing note missing from the page"

    # Muted "typical $X" pairing sub-line renders somewhere in the per-control
    # ledger (fair-share/range and/or if-removed cells).
    assert "typical $" in html, "no muted typical-case pairing sub-line rendered"


@pytest.mark.asyncio
async def test_aggregate_legacy_basis_never_renders_typical_pairing_subline(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_user: Any,
    seed_scenario_factory: Callable[..., Awaitable[Any]],
) -> None:
    """SWE-review negative pin (2026-07-04): on a TRUE legacy run (no *_mean
    adjustment keys, no blob basis key) the paired "typical $X" sub-lines must
    NOT render — the ledger's typical fields are populated from the SAME
    shapley_value the legacy primary shows, so an ungated sub-line would
    double-print the same figure. Locks the matrix.basis == "mean" gates in
    control_ledger.html / data_grid.html (a naive is-not-none 'simplification'
    turns this red)."""
    from datetime import UTC, datetime

    from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType
    from idraa.services._view_model_helpers import MEAN_BASIS_PAIRING_NOTE

    _, org_id = authed_analyst
    s1 = await seed_scenario_factory(
        name="wr-legacy-agg-s1", organization_id=org_id, created_by=seed_user.id
    )
    s2 = await seed_scenario_factory(
        name="wr-legacy-agg-s2", organization_id=org_id, created_by=seed_user.id
    )
    s1_id, s2_id = str(s1.id), str(s2.id)

    sr = _agg_sr_with_tail((s1_id, s2_id))  # legacy shape: shapley_value only
    wr = _weight_robustness_blob_with_flipped_pair()  # legacy: no basis key
    assert "basis" not in wr and "canonical_value_typical" not in wr

    run = RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=org_id,
        mc_iterations=200,
        inputs_hash="wr-legacy-agg-" + uuid.uuid4().hex[:8],
        controls_snapshot=[],
        control_ids_used=[],
        status=RunStatus.COMPLETED,
        run_type=RunType.AGGREGATE,
        created_by=seed_user.id,
        simulation_results=sr,
        weight_robustness=wr,
        completed_at=datetime.now(UTC),
        aggregate_scenario_ids=[s1_id, s2_id],
        aggregate_control_ids_per_scenario={s1_id: [], s2_id: []},
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)

    client, _ = authed_analyst
    resp = await client.get(f"/runs/{run.id}")
    assert resp.status_code == 200
    html = resp.text

    assert "typical $" not in html, (
        "legacy run must NOT render paired 'typical $' sub-lines (double-print of the same figure)"
    )
    assert MEAN_BASIS_PAIRING_NOTE not in html, (
        "mean-basis pairing note must not render on a legacy run"
    )
    # Legacy copy retained (byte-identical branch), not the mean-basis rewording.
    assert "average case" not in html.lower()


@pytest.mark.asyncio
async def test_single_run_detail_mean_basis_renders_value_range_and_typical_pairing(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_user: Any,
    seed_scenario_factory: Callable[..., Awaitable[Any]],
) -> None:
    """SINGLE-run equivalent of the AGGREGATE mean-basis test above: the
    headline explainer box drops the "typical-case" claim in favor of "same
    average basis" copy, and the per-control table pairs a muted typical
    sub-line for both the value range and the if-removed cell."""
    from datetime import UTC, datetime

    from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType
    from idraa.services._view_model_helpers import MEAN_BASIS_PAIRING_NOTE

    _, org_id = authed_analyst
    scenario = await seed_scenario_factory(
        name="wr-mean-single", organization_id=org_id, created_by=seed_user.id
    )

    single_sr = {
        "base_risk": {
            "annualized_loss_expectancy": 1_500_000.0,
            "mean": 1_500_000.0,
            "median": 1_350_000.0,
            "std_deviation": 300_000.0,
            "loss_event_frequency": 3.0,
            "loss_magnitude": 500_000.0,
            "n_simulations": 200,
            "simulation_results": [],
        },
        "residual_risk": {
            "annualized_loss_expectancy": 900_000.0,
            "mean": 900_000.0,
            "median": 810_000.0,
            "std_deviation": 180_000.0,
            "loss_event_frequency": 1.5,
            "loss_magnitude": 600_000.0,
            "n_simulations": 200,
            "simulation_results": [],
        },
        "control_adjustments": [
            {
                "control_id": "c1",
                "effectiveness": 0.6,
                "if_removed_value_mean": 130_000.0,
                "if_removed_value": 90_000.0,
            }
        ],
        "confidence_intervals": {
            "lower_bound": 750_000.0,
            "upper_bound": 1_050_000.0,
            "interval_pct": 95,
            "sample_size": 200,
        },
        "loss_exceedance_curve": [],
        "exceedance_probability_curve": [],
    }
    wr: dict[str, Any] = {
        "band": None,
        "canonical_value": None,
        "basis": "mean",
        "headline": {
            "reduction_p5": 450_000.0,
            "reduction_p50": 600_000.0,
            "reduction_p95": 800_000.0,
        },
        "per_control": {
            "c1": {
                "reduction_p5": 90_000.0,
                "reduction_p50": 140_000.0,
                "reduction_p95": 210_000.0,
                "rank_p50": 0,
                "rank_min": 0,
                "rank_max": 0,
                "stability_class": "not_applicable",
            }
        },
        "canonical_value_typical": {"c1": 95_000.0},
        "kendall_tau_p50": None,
        "topk_preservation_k": None,
        "topk_preservation_prob": None,
        "indistinguishable_pairs": [],
        "rank_stability_available": False,
        "draws_used": 64,
        "degraded": False,
        "state": "ok",
    }

    run = RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=org_id,
        scenario_id=scenario.id,
        mc_iterations=200,
        inputs_hash="wr-mean-single-" + uuid.uuid4().hex[:8],
        controls_snapshot=[
            {"snapshot_version": 2, "control_id": "c1", "name": "Control One", "assignments": []}
        ],
        control_ids_used=[],
        status=RunStatus.COMPLETED,
        run_type=RunType.SINGLE,
        created_by=seed_user.id,
        simulation_results=single_sr,
        weight_robustness=wr,
        completed_at=datetime.now(UTC),
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)

    client, _ = authed_analyst
    resp = await client.get(f"/runs/{run.id}")
    assert resp.status_code == 200
    html = resp.text
    low = html.lower()

    # Headline explainer box: "same average basis", NOT "typical-case value range".
    assert "same average basis" in low, "mean-basis SINGLE explainer should drop 'typical-case'"
    assert "typical-case value range" not in low, (
        "mean-basis SINGLE explainer must not claim 'typical-case value range'"
    )

    # Mean-basis pairing note renders.
    assert MEAN_BASIS_PAIRING_NOTE in html, "mean-basis pairing note missing from SINGLE page"

    # Per-control table pairs a muted typical sub-line (range and/or if-removed).
    assert "typical $" in html, "no muted typical-case pairing sub-line rendered (SINGLE)"


@pytest.mark.asyncio
async def test_single_legacy_run_omits_tail_rows_shows_note(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_user: Any,
    seed_scenario_factory: Callable[..., Awaitable[Any]],
) -> None:
    """SINGLE legacy run (no var_90/expected_shortfall) suppresses tail rows
    and shows the 'not available for this run' note.
    """
    from datetime import UTC, datetime

    from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType

    _, org_id = authed_analyst
    scenario = await seed_scenario_factory(
        name="legacy-single-test", organization_id=org_id, created_by=seed_user.id
    )

    # Legacy-shaped simulation_results: has var_95/var_99 but NOT var_90/var_999/expected_shortfall
    legacy_sr = {
        "base_risk": {
            "annualized_loss_expectancy": 1_500_000.0,
            "mean": 1_500_000.0,
            "median": 1_350_000.0,
            "std_deviation": 300_000.0,
            "var_95": 2_100_000.0,
            "var_99": 2_500_000.0,
            # no var_90, var_999, expected_shortfall
            "loss_event_frequency": 3.0,
            "loss_magnitude": 500_000.0,
            "n_simulations": 200,
            "simulation_results": [],
        },
        "residual_risk": {
            "annualized_loss_expectancy": 900_000.0,
            "mean": 900_000.0,
            "median": 810_000.0,
            "std_deviation": 180_000.0,
            "var_95": 1_260_000.0,
            "var_99": 1_500_000.0,
            # no var_90, var_999, expected_shortfall
            "loss_event_frequency": 1.5,
            "loss_magnitude": 600_000.0,
            "n_simulations": 200,
            "simulation_results": [],
        },
        "control_adjustments": [],
        "confidence_intervals": {
            "lower_bound": 750_000.0,
            "upper_bound": 1_050_000.0,
            "interval_pct": 95,
            "sample_size": 200,
        },
        "loss_exceedance_curve": [],
        "exceedance_probability_curve": [],
    }

    run = RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=org_id,
        scenario_id=scenario.id,
        mc_iterations=200,
        inputs_hash="legacy-single-test-" + uuid.uuid4().hex[:8],
        controls_snapshot=[],
        control_ids_used=[],
        status=RunStatus.COMPLETED,
        run_type=RunType.SINGLE,
        created_by=seed_user.id,
        simulation_results=legacy_sr,
        completed_at=datetime.now(UTC),
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)

    client, _ = authed_analyst
    resp = await client.get(f"/runs/{run.id}")
    assert resp.status_code == 200
    html = resp.text
    # Note is present
    assert "not available for this run" in html, "Legacy gating note missing"
    # Tail-only rows are absent
    assert "VaR 90%" not in html, "VaR 90% must be suppressed for legacy run"
    assert "ES 95%" not in html, "ES 95% must be suppressed for legacy run"
