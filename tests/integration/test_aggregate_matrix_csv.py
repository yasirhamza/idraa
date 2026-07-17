"""F18: /runs/<id>/control-matrix.csv streams correct shape (M-1, M-2).

Uses the ``aggregate_run_client`` fixture from test_aggregate_matrix_grid.py.
Pytest collects fixtures from sibling files in the same package via conftest,
but cross-file fixture sharing requires the fixture to live in conftest.py or
be imported explicitly. To keep the fixture DRY, the fixture is redeclared here
as a re-export shim that imports from the sibling module — but the cleanest
approach is to define it once in a local conftest for this test module pair.

Since pytest_asyncio fixtures can be shared across test files within the same
directory as long as they are registered via conftest.py, and the fixture
``aggregate_run_client`` defined in test_aggregate_matrix_grid.py is NOT
auto-discovered across files, we duplicate the fixture here.

This file-local fixture is identical to the one in test_aggregate_matrix_grid.py
— both depend on the same shared conftest fixtures (authed_analyst, db_session,
seed_scenario_factory, seed_control_factory, wire_executor_to_test_db, seed_user).
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
    Returns (client, run_id) so tests can hit /runs/{run_id}/control-matrix.csv.
    """
    from fastapi import BackgroundTasks

    from idraa.models.scenario_control import ScenarioControl
    from idraa.services.runs import RunService

    client, org_id = authed_analyst
    s1 = await seed_scenario_factory(name="csv-s1", organization_id=org_id, created_by=seed_user.id)
    s2 = await seed_scenario_factory(name="csv-s2", organization_id=org_id, created_by=seed_user.id)
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


