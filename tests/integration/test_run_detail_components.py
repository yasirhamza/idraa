"""Component-level assertions for the redesigned aggregate run detail page.

Grows across P1 tasks 4-6. All tests drive the REAL page render (GET /runs/{id})
so component wiring, caveat numbering, and label mapping are asserted on the
composed result, not on isolated macro renders.
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

from idraa.models.risk_analysis_run import RiskAnalysisRun

# Fixture copied from tests/integration/test_run_detail_aggregate.py (same
# convention — fixtures there are file-local, not shared via conftest; a
# cross-module import trips ruff F811 on the parameter shadowing the import).


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
async def analyst_org_aggregate_run_with_controls(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_user: Any,
    seed_scenario_factory: Callable[..., Awaitable[Any]],
    seed_control_factory: Callable[..., Awaitable[Any]],
    wire_executor_to_test_db: None,
) -> RiskAnalysisRun:
    """A COMPLETED AGGREGATE run WITH mitigating controls (attribution matrix
    populated). Copied from test_run_detail_aggregate.py — a cross-module import
    trips ruff F811 on the parameter shadowing. Needed by the ledger + caveat
    attribution tests, which the bare ``analyst_org_aggregate_run`` (no controls)
    cannot exercise (no Shapley matrix → no ledger table / fair-share chips)."""
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


@pytest.mark.anyio
async def test_verdict_strip_renders_with_mean_labels(client, analyst_org_aggregate_run):
    run = analyst_org_aggregate_run
    resp = await client.get(f"/runs/{run.id}")
    assert resp.status_code == 200
    html = resp.text
    assert 'id="verdict-strip"' in html
    assert "Residual ALE" in html
    assert "(mean)" in html


@pytest.mark.anyio
async def test_trust_chips_present_with_sims_and_hash(client, analyst_org_aggregate_run):
    run = analyst_org_aggregate_run
    resp = await client.get(f"/runs/{run.id}")
    html = resp.text
    assert 'id="trust-chips"' in html
    assert "scenarios</span>" in html
    assert f"{run.inputs_hash[:4]}" in html


@pytest.mark.anyio
async def test_caveat_chips_resolve_to_panel_anchors(
    client, analyst_org_aggregate_run_with_controls
):
    """Every rendered chip href has a matching panel anchor id — both ways.

    Uses the controls-bearing run so the attribution caveats (fair-share,
    weight-provenance, if-removed-partial) are exercised, not just the always-on
    mean/dist entries."""
    run = analyst_org_aggregate_run_with_controls
    html = (await client.get(f"/runs/{run.id}")).text
    chip_targets = set(re.findall(r'class="cv" href="#cv-([a-z-]+)"', html))
    panel_ids = set(re.findall(r'<li id="cv-([a-z-]+)"', html))
    assert chip_targets, "no caveat chips rendered"
    assert chip_targets <= panel_ids, f"orphan chips: {chip_targets - panel_ids}"
    # Reverse direction: every panel entry is referenced by at least one chip —
    # an unreferenced entry is dead prose that should have been flag-gated off.
    assert panel_ids <= chip_targets, f"unreferenced panel entries: {panel_ids - chip_targets}"


@pytest.mark.anyio
async def test_control_ledger_totals_match_matrix_column_totals(
    client, analyst_org_aggregate_run_with_controls
):
    """The ledger fair-share figure and the matrix column total are the same
    number rendered twice — they must never disagree (methodology BLOCKER)."""
    run = analyst_org_aggregate_run_with_controls
    html = (await client.get(f"/runs/{run.id}")).text
    assert 'id="control-ledger"' in html
    assert 'id="shapley-matrix-disclosure"' in html
    # Money strings inside the ledger tbody must all reappear in the matrix
    # totals row (data_grid). Exact-assertion strategy: parse with a simple
    # regex on the ledger's "Fair share" column cells; each formatted value
    # must occur at least twice in the page (ledger + matrix total).
    ledger = html.split('id="control-ledger"', 1)[1].split("</section>", 1)[0]
    # Scoped to the fair-share column ONLY (data-col marker): the if-removed
    # cell is also `class="text-right"` but its LOO values are NOT mirrored in
    # the matrix totals, so an unscoped regex would demand they appear twice
    # and spuriously fail. (Task-6 review [Important].)
    fair_share_cells = re.findall(
        r'<td class="text-right" data-col="fair-share">([^<]+?)(?:<br|</td>)', ledger
    )
    amounts = [c.strip() for c in fair_share_cells if c.strip() and c.strip() != "—"]
    for amount in amounts:
        assert html.count(amount) >= 2, f"ledger amount {amount} not mirrored in matrix totals"


@pytest.mark.anyio
async def test_controls_snapshot_condensed_with_expansion(client, analyst_org_aggregate_run):
    run = analyst_org_aggregate_run
    html = (await client.get(f"/runs/{run.id}")).text
    assert 'id="controls-snapshot"' in html
    assert "<details" in html.split('id="controls-snapshot"', 1)[1]


@pytest.mark.anyio
async def test_scenario_dumbbell_renders_css_not_chart_vendor(client, analyst_org_aggregate_run):
    """The prior chart vendor's per-scenario scatter is retired in favor of
    a CSS dumbbell (design-mock match — the chart vendor's modebar
    overlapped data and the largest value label clipped at the right edge).
    ``analyst_org_aggregate_run``
    seeds 2 constituent scenarios — the iteration contract minimum — so both
    must render as their own ``.dumbbell-track`` row, not just the first."""
    run = analyst_org_aggregate_run
    html = (await client.get(f"/runs/{run.id}")).text
    assert 'id="scenario-dumbbell"' in html

    section = html[
        html.index('id="scenario-dumbbell"') : html.index(
            "</section>", html.index('id="scenario-dumbbell"')
        )
    ]
    assert "var(--chart-inherent)" in section
    assert "var(--chart-residual)" in section
    assert section.count("dumbbell-track") == 2, "expected one track per scenario (2 seeded)"


# ---------------------------------------------------------------------------
# T7 wiring-level tests (label map on the live page; overflow-menu RBAC; the
# status-fragment cost-carry gap; verdict-strip negative-value honesty).
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_display_labels_rendered_on_aggregate_page(client, analyst_org_aggregate_run):
    """Spec §Testing: label map pinned on the real rendered page."""
    html = (await client.get(f"/runs/{analyst_org_aggregate_run.id}")).text
    assert "Typical case (median)" in html
    assert "Mean (average)" in html
    assert "Median (P50)" not in html  # old label gone
    # Tail labels only when the fixture run has tail metrics:
    if "1-in-10 year" in html:
        assert "1-in-10 year (VaR 90%)" in html


@pytest.mark.anyio
async def test_viewer_role_sees_no_danger_actions(
    client, authed_analyst, analyst_org_aggregate_run, db_session
):
    """Spec §Testing: RBAC on the overflow menu.

    The brief named ``authed_viewer``, but that fixture (a) returns a tuple, and
    (b) mints its OWN org, so it would 404 on the analyst's run and test IDOR,
    not menu gating (plan-gate Spec-N2 — verify before use). To exercise the
    real gate we mint a VIEWER *in the run's org* and drive the page with it.
    """
    from idraa.models.enums import UserRole
    from idraa.models.organization import Organization
    from idraa.services.auth import SESSION_COOKIE
    from tests.factories import create_user, login_client_as

    _, org_id = authed_analyst
    org = await db_session.get(Organization, org_id)
    viewer = await create_user(
        db_session, org, email="viewer-menu@test.local", role=UserRole.VIEWER
    )
    cookie = await login_client_as(db_session, viewer)
    client.cookies.set(SESSION_COOKIE, cookie)

    resp = await client.get(f"/runs/{analyst_org_aggregate_run.id}")
    assert resp.status_code == 200  # same-org viewer can read the page
    html = resp.text
    assert "Purge sample arrays" not in html
    assert "Delete run" not in html


@pytest.mark.anyio
async def test_status_fragment_completed_carries_cost(client, analyst_org_aggregate_run):
    """A poll-completion render must match a fresh page load (with cost/ROI) —
    the fragment handler must pass converted_cost_summary (plan-gate Arch-I1).

    Asserted as BYTE PARITY of the verdict-strip block between the fresh page
    (`/runs/{id}`) and the poll fragment (`/runs/{id}/status`): the strip is
    where cost/ROI cells and the cost-dedup caveat chip live, so a fragment that
    dropped `converted_cost_summary` (or diverged on caveat numbering) would
    render a different strip and fail here — independent of whether THIS fixture
    happens to carry priced controls.
    """
    run = analyst_org_aggregate_run
    page = (await client.get(f"/runs/{run.id}")).text
    frag = (await client.get(f"/runs/{run.id}/status")).text
    assert 'id="verdict-strip"' in frag

    def _verdict(html: str) -> str:
        return html[html.index('id="verdict-strip"') : html.index('id="trust-chips"')]

    assert _verdict(page) == _verdict(frag)


def test_verdict_strip_negative_control_value_is_honest() -> None:
    """T4-watch: a NEGATIVE modeled control value states the negative case
    honestly AND suppresses the green '−X% vs without controls' reduction line
    (sign-gated on cvh.percent > 0 after T4.a)."""
    from types import SimpleNamespace

    from idraa.app import templates

    dr = {
        "currency": {"code": "USD", "symbol": "$"},
        "currency_provenance": None,
        "control_value_headline": {"dollars": -5000, "percent": -15},
        "weight_robustness": None,
        "headline_ale": {"value": 120000, "has_ci_band": False},
    }
    run = SimpleNamespace(controls_snapshot=[{"name": "c1", "snapshot_version": 3}])
    caveats = {"numbers": {}, "entries": []}
    src = (
        "{% from 'runs/components/verdict_strip.html' import verdict_strip %}"
        "{{ verdict_strip(dr, none, run, caveats) }}"
    )
    html = templates.env.from_string(src).render(dr=dr, run=run, caveats=caveats)
    assert "did not measurably reduce" in html
    assert "modeled value" in html  # the negative dollar figure is disclosed
    assert "vs without controls" not in html  # green reduction line suppressed
