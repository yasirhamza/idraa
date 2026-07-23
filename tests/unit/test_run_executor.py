"""run_executor.execute_run: BackgroundTask body.

Tests use the SYNC path (mc_iterations < 1000) so the executor runs
inline within the test without BG-task scheduling. Tests bypass
RunService and call execute_run directly with a pre-seeded QUEUED row.

IMPORTANT: execute_run uses _get_sessionmaker() to open its own session.
The test's db_session and the executor's session must share the same
DB. This is arranged via the wire_executor_to_test_db fixture in
conftest.py — it sets DATABASE_URL to db_url and resets the config +
db singletons so _get_sessionmaker() picks up the test DB file.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.organization import Organization
from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType
from idraa.models.scenario import Scenario
from idraa.models.user import User
from idraa.services.run_executor import _RUN_FAILURE_MESSAGE, execute_run


@pytest.fixture
async def queued_run(
    db_session: AsyncSession,
    seed_scenario_with_controls: Scenario,
    seed_user: User,
    seed_organization: Organization,
    wire_executor_to_test_db: None,
) -> RiskAnalysisRun:
    scenario = seed_scenario_with_controls
    run = RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=seed_organization.id,
        scenario_id=scenario.id,
        mc_iterations=200,
        inputs_hash="h" * 64,
        controls_snapshot=[],
        control_ids_used=[str(c.id) for c in scenario.mitigating_controls],
        status=RunStatus.QUEUED,
        created_by=seed_user.id,
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)
    return run


@pytest.mark.asyncio
async def test_execute_run_happy_path_completes(
    db_session: AsyncSession,
    queued_run: RiskAnalysisRun,
) -> None:
    """Happy path: QUEUED → RUNNING → COMPLETED with simulation_results populated."""
    await execute_run(queued_run.id)
    await db_session.refresh(queued_run)
    assert queued_run.status == RunStatus.COMPLETED
    assert queued_run.started_at is not None
    assert queued_run.completed_at is not None
    assert queued_run.simulation_results is not None
    assert "base_risk" in queued_run.simulation_results
    assert "residual_risk" in queued_run.simulation_results
    assert "loss_exceedance_curve" in queued_run.simulation_results
    # SC-I1 (review): pin the schema-version stamp on the SINGLE path too —
    # a refactor that moves the stamp inside the AGGREGATE branch must fail here.
    from idraa.services.simulation_payload import SIMULATION_RESULTS_SCHEMA_VERSION

    assert queued_run.simulation_results.get("schema_version") == SIMULATION_RESULTS_SCHEMA_VERSION


@pytest.mark.asyncio
async def test_execute_run_populates_controls_snapshot(
    db_session: AsyncSession,
    queued_run: RiskAnalysisRun,
) -> None:
    await execute_run(queued_run.id)
    await db_session.refresh(queued_run)
    snapshot = queued_run.controls_snapshot
    assert isinstance(snapshot, list)
    assert len(snapshot) >= 1
    first = snapshot[0]
    # V3 snapshot shape (issue #131 T6.5): per-assignment ``unit_type`` is
    # captured at write time so re-runs are reproducible across future
    # SUB_FUNCTION_UNITS mutations. New writes are V3; V2/V1 stay read-only.
    assert "control_id" in first
    assert "name" in first
    assert first.get("snapshot_version") == 3
    assert "assignments" in first
    assert len(first["assignments"]) >= 1
    assignment = first["assignments"][0]
    assert "capability_value" in assignment
    # V3 contract: each assignment carries unit_type captured at write time.
    assert "unit_type" in assignment


@pytest.mark.asyncio
async def test_execute_run_skips_if_cancelled_in_queue(
    db_session: AsyncSession,
    queued_run: RiskAnalysisRun,
) -> None:
    """If the run was already CANCELLED before exec started, executor returns without write."""
    queued_run.status = RunStatus.CANCELLED
    await db_session.commit()

    await execute_run(queued_run.id)

    await db_session.refresh(queued_run)
    assert queued_run.status == RunStatus.CANCELLED
    assert queued_run.simulation_results is None
    assert queued_run.started_at is None


@pytest.mark.asyncio
async def test_execute_run_returns_cleanly_on_cancel_pre_calibrate(
    db_session: AsyncSession,
    queued_run: RiskAnalysisRun,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If status flips to CANCELLED between Phase 1 and Phase 2, executor returns
    cleanly without writing FAILED."""
    from idraa.services import run_executor as runner

    real_check = runner._check_cancelled_or_continue
    call_count: dict[str, int] = {"n": 0}

    async def fake_check(session: AsyncSession, run_id: uuid.UUID) -> bool:
        call_count["n"] += 1
        if call_count["n"] == 1:
            run = await session.get(RiskAnalysisRun, run_id)
            assert run is not None
            run.status = RunStatus.CANCELLED
            await session.commit()
            return False
        return await real_check(session, run_id)

    monkeypatch.setattr(runner, "_check_cancelled_or_continue", fake_check)

    await execute_run(queued_run.id)

    await db_session.refresh(queued_run)
    assert queued_run.status == RunStatus.CANCELLED
    assert queued_run.simulation_results is None