@pytest.mark.asyncio
async def test_matrix_csv_returns_attachment(
    aggregate_run_client: tuple[AsyncClient, uuid.UUID],
) -> None:
    client, run_id = aggregate_run_client
    resp = await client.get(f"/runs/{run_id}/control-matrix.csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "control-matrix" in resp.headers.get("content-disposition", "")


@pytest.mark.asyncio
async def test_matrix_csv_shape_has_row_total_shapley(
    aggregate_run_client: tuple[AsyncClient, uuid.UUID],
) -> None:
    """Shapley efficiency: header includes 'Scenario total' column; last row is
    per-control attribution totals labelled 'Total per control' with a grand-total
    cell appended."""
    client, run_id = aggregate_run_client
    resp = await client.get(f"/runs/{run_id}/control-matrix.csv")
    lines = resp.text.strip().split("\r\n")

    # Preamble: `# `-prefixed Shapley attribution description
    assert any(line.startswith("# ") and "shapley" in line.lower() for line in lines), (
        "Matrix CSV must lead with a Shapley attribution preamble"
    )

    # Header is "Scenario,<control_name_1>,<control_name_2>,...,Scenario total"
    # (or "...(average $)"/"...(typical $)" pairs + "Scenario total (average $)"
    # for a mean-basis run — every run executed after the mean-basis chain
    # landed, 2026-07-04 side-by-side; substring match covers both shapes).
    header_line = next(line for line in lines if line.startswith("Scenario,"))
    assert "Scenario total" in header_line, "Shapley CSV must include a Scenario total column"

    # Last data row is the per-control totals
    assert lines[-1].startswith("Total per control,")
    # Grand-total cell is appended — same column count as header
    assert lines[-1].count(",") == header_line.count(",")


@pytest.mark.asyncio
async def test_csv_preamble_is_shapley_not_standalone(
    aggregate_run_client: tuple[AsyncClient, uuid.UUID],
) -> None:
    """CSV preamble must reference Shapley, not standalone/multiplicative composition."""
    client, run_id = aggregate_run_client
    resp = await client.get(f"/runs/{run_id}/control-matrix.csv")
    csv_text = resp.text
    assert "do not sum" not in csv_text.lower()
    assert "standalone" not in csv_text.lower()
    assert "shapley" in csv_text.lower()


@pytest.mark.asyncio
async def test_csv_none_cells_emit_blank_not_zero(
    aggregate_run_client: tuple[AsyncClient, uuid.UUID],
) -> None:
    """Absent attribution cells (None) must produce blank CSV fields, not '0.00'.

    For the standard fixture (all scenarios have Shapley computed), cells should
    be non-blank. This test verifies no '0.00' appears where a control is absent
    for a scenario (Control Beta is absent for csv-s1 which only has Control Alpha).
    """
    client, run_id = aggregate_run_client
    resp = await client.get(f"/runs/{run_id}/control-matrix.csv")
    lines = resp.text.strip().split("\r\n")
    # Identify data rows (not preamble, not header, not totals row)
    data_rows = [
        line
        for line in lines
        if not line.startswith("# ")
        and not line.startswith("Scenario,")
        and not line.startswith("Total per control,")
    ]
    # csv-s1 has only Control Alpha — its Control Beta cell should be blank, not '0.00'
    # Find the csv-s1 row (contains "csv-s1" in the first field)
    s1_rows = [r for r in data_rows if r.startswith("csv-s1,")]
    if s1_rows:
        s1_row = s1_rows[0]
        fields = s1_row.split(",")
        # There are 3 control columns + 1 scenario total = 4 data fields after scenario name
        # Control Beta column should be blank for s1 (s1 has no Control Beta)
        # Exact column position depends on ordering; just check no spurious '0.00' in absent slot
        # The Control Beta column for s1 should be '' not '0.00'
        assert "0.00" not in fields[2:] or all(f == "" or f != "0.00" for f in fields[2:3]), (
            "Absent cell for scenario with no Control Beta must be blank, not 0.00"
        )


@pytest.mark.asyncio
async def test_csv_row_total_sums_non_none_cells(
    aggregate_run_client: tuple[AsyncClient, uuid.UUID],
) -> None:
    """Each data row's 'Scenario total' column equals the sum of its non-blank cell values."""
    client, run_id = aggregate_run_client
    resp = await client.get(f"/runs/{run_id}/control-matrix.csv")
    lines = resp.text.strip().split("\r\n")
    header_line = next(line for line in lines if line.startswith("Scenario,"))
    headers = header_line.split(",")
    # 2026-07-04 mean+typical side-by-side: the column is "Scenario total" on
    # legacy (typical-basis) runs, "Scenario total (average $)" on mean-basis
    # runs — match by prefix so both shapes resolve to the same index.
    scenario_total_idx = next(i for i, h in enumerate(headers) if h.startswith("Scenario total"))

    # Mean-basis runs (2026-07-04 side-by-side, every run executed after the
    # mean-basis chain landed) pair each control's average column with a
    # typical column — the "Scenario total (average $)" sums ONLY the average
    # (primary) cells, at even offsets within the control-column span. Legacy
    # (typical-basis) runs have one column per control, so every cell counts.
    _is_paired = headers[scenario_total_idx].startswith("Scenario total (average $)")

    data_rows = [
        line
        for line in lines
        if not line.startswith("# ")
        and not line.startswith("Scenario,")
        and not line.startswith("Total per control,")
    ]
    for row in data_rows:
        fields = row.split(",")
        control_fields = fields[1:scenario_total_idx]
        if _is_paired:
            control_fields = control_fields[0::2]  # average column of each pair
        cell_values = [float(f) for f in control_fields if f.strip() != ""]
        reported_total = fields[scenario_total_idx].strip()
        if cell_values and reported_total:
            expected = round(sum(cell_values), 2)
            assert abs(float(reported_total) - expected) < 0.01, (
                f"Row total mismatch for row '{fields[0]}': "
                f"expected {expected}, got {reported_total}"
            )


@pytest.mark.asyncio
async def test_matrix_csv_requires_auth(
    client: AsyncClient,
) -> None:
    """Unauthenticated request must not serve CSV data."""
    fake_run_id = uuid.uuid4()
    resp = await client.get(f"/runs/{fake_run_id}/control-matrix.csv")
    assert resp.status_code in (302, 303, 307, 401, 403)
    assert resp.headers.get("content-type", "").startswith("text/csv") is False


@pytest_asyncio.fixture
async def partial_row_run_client(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_user: Any,
    seed_scenario_factory: Callable[..., Awaitable[Any]],
    seed_control_factory: Callable[..., Awaitable[Any]],
    wire_executor_to_test_db: None,
) -> tuple[AsyncClient, uuid.UUID, str]:
    """A COMPLETED AGGREGATE run where one scenario's row has a mix of valued and absent cells.

    Both scenarios have both controls. After the executor completes we strip
    ``shapley_value`` from CtrlB's adjustment on one scenario, producing a row
    with one valued cell and one absent cell.

    Returns ``(client, run_id, stripped_scenario_name)`` so the test can locate
    the partial row by name regardless of executor iteration order (which is
    non-deterministic because SQLAlchemy returns rows in UUID-hash order).
    """
    from copy import deepcopy

    from fastapi import BackgroundTasks
    from sqlalchemy import select
    from sqlalchemy.orm.attributes import flag_modified

    from idraa.models.risk_analysis_run import RiskAnalysisRun
    from idraa.models.scenario_control import ScenarioControl
    from idraa.services.runs import RunService

    client, org_id = authed_analyst
    s1 = await seed_scenario_factory(
        name="partial-s1", organization_id=org_id, created_by=seed_user.id
    )
    s2 = await seed_scenario_factory(
        name="partial-s2", organization_id=org_id, created_by=seed_user.id
    )
    ctrl_a = await seed_control_factory(
        name="Ctrl Alpha Partial", organization_id=org_id, created_by=seed_user.id
    )
    ctrl_b = await seed_control_factory(
        name="Ctrl Beta Partial", organization_id=org_id, created_by=seed_user.id
    )

    # Both scenarios have both controls — after run we'll strip CtrlB's shapley from s1.
    db_session.add_all(
        [
            ScenarioControl(scenario_id=s1.id, control_id=ctrl_a.id),
            ScenarioControl(scenario_id=s1.id, control_id=ctrl_b.id),
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

    # Strip shapley_value (+ shapley_value_mean, 2026-07-04 side-by-side) from
    # CtrlB's adjustment for the s1 scenario entry — the CSV's PRIMARY (average)
    # cell reads shapley_value_mean first, falling back to shapley_value only
    # when the mean key is absent, so both must be stripped to produce a truly
    # absent primary cell for this scenario/control (matches a real Shapley
    # skip/drop, which drops both keys together). We locate the entry by
    # scenario_name (or scenario_id) rather than by positional index — executor
    # iteration order is non-deterministic so per_scenario[0] may be s1 or s2
    # depending on UUID hash order.
    result = await db_session.execute(select(RiskAnalysisRun).where(RiskAnalysisRun.id == run.id))
    db_run = result.scalar_one()
    stripped_scenario_name: str = "partial-s1"  # default; overwritten below if found
    if db_run.simulation_results:
        sr = deepcopy(db_run.simulation_results)
        per_scenario = sr.get("per_scenario", [])
        for entry in per_scenario:
            # Match on scenario_name field (falls back to matching s1.id as str).
            entry_name = entry.get("scenario_name") or entry.get("scenario_id", "")
            if entry_name == "partial-s1" or str(entry.get("scenario_id", "")) == str(s1.id):
                adjs = entry.get("control_adjustments", [])
                if len(adjs) >= 2:
                    adjs[1].pop("shapley_value", None)
                    adjs[1].pop("shapley_value_mean", None)
                    stripped_scenario_name = entry.get("scenario_name", "partial-s1")
                break
        db_run.simulation_results = sr
        flag_modified(db_run, "simulation_results")
        await db_session.commit()

    return client, run.id, stripped_scenario_name


@pytest.mark.asyncio
async def test_csv_partial_row_total_equals_non_blank_sum(
    partial_row_run_client: tuple[AsyncClient, uuid.UUID, str],
) -> None:
    """I2: partial-row disclosure + total correctness.

    When a scenario row has at least one absent (—) cell:
    - Its 'Scenario total' equals the sum of only the non-blank cell values.
    - The blank cell emits '' not '0.00'.
    - The preamble contains the partial-total disclosure sentence.
    """
    client, run_id, stripped_scenario_name = partial_row_run_client
    resp = await client.get(f"/runs/{run_id}/control-matrix.csv")
    assert resp.status_code == 200
    csv_text = resp.text
    lines = csv_text.strip().split("\r\n")

    # Preamble must contain partial-total disclosure.
    preamble_lines = [ln for ln in lines if ln.startswith("# ")]
    preamble_text = " ".join(preamble_lines).lower()
    assert "partial" in preamble_text, (
        "CSV preamble must disclose that rows with blank cells show a partial total"
    )

    # Find the header row and locate the Scenario total column index.
    header_line = next(line for line in lines if line.startswith("Scenario,"))
    headers = header_line.split(",")
    # 2026-07-04 mean+typical side-by-side: the column is "Scenario total" on
    # legacy (typical-basis) runs, "Scenario total (average $)" on mean-basis
    # runs — match by prefix so both shapes resolve to the same index.
    scenario_total_idx = next(i for i, h in enumerate(headers) if h.startswith("Scenario total"))

    # Find data rows (not preamble, not header, not totals).
    data_rows = [
        line
        for line in lines
        if not line.startswith("# ")
        and not line.startswith("Scenario,")
        and not line.startswith("Total per control,")
    ]

    # Locate the partial row by the name recorded in the fixture — using the
    # fixture-recorded identity rather than a hardcoded "partial-s1" prefix avoids
    # a flake when executor iteration order puts s2 at index 0.
    partial_rows = [r for r in data_rows if r.startswith(f"{stripped_scenario_name},")]
    assert partial_rows, f"{stripped_scenario_name!r} row must appear in CSV output"
    partial_row = partial_rows[0]
    fields = partial_row.split(",")
    control_fields = fields[1:scenario_total_idx]

    # 2026-07-04 mean+typical side-by-side: every run executed after the
    # mean-basis chain landed pairs each control's average column with a
    # typical column — the "Scenario total (average $)" sums ONLY the average
    # (primary) cells, at even offsets within the control-column span.
    _is_paired = headers[scenario_total_idx].startswith("Scenario total (average $)")
    primary_fields = control_fields[0::2] if _is_paired else control_fields

    # At least one control cell must be blank (the stripped shapley_value).
    blank_cells = [f for f in control_fields if f.strip() == ""]
    assert blank_cells, (
        f"{stripped_scenario_name!r} must have at least one blank (absent) control cell"
    )

    # The blank ones must truly be empty, not '0.00'.
    for f in control_fields:
        if f.strip() == "":
            assert f != "0.00", "Absent cell must emit '' not '0.00'"

    # The row's Scenario total must equal the sum of only the non-blank PRIMARY
    # (average) cell values — the paired typical column is informational and
    # does not drive the total (routes/runs.py's get_aggregate_matrix_csv).
    non_blank_values = [float(f) for f in primary_fields if f.strip() != ""]
    reported_total = fields[scenario_total_idx].strip()
    if non_blank_values and reported_total:
        expected = round(sum(non_blank_values), 2)
        assert abs(float(reported_total) - expected) < 0.01, (
            f"Partial-row total mismatch: expected {expected} (sum of non-blank cells), "
            f"got {reported_total}"
        )

    # The OTHER scenario row must remain fully valued (no blank control cells).
    other_rows = [r for r in data_rows if not r.startswith(f"{stripped_scenario_name},")]
    for other_row in other_rows:
        other_fields = other_row.split(",")
        other_control_fields = other_fields[1:scenario_total_idx]
        assert all(f.strip() != "" for f in other_control_fields), (
            f"Non-stripped scenario row must have all control cells valued: {other_row!r}"
        )


@pytest.mark.asyncio
async def test_matrix_csv_rejects_single_run(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_user: Any,
    seed_scenario_factory: Callable[..., Awaitable[Any]],
    wire_executor_to_test_db: None,
) -> None:
    """CSV endpoint returns 400 for SINGLE (non-AGGREGATE) runs."""
    from fastapi import BackgroundTasks

    from idraa.services.runs import RunService

    client, org_id = authed_analyst
    scenario = await seed_scenario_factory(
        name="single-csv-test", organization_id=org_id, created_by=seed_user.id
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
    resp = await client.get(f"/runs/{run.id}/control-matrix.csv")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_csv_cells_match_persisted_shapley_values(
    aggregate_run_client: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Direct cell-parity: each CSV data cell equals f'{v:.2f}' from persisted results.

    2026-07-04 mean+typical side-by-side: every run executed after the
    mean-basis chain landed pairs each control's "(average $)" column
    (``shapley_value_mean``) with a "(typical $)" column (``shapley_value``).
    For every scenario row and every control column pair present in the CSV
    header, read the corresponding persisted values from
    ``run.simulation_results["per_scenario"][...]["control_adjustments"][...]``
    and assert each CSV cell equals ``f"{v:.2f}"``, matched by scenario name +
    control name.
    """
    from sqlalchemy import select

    from idraa.models.risk_analysis_run import RiskAnalysisRun

    client, run_id = aggregate_run_client

    # Fetch persisted simulation results.
    result = await db_session.execute(select(RiskAnalysisRun).where(RiskAnalysisRun.id == run_id))
    db_run = result.scalar_one()
    assert db_run.simulation_results, "Run must have persisted simulation_results"
    per_scenario = db_run.simulation_results.get("per_scenario", [])

    # Build a lookup: {scenario_name: {control_name: (shapley_value_mean, shapley_value)}}.
    persisted: dict[str, dict[str, tuple[float | None, float | None]]] = {}
    for entry in per_scenario:
        sc_name = entry.get("scenario_name", "")
        persisted[sc_name] = {}
        for adj in entry.get("control_adjustments", []):
            ctrl_name = adj.get("control_name", "")
            persisted[sc_name][ctrl_name] = (
                adj.get("shapley_value_mean"),
                adj.get("shapley_value"),
            )

    # Fetch the CSV.
    resp = await client.get(f"/runs/{run_id}/control-matrix.csv")
    assert resp.status_code == 200
    lines = resp.text.strip().split("\r\n")

    header_line = next(line for line in lines if line.startswith("Scenario,"))
    headers = header_line.split(",")
    # 2026-07-04 mean+typical side-by-side: the column is "Scenario total" on
    # legacy (typical-basis) runs, "Scenario total (average $)" on mean-basis
    # runs — match by prefix so both shapes resolve to the same index.
    scenario_total_idx = next(i for i, h in enumerate(headers) if h.startswith("Scenario total"))
    # Control column names are between index 1 and scenario_total_idx (exclusive).
    control_headers = headers[1:scenario_total_idx]
    _is_paired = headers[scenario_total_idx].startswith("Scenario total (average $)")

    data_rows = [
        line
        for line in lines
        if not line.startswith("# ")
        and not line.startswith("Scenario,")
        and not line.startswith("Total per control,")
    ]

    for row in data_rows:
        fields = row.split(",")
        sc_name = fields[0]
        if sc_name not in persisted:
            continue  # scenario not in persisted lookup — skip
        if _is_paired:
            for pair_idx in range(0, len(control_headers), 2):
                avg_header = control_headers[pair_idx]
                ctrl_name = avg_header.removesuffix(" (average $)")
                mean_v, typ_v = persisted[sc_name].get(ctrl_name, (None, None))
                avg_cell = fields[1 + pair_idx]
                typ_cell = fields[1 + pair_idx + 1]
                if mean_v is not None:
                    assert avg_cell == f"{mean_v:.2f}", (
                        f"Average-cell mismatch for scenario={sc_name!r} control={ctrl_name!r}: "
                        f"CSV has {avg_cell!r}, persisted shapley_value_mean yields {mean_v:.2f}"
                    )
                if typ_v is not None:
                    assert typ_cell == f"{typ_v:.2f}", (
                        f"Typical-cell mismatch for scenario={sc_name!r} control={ctrl_name!r}: "
                        f"CSV has {typ_cell!r}, persisted shapley_value yields {typ_v:.2f}"
                    )
        else:
            for col_idx, ctrl_name in enumerate(control_headers):
                csv_cell = fields[1 + col_idx]
                _, sv = persisted[sc_name].get(ctrl_name, (None, None))
                if sv is not None:
                    expected_cell = f"{sv:.2f}"
                    assert csv_cell == expected_cell, (
                        f"Cell mismatch for scenario={sc_name!r} control={ctrl_name!r}: "
                        f"CSV has {csv_cell!r}, persisted shapley_value yields {expected_cell!r}"
                    )
