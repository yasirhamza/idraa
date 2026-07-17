"""Route-level tests for GET / (omicron-1).

All tests use authed_admin / authed_analyst / authed_reviewer / authed_viewer
tuple-form fixtures (NOT admin_client + organization, which would create
two distinct orgs and make require_sole_org nondeterministic).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Awaitable, Callable
from decimal import Decimal
from pathlib import Path

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.attack import ScenarioAttackMapping
from idraa.models.control import Control
from idraa.models.control_function_assignment import ControlFunctionAssignment
from idraa.models.enums import (
    AssetClass,
    ControlType,
    FairCamSubFunction,
    IndustrySubSector,
    ThreatActorType,
    ThreatCategory,
)
from idraa.models.framework_crosswalk import FrameworkControl
from idraa.models.organization import Organization
from idraa.models.risk_analysis_run import RunStatus
from idraa.models.scenario_library import ScenarioLibraryEntry
from idraa.models.user import User
from idraa.services.dashboard import build_dashboard
from idraa.services.fx_rates import FxRateService
from tests.integration._dashboard_fixtures import (
    _make_completed_aggregate_run,
    _make_completed_single_run,
    _make_scenario,
)
from tests.models.test_attack_models import _tactic, _technique

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "src" / "idraa" / "templates"

# ---------- 1. Cold-start RBAC matrix ----------


async def test_dashboard_cold_start_admin_renders_with_ctas(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    client, _ = authed_admin
    r = await client.get("/")
    assert r.status_code == 200
    assert "Dashboard" in r.text
    assert "Run aggregate analysis" in r.text


async def test_dashboard_cold_start_analyst_renders_with_ctas(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
) -> None:
    client, _ = authed_analyst
    r = await client.get("/")
    assert r.status_code == 200
    assert "Run aggregate analysis" in r.text


async def test_dashboard_cold_start_reviewer_hides_ctas(
    authed_reviewer: tuple[AsyncClient, uuid.UUID],
) -> None:
    # Plan used @parametrize+getfixturevalue, which fails for async fixtures
    # under pytest-asyncio (Runner.run from running loop). Split per-role.
    client, _ = authed_reviewer
    r = await client.get("/")
    assert r.status_code == 200
    assert "Run aggregate analysis" not in r.text
    assert "Run your first analysis" not in r.text


async def test_dashboard_cold_start_viewer_hides_ctas(
    authed_viewer: tuple[AsyncClient, uuid.UUID],
) -> None:
    client, _ = authed_viewer
    r = await client.get("/")
    assert r.status_code == 200
    assert "Run aggregate analysis" not in r.text
    assert "Run your first analysis" not in r.text


# ---------- 2. Unauthenticated redirect ----------


async def test_dashboard_unauthenticated_redirects_to_login(
    anonymous_client: AsyncClient,
    admin_user: User,
    db_session: AsyncSession,
) -> None:
    # admin_user seeds a user so setup_guard does not 307→/setup; the route
    # then runs require_user → 401 → _auth_redirect_handler → 303 /login.
    # The factories.create_user only flushes, so commit explicitly so the
    # client's separate engine can observe the User row.
    await db_session.commit()
    r = await anonymous_client.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login")


# ---------- 3. Populated AGGREGATE ----------


async def test_dashboard_with_completed_aggregate_populates_cards_with_runs_link(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_admin
    s1, s2 = uuid.uuid4(), uuid.uuid4()
    run = _make_completed_aggregate_run(
        org_id=org_id,
        name="My Portfolio",
        scenario_ids=[s1, s2],
        ale_with_controls=100_000.0,
        ale_without_controls=500_000.0,
        control_value_dollars=400_000.0,
        control_value_percent=80.0,
    )
    db_session.add(run)
    await db_session.commit()

    r = await client.get("/")
    assert r.status_code == 200
    # Task 4 dashboard redesign (#476-#480): the old KPI-tile "Control Value"
    # label + full-form "$400,000" figure were replaced by the posture band's
    # "Control value / year" support card, rendered COMPACT via the money
    # filter ("$400k", not "$400,000").
    assert "Control value" in r.text
    assert "$400k" in r.text
    assert "$400,000" not in r.text
    assert "My Portfolio" in r.text
    # Critical: link target is /runs/{id}, NOT /analyses/{id}
    assert f'href="/runs/{run.id}"' in r.text
    assert f'href="/analyses/{run.id}"' not in r.text


async def test_dashboard_control_value_tile_shows_provenance_disclaimer(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Issue #413: the dashboard Control Value tile renders a control-value $
    figure that rests on implementation-calibrated composition weights, so the
    canonical provenance disclaimer must appear on the dashboard alongside it."""
    from idraa.services._view_model_helpers import CONTROL_WEIGHT_PROVENANCE_DISCLAIMER_BASE

    client, org_id = authed_admin
    run = _make_completed_aggregate_run(
        org_id=org_id,
        name="My Portfolio",
        scenario_ids=[uuid.uuid4(), uuid.uuid4()],
        ale_with_controls=100_000.0,
        ale_without_controls=500_000.0,
        control_value_dollars=400_000.0,
        control_value_percent=80.0,
    )
    db_session.add(run)
    await db_session.commit()

    r = await client.get("/")
    assert r.status_code == 200
    # Task 4 dashboard redesign: label moved from "Control Value" (KPI tile)
    # to "Control value / year" (posture band support card).
    assert "Control value" in r.text
    # M4 fix (#419): robustness-absent runs render the BASE variant (first sentence only);
    # the "indistinguishable" caveat is gated on weight_robustness being present.
    assert CONTROL_WEIGHT_PROVENANCE_DISCLAIMER_BASE in r.text


