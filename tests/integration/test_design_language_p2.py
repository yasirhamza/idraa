"""Design-language Phase 2 acceptance tests (issue #59).

Task 1: dashboard density — compact `empty_row` instrument rows replace tall
empty-state cards on the dashboard's previously-void bands (posture,
loss-distributions, recent-activity panels), and the "Scenarios with runs" /
"Recent runs" KPI tiles merge into a single `readout_strip`. Task 3: the
run-detail verdict strip gets a typography-only mono-instrument restyle
(labels byte-verbatim). Later tasks in the same epic extend this module with
chart-style assertions — keep this module the single home for design-language
P2 acceptance tests rather than scattering one-off test files per task.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.risk_analysis_run import RiskAnalysisRun
from tests.integration._dashboard_fixtures import _make_completed_aggregate_run

pytestmark = pytest.mark.asyncio


async def test_fresh_dashboard_is_compact(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    """A brand-new org's dashboard (no scenarios, no runs) renders its empty
    bands as compact single-row `empty_row` instrument rows — not the old
    tall empty-state cards — and the scenario/run stat tiles merge into ONE
    `readout_strip` rather than a 2-up kpi_card grid. Copy is today's copy,
    verbatim, just re-homed into the compact row."""
    client, _ = authed_admin
    r = await client.get("/")
    assert r.status_code == 200
    body = r.text

    # At least the Loss-distributions + Recent-activity (top-scenarios,
    # recent-runs) + posture bands render as compact empty rows.
    assert body.count("data-empty-row") >= 2

    # Today's copy, verbatim, now living inside the compact rows.
    assert "Run an analysis across 2+ scenarios to see the portfolio loss distributions." in body
    assert "No completed runs yet." in body
    assert "No runs yet." in body

    # Stat tiles merged into a single readout strip (was a 2-up kpi_card grid).
    assert "data-readout" in body


async def test_populated_dashboard_unaffected(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """A populated dashboard (completed AGGREGATE run with per-scenario
    breakdown) keeps its posture band + charts + recent-activity panels
    rendering their real content — density changes only touch the
    previously-empty bands, so a populated dashboard has NO `data-empty-row`
    instances."""
    client, org_id = authed_admin
    s1, s2 = uuid.uuid4(), uuid.uuid4()
    run = _make_completed_aggregate_run(
        org_id=org_id,
        name="Populated Portfolio",
        scenario_ids=[s1, s2],
    )
    db_session.add(run)
    await db_session.commit()

    r = await client.get("/")
    assert r.status_code == 200
    body = r.text

    assert "Risk posture" in body
    assert 'data-chart="dual-lec"' in body
    assert 'data-chart="dual-epc"' in body
    assert "Populated Portfolio" in body
    assert "data-empty-row" not in body


# ---------------------------------------------------------------------------
# Task 3: run-detail verdict-strip readout restyle.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def analyst_org_aggregate_run_priced_controls(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_user: Any,
    seed_scenario_factory: Callable[..., Awaitable[Any]],
    seed_control_factory: Callable[..., Awaitable[Any]],
    wire_executor_to_test_db: None,
) -> RiskAnalysisRun:
    """A COMPLETED AGGREGATE run whose mitigating controls carry a nonzero
    ``annual_cost`` — exercises ALL THREE verdict-strip cells (Residual ALE,
    Control value / yr, Return on control spend). Fixture topology copied
    from ``tests/integration/test_run_detail_components.py``'s
    ``analyst_org_aggregate_run_with_controls`` (same file-local-fixture
    convention there — a cross-module import trips ruff F811 on the
    parameter shadowing the import), with an added ``annual_cost`` override:
    ``seed_control_factory`` has no ``annual_cost`` kwarg and hardcodes
    ``Decimal("0")``, which would leave ``aggregate_roi`` None (the
    "Return on control spend" cell is gated on ``_roi is not none``).
    """
    from fastapi import BackgroundTasks

    from idraa.models.scenario_control import ScenarioControl
    from idraa.services.runs import RunService

    _, org_id = authed_analyst
    s1 = await seed_scenario_factory(
        name="p2-priced-s1", organization_id=org_id, created_by=seed_user.id
    )
    s2 = await seed_scenario_factory(
        name="p2-priced-s2", organization_id=org_id, created_by=seed_user.id
    )
    ctrl_a = await seed_control_factory(
        name="P2 Control Alpha", organization_id=org_id, created_by=seed_user.id
    )
    ctrl_b = await seed_control_factory(
        name="P2 Control Beta", organization_id=org_id, created_by=seed_user.id
    )
    ctrl_a.annual_cost = Decimal("5000")
    ctrl_b.annual_cost = Decimal("3000")
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


async def test_verdict_strip_labels_verbatim(
    client: AsyncClient,
    analyst_org_aggregate_run_priced_controls: RiskAnalysisRun,
) -> None:
    """Task 3 (#59): the verdict-strip restyle is TYPOGRAPHY-ONLY — labels
    stay byte-verbatim and the value cells move from the ``text-number-lg``
    big-number treatment to the mono ``text-number-md`` instrument
    treatment; labels adopt the mono/uppercase/10px eyebrow treatment used
    elsewhere in the P2 restyle (macros/readout.html)."""
    run = analyst_org_aggregate_run_priced_controls
    resp = await client.get(f"/runs/{run.id}")
    assert resp.status_code == 200
    html = resp.text

    assert 'id="verdict-strip"' in html
    strip = html[html.index('id="verdict-strip"') : html.index('id="trust-chips"')]

    for label in ("Residual ALE", "Control value / yr", "Return on control spend"):
        assert label in strip, f"missing verdict-strip label: {label!r}"

    # Mono/uppercase/10px eyebrow label classes (readout.html's pattern).
    assert strip.count("font-mono uppercase tracking-[0.12em] text-[10px] text-ink-3") == 3
    # Values moved to the compact mono instrument size; the old big-number
    # size must be fully retired from the strip.
    assert strip.count("text-number-md font-mono") == 3
    assert "text-number-lg" not in strip
