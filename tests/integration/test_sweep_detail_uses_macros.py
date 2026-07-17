"""F23: dashboard + detail pages use page_header + kpi_card / form_field(mode='display')."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


async def test_dashboard_uses_page_header(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    """Dashboard must emit the page_header sticky marker."""
    client, _ = authed_admin
    resp = await client.get("/")
    assert resp.status_code == 200
    body = resp.text
    assert "sticky" in body, "Dashboard missing page_header sticky marker"


async def test_dashboard_uses_kpi_cards(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    """Dashboard must use kpi_card big-number styling (text-number-lg class)."""
    client, _ = authed_admin
    resp = await client.get("/")
    assert resp.status_code == 200
    body = resp.text
    assert "text-number-lg" in body, "Dashboard should use kpi_card big-number styling"


async def test_dashboard_recent_runs_uses_data_table(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Dashboard with a seeded run renders recent-runs via data_table."""
    from tests.integration._dashboard_fixtures import (
        _make_completed_single_run,
        _make_scenario,
    )

    client, org_id = authed_admin
    scenario = _make_scenario(org_id=org_id, name="DataTableTestScenario")
    db_session.add(scenario)
    await db_session.flush()
    db_session.add(
        _make_completed_single_run(
            org_id=org_id,
            scenario_id=scenario.id,
            name="DataTableTestRun",
            residual_ale=50_000.0,
        )
    )
    await db_session.commit()

    resp = await client.get("/")
    assert resp.status_code == 200
    body = resp.text
    # data_table desktop wrapper (overflow-x-auto) or mobile card stack
    assert "overflow-x-auto" in body or "md:hidden" in body, (
        "Dashboard recent-runs should use data_table"
    )
    assert "DataTableTestRun" in body


# ---------------------------------------------------------------------------
# Scenario view
# ---------------------------------------------------------------------------


async def test_scenario_view_uses_page_header(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Scenario view must use page_header (sticky)."""
    from tests.integration._dashboard_fixtures import _make_scenario

    client, org_id = authed_admin
    scenario = _make_scenario(org_id=org_id, name="F23ViewHeaderTest")
    db_session.add(scenario)
    await db_session.commit()

    resp = await client.get(f"/scenarios/{scenario.id}")
    if resp.status_code == 404:
        pytest.skip("Scenario view route not mounted")
    assert resp.status_code == 200
    body = resp.text
    assert "sticky" in body, f"/scenarios/{scenario.id} missing page_header sticky marker"


async def test_scenario_view_uses_form_field_display(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Scenario view must render display-mode form_field (text-meta label class)."""
    from tests.integration._dashboard_fixtures import _make_scenario

    client, org_id = authed_admin
    scenario = _make_scenario(org_id=org_id, name="F23FormFieldDisplayTest")
    db_session.add(scenario)
    await db_session.commit()

    resp = await client.get(f"/scenarios/{scenario.id}")
    if resp.status_code == 404:
        pytest.skip("Scenario view route not mounted")
    assert resp.status_code == 200
    body = resp.text
    assert "text-meta" in body, "Scenario view should use form_field(mode='display') label class"


async def test_scenario_view_uses_status_pill(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Scenario view must render status_pill (aria-label with 'control:' prefix)."""
    from tests.integration._dashboard_fixtures import _make_scenario

    client, org_id = authed_admin
    scenario = _make_scenario(org_id=org_id, name="F23StatusPillTest")
    db_session.add(scenario)
    await db_session.commit()

    resp = await client.get(f"/scenarios/{scenario.id}")
    if resp.status_code == 404:
        pytest.skip("Scenario view route not mounted")
    assert resp.status_code == 200
    body = resp.text
    assert 'aria-label="control:' in body, "Scenario view should use status_pill"


# ---------------------------------------------------------------------------
# Library entry detail
# ---------------------------------------------------------------------------


async def test_library_entry_detail_uses_page_header(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Library entry detail must use page_header (sticky)."""
    from sqlalchemy import select

    from idraa.models.scenario_library import ScenarioLibraryEntry

    client, _ = authed_admin
    # Fetch any published seeded entry (seed_library loads entries on startup)
    row = (
        await db_session.execute(
            select(ScenarioLibraryEntry).where(ScenarioLibraryEntry.status == "published").limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        pytest.skip("No published library entries seeded")

    resp = await client.get(f"/library/entries/{row.id}")
    if resp.status_code == 404:
        pytest.skip("Library entry detail route not mounted")
    assert resp.status_code == 200
    body = resp.text
    assert "sticky" in body, f"/library/entries/{row.id} missing page_header sticky"
    assert "text-meta" in body, "Library entry detail should use form_field(mode='display')"


# ---------------------------------------------------------------------------
# Overlay view
# ---------------------------------------------------------------------------


async def test_overlay_view_uses_page_header(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Overlay view must use page_header (sticky) and kpi_card for multipliers."""
    from idraa.models.overlay import OverlayDefinition

    client, org_id = authed_admin

    # Seed a minimal overlay (methodology must be >= 20 chars per DB check)
    overlay = OverlayDefinition(
        organization_id=org_id,
        tag="f23-test-overlay",
        display_name="F23 Test Overlay",
        frequency_multiplier=1.2,
        magnitude_multiplier=0.9,
        methodology="Test methodology for F23 page_header sweep test.",
        sources=["https://example.com"],
        is_active=True,
        version=1,
    )
    db_session.add(overlay)
    await db_session.commit()

    resp = await client.get(f"/overlays/{overlay.id}")
    if resp.status_code == 404:
        pytest.skip("Overlay view route not mounted")
    assert resp.status_code == 200
    body = resp.text
    assert "sticky" in body, f"/overlays/{overlay.id} missing page_header sticky"
    # kpi_card for the multiplier stats
    assert "text-number-lg" in body, "Overlay view should use kpi_card for multipliers"
    # form_field display for tag/version
    assert "text-meta" in body, "Overlay view should use form_field(mode='display')"


# ---------------------------------------------------------------------------
# Runs detail (outer chrome only — _aggregate_results_panel untouched)
# ---------------------------------------------------------------------------


async def test_run_detail_uses_page_header(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Run detail outer chrome must use page_header (sticky).

    Uses a QUEUED run to avoid triggering _results_panel.html which requires
    full simulation_results (mean, var_95, etc.) that the minimal fixture omits.
    The outer chrome (page_header) renders regardless of run status.
    """
    import hashlib

    from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType
    from tests.integration._dashboard_fixtures import _make_scenario

    client, org_id = authed_admin
    scenario = _make_scenario(org_id=org_id, name="F23RunDetailTest")
    db_session.add(scenario)
    await db_session.flush()

    # Seed a QUEUED (non-completed) run — bypasses _results_panel.html so
    # we test only the outer chrome (page_header, status section, controls).
    run = RiskAnalysisRun(
        organization_id=org_id,
        name="F23 Run Detail",
        run_type=RunType.SINGLE,
        status=RunStatus.QUEUED,
        scenario_id=scenario.id,
        aggregate_scenario_ids=None,
        control_ids_used=[],
        controls_snapshot=[],
        mc_iterations=200,
        inputs_hash=hashlib.sha256(b"f23-test").hexdigest(),
        simulation_results=None,
    )
    db_session.add(run)
    await db_session.commit()

    resp = await client.get(f"/runs/{run.id}")
    if resp.status_code == 404:
        pytest.skip("Run detail route not mounted")
    assert resp.status_code == 200
    body = resp.text
    assert "sticky" in body, f"/runs/{run.id} missing page_header sticky marker"
