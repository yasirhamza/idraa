"""Phase 1.4 E2E coverage:
- Async dispatch (mc_iterations >= 1000) — BG task runs after response
- Cancel during running — executor returns cleanly without FAILED
- Failure during calibration — FAILED with structured error_message
- Re-run from FAILED — new run created with same inputs_hash
- Reviewer RBAC — POST /run / POST /cancel return 403; GET /runs/{id} 200
- XSS guard — error_message rendered in status fragment is HTML-escaped

Uses authed_analyst/authed_reviewer (tuple[AsyncClient, org_id]) +
csrf_post helper from conftest (matches F8/F9/F11/F12 precedent).
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.enums import (
    ControlType,
    EntityStatus,
    FairCamSubFunction,
    ScenarioType,
    ThreatCategory,
)
from idraa.models.organization import Organization
from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType
from idraa.models.scenario import Scenario
from idraa.models.user import User
from tests.conftest import csrf_post

# ---------------------------------------------------------------------------
# Inline seeding helpers — seed scenario in a given org (not seed_organization)
# ---------------------------------------------------------------------------


def _make_scenario(
    *,
    org_id: uuid.UUID,
    created_by: uuid.UUID,
    name: str = "e2e-test-scenario",
) -> Scenario:
    """Return an unsaved Scenario suitable for the analyst's org.

    Caller must ``db_session.add(...)`` + ``await db_session.commit()``.
    """
    return Scenario(
        organization_id=org_id,
        name=name,
        scenario_type=ScenarioType.CUSTOM,
        threat_category=ThreatCategory.RANSOMWARE,
        threat_event_frequency={"distribution": "PERT", "low": 0.1, "mode": 0.5, "high": 2.0},
        vulnerability={"distribution": "PERT", "low": 0.2, "mode": 0.4, "high": 0.6},
        primary_loss={
            "distribution": "PERT",
            "low": 50_000,
            "mode": 250_000,
            "high": 2_000_000,
        },
        status=EntityStatus.ACTIVE,
        created_by=created_by,
    )


def _make_run(
    *,
    org_id: uuid.UUID,
    scenario_id: uuid.UUID,
    created_by: uuid.UUID,
    status: RunStatus = RunStatus.QUEUED,
    mc_iterations: int = 200,
    error_message: str | None = None,
) -> RiskAnalysisRun:
    """Return an unsaved RiskAnalysisRun in the given org.

    Caller must ``db_session.add(...)`` + ``await db_session.commit()``.
    """
    run = RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=org_id,
        scenario_id=scenario_id,
        mc_iterations=mc_iterations,
        inputs_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        controls_snapshot=[],
        control_ids_used=[],
        status=status,
        run_type=RunType.SINGLE,
        created_by=created_by,
    )
    if error_message is not None:
        run.error_message = error_message
    return run


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_dispatch_path(
    authed_analyst: tuple[AsyncClient, Any],
    db_session: AsyncSession,
    wire_executor_to_test_db: None,
) -> None:
    """mc_iterations=10000 → QUEUED → BG task executes → COMPLETED.

    Uses polling loop (httpx.AsyncClient doesn't guarantee BG-task sync
    completion before next coroutine resumes — see spec §12.1).

    Seeds a scenario in the analyst's own org so the org-scoped run trigger
    can find it (cross-org scenario_id would return 404 before RBAC).
    """
    from idraa.models.control import Control
    from idraa.models.scenario_control import ScenarioControl

    client, org_id = authed_analyst

    # Resolve analyst's user_id from the session (via organization membership).
    from sqlalchemy import select as sa_select

    from idraa.models.user import User as UserModel

    analyst_user = (
        await db_session.execute(sa_select(UserModel).where(UserModel.organization_id == org_id))
    ).scalar_one()

    # Seed scenario in analyst's org
    scenario = _make_scenario(
        org_id=org_id,
        created_by=analyst_user.id,
        name="e2e-async-dispatch",
    )
    db_session.add(scenario)
    await db_session.commit()
    await db_session.refresh(scenario)

    from datetime import UTC, datetime

    from idraa.models.control_function_assignment import ControlFunctionAssignment

    # Seed 2 controls in analyst's org + attach to scenario
    c1 = Control(
        id=uuid.uuid4(),
        organization_id=org_id,
        name="Firewall-async",
        type=ControlType.TECHNICAL,
        annual_cost=Decimal("0"),
        nist_csf_functions=[],
        iso_27001_domains=[],
        compliance_mappings={},
        skill_requirements=[],
        technology_dependencies=[],
        applicable_industries=[],
        applicable_org_sizes=[],
        status=EntityStatus.ACTIVE,
        version="1.0",
        created_by=analyst_user.id,
    )
    c2 = Control(
        id=uuid.uuid4(),
        organization_id=org_id,
        name="EDR-async",
        type=ControlType.TECHNICAL,
        annual_cost=Decimal("0"),
        nist_csf_functions=[],
        iso_27001_domains=[],
        compliance_mappings={},
        skill_requirements=[],
        technology_dependencies=[],
        applicable_industries=[],
        applicable_org_sizes=[],
        status=EntityStatus.ACTIVE,
        version="1.0",
        created_by=analyst_user.id,
    )
    db_session.add(c1)
    db_session.add(c2)
    await db_session.flush()

    _now = datetime.now(UTC)
    for _ctrl, _cap in ((c1, 0.7), (c2, 0.65)):
        db_session.add(
            ControlFunctionAssignment(
                control_id=_ctrl.id,
                organization_id=org_id,
                sub_function=FairCamSubFunction.VMC_PREV_REDUCE_VARIANCE_PROB,
                capability_value=_cap,
                coverage=0.8,
                reliability=0.85,
                confirmed_by_user_at=_now,
            )
        )

    db_session.add(ScenarioControl(scenario_id=scenario.id, control_id=c1.id))
    db_session.add(ScenarioControl(scenario_id=scenario.id, control_id=c2.id))
    await db_session.commit()

    response = await csrf_post(
        client,
        f"/scenarios/{scenario.id}/run",
        {"mc_iterations": "10000"},
        follow_redirects=False,
    )
    assert response.status_code == 204
    assert "HX-Redirect" in response.headers
    run_id = uuid.UUID(response.headers["HX-Redirect"].rsplit("/", 1)[-1])

    # Poll for terminal status (up to 5s — BG task runs asynchronously)
    run: RiskAnalysisRun | None = None
    for _ in range(50):
        await asyncio.sleep(0.1)
        result = await db_session.execute(
            select(RiskAnalysisRun).where(RiskAnalysisRun.id == run_id)
        )
        run = result.scalar_one_or_none()
        if run and run.status in (
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        ):
            break
    else:
        pytest.fail(f"Run {run_id} did not terminate within 5s")

    assert run is not None
    assert run.status == RunStatus.COMPLETED
    assert run.simulation_results is not None


@pytest.mark.asyncio
async def test_cancel_before_executor_starts_is_safe(
    db_session: AsyncSession,
    seed_scenario_with_controls: Scenario,
    seed_user: User,
    seed_organization: Organization,
    wire_executor_to_test_db: None,
) -> None:
    """A run flipped to CANCELLED before executor lands → executor returns
    cleanly without writing FAILED, simulation_results stays None.

    The mid-RUNNING cancel race is covered at unit level by
    test_execute_run_returns_cleanly_on_cancel_pre_calibrate (mocks the
    first cancel checkpoint inside execute_run).

    This test covers the early-exit guard (``if run.status != RunStatus.QUEUED:
    return``), which means a pre-cancelled run is a safe no-op. This test does NOT
    need wire_executor_to_test_db because execute_run either:
    - finds the run in its own session (same DB if wire is active) and returns early, OR
    - finds no row (different DB, wire not active) and returns early.
    Either branch satisfies the assertion that status remains CANCELLED.
    """
    from idraa.services.run_executor import execute_run

    run = RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=seed_organization.id,
        scenario_id=seed_scenario_with_controls.id,
        mc_iterations=200,
        inputs_hash="c" * 64,
        controls_snapshot=[],
        control_ids_used=[],
        status=RunStatus.CANCELLED,  # already cancelled before executor sees it
        run_type=RunType.SINGLE,
        created_by=seed_user.id,
    )
    db_session.add(run)
    await db_session.commit()

    await execute_run(run.id)

    await db_session.refresh(run)
    # Executor must NOT have flipped status to FAILED or RUNNING
    assert run.status == RunStatus.CANCELLED
    assert run.simulation_results is None
    assert run.started_at is None  # never transitioned to RUNNING


# PR pi F12 deleted ``test_failure_during_calibration_marks_failed`` — the
# calibrate_scenario service was excised in F3 and iris_calibration_year no
# longer drives a calibration error.


@pytest.mark.asyncio
async def test_re_run_from_failed_creates_new_run_same_hash(
    authed_analyst: tuple[AsyncClient, Any],
    db_session: AsyncSession,
    wire_executor_to_test_db: None,
) -> None:
    """Trigger 2 runs with same inputs → different run rows, same inputs_hash.

    Seeds a valid scenario in the analyst's org and triggers the run form
    twice with the same mc_iterations. Each trigger creates a new
    RiskAnalysisRun row; both rows must share the same inputs_hash because
    the hash is derived from the scenario's calibration pins + controls +
    iterations — not from a random nonce.
    """
    from sqlalchemy import select as sa_select

    from idraa.models.control import Control
    from idraa.models.scenario_control import ScenarioControl
    from idraa.models.user import User as UserModel

    client, org_id = authed_analyst

    analyst_user = (
        await db_session.execute(sa_select(UserModel).where(UserModel.organization_id == org_id))
    ).scalar_one()

    # Seed scenario + control in analyst's org
    scenario = _make_scenario(
        org_id=org_id,
        created_by=analyst_user.id,
        name="e2e-rerun-hash",
    )
    db_session.add(scenario)
    await db_session.flush()

    from datetime import UTC, datetime

    from idraa.models.control_function_assignment import ControlFunctionAssignment

    ctrl = Control(
        id=uuid.uuid4(),
        organization_id=org_id,
        name="Firewall-rerun",
        type=ControlType.TECHNICAL,
        annual_cost=Decimal("0"),
        nist_csf_functions=[],
        iso_27001_domains=[],
        compliance_mappings={},
        skill_requirements=[],
        technology_dependencies=[],
        applicable_industries=[],
        applicable_org_sizes=[],
        status=EntityStatus.ACTIVE,
        version="1.0",
        created_by=analyst_user.id,
    )
    db_session.add(ctrl)
    await db_session.flush()

    db_session.add(
        ControlFunctionAssignment(
            control_id=ctrl.id,
            organization_id=org_id,
            sub_function=FairCamSubFunction.VMC_PREV_REDUCE_VARIANCE_PROB,
            capability_value=0.7,
            coverage=0.8,
            reliability=0.85,
            confirmed_by_user_at=datetime.now(UTC),
        )
    )

    db_session.add(ScenarioControl(scenario_id=scenario.id, control_id=ctrl.id))
    await db_session.commit()
    await db_session.refresh(scenario)

    response1 = await csrf_post(
        client,
        f"/scenarios/{scenario.id}/run",
        {"mc_iterations": "200"},
        follow_redirects=False,
    )
    assert response1.status_code == 204
    run_id_1 = uuid.UUID(response1.headers["HX-Redirect"].rsplit("/", 1)[-1])

    response2 = await csrf_post(
        client,
        f"/scenarios/{scenario.id}/run",
        {"mc_iterations": "200"},
        follow_redirects=False,
    )
    assert response2.status_code == 204
    run_id_2 = uuid.UUID(response2.headers["HX-Redirect"].rsplit("/", 1)[-1])

    assert run_id_1 != run_id_2  # separate rows

    rows = (
        (
            await db_session.execute(
                select(RiskAnalysisRun).where(RiskAnalysisRun.id.in_([run_id_1, run_id_2]))
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2
    hashes = {r.inputs_hash for r in rows}
    assert len(hashes) == 1  # deterministic hash for identical inputs


@pytest.mark.asyncio
async def test_reviewer_cannot_trigger_run(
    authed_reviewer: tuple[AsyncClient, Any],
    seed_scenario_with_controls: Scenario,
) -> None:
    """Mirrors PR ε P12: reviewer attempts POST /run → 403.

    require_role(ANALYST, ADMIN) fires at the dependency level before
    any scenario-org lookup, so the cross-org scenario_id from
    seed_scenario_with_controls is irrelevant — the response is 403
    regardless of whether the scenario exists in the reviewer's org.
    """
    client, _ = authed_reviewer
    response = await csrf_post(
        client,
        f"/scenarios/{seed_scenario_with_controls.id}/run",
        {"mc_iterations": "200"},
        follow_redirects=False,
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_reviewer_cannot_cancel_run(
    authed_reviewer: tuple[AsyncClient, Any],
) -> None:
    """Reviewer cannot cancel a run — require_role rejects with 403.

    Uses a random run_id: RBAC (require_role) fires before the DB lookup,
    so the run doesn't need to exist in any org to receive 403.
    """
    client, _ = authed_reviewer
    response = await csrf_post(
        client,
        f"/runs/{uuid.uuid4()}/cancel",
        {},
        follow_redirects=False,
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_reviewer_can_view_run(
    authed_reviewer: tuple[AsyncClient, Any],
    db_session: AsyncSession,
    wire_executor_to_test_db: None,
) -> None:
    """Reviewer CAN view runs (read-only RBAC).

    Seeds a COMPLETED run in the reviewer's own org so the org-scoped
    lookup in get_run_detail succeeds (run in a different org would 404).
    """
    from fastapi import BackgroundTasks
    from sqlalchemy import select as sa_select

    from idraa.models.control import Control
    from idraa.models.scenario_control import ScenarioControl
    from idraa.models.user import User as UserModel
    from idraa.services.runs import RunService

    client, org_id = authed_reviewer

    reviewer_user = (
        await db_session.execute(sa_select(UserModel).where(UserModel.organization_id == org_id))
    ).scalar_one()

    # Seed scenario + 1 control in reviewer's org
    scenario = _make_scenario(
        org_id=org_id,
        created_by=reviewer_user.id,
        name="e2e-reviewer-view",
    )
    db_session.add(scenario)
    await db_session.flush()

    from datetime import UTC, datetime

    from idraa.models.control_function_assignment import ControlFunctionAssignment

    ctrl = Control(
        id=uuid.uuid4(),
        organization_id=org_id,
        name="Firewall-reviewer",
        type=ControlType.TECHNICAL,
        annual_cost=Decimal("0"),
        nist_csf_functions=[],
        iso_27001_domains=[],
        compliance_mappings={},
        skill_requirements=[],
        technology_dependencies=[],
        applicable_industries=[],
        applicable_org_sizes=[],
        status=EntityStatus.ACTIVE,
        version="1.0",
        created_by=reviewer_user.id,
    )
    db_session.add(ctrl)
    await db_session.flush()

    db_session.add(
        ControlFunctionAssignment(
            control_id=ctrl.id,
            organization_id=org_id,
            sub_function=FairCamSubFunction.VMC_PREV_REDUCE_VARIANCE_PROB,
            capability_value=0.7,
            coverage=0.8,
            reliability=0.85,
            confirmed_by_user_at=datetime.now(UTC),
        )
    )

    db_session.add(ScenarioControl(scenario_id=scenario.id, control_id=ctrl.id))
    await db_session.commit()
    await db_session.refresh(scenario)

    # Create a completed run via RunService (sync path, mc_iterations=200)
    bg = BackgroundTasks()
    service = RunService(db_session)
    run = await service.create_and_dispatch(
        organization_id=org_id,
        scenario_ids=[scenario.id],
        mc_iterations_override=200,
        created_by=reviewer_user.id,
        background_tasks=bg,
    )
    await db_session.refresh(run)
    assert run.status == RunStatus.COMPLETED

    response = await client.get(f"/runs/{run.id}")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_run_status_fragment_escapes_error_message(
    authed_analyst: tuple[AsyncClient, Any],
    db_session: AsyncSession,
) -> None:
    """error_message rendered in the FAILED status fragment must be HTML-escaped.

    Asserts Jinja2 auto-escape works for the error_message field:
    ``{{ run.error_message }}`` in _status_poll.html must render the script
    tag as ``&lt;script&gt;`` — not as live HTML — preventing XSS via
    a crafted exception message (spec §12.1).

    Seeds the run in the analyst's own org so the status fragment endpoint
    can find it (org-scoped lookup; cross-org would 404).
    """
    from sqlalchemy import select as sa_select

    from idraa.models.control import Control
    from idraa.models.scenario_control import ScenarioControl
    from idraa.models.user import User as UserModel

    client, org_id = authed_analyst

    analyst_user = (
        await db_session.execute(sa_select(UserModel).where(UserModel.organization_id == org_id))
    ).scalar_one()

    # Seed a minimal scenario + control in analyst's org
    scenario = _make_scenario(
        org_id=org_id,
        created_by=analyst_user.id,
        name="e2e-xss-guard",
    )
    db_session.add(scenario)
    await db_session.flush()

    from datetime import UTC, datetime

    from idraa.models.control_function_assignment import ControlFunctionAssignment

    ctrl = Control(
        id=uuid.uuid4(),
        organization_id=org_id,
        name="Firewall-xss",
        type=ControlType.TECHNICAL,
        annual_cost=Decimal("0"),
        nist_csf_functions=[],
        iso_27001_domains=[],
        compliance_mappings={},
        skill_requirements=[],
        technology_dependencies=[],
        applicable_industries=[],
        applicable_org_sizes=[],
        status=EntityStatus.ACTIVE,
        version="1.0",
        created_by=analyst_user.id,
    )
    db_session.add(ctrl)
    await db_session.flush()

    db_session.add(
        ControlFunctionAssignment(
            control_id=ctrl.id,
            organization_id=org_id,
            sub_function=FairCamSubFunction.VMC_PREV_REDUCE_VARIANCE_PROB,
            capability_value=0.7,
            coverage=0.8,
            reliability=0.85,
            confirmed_by_user_at=datetime.now(UTC),
        )
    )

    db_session.add(ScenarioControl(scenario_id=scenario.id, control_id=ctrl.id))
    await db_session.commit()
    await db_session.refresh(scenario)

    # Insert a FAILED run with a crafted XSS payload in error_message
    run = _make_run(
        org_id=org_id,
        scenario_id=scenario.id,
        created_by=analyst_user.id,
        status=RunStatus.FAILED,
        error_message="<script>alert(1)</script>",
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)

    response = await client.get(f"/runs/{run.id}/status")
    assert response.status_code == 200
    body = response.text
    # Raw script tag must not appear as live HTML
    assert "<script>alert(1)</script>" not in body
    # HTML-escaped form must be present
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
