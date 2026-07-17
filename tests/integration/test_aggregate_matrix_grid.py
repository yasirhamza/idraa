"""F18: AGGREGATE matrix block renders as data_grid (sticky axes).

Fixture topology: ``authed_analyst`` creates the org. ``aggregate_run_client``
seeds 2 scenarios + 2 controls in that org, links controls to scenarios via
ScenarioControl, dispatches a completed AGGREGATE run (inline executor via
mc_iterations_override=200), then returns ``(client, run_id)``.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def aggregate_run_client(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_user: Any,
    seed_scenario_factory: Callable[..., Awaitable[Any]],
    seed_control_factory: Callable[..., Awaitable[Any]],
    wire_executor_to_test_db: None,
) -> tuple[AsyncClient, uuid.UUID]:
    """A COMPLETED AGGREGATE run with 2 scenarios and 2 controls.

    Scenario 1 → [Control Alpha]; Scenario 2 → [Control Alpha, Control Beta].
    Returns (client, run_id) so tests can hit /runs/{run_id} and
    /runs/{run_id}/control-matrix.csv.
    """
    from fastapi import BackgroundTasks

    from idraa.models.scenario_control import ScenarioControl
    from idraa.services.runs import RunService

    client, org_id = authed_analyst
    s1 = await seed_scenario_factory(
        name="grid-s1", organization_id=org_id, created_by=seed_user.id
    )
    s2 = await seed_scenario_factory(
        name="grid-s2", organization_id=org_id, created_by=seed_user.id
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
    return client, run.id


@pytest_asyncio.fixture
async def legacy_aggregate_run_client(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_user: Any,
    seed_scenario_factory: Callable[..., Awaitable[Any]],
    seed_control_factory: Callable[..., Awaitable[Any]],
    wire_executor_to_test_db: None,
) -> tuple[AsyncClient, uuid.UUID]:
    """A COMPLETED AGGREGATE run whose simulation_results have control_adjustments
    but NO shapley_value keys — simulating a legacy run predating Shapley attribution.

    After the executor completes normally, we patch the stored simulation_results
    to strip all shapley_value keys so that _build_per_scenario_control_matrix
    returns {"controls": [], "rows": [], "unavailable": True}.
    """

    from fastapi import BackgroundTasks

    from idraa.models.risk_analysis_run import RiskAnalysisRun
    from idraa.models.scenario_control import ScenarioControl
    from idraa.services.runs import RunService

    client, org_id = authed_analyst
    s1 = await seed_scenario_factory(
        name="legacy-s1", organization_id=org_id, created_by=seed_user.id
    )
    s2 = await seed_scenario_factory(
        name="legacy-s2", organization_id=org_id, created_by=seed_user.id
    )
    ctrl_a = await seed_control_factory(
        name="Control Legacy", organization_id=org_id, created_by=seed_user.id
    )
    db_session.add_all(
        [
            ScenarioControl(scenario_id=s1.id, control_id=ctrl_a.id),
            ScenarioControl(scenario_id=s2.id, control_id=ctrl_a.id),
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

    # Strip shapley_value (+ shapley_value_mean, 2026-07-04 side-by-side) from
    # every control_adjustment to simulate a legacy run. The matrix's PRIMARY
    # cell reads shapley_value_mean first, falling back to shapley_value only
    # when the mean key is absent — so both must be stripped, or the run's
    # real (post-mean-basis) shapley_value_mean keys would keep the matrix
    # "available" and this fixture would no longer simulate a legacy run.
    from copy import deepcopy

    from sqlalchemy import select
    from sqlalchemy.orm.attributes import flag_modified

    result = await db_session.execute(select(RiskAnalysisRun).where(RiskAnalysisRun.id == run.id))
    db_run = result.scalar_one()
    if db_run.simulation_results:
        # deepcopy so SQLAlchemy detects the mutation (JSON column change tracking)
        sr = deepcopy(db_run.simulation_results)
        for ps in sr.get("per_scenario", []):
            for adj in ps.get("control_adjustments", []):
                adj.pop("shapley_value", None)
                adj.pop("shapley_value_mean", None)
        db_run.simulation_results = sr
        flag_modified(db_run, "simulation_results")
        await db_session.commit()

    return client, run.id


@pytest.mark.asyncio
async def test_aggregate_matrix_renders_sticky_grid(
    aggregate_run_client: tuple[AsyncClient, uuid.UUID],
) -> None:
    """When opening a completed AGGREGATE run's detail page, the matrix block
    renders via data_grid (sticky positioning markers present)."""
    client, run_id = aggregate_run_client
    resp = await client.get(f"/runs/{run_id}")
    body = resp.text
    assert resp.status_code == 200
    # Sticky positioning markers from data_grid
    assert "sticky top-0" in body
    assert "sticky left-0" in body
    # Compact money format on cells (abbreviate_money filter uses $K/$M prefixes
    # but for small values may render $0 or similar — any $ sign suffices)
    assert "$" in body
    # Totals row label from data_grid tfoot
    assert "Total per control" in body


@pytest.mark.asyncio
async def test_aggregate_matrix_carries_shapley_subscript(
    aggregate_run_client: tuple[AsyncClient, uuid.UUID],
) -> None:
    """Shapley semantics: column headers carry '(Shapley $)' subscript (not isol.)."""
    client, run_id = aggregate_run_client
    resp = await client.get(f"/runs/{run_id}")
    assert "(Shapley $)" in resp.text
    assert "(isol. $)" not in resp.text


@pytest.mark.asyncio
async def test_attribution_matrix_has_no_do_not_sum_disclaimer(
    aggregate_run_client: tuple[AsyncClient, uuid.UUID],
) -> None:
    """Shapley cells sum to totals — the old 'do not sum' / multiplicative disclaimer
    must be absent and the new Shapley disclaimer must be present."""
    client, run_id = aggregate_run_client
    resp = await client.get(f"/runs/{run_id}")
    body = resp.text
    assert resp.status_code == 200
    assert "do not sum" not in body.lower()
    assert "compose multiplicatively" not in body.lower()
    assert "Shapley" in body


@pytest.mark.asyncio
async def test_legacy_run_renders_unavailable_banner(
    legacy_aggregate_run_client: tuple[AsyncClient, uuid.UUID],
) -> None:
    """A run with adjustments but no shapley_value keys renders the unavailable
    banner rather than the data grid or a no-controls alert."""
    client, run_id = legacy_aggregate_run_client
    resp = await client.get(f"/runs/{run_id}")
    body = resp.text
    assert resp.status_code == 200
    assert "predates Shapley attribution" in body
    # Should NOT render the data grid (no sticky grid markers in the matrix section)
    # The page may have sticky classes elsewhere, so we check the absence of
    # the data_grid tfoot label instead.
    assert "Total per control" not in body


@pytest.mark.asyncio
async def test_aggregate_matrix_hidden_on_mobile_no_csv_offer(
    aggregate_run_client: tuple[AsyncClient, uuid.UUID],
) -> None:
    """The per-scenario control-attribution matrix is a tablet/desktop-only
    artifact: on phones the whole section is hidden (wrapped in
    ``hidden sm:block``) with NO mobile CSV-offer fallback."""
    client, run_id = aggregate_run_client
    resp = await client.get(f"/runs/{run_id}")
    body = resp.text
    # The attribution section still renders for tablet/desktop ...
    # T7: the matrix moved into the control ledger ("What each control is worth").
    idx = body.find("What each control is worth")
    assert idx != -1, "control-ledger attribution section should still render (desktop/tablet)"
    # ... wrapped so it is hidden on phones (the section sits inside hidden sm:block) ...
    assert "hidden sm:block" in body[max(0, idx - 400) : idx], (
        "matrix section must be wrapped hidden on <sm"
    )
    # ... the old mobile CSV-offer card is gone ...
    assert "wider screen" not in body.lower()
    assert "Download matrix CSV" not in body
    # ... and the desktop export link remains.
    assert f"/runs/{run_id}/control-matrix.csv" in body