@pytest.mark.asyncio
async def test_execute_run_single_scenario_fetch_is_org_scoped(
    db_session: AsyncSession,
    seed_scenario_with_controls: Scenario,
    seed_user: User,
    seed_organization: Organization,
    seed_organization_factory: Any,
    wire_executor_to_test_db: None,
) -> None:
    """Issue #265: the SINGLE-run scenario fetch is scoped to run.organization_id.

    A run whose organization_id points at a DIFFERENT org than the scenario must
    NOT silently load that scenario (the old ``session.get(Scenario, id)`` was
    PK-only). With ScenarioRepo.get_for_org_or_raise the executor fails loud.
    """
    scenario = seed_scenario_with_controls
    other_org = await seed_organization_factory(name="Other Org 265")
    run = RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=other_org.id,  # cross-org: scenario belongs to seed_organization
        scenario_id=scenario.id,
        mc_iterations=200,
        inputs_hash="h" * 64,
        controls_snapshot=[],
        control_ids_used=[],
        status=RunStatus.QUEUED,
        created_by=seed_user.id,
    )
    db_session.add(run)
    await db_session.commit()

    await execute_run(run.id)

    await db_session.refresh(run)
    assert run.status == RunStatus.FAILED, (
        "cross-org SINGLE scenario fetch should fail, not silently load by PK"
    )
    # #211 Phase 2 (review SC-I1): the FAILURE path must also unregister —
    # a try/except without finally would leave the id stuck in the registry
    # and permanently sweep-exempt the zombie row.
    from idraa.services.run_reaper import active_run_ids

    assert run.id not in active_run_ids()


# PR pi F12 deleted ``test_execute_run_marks_failed_on_calibration_error``
# alongside the xfail marker added in F3 -- calibrate_scenario was excised
# from the SINGLE run path. PR pi F14 deleted
# ``test_execute_run_failed_does_not_update_scenario_markers`` because
# Scenario.last_simulated_at and Scenario.last_simulation_inputs_hash were
# dropped in the same atomic schema-day commit; with no markers to update,
# the FAILED-path no-op assertion is no longer meaningful.


# ---- Issue #89 AGGREGATE per-scenario executor tests ----


@pytest.fixture
async def aggregate_queued_run(
    db_session: AsyncSession,
    seed_scenario_factory: Any,
    seed_control_factory: Any,
    seed_user: User,
    seed_organization: Organization,
    wire_executor_to_test_db: None,
) -> tuple[RiskAnalysisRun, Scenario, Scenario, Any, Any]:
    """An AGGREGATE QUEUED run with two scenarios + disjoint controls."""
    from idraa.models.scenario_control import ScenarioControl

    c1 = await seed_control_factory(name="ctrl_x_only")
    c2 = await seed_control_factory(name="ctrl_y_only")
    s1 = await seed_scenario_factory(name="agg_exec_s1")
    s2 = await seed_scenario_factory(name="agg_exec_s2")
    db_session.add(ScenarioControl(scenario_id=s1.id, control_id=c1.id))
    db_session.add(ScenarioControl(scenario_id=s2.id, control_id=c2.id))
    await db_session.commit()
    run = RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=seed_organization.id,
        scenario_id=None,
        run_type=RunType.AGGREGATE,
        mc_iterations=200,
        inputs_hash="h" * 64,
        controls_snapshot=[],
        control_ids_used=[str(c1.id), str(c2.id)],
        aggregate_scenario_ids=sorted([str(s1.id), str(s2.id)]),
        aggregate_control_ids_per_scenario={
            str(s1.id): [str(c1.id)],
            str(s2.id): [str(c2.id)],
        },
        status=RunStatus.QUEUED,
        created_by=seed_user.id,
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)
    return run, s1, s2, c1, c2


