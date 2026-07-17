# tests/contracts/test_list_iteration.py
"""N>=3 list-iteration preservation tests over 7 conversion-layer targets (PR rho A4).

Each test wraps assert_preserves_list_count around a target function in
the conversion layer. N=3 is the minimum that surfaces [0]/[-1]/[mid]
silent-data-loss bugs (the kappa pattern).

Existing entity-specific iteration tests in
tests/integration/test_run_executor_adapter_lambda.py et al. are NOT
moved — they have entity-specific assertions beyond pure cardinality.
This file is the cardinality-only safety net; the older files are the
deeper validation layer.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any
from uuid import uuid4

from tests.contracts.helpers import assert_preserves_list_count

# ---- 1. _v3_to_fair_cam_control loop in run_executor (the kappa-bug spot) ----


def test_v3_to_fair_cam_control_loop_preserves_count() -> None:
    """Iterating over N=3 v3 Controls produces 3 fair_cam Controls."""
    from fair_cam.models.control import Control as FairCamControl

    from idraa.models.control import Control as V3Control
    from idraa.models.control_function_assignment import ControlFunctionAssignment
    from idraa.models.enums import (
        ControlType,
        EntityStatus,
        FairCamSubFunction,
    )
    from idraa.services.run_executor import _v3_to_fair_cam_control

    def _make_valid_assignment(
        i: int, org_id: uuid.UUID, ctrl_id: uuid.UUID
    ) -> ControlFunctionAssignment:
        return ControlFunctionAssignment(
            id=uuid4(),
            organization_id=org_id,
            control_id=ctrl_id,
            sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
            capability_value=0.7 + 0.05 * i,
            coverage=0.8,
            reliability=0.85,
        )

    def build_input(n: int) -> list[V3Control]:
        controls: list[V3Control] = []
        for i in range(n):
            org_id = uuid4()
            ctrl_id = uuid4()
            ctrl = V3Control(
                id=ctrl_id,
                organization_id=org_id,
                name=f"control_{i}",
                type=ControlType.TECHNICAL,
                # Set explicitly: column default fires at flush; this test
                # builds in-memory Controls and never flushes.
                annual_cost=Decimal("0"),
                status=EntityStatus.ACTIVE,
                version="1.0",
            )
            ctrl.assignments = [_make_valid_assignment(i, org_id, ctrl_id)]
            controls.append(ctrl)
        return controls

    def loop_body(v3_controls: list[V3Control]) -> list[FairCamControl]:
        return [_v3_to_fair_cam_control(c) for c in v3_controls]

    assert_preserves_list_count(func=loop_body, build_input=build_input, n=3)


# ---- 5. aggregate_run_view_model.build_aggregate_display_results ----


def test_build_aggregate_display_results_preserves_count() -> None:
    """N=3 constituent scenarios -> 3 per-scenario rows in the display payload."""
    from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType
    from idraa.services.aggregate_run_view_model import build_aggregate_display_results

    def build_input(n: int) -> RiskAnalysisRun:
        run = RiskAnalysisRun()
        run.run_type = RunType.AGGREGATE
        run.status = RunStatus.COMPLETED
        run.aggregate_scenario_ids = [str(uuid4()) for _ in range(n)]
        run.simulation_results = {
            "per_scenario": [
                {
                    "scenario_id": run.aggregate_scenario_ids[i],
                    "scenario_name": f"scenario_{i}",
                    "with_controls": {"ale_mean": 1000.0, "ale_p95": 2000.0},
                    "without_controls": {"ale_mean": 5000.0, "ale_p95": 10000.0},
                }
                for i in range(n)
            ],
            "aggregate_with_controls": {"ale_mean": 3000.0, "ale_p95": 6000.0},
            "aggregate_without_controls": {"ale_mean": 15000.0, "ale_p95": 30000.0},
            "control_value": {"absolute_dollars": 12000.0, "percent_reduction": 0.8},
        }
        return run

    def extract_per_scenario_ale_rows(run: RiskAnalysisRun) -> list[Any]:
        result = build_aggregate_display_results(run)
        if result is None:
            return []
        return list(result.get("per_scenario_ale_rows", []))

    assert_preserves_list_count(func=extract_per_scenario_ale_rows, build_input=build_input, n=3)


# ---- 2. controls_importer.import_csv ----


async def test_controls_importer_preserves_count(
    db_session: Any,
    seed_organization: Any,
    seed_user: Any,
) -> None:
    """Importing N=3 valid CSV rows produces 3 imported Control ORM rows."""
    from sqlalchemy import select

    from idraa.models.control import Control
    from idraa.services.controls_importer import import_csv

    # The curated FAIR-CAM CSV format: col 0 = blank, col 1 = name, col 2 = description,
    # col 3 = FAIR-CAM domain text. The importer skips blank names and names == "control".
    # The header row uses "Control" in col 1 which the importer skips automatically.
    header = ",Control,Description,Domain\n"
    rows = "\n".join(f",ctrl_iteration_test_{i},desc_{i},Loss Event Control" for i in range(3))
    csv_bytes = (header + rows).encode("utf-8")

    imported, _skipped = await import_csv(
        db_session,
        org_id=seed_organization.id,
        user_id=seed_user.id,
        csv_bytes=csv_bytes,
    )

    assert imported == 3, (
        f"controls_importer.import_csv: expected 3 imported, got {imported}. "
        f"kappa-class silent-data-loss if < 3."
    )

    # Cross-check via DB query (belt-and-suspenders).
    result = await db_session.execute(
        select(Control).where(
            Control.organization_id == seed_organization.id,
            Control.name.like("ctrl_iteration_test_%"),
        )
    )
    rows_in_db = result.scalars().all()
    assert len(rows_in_db) == 3, (
        f"controls_importer.import_csv: expected 3 Control rows in DB, got {len(rows_in_db)}."
    )


# ---- 3. overlays_importer._validate_rows ----


def test_overlays_importer_validate_rows_preserves_count() -> None:
    """N=3 (lineno, row_dict) pairs -> 3 entries in the validated preview list."""
    from idraa.services.overlays_importer import _validate_rows

    def build_input(n: int) -> list[tuple[int, dict[str, str]]]:
        return [
            (
                i + 2,  # physical line number (1-indexed; line 1 is header)
                {
                    "tag": f"overlay_iter_test_{i}",
                    "display_name": f"Overlay Iteration Test {i}",
                    "frequency_multiplier": "1.5",
                    "magnitude_multiplier": "2.0",
                    "sources": "",
                    "methodology": f"Test methodology for overlay {i} — anchored to internal data.",
                    "methodology_change_reason": "Initial bulk import",
                },
            )
            for i in range(n)
        ]

    def extract_preview(pairs: list[tuple[int, dict[str, str]]]) -> list[dict[str, Any]]:
        preview, _errors, _forms = _validate_rows(
            pairs,
            existing_active_tags=set(),
            inactive_tags=set(),
        )
        return preview

    assert_preserves_list_count(func=extract_preview, build_input=build_input, n=3)


# ---- 6. RunService.create_and_dispatch scenario_ids handling ----


async def test_run_service_create_and_dispatch_preserves_scenario_count(
    db_session: Any,
    seed_organization: Any,
    seed_scenario_factory: Any,
    seed_user: Any,
    wire_executor_to_test_db: Any,
) -> None:
    """N=3 scenario_ids -> run.aggregate_scenario_ids has length 3 (AGGREGATE)."""
    from fastapi import BackgroundTasks

    from idraa.models.risk_analysis_run import RunType
    from idraa.services.runs import RunService

    # Create 3 distinct scenarios.
    s1 = await seed_scenario_factory(name="iter_test_s1")
    s2 = await seed_scenario_factory(name="iter_test_s2")
    s3 = await seed_scenario_factory(name="iter_test_s3")
    scenario_ids = [s1.id, s2.id, s3.id]

    service = RunService(db_session)
    bg = BackgroundTasks()
    run = await service.create_and_dispatch(
        organization_id=seed_organization.id,
        scenario_ids=scenario_ids,
        mc_iterations_override=10000,
        created_by=seed_user.id,
        background_tasks=bg,
    )

    assert run.run_type == RunType.AGGREGATE
    assert run.aggregate_scenario_ids is not None
    assert len(run.aggregate_scenario_ids) == 3, (
        f"RunService.create_and_dispatch: expected aggregate_scenario_ids length 3, "
        f"got {len(run.aggregate_scenario_ids)}. kappa-class silent-data-loss if < 3."
    )


# ---- 8. _snapshot_control_v2.domains (single-ORM-with-list-field → DTO-with-list-field) ----


def test_snapshot_v2_domains_preserves_n3_distinct_domains() -> None:
    """N=3 distinct-domain assignments → 3 sorted entries in snap.domains.

    `_snapshot_control_v2` reads `c.assignments` (a list) and emits
    `snap.domains: list[str]` derived via subfunction_to_domain → set →
    sorted. Per CLAUDE.md data-contract enforcement, any
    `single-ORM-with-list-field → DTO-with-list-field` mapping needs a
    regression guard against future `[0]` / `[first]` truncation.
    """
    from idraa.models.control import Control
    from idraa.models.control_function_assignment import ControlFunctionAssignment
    from idraa.models.enums import (
        ControlType,
        FairCamSubFunction,
    )
    from idraa.services.run_executor import _snapshot_control_v2

    # Three sub_functions each from a distinct FAIR-CAM domain
    # (loss_event / variance_management / decision_support).
    distinct_domain_sub_functions = [
        FairCamSubFunction.LEC_PREV_RESISTANCE,
        FairCamSubFunction.VMC_PREV_REDUCE_VARIANCE_PROB,
        FairCamSubFunction.DSC_PREV_DEFINED_EXPECTATIONS,
    ]

    org_id = uuid4()
    ctrl = Control(
        id=uuid4(),
        organization_id=org_id,
        name="multi-domain-iteration-control",
        type=ControlType.TECHNICAL,
        annual_cost=Decimal("0"),
        version="1.0",
    )
    ctrl.assignments = [
        ControlFunctionAssignment(
            id=uuid4(),
            organization_id=org_id,
            control_id=ctrl.id,
            sub_function=sf,
            capability_value=0.7 + 0.05 * i,
            coverage=0.8,
            reliability=0.85,
        )
        for i, sf in enumerate(distinct_domain_sub_functions)
    ]

    snap = _snapshot_control_v2(ctrl)

    # Explicit cardinality guard — catches `[0]` / `[first]` regressions.
    assert len(snap.domains) == 3, (
        f"_snapshot_control_v2.domains: expected 3 distinct domains, got "
        f"{len(snap.domains)} ({snap.domains!r}). kappa-class silent-data-loss "
        f"if < 3."
    )
    # Sorted alphabetically — exact expected ordering.
    assert snap.domains == ["decision_support", "loss_event", "variance_management"]


# ---- 7. ScenarioRepo.fetch_by_ids_for_org (filter mode — le) ----


async def test_scenario_repo_fetch_by_ids_preserves_le_count(
    db_session: Any,
    seed_organization: Any,
    seed_scenario_factory: Any,
) -> None:
    """N=3 IDs in -> <=3 scenarios out (filter-style; missing IDs silently dropped).

    We insert exactly 3 Scenarios and query with exactly those 3 IDs, so we
    expect exactly 3 back. The le mode accommodates missing-ID drop semantics;
    with all 3 present the result is exactly 3.
    """
    from idraa.repositories.scenario_repo import ScenarioRepo

    s1 = await seed_scenario_factory(name="iter_repo_s1")
    s2 = await seed_scenario_factory(name="iter_repo_s2")
    s3 = await seed_scenario_factory(name="iter_repo_s3")
    scenario_ids = [s1.id, s2.id, s3.id]

    repo = ScenarioRepo(db_session)
    result = await repo.fetch_by_ids_for_org(seed_organization.id, scenario_ids)

    # Primary assertion: exactly 3 returned (all inserted IDs found).
    assert len(result) == 3, (
        f"ScenarioRepo.fetch_by_ids_for_org: expected 3 scenarios, got {len(result)}. "
        f"kappa-class silent-data-loss if < 3."
    )

    # assert_preserves_list_count in le mode — verifies the filter-style contract.
    def build_input_with_existing_ids(n: int) -> list[Any]:
        # n is always 3 here; we use the pre-created IDs.
        return scenario_ids[:n]

    def fetch_sync(ids: list[Any]) -> list[Any]:
        # We already have the result from the await above; return it directly
        # so assert_preserves_list_count can check the le cardinality invariant.
        return result

    assert_preserves_list_count(
        func=fetch_sync,
        build_input=build_input_with_existing_ids,
        n=3,
        expected_len_relation="le",
    )
