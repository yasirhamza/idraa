"""Design-language Phase 2 acceptance tests (issue #59).

Task 1: dashboard density — compact `empty_row` instrument rows replace tall
empty-state cards on the dashboard's previously-void bands (posture,
loss-distributions, recent-activity panels), and the "Scenarios with runs" /
"Recent runs" KPI tiles merge into a single `readout_strip`. Later tasks in
the same epic extend this module with chart-style + run-detail readout
assertions — keep this module the single home for design-language P2
acceptance tests rather than scattering one-off test files per task.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

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