@pytest.mark.asyncio
async def test_aggregate_executor_passes_per_scenario_dict_to_fair_cam(
    db_session: AsyncSession,
    aggregate_queued_run: tuple[RiskAnalysisRun, Scenario, Scenario, Any, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #89: executor forwards run.aggregate_control_ids_per_scenario to fair_cam.

    AUDIT (Epic A #324, Task 8 cutover): re-pointed the spy from the retired
    ``ControlAwareRiskCalculator`` (pyfair) to ``NativeControlAwareRiskCalculator``.
    The executor now constructs the native calculator; the #89 forwarding
    invariant (per-scenario dict reaches ``calculate_aggregate_enhanced_risk``)
    is unchanged — only the calculator class did.
    """
    from fair_cam.risk_engine.native_control_aware import NativeControlAwareRiskCalculator

    run, s1, s2, c1, c2 = aggregate_queued_run
    captured: dict[str, Any] = {}
    real_method = NativeControlAwareRiskCalculator.calculate_aggregate_enhanced_risk

    def spy(self: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return real_method(self, **kwargs)

    monkeypatch.setattr(NativeControlAwareRiskCalculator, "calculate_aggregate_enhanced_risk", spy)
    await execute_run(run.id)
    await db_session.refresh(run)
    assert run.status == RunStatus.COMPLETED, run.error_message
    assert captured["per_scenario_active_control_ids"] == {
        str(s1.id): [str(c1.id)],
        str(s2.id): [str(c2.id)],
    }


@pytest.mark.asyncio
async def test_aggregate_executor_passes_none_for_legacy_null_column_row(
    db_session: AsyncSession,
    aggregate_queued_run: tuple[RiskAnalysisRun, Scenario, Scenario, Any, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Back-compat: legacy AGGREGATE row (NULL column) -> None passed to fair_cam.

    AUDIT (Epic A #324, Task 8 cutover): re-pointed the spy from the retired
    ``ControlAwareRiskCalculator`` to ``NativeControlAwareRiskCalculator``. The
    NULL-column back-compat invariant (None forwarded for legacy rows) is
    unchanged.
    """
    from fair_cam.risk_engine.native_control_aware import NativeControlAwareRiskCalculator

    run, _, _, _, _ = aggregate_queued_run
    # Simulate legacy row (created before issue #89).
    run.aggregate_control_ids_per_scenario = None
    await db_session.commit()

    captured: dict[str, Any] = {}
    real_method = NativeControlAwareRiskCalculator.calculate_aggregate_enhanced_risk

    def spy(self: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return real_method(self, **kwargs)

    monkeypatch.setattr(NativeControlAwareRiskCalculator, "calculate_aggregate_enhanced_risk", spy)
    await execute_run(run.id)
    await db_session.refresh(run)
    assert run.status == RunStatus.COMPLETED
    assert captured["per_scenario_active_control_ids"] is None


@pytest.mark.asyncio
async def test_aggregate_executor_raises_on_stale_per_scenario_ids_with_audit_row(
    db_session: AsyncSession,
    aggregate_queued_run: tuple[RiskAnalysisRun, Scenario, Scenario, Any, Any],
) -> None:
    """M5 (plan-gate): executor fails loud + audits when per-scenario dict
    references a control_id that ControlRepo.fetch_by_ids_for_org filters out
    (cross-org via poisoned scenario_controls; or any future filtering reason).

    Constructs the failure by raw-SQL UPDATE of the run row — bypasses the ORM
    @validates cross-field check (which is the in-band defense; this test
    covers the out-of-band executor defense).
    """
    import json

    from sqlalchemy import select, text

    from idraa.models.audit_log import AuditLog

    run, s1, _s2, c1, _c2 = aggregate_queued_run
    # Inject a phantom cid that ControlRepo.fetch_by_ids_for_org won't load.
    # Raw SQL bypasses @validates so the in-band cross-field defense doesn't catch this.
    # SQLite stores UUIDs as 32-char compact (no hyphens); use uuid.hex, not str().
    phantom_cid = str(uuid.uuid4())
    new_per_scenario = dict(run.aggregate_control_ids_per_scenario)  # type: ignore[arg-type]
    new_per_scenario[str(s1.id)] = [str(c1.id), phantom_cid]
    await db_session.execute(
        text(
            "UPDATE risk_analysis_runs SET aggregate_control_ids_per_scenario = :v WHERE id = :id"
        ),
        {"v": json.dumps(new_per_scenario), "id": run.id.hex},
    )
    await db_session.commit()

    await execute_run(run.id)
    await db_session.refresh(run)
    assert run.status == RunStatus.FAILED, run.error_message
    assert run.error_message is not None
    # #82: the user-facing error_message is genericized (no internal detail
    # leak). The stale/race forensic evidence now lives in the audit row
    # asserted below, not in error_message.
    assert run.error_message == _RUN_FAILURE_MESSAGE

    rs = await db_session.execute(
        select(AuditLog).where(
            AuditLog.entity_id == run.id,
            AuditLog.action == "run.stale_per_scenario_control_ids",
        )
    )
    assert rs.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_execute_run_registry_lifecycle_success(
    queued_run: RiskAnalysisRun,
    db_session: AsyncSession,
) -> None:
    """#211 Phase 2: execute_run registers the run id for the sweep-exclusion
    registry and ALWAYS unregisters on exit — a completed run must leave the
    registry empty so the periodic reaper can never be blocked by it."""
    from idraa.services.run_reaper import active_run_ids

    await execute_run(queued_run.id)
    assert queued_run.id not in active_run_ids()


@pytest.mark.asyncio
async def test_execute_run_registry_lifecycle_on_missing_run(
    db_session: AsyncSession,
    wire_executor_to_test_db: None,
) -> None:
    """Early-return path (unknown run id) must also unregister."""
    import uuid as _uuid

    from idraa.services.run_reaper import active_run_ids

    ghost = _uuid.uuid4()
    await execute_run(ghost)
    assert ghost not in active_run_ids()