async def test_dashboard_cold_start_omits_provenance_disclaimer(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    """Issue #413: with no aggregate run there is no Control Value $ figure, so
    the disclaimer must NOT render (it is gated on data.control_value)."""
    from idraa.services._view_model_helpers import CONTROL_WEIGHT_PROVENANCE_DISCLAIMER

    client, _ = authed_admin
    r = await client.get("/")
    assert r.status_code == 200
    assert CONTROL_WEIGHT_PROVENANCE_DISCLAIMER not in r.text


async def test_dashboard_control_value_tile_mean_basis_shows_value_range_not_typical_case(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Final display slice (2026-07-04): the Control Value tile's weight_robustness
    sub-line reads services/dashboard.py's ``agg_view["weight_robustness"]`` — a
    mean-basis run's range is on the SAME average basis as the tile's headline
    dollar figure above it, so the sub-line must say "value range", not the
    legacy "typical-case value range" (which would misdescribe a mean-basis
    figure as a separate typical-case measurement)."""
    client, org_id = authed_admin
    run = _make_completed_aggregate_run(
        org_id=org_id,
        name="Mean-basis Portfolio",
        scenario_ids=[uuid.uuid4(), uuid.uuid4()],
        ale_with_controls=100_000.0,
        ale_without_controls=500_000.0,
        control_value_dollars=400_000.0,
        control_value_percent=80.0,
    )
    run.weight_robustness = {
        "basis": "mean",
        "band": None,
        "canonical_value": None,
        "headline": {
            "reduction_p5": 300_000.0,
            "reduction_p50": 400_000.0,
            "reduction_p95": 500_000.0,
        },
        "per_control": {},
        "kendall_tau_p50": None,
        "topk_preservation_k": None,
        "topk_preservation_prob": None,
        "indistinguishable_pairs": [],
        "rank_stability_available": False,
        "draws_used": 64,
        "degraded": False,
        "state": "ok",
    }
    db_session.add(run)
    await db_session.commit()

    r = await client.get("/")
    assert r.status_code == 200
    low = r.text.lower()
    assert "value range (same average basis" in low, (
        "mean-basis dashboard tile should say 'value range', not 'typical-case value range'"
    )
    assert "typical-case value range" not in low


async def test_dashboard_ale_column_header_has_space_between_currency_and_ale(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Regression: the runs-table ALE column header is "<CODE> ALE", not the
    concatenated "USDALE". The label is built from data.currency.code ~ " ALE"
    in dashboard/index.html; a missing space rendered "USDALE"."""
    client, org_id = authed_admin
    run = _make_completed_aggregate_run(
        org_id=org_id,
        name="Header Check Portfolio",
        scenario_ids=[uuid.uuid4(), uuid.uuid4()],
        ale_with_controls=100_000.0,
        ale_without_controls=500_000.0,
        control_value_dollars=400_000.0,
        control_value_percent=80.0,
    )
    db_session.add(run)
    await db_session.commit()

    r = await client.get("/")
    assert r.status_code == 200
    assert "USDALE" not in r.text
    assert "USD ALE" in r.text


# ---------- 4. SINGLE-only fallback + chart-renders-cleanly ----------


async def test_dashboard_single_only_populates_top_scenarios_with_real_scenario_names(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Uses _make_scenario directly (NOT seed_scenario_factory which is
    bound to seed_organization, a DIFFERENT org from authed_admin's)."""
    client, org_id = authed_admin
    s1 = _make_scenario(org_id=org_id, name="Ransomware Q2")
    s2 = _make_scenario(org_id=org_id, name="Insider Threat")
    db_session.add_all([s1, s2])
    await db_session.flush()  # populate scenarios before runs FK
    db_session.add_all(
        [
            _make_completed_single_run(
                org_id=org_id, name="r1", scenario_id=s1.id, residual_ale=300.0
            ),
            _make_completed_single_run(
                org_id=org_id, name="r2", scenario_id=s2.id, residual_ale=700.0
            ),
        ]
    )
    await db_session.commit()

    r = await client.get("/")
    assert r.status_code == 200
    # Top scenarios labeled by Scenario.name (not run name)
    assert "Insider Threat" in r.text  # Highest ALE = first bar
    assert "Ransomware Q2" in r.text
    # Aggregate cards remain empty
    assert "No aggregate run yet" in r.text
    # F14 macro must elide the without-controls trace on fallback path
    assert "Without controls" not in r.text


# ---------- 5. All-five-status feed ----------


async def test_dashboard_recent_runs_shows_all_five_statuses_with_em_dash(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Status badge rendering does not require Scenario lookup; an
    arbitrary uuid for scenario_id is fine — the status assertion is
    independent of fallback-name resolution. A backing Scenario row is
    seeded so the SQLite FK on RiskAnalysisRun.scenario_id is satisfied."""
    client, org_id = authed_admin
    scenario = _make_scenario(org_id=org_id, name="StatusFeedScenario")
    db_session.add(scenario)
    await db_session.flush()
    sid = scenario.id
    base_time = dt.datetime(2026, 5, 1, tzinfo=dt.UTC)
    statuses_with_results = [
        (RunStatus.QUEUED, "queued_X9F2", None),
        (RunStatus.RUNNING, "running_X9F2", None),
        (
            RunStatus.COMPLETED,
            "completed_X9F2",
            {
                "base_risk": {"annualized_loss_expectancy": 2468.0},
                "residual_risk": {"annualized_loss_expectancy": 1234.0},
            },
        ),
        (RunStatus.FAILED, "failed_X9F2", None),
        (RunStatus.CANCELLED, "cancelled_X9F2", None),
    ]
    for i, (status, name, sim_results) in enumerate(statuses_with_results):
        run = _make_completed_single_run(
            org_id=org_id,
            name=name,
            scenario_id=sid,
            residual_ale=1234.0,
            created_at=base_time + dt.timedelta(hours=i),
        )
        # Override status + simulation_results post-construction.
        run.status = status
        run.simulation_results = sim_results
        db_session.add(run)
    await db_session.commit()

    r = await client.get("/")
    assert r.status_code == 200
    for _, name, _ in statuses_with_results:
        assert name in r.text
    # Em dash for the four non-COMPLETED rows
    em_count = r.text.count("&mdash;") + r.text.count("—")
    assert em_count >= 4


# ---------- 6. Truncation ----------


async def test_dashboard_recent_runs_truncated_to_10(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_admin
    scenario = _make_scenario(org_id=org_id, name="TruncationScenario")
    db_session.add(scenario)
    await db_session.flush()
    sid = scenario.id
    base_time = dt.datetime(2026, 5, 1, tzinfo=dt.UTC)
    runs = [
        _make_completed_single_run(
            org_id=org_id,
            scenario_id=sid,
            residual_ale=float(i),
            name=f"r_{i:02d}",
            created_at=base_time + dt.timedelta(hours=i),
        )
        for i in range(15)
    ]
    db_session.add_all(runs)
    await db_session.commit()

    r = await client.get("/")
    assert r.status_code == 200
    assert "r_14" in r.text  # newest
    assert "r_00" not in r.text  # truncated


async def test_dashboard_top_scenarios_caps_at_five_with_exact_subtitle(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_admin
    scenarios = [_make_scenario(org_id=org_id, name=f"Scenario {i}") for i in range(7)]
    db_session.add_all(scenarios)
    await db_session.flush()
    for i, s in enumerate(scenarios):
        db_session.add(
            _make_completed_single_run(
                org_id=org_id,
                scenario_id=s.id,
                residual_ale=float(100 - i),
                name=f"run_{i}",
            )
        )
    await db_session.commit()

    r = await client.get("/")
    assert r.status_code == 200
    assert "Top 5 of 7" in r.text  # Tight: substring is unique enough


# ---------- 7. Revenue formatting ----------


async def test_dashboard_revenue_set_renders_pct(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_admin
    org = (
        await db_session.execute(select(Organization).where(Organization.id == org_id))
    ).scalar_one()
    org.annual_revenue = Decimal("100000000")
    s1, s2 = uuid.uuid4(), uuid.uuid4()
    db_session.add(
        _make_completed_aggregate_run(
            org_id=org_id,
            name="P",
            scenario_ids=[s1, s2],
            ale_with_controls=100_000.0,
            ale_without_controls=500_000.0,
            control_value_dollars=400_000.0,
            control_value_percent=80.0,
        )
    )
    await db_session.commit()

    r = await client.get("/")
    assert r.status_code == 200
    assert "0.10% of annual revenue" in r.text


async def test_dashboard_revenue_unset_admin_sees_org_profile_link(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_admin
    s1, s2 = uuid.uuid4(), uuid.uuid4()
    db_session.add(
        _make_completed_aggregate_run(
            org_id=org_id,
            name="P",
            scenario_ids=[s1, s2],
            ale_with_controls=100_000.0,
            ale_without_controls=500_000.0,
            control_value_dollars=400_000.0,
            control_value_percent=80.0,
        )
    )
    await db_session.commit()

    r = await client.get("/")
    assert r.status_code == 200
    assert "Set annual revenue" in r.text
    # Admin sees the dashboard-side "Organization profile" link
    # (admin_only macro). Plain "Organization" nav link is unconditional.
    assert "Organization profile" in r.text
    assert 'href="/organization"' in r.text


async def _assert_revenue_unset_no_profile_link(
    client: AsyncClient,
    org_id: uuid.UUID,
    db_session: AsyncSession,
) -> None:
    """Shared body — non-admin sees "Set annual revenue" subtitle but
    NOT the dashboard's contextual Organization-profile link (admin_only
    macro hides it). The base navbar always includes a top-level
    /organization link, so we assert on the "Organization profile" anchor
    text specifically — that's the dashboard-side admin-only link.
    """
    s1, s2 = uuid.uuid4(), uuid.uuid4()
    db_session.add(
        _make_completed_aggregate_run(
            org_id=org_id,
            name="P",
            scenario_ids=[s1, s2],
            ale_with_controls=100_000.0,
            ale_without_controls=500_000.0,
            control_value_dollars=400_000.0,
            control_value_percent=80.0,
        )
    )
    await db_session.commit()

    r = await client.get("/")
    assert r.status_code == 200
    assert "Set annual revenue" in r.text
    # Dashboard-specific link text is "Organization profile" (in residual subtitle).
    # Nav has plain "Organization" — distinguishable.
    assert "Organization profile" not in r.text


async def test_dashboard_revenue_unset_analyst_no_org_profile_link(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    # Plan used @parametrize+getfixturevalue across analyst/reviewer/viewer;
    # async fixtures under pytest-asyncio break that pattern. Split per-role.
    client, org_id = authed_analyst
    await _assert_revenue_unset_no_profile_link(client, org_id, db_session)


async def test_dashboard_revenue_unset_reviewer_no_org_profile_link(
    authed_reviewer: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_reviewer
    await _assert_revenue_unset_no_profile_link(client, org_id, db_session)


async def test_dashboard_revenue_unset_viewer_no_org_profile_link(
    authed_viewer: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_viewer
    await _assert_revenue_unset_no_profile_link(client, org_id, db_session)


# ---------- 8. IDOR matrix ----------


async def test_dashboard_idor_recent_runs_no_cross_org_leakage(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_organization_factory: Callable[..., Awaitable[Organization]],
) -> None:
    """Own-org scenario is built via _make_scenario in authed_admin's
    org (NOT seed_scenario_with_controls, which is in seed_organization
    — a different org)."""
    client, org_id = authed_admin
    other_org = await seed_organization_factory(name="OtherInc")
    own_scenario = _make_scenario(org_id=org_id, name="own_scenario")
    cross_org_scenario = _make_scenario(org_id=other_org.id, name="cross_scenario")
    db_session.add_all([own_scenario, cross_org_scenario])
    await db_session.flush()

    # Cross-org canary
    db_session.add(
        _make_completed_single_run(
            org_id=other_org.id,
            scenario_id=cross_org_scenario.id,
            residual_ale=999.0,
            name="OTHER_ORG_LEAK_CANARY_K7P3",
        )
    )
    # Own-org control row
    db_session.add(
        _make_completed_single_run(
            org_id=org_id,
            scenario_id=own_scenario.id,
            residual_ale=100.0,
            name="OWN_RUN_VISIBLE_X9F2",
        )
    )
    await db_session.commit()

    r = await client.get("/")
    assert r.status_code == 200
    assert "OWN_RUN_VISIBLE_X9F2" in r.text
    assert "OTHER_ORG_LEAK_CANARY_K7P3" not in r.text


async def test_dashboard_idor_aggregate_card_no_cross_org_leakage(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_organization_factory: Callable[..., Awaitable[Organization]],
) -> None:
    """Critical: latest_aggregate_for_org dropping the org filter would
    surface another org's headline. Use a distinctive control_value_dollars
    as a canary."""
    client, org_id = authed_admin
    other_org = await seed_organization_factory(name="OtherInc")
    s1o, s2o = uuid.uuid4(), uuid.uuid4()
    db_session.add(
        _make_completed_aggregate_run(
            org_id=other_org.id,
            name="OTHER_ORG_AGG",
            scenario_ids=[s1o, s2o],
            ale_with_controls=42.0,
            ale_without_controls=84.0,
            control_value_dollars=999_999_999.0,
            control_value_percent=50.0,
        )
    )
    s1, s2 = uuid.uuid4(), uuid.uuid4()
    db_session.add(
        _make_completed_aggregate_run(
            org_id=org_id,
            name="OUR_AGG",
            scenario_ids=[s1, s2],
            ale_with_controls=100_000.0,
            ale_without_controls=500_000.0,
            control_value_dollars=400_000.0,
            control_value_percent=80.0,
        )
    )
    await db_session.commit()

    r = await client.get("/")
    assert r.status_code == 200
    assert "$400k" in r.text  # Own org's headline (compact — Task 4 posture band)
    assert "999,999,999" not in r.text  # Other org's canary absent
    assert "OUR_AGG" in r.text
    assert "OTHER_ORG_AGG" not in r.text


# ---------- 9. Run-name displays correctly ----------


async def test_dashboard_run_with_name_displays_name(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_admin
    sc = _make_scenario(org_id=org_id, name="ScenarioForNameTest")
    db_session.add(sc)
    await db_session.flush()
    db_session.add(
        _make_completed_single_run(
            org_id=org_id,
            scenario_id=sc.id,
            residual_ale=100.0,
            name="Q2 Ransomware Drill",
        )
    )
    await db_session.commit()

    r = await client.get("/")
    assert "Q2 Ransomware Drill" in r.text


async def test_dashboard_run_without_name_displays_scenario_name_fallback(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_admin
    sc = _make_scenario(org_id=org_id, name="UniqueScenarioName_FallbackTest")
    db_session.add(sc)
    await db_session.flush()
    run = _make_completed_single_run(
        org_id=org_id,
        scenario_id=sc.id,
        residual_ale=100.0,
        name=None,  # explicit: no name
        created_at=dt.datetime(2026, 5, 5, 12, 0, tzinfo=dt.UTC),
    )
    db_session.add(run)
    await db_session.commit()

    r = await client.get("/")
    # Issue #263: the fallback label is the scenario name with NO baked date.
    # The date renders separately in the localized "Created" column via the
    # format_datetime / <time data-localize> pipeline (not raw strftime).
    assert "UniqueScenarioName_FallbackTest" in r.text
    assert "UniqueScenarioName_FallbackTest · 2026-05-05" not in r.text
    # created_at goes through the localizer, not a baked UTC string.
    assert 'data-localize="datetime"' in r.text


# ---------- 10. PR omega T6: dual EPC + dual LEC side-by-side ----------


async def test_dashboard_renders_dual_epc_alongside_dual_lec(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Dashboard renders dual_lec + dual_epc side-by-side in Card 3."""
    client, org_id = authed_analyst
    s1, s2 = uuid.uuid4(), uuid.uuid4()
    run = _make_completed_aggregate_run(
        org_id=org_id,
        name="EPC Portfolio",
        scenario_ids=[s1, s2],
        ale_with_controls=100_000.0,
        ale_without_controls=500_000.0,
        control_value_dollars=400_000.0,
        control_value_percent=80.0,
    )
    db_session.add(run)
    await db_session.commit()

    response = await client.get("/")
    assert response.status_code == 200
    body = response.text
    # epic #547 P1 Task 3/4: dual_lec_curve + dual_epc_curve are both
    # first-party SVG now (not the prior chart vendor) — coverage moves from
    # the retired container divs to the SVG contract.
    assert 'data-chart="dual-epc"' in body
    assert 'data-chart="dual-lec"' in body
    assert "lg:grid-cols-2" in body


# ---------- Quick-start card (whole-project-eval onboarding polish) ----------


async def test_dashboard_zero_scenarios_shows_quick_start(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    """Brand-new org (zero scenarios): the quick-start card renders with the
    3-step guide + library-first CTAs instead of only dead-end run CTAs."""
    client, _ = authed_admin
    r = await client.get("/")
    assert r.status_code == 200
    assert "Get started" in r.text
    assert "Browse scenario library" in r.text
    assert 'href="/library"' in r.text
    assert 'href="/scenarios/new/wizard"' in r.text


async def test_dashboard_with_scenarios_hides_quick_start(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Once any scenario exists the quick-start card disappears."""
    client, org_id = authed_admin
    db_session.add(_make_scenario(org_id=org_id, name="QuickstartGone"))
    await db_session.commit()

    r = await client.get("/")
    assert r.status_code == 200
    assert "Get started" not in r.text


async def test_dashboard_quick_start_ctas_hidden_for_viewer(
    authed_viewer: tuple[AsyncClient, uuid.UUID],
) -> None:
    """Viewer sees the guide text but not the authoring CTAs (RBAC macro)."""
    client, _ = authed_viewer
    r = await client.get("/")
    assert r.status_code == 200
    assert "Get started" in r.text
    assert "Browse scenario library" not in r.text


# ---------- P3 regression: dashboard renders EUR symbol, not raw '$' ----------


async def test_dashboard_eur_org_shows_euro_symbol_not_dollar(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """P3 regression guard (2a): dashboard KPI cards for a EUR org must
    use '€' (or 'EUR'), not raw '$', for money values.

    Setup: EUR org with an active FxRate (rate=0.92) + a completed
    AGGREGATE run.  The dashboard service resolves the reporting currency
    from the run's presentation_fx_snapshot (or the live active rate) and
    threads it into the template via data.currency.symbol.

    The pre-fix bug: dashboard templates hard-coded '$' or read
    data.currency.symbol='$' because the service never set the EUR symbol.
    This test catches that by asserting '€' appears in the page while
    assuring no raw '$' appears in the ALE KPI block.

    The fix: build_dashboard now resolves reporting currency and sets
    currency_meta correctly; templates use data.currency.symbol.
    """
    import re as _re

    client, org_id = authed_admin

    # Set org to EUR + seed active FxRate
    org = (
        await db_session.execute(select(Organization).where(Organization.id == org_id))
    ).scalar_one()
    org.preferred_currency = "EUR"
    db_session.add(org)
    await FxRateService(db_session).upsert_rate(
        org_id,
        "EUR",
        Decimal("0.92"),
        dt.date(2026, 6, 14),
        "ECB",
        user_id=None,
    )

    # Seed an AGGREGATE run with a presentation_fx_snapshot pinned to EUR
    s1, s2 = uuid.uuid4(), uuid.uuid4()
    run = _make_completed_aggregate_run(
        org_id=org_id,
        name="EUR Portfolio",
        scenario_ids=[s1, s2],
        ale_with_controls=500_000.0,
        ale_without_controls=2_000_000.0,
        control_value_dollars=1_500_000.0,
        control_value_percent=75.0,
    )
    # Pin the FX snapshot on the run so the dashboard picks it up
    run.presentation_fx_snapshot = {
        "code": "EUR",
        "usd_rate": "0.92",
        "as_of_date": "2026-06-14",
        "source": "ECB",
    }
    db_session.add(run)
    await db_session.commit()

    r = await client.get("/")
    assert r.status_code == 200, r.text[:300]
    body = r.text

    # EUR symbol must appear somewhere in the page (currency KPI blocks)
    assert "€" in body or "EUR" in body, (
        "EUR org dashboard: '€' or 'EUR' must appear. "
        "Check that build_dashboard threads reporting currency to templates."
    )

    # The ALE KPI block must not contain a raw '$' for a EUR run.
    # The ALE block is identified by the 'residual-ale' section or the
    # 'Residual ALE' heading near the KPI value.
    # Strategy: split body into lines, find the ALE-rendering section,
    # assert no '$' immediately precedes a digit (money-format pattern).
    _money_dollar = _re.compile(r"\$[\d,]+")
    ale_matches = _money_dollar.findall(body)
    # Filter out Alpine.js $store/$el etc. — those won't match \$\d
    assert not ale_matches, (
        f"Raw '$NNN' money format found on EUR org dashboard: {ale_matches[:5]}. "
        "All money KPI values must use the reporting currency symbol (€ for EUR). "
        "Check dashboard templates and build_dashboard currency threading."
    )

    # Regression (2026-06-15): the dashboard's charts (dual_lec / dual_epc)
    # must also render in the reporting currency. The pre-fix bug:
    # dashboard/index.html called these macros WITHOUT currency=, so each
    # defaulted to '$' even though the series values were already
    # EUR-converted in the view-model — axis ticks and bar labels showed '$'
    # over EUR magnitudes.
    #
    # epic #547 P1 (dual_lec/dual_epc) + P2 (per_scenario_ale_bar deleted as
    # dead code — zero template callers, confirmed during the P2 caller
    # sweep): every chart macro the dashboard renders is first-party SVG now,
    # so NONE of them can emit a chart-vendor axis tickprefix/texttemplate at
    # all — these two assertions are a permanent regression guard against the
    # retired chart vendor (or a raw '$' literal) ever creeping back into a
    # dashboard chart, not a live per_scenario_ale_bar contract (that macro
    # no longer exists).
    assert 'tickprefix":"$"' not in body, (
        "Chart-vendor axis tickprefix '$' found on EUR org dashboard charts "
        "— no dashboard chart macro should emit chart-vendor markup anymore (epic #547)."
    )
    assert "$%{" not in body, (
        "Chart-vendor money texttemplate '$%{x}' found on EUR org dashboard "
        "— no dashboard chart macro should emit chart-vendor markup anymore (epic #547)."
    )

    # epic #547 P1 Task 3: dual_lec_curve is first-party SVG now (not the
    # prior chart vendor), so its currency coverage no longer lives in the
    # tickprefix/texttemplate
    # tokens above — chart_svg._fmt_money(value, currency_symbol) formats each
    # axis-tick <text> node directly. Scope to the LEC <figure> so this can't
    # accidentally pass off currency symbols rendered elsewhere on the page.
    lec_fig = body.split('data-chart="dual-lec"', 1)[1].split("</figure>", 1)[0]
    assert "€" in lec_fig, "EUR org: dual-lec SVG axis ticks must render '€', not '$'"
    assert "$" not in lec_fig, "EUR org: dual-lec SVG must not render a raw '$' tick"

    # epic #547 P1 Task 4: dual_epc_curve is first-party SVG now too — same
    # scoped-to-<figure> currency contract as the LEC card above.
    epc_fig = body.split('data-chart="dual-epc"', 1)[1].split("</figure>", 1)[0]
    assert "€" in epc_fig, "EUR org: dual-epc SVG axis ticks must render '€', not '$'"
    assert "$" not in epc_fig, "EUR org: dual-epc SVG must not render a raw '$' tick"


# ---------- 9. Posture band (Task 4, #476-#480 dashboard redesign) ----------


async def test_dashboard_posture_shows_within_appetite_verdict(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """The posture band's hero verdict card shows "Within appetite" when the
    residual-loss LEC keeps P(loss >= tolerance amount) at/under the org's
    configured tolerance probability, and the residual-ALE hero figure renders
    COMPACT via the money filter (not the old KPI tile's full-dollar form)."""
    client, org_id = authed_admin
    org = (
        await db_session.execute(select(Organization).where(Organization.id == org_id))
    ).scalar_one()
    # Amount sits beyond the fixture's max sampled loss (ale_with_controls * 5)
    # so the interpolated exceedance probability clamps to the curve's lowest
    # recorded probability (0.0) — comfortably "within" any tolerance.probability.
    org.loss_tolerance_amount = Decimal("50000000")
    org.loss_tolerance_probability = 0.05
    s1, s2 = uuid.uuid4(), uuid.uuid4()
    db_session.add(
        _make_completed_aggregate_run(
            org_id=org_id,
            name="Posture run",
            scenario_ids=[s1, s2],
            ale_with_controls=2_650_000.0,
            ale_without_controls=13_250_000.0,
            control_value_dollars=39_592_911.34,
            control_value_percent=94.0,
        )
    )
    await db_session.commit()

    r = await client.get("/")
    assert r.status_code == 200
    assert "Within appetite" in r.text
    assert "$2.65M" in r.text  # compact residual ALE hero (money filter)
    assert "$39,592,911" not in r.text  # NOT full-form


async def test_dashboard_posture_no_tolerance_prompts_set_appetite(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """With an aggregate run present but no org risk-tolerance configured,
    ``posture.verdict`` is None (nothing to compare against) — the hero card
    must omit the verdict badge entirely and prompt to configure an
    appetite instead of showing a stale/default verdict."""
    client, org_id = authed_admin
    s1, s2 = uuid.uuid4(), uuid.uuid4()
    db_session.add(
        _make_completed_aggregate_run(
            org_id=org_id,
            name="No tolerance run",
            scenario_ids=[s1, s2],
            ale_with_controls=100_000.0,
            ale_without_controls=500_000.0,
            control_value_dollars=400_000.0,
            control_value_percent=80.0,
        )
    )
    await db_session.commit()

    r = await client.get("/")
    assert r.status_code == 200
    assert "Within appetite" not in r.text and "Exceeds appetite" not in r.text
    assert "Set a risk appetite" in r.text


# ---------- Task 5 (#476-#480): coverage & budget band ----------


def _make_library_entry(
    *,
    slug: str,
    sub_sectors: list[str] | None,
) -> ScenarioLibraryEntry:
    """Minimal-valid published ScenarioLibraryEntry — mirrors the precedent
    factory in tests/integration/test_dashboard_service.py."""
    return ScenarioLibraryEntry(
        id=uuid.uuid4(),
        version=1,
        slug=slug,
        name=slug,
        status="published",
        threat_event_type=ThreatCategory.RANSOMWARE,
        threat_actor_type=ThreatActorType.CYBERCRIMINALS,
        asset_class=AssetClass.SYSTEMS,
        tags=[],
        description="d",
        canonical_fair_gap="g",
        source_citations=[],
        applicable_sub_sectors=sub_sectors,
        threat_event_frequency={"distribution": "PERT", "low": 1.0, "mode": 4.0, "high": 12.0},
        vulnerability={"distribution": "PERT", "low": 0.05, "mode": 0.20, "high": 0.50},
        primary_loss={
            "distribution": "PERT",
            "low": 100_000.0,
            "mode": 750_000.0,
            "high": 5_000_000.0,
        },
        suggested_control_ids=[],
    )


async def test_coverage_budget_gauge_and_framework_bars(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Seeds a budget, a control tagged into BOTH seeded frameworks (nist_csf
    + cis) with a FAIR-CAM domain assignment, and a partially-covered
    scenario-library reference set. Asserts the framework/domain labels
    render from the SEEDED_FRAMEWORKS / ControlDomain loop (never a literal
    in the template) and the scenario-coverage ratio copy appears."""
    client, org_id = authed_admin
    org = (
        await db_session.execute(select(Organization).where(Organization.id == org_id))
    ).scalar_one()
    org.annual_security_budget = Decimal("3500000")
    org.industry_sub_sector = IndustrySubSector.WATER_UTILITY

    control = Control(
        organization_id=org.id,
        name="EDR Platform",
        type=ControlType.TECHNICAL,
        annual_cost=Decimal("2670000"),
        nist_csf_functions=["PR.AC-7"],
        compliance_mappings={"cis_safeguards": ["1.1"]},
    )
    db_session.add(control)
    await db_session.flush()  # populate control.id before the assignment FK

    assignment = ControlFunctionAssignment(
        control_id=control.id,
        organization_id=org.id,
        sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
        capability_value=0.8,
        coverage=0.9,
        reliability=0.9,
    )
    db_session.add(assignment)

    db_session.add_all(
        [
            FrameworkControl(
                framework="nist_csf",
                framework_version="1.1",
                code="PR.AC-7",
                title="Access Permissions Management",
                description=None,
                asset_type=None,
                security_function=None,
                citation={"source": "FAIR Institute"},
            ),
            FrameworkControl(
                framework="cis",
                framework_version="8",
                code="1.1",
                title="Establish and Maintain Detailed Enterprise Asset Inventory",
                description=None,
                asset_type=None,
                security_function=None,
                citation={"source": "FAIR Institute"},
            ),
        ]
    )

    pinned_entry = _make_library_entry(slug="water-pinned", sub_sectors=["water_utility"])
    unpinned_entry = _make_library_entry(slug="water-unpinned", sub_sectors=["water_utility"])
    db_session.add_all([pinned_entry, unpinned_entry])
    await db_session.flush()  # entry ids stable before use in library_pin

    scenario = _make_scenario(org_id=org.id, name="Water Utility SCADA Ransomware")
    scenario.library_pin = {"entry_id": str(pinned_entry.id), "version": pinned_entry.version}
    db_session.add(scenario)
    await db_session.commit()

    page = (await client.get("/")).text
    # From SEEDED_FRAMEWORKS via the frameworks loop, not literals in the template.
    assert "NIST CSF" in page and "CIS" in page
    # FAIR-CAM domain label, derived from the fair_cam dict loop.
    assert "Loss-event" in page
    # Scenario-coverage ratio copy + the actionable gap.
    assert "recommended" in page.lower()
    assert "1 of 2 recommended scenarios modeled" in page
    assert "browse the library" in page
    # Budget gauge (budget IS configured): spend/budget/committed%/headroom.
    assert "$2.67M" in page
    assert "$3.50M" in page
    assert "76% committed" in page
    # #475 follow-up: placeholder badge + from-scratch caveat are retired.
    assert "#475" not in page
    assert "technique-level mapping not modeled yet" not in page
    assert "From-scratch scenarios" not in page
    # Catalog unseeded in this test's DB -> the ATT&CK block is hidden.
    assert "of 2 tactics" not in page


async def test_coverage_budget_cost_and_roi_when_no_budget_configured(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """No org.annual_security_budget configured -> the budget panel falls
    back to cost + ROI with no gauge (Step 3 of the brief)."""
    client, org_id = authed_admin
    org = (
        await db_session.execute(select(Organization).where(Organization.id == org_id))
    ).scalar_one()
    assert org.annual_security_budget is None

    control = Control(
        organization_id=org.id,
        name="MFA",
        type=ControlType.TECHNICAL,
        annual_cost=Decimal("50000"),
    )
    db_session.add(control)

    s1, s2 = uuid.uuid4(), uuid.uuid4()
    db_session.add(
        _make_completed_aggregate_run(
            org_id=org_id,
            name="Portfolio",
            scenario_ids=[s1, s2],
            ale_with_controls=100_000.0,
            ale_without_controls=500_000.0,
            control_value_dollars=400_000.0,
            control_value_percent=80.0,
        )
    )
    await db_session.commit()

    page = (await client.get("/")).text
    assert "$50k" in page  # money(_code) compact form
    assert "committed" not in page  # no gauge copy when budget is unset
    assert "Set a security budget" in page


async def test_build_dashboard_populates_attack_coverage(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """DashboardData.attack_coverage: [] on an empty catalog; per-domain
    tactic rollups once catalog + a mapping exist (wiring test — template
    rendering is covered separately)."""
    _, org_id = authed_admin
    org = (
        await db_session.execute(select(Organization).where(Organization.id == org_id))
    ).scalar_one()

    data = await build_dashboard(db_session, org)
    assert data.attack_coverage == []  # no catalog seeded in the test DB

    ta1 = _tactic()  # enterprise TA0001 initial-access
    ta2 = _tactic(tactic_id="TA0002", shortname="execution", name="Execution", display_order=1)
    t1 = _technique()  # enterprise T1566, tactics=["initial-access"]
    db_session.add_all([ta1, ta2, t1])
    scenario = _make_scenario(org_id=org_id, name="Phish To Ransomware")
    db_session.add(scenario)
    await db_session.flush()
    db_session.add(
        ScenarioAttackMapping(
            organization_id=org_id,
            scenario_id=scenario.id,
            technique_id=t1.id,
            source="user",
        )
    )
    await db_session.flush()

    data = await build_dashboard(db_session, org)
    (ent,) = data.attack_coverage
    assert ent.domain == "enterprise"
    assert ent.tactic_result.covered_count == 1
    assert ent.tactic_result.reference_count == 2
    assert ent.technique_count_mapped == 1


def test_coverage_template_has_no_hardcoded_reference_lists() -> None:
    """Acceptance test for the hard rule: NO hardcoded reference lists. The
    only reference enumerations are `control_coverage.fair_cam.items()` and
    `control_coverage.frameworks` — both looped from the view-model, never
    re-listed as literals in the template."""
    src = (TEMPLATES_DIR / "dashboard" / "_coverage_budget.html").read_text()
    for literal in ["nist_csf", "RANSOMWARE", "OT_SYSTEMS", "iso_27001", "TA00", "initial-access"]:
        assert literal not in src, f"hardcoded reference {literal} in coverage template"


async def test_dashboard_attack_block_populated(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Populated ATT&CK block: per-domain tactic bars + technique count +
    breakdown link; labels/denominators all from catalog data."""
    client, org_id = authed_admin
    ta1 = _tactic()
    ta2 = _tactic(tactic_id="TA0002", shortname="execution", name="Execution", display_order=1)
    ta_ics = _tactic(
        domain="ics",
        tactic_id="TA0108",
        shortname="impair-process-control",
        name="Impair Process Control",
        display_order=0,
    )
    t1 = _technique()  # enterprise T1566 initial-access
    t_ics = _technique(
        domain="ics",
        technique_id="T0836",
        name="Modify Parameter",
        tactics=["impair-process-control"],
    )
    db_session.add_all([ta1, ta2, ta_ics, t1, t_ics])
    scenario = _make_scenario(org_id=org_id, name="Phish To Ransomware")
    db_session.add(scenario)
    await db_session.flush()
    db_session.add(
        ScenarioAttackMapping(
            organization_id=org_id, scenario_id=scenario.id, technique_id=t1.id, source="user"
        )
    )
    await db_session.commit()

    page = (await client.get("/")).text
    assert "MITRE ATT&amp;CK" in page
    assert "1 of 2 tactics" in page  # Enterprise rollup
    assert "0 of 1 tactic" in page  # ICS rollup (singular)
    assert "Enterprise" in page and "ICS" in page
    assert "1 technique mapped" in page
    assert 'href="/scenarios/attack-coverage"' in page
    # Zero-mapping nudge must NOT show when mappings exist.
    assert "No scenarios are mapped" not in page


async def test_dashboard_attack_block_zero_mappings_nudge(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Catalog seeded but no mappings: 0-of-N bars render plus one nudge line."""
    client, _ = authed_admin
    db_session.add_all(
        [
            _tactic(),
            _tactic(tactic_id="TA0002", shortname="execution", name="Execution", display_order=1),
            _technique(),
        ]
    )
    await db_session.commit()

    page = (await client.get("/")).text
    assert "0 of 2 tactics" in page
    assert "No scenarios are mapped to ATT&amp;CK techniques yet" in page
    assert 'href="/scenarios/attack-coverage"' in page


# ---------- Task 6 (#476-#480): recent-activity band + full-page assembly ----------


async def test_dashboard_recent_activity_band_renders_dumbbells_and_table(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """The recent-activity band (Band 4) renders the top-scenario CSS
    dumbbells (reusing the shipped .dumbbell-* classes, without → with
    controls) AND the recent-runs data_table, from the same
    TopScenarioRow / RecentRunRow lists the old cards consumed."""
    client, org_id = authed_admin
    s1, s2 = uuid.uuid4(), uuid.uuid4()
    run = _make_completed_aggregate_run(
        org_id=org_id,
        name="Dumbbell Portfolio",
        scenario_ids=[s1, s2],
        per_scenario=[
            {
                "scenario_id": str(s1),
                "scenario_name": "High Inherent Scenario",
                "base_risk": {"annualized_loss_expectancy": 900_000.0},
                "residual_risk": {"annualized_loss_expectancy": 100_000.0},
            },
            {
                "scenario_id": str(s2),
                "scenario_name": "Low Inherent Scenario",
                "base_risk": {"annualized_loss_expectancy": 200_000.0},
                "residual_risk": {"annualized_loss_expectancy": 50_000.0},
            },
        ],
    )
    db_session.add(run)
    await db_session.commit()

    page = (await client.get("/")).text
    assert "Recent activity" in page
    assert "Top scenarios by residual risk" in page
    # Reused dumbbell CSS classes (runs/components/scenario_dumbbell.html)
    assert "dumbbell-track" in page
    assert "dumbbell-bar" in page
    assert "dumbbell-dot" in page
    assert "var(--chart-inherent)" in page
    assert "var(--chart-residual)" in page
    assert "Without controls" in page  # both rows have base_ale -> legend renders
    assert "High Inherent Scenario" in page
    assert "Low Inherent Scenario" in page
    # Recent-runs table, compact money via money(_code)
    assert "Recent runs" in page
    assert "Dumbbell Portfolio" in page
    assert "$100k" in page  # aggregate_with_controls ALE, compact


async def test_dashboard_recent_activity_dumbbell_degrades_gracefully_without_base_ale(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """SINGLE-fallback path: TopScenarioRow.base_ale is None. The dumbbell
    must degrade to a single dot (no bar, no "Without controls" legend)
    rather than crash or render a bogus zero-width bar."""
    client, org_id = authed_admin
    scenario = _make_scenario(org_id=org_id, name="Fallback Only Scenario")
    db_session.add(scenario)
    await db_session.flush()
    db_session.add(
        _make_completed_single_run(
            org_id=org_id,
            name="fallback_run",
            scenario_id=scenario.id,
            residual_ale=42_000.0,
        )
    )
    await db_session.commit()

    page = (await client.get("/")).text
    assert "Fallback Only Scenario" in page
    assert "dumbbell-track" in page
    assert "dumbbell-dot" in page
    assert "dumbbell-bar" not in page
    assert "Without controls" not in page


async def test_dashboard_full_page_all_four_bands_in_order(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Final page assembly: all four redesigned bands render, in the order
    posture -> loss distributions -> coverage & budget -> recent activity."""
    client, org_id = authed_admin
    org = (
        await db_session.execute(select(Organization).where(Organization.id == org_id))
    ).scalar_one()
    org.loss_tolerance_amount = Decimal("50000000")
    org.loss_tolerance_probability = 0.05
    s1, s2 = uuid.uuid4(), uuid.uuid4()
    db_session.add(
        _make_completed_aggregate_run(
            org_id=org_id,
            name="Full Page Portfolio",
            scenario_ids=[s1, s2],
        )
    )
    await db_session.commit()

    page = (await client.get("/")).text
    # Band-level heading markers. NOTE: "Recent activity" (the Band 4 h2), not
    # "Recent runs" — the pre-existing Task 4 two-up KPI strip ("Scenarios
    # with runs" / "Recent runs" tiles, rendered directly under the posture
    # band) also contains the literal text "Recent runs", which would make an
    # ordering assertion on that string false-positive against the wrong
    # occurrence. "Recent activity" is Band 4's own unique h2.
    for marker in ["Risk posture", "Loss distributions", "Coverage", "Recent activity"]:
        assert marker in page
    assert (
        page.index("Risk posture")
        < page.index("Loss distributions")
        < page.index("Coverage")
        < page.index("Recent activity")
    )
    # Band 4's recent-runs panel still renders (just not used as the
    # ordering marker above, for the reason noted).
    assert "Recent runs" in page
