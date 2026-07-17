"""Dual LEC + EPC cards render first-party SVG (epic #547 P1, replaces the
prior JS chart vendor).

Uses the seeded aggregate-run fixture (same one test_run_detail_components
uses) and asserts the SERVER html contains real SVG geometry + figure-internal
controls — stronger than the old embedded-chart-JSON assertions.

The LEC card flipped to SVG in Task 3 (full hydration: slider + linear/log
toggle). The EPC card flips to SVG here in Task 4 (hover-only hydration: no
slider/toggle controls, single log-y svg).
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

from idraa.models.organization import Organization
from idraa.models.risk_analysis_run import RiskAnalysisRun

# Fixtures are file-local (not conftest-shared) — same convention documented in
# tests/integration/test_run_detail_components.py: a cross-module import of a
# same-named fixture trips ruff F811 on the parameter shadowing the import.


@pytest_asyncio.fixture
async def client_completed_aggregate(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_user: Any,
    seed_scenario_factory: Callable[..., Awaitable[Any]],
    wire_executor_to_test_db: None,
) -> tuple[AsyncClient, RiskAnalysisRun]:
    """(client, run) for a COMPLETED AGGREGATE run in the analyst's org.

    Seeds 2 scenarios and dispatches inline (mc_iterations_override=200, below
    the async-dispatch threshold) so the run is COMPLETED with a real
    simulation_results blob (dual_lec/dual_epc points included) before the
    fixture returns — the real producer shape, not a hand-rolled payload.
    """
    from fastapi import BackgroundTasks

    from idraa.services.runs import RunService

    client, org_id = authed_analyst
    s1 = await seed_scenario_factory(
        name="svg-lec-s1", organization_id=org_id, created_by=seed_user.id
    )
    s2 = await seed_scenario_factory(
        name="svg-lec-s2", organization_id=org_id, created_by=seed_user.id
    )

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
    return client, run


@pytest_asyncio.fixture
async def client_completed_aggregate_with_tolerance(
    client_completed_aggregate: tuple[AsyncClient, RiskAnalysisRun],
    db_session: AsyncSession,
) -> tuple[AsyncClient, RiskAnalysisRun]:
    """Same as ``client_completed_aggregate``, with an org loss tolerance set
    so the LEC card's tolerance marker renders."""
    client, run = client_completed_aggregate
    org = await db_session.get(Organization, run.organization_id)
    assert org is not None
    org.loss_tolerance_amount = 8_000_000
    org.loss_tolerance_probability = 0.05
    db_session.add(org)
    await db_session.commit()
    return client, run


@pytest.mark.anyio
async def test_run_detail_dual_lec_svg(client_completed_aggregate):
    client, run = client_completed_aggregate
    r = await client.get(f"/runs/{run.id}")
    assert r.status_code == 200
    html = r.text
    assert 'data-chart="dual-lec"' in html
    assert 'data-chart-hydrate="lec"' in html
    assert 'data-y-scale="linear"' in html and 'data-y-scale="log"' in html
    assert html.count("<path") >= 2  # two series in at least the visible variant
    assert "data-chart-data" in html
    assert '"view"' in html  # view geometry embedded for the JS
    assert 'data-role="p-slider"' in html  # controls emitted INSIDE the macro
    # Legend row replaces the former on-curve endpoint labels (which clipped to
    # "W" where the dual curves converge); identity via swatch + text.
    assert "chart-legend" in html
    assert "Without controls" in html and "With controls" in html
    assert "chart-series-label" not in html  # endpoint labels gone
    # "Download data" CSV export restored (was the retired chart vendor's modebar button):
    assert 'data-role="csv"' in html
    # The retired chart vendor must NOT be used for this card anymore:
    assert "dual-lec-curve-container" not in html


@pytest.mark.anyio
async def test_tolerance_marker_present_iff_org_tolerance(
    client_completed_aggregate_with_tolerance,
):
    """Page-wide marker presence PLUS a figure-scoped EPC check (Task 4
    methodology NTH): the tolerance marker must render INSIDE the EPC figure
    itself, not just somewhere on the page — the LEC figure also emits a
    tolerance-marker, so a page-wide substring check alone can't tell which
    figure carries it.

    Both assertions deliberately share this ONE fixture/GET rather than
    living in a separate test: every ``client_completed_aggregate``
    instantiation executes a full Monte-Carlo aggregate run, and adding one
    more was measured (Task 7, 2026-07-13) tipping the later wizard
    double-post test's scipy quantile-fit over its 500ms wall-clock budget
    in full-suite runs (QuantilePoolingError flake; reproducible 4/4 with
    the extra test, 0/2 without)."""
    client, run = client_completed_aggregate_with_tolerance
    html = (await client.get(f"/runs/{run.id}")).text
    assert 'data-role="tolerance-marker"' in html
    epc_fig = html.split('data-chart="dual-epc"', 1)[1].split("</figure>", 1)[0]
    assert 'data-role="tolerance-marker"' in epc_fig


@pytest.mark.anyio
async def test_dashboard_dual_svg(client_completed_aggregate):
    client, _run = client_completed_aggregate
    r = await client.get("/")
    assert r.status_code == 200
    assert 'data-chart="dual-lec"' in r.text


@pytest.mark.anyio
async def test_run_detail_dual_epc_svg(client_completed_aggregate):
    client, run = client_completed_aggregate
    html = (await client.get(f"/runs/{run.id}")).text
    assert 'data-chart="dual-epc"' in html
    assert 'data-chart-hydrate="epc"' in html
    # EPC is hover-only: no slider/toggle controls inside the EPC figure.
    epc_fig = html.split('data-chart="dual-epc"', 1)[1].split("</figure>", 1)[0]
    assert 'data-role="p-slider"' not in epc_fig
    assert 'data-y-scale="log"' in epc_fig  # single log-y svg
    # EPC figure gets its own legend + Download-data button (hover-only, no slider):
    assert "chart-legend" in epc_fig
    assert 'data-role="csv"' in epc_fig
    assert "dual-epc-curve-container" not in html  # retired chart vendor gone for this card


@pytest.mark.anyio
async def test_appetite_strip_agrees_with_dashboard(client_completed_aggregate_with_tolerance):
    client, run = client_completed_aggregate_with_tolerance
    run_html = (await client.get(f"/runs/{run.id}")).text
    dash_html = (await client.get("/")).text
    # Scoped extraction — no bare substring checks (plan-gate M-N3/A-N3):
    strip_verdict = re.search(
        r'data-testid="appetite-strip"[^>]*data-verdict-with="(within|exceeds)"', run_html
    ).group(1)
    dash_verdict = re.search(
        r'data-testid="posture-verdict"[^>]*data-verdict="(within|exceeds)"', dash_html
    ).group(1)
    assert strip_verdict == dash_verdict


@pytest.mark.anyio
async def test_appetite_strip_elided_without_tolerance(client_completed_aggregate):
    client, run = client_completed_aggregate
    assert 'data-testid="appetite-strip"' not in (await client.get(f"/runs/{run.id}")).text
