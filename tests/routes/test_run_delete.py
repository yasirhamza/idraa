"""Manual per-run delete + purge-samples route tests (#297).

Covers the org-scoped, CSRF-protected, confirm-gated lifecycle endpoints:

- ``POST /runs/{id}/delete``        — hard-delete the run row (ON DELETE
  CASCADE removes its run_samples row).
- ``POST /runs/{id}/purge-samples`` — delete just the run_samples row,
  keeping the run + summary.

Six behaviours:
1. delete removes run + samples
2. purge keeps run, drops samples
3. missing ``confirm`` -> 400
4. reviewer -> 403 (require_role(ANALYST, ADMIN))
5. cross-org -> 404 (RunNotFoundError)
6. delete RUNNING without force -> 409 (RunBusyError)

Mirrors the fixture topology of
tests/integration/test_run_detail_v2_banner_and_log.py: seed a scenario in
the authed analyst's org, then seed a run with ``organization_id=<that org>``.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.audit_log import AuditLog
from idraa.models.enums import EntityStatus, ScenarioType, ThreatCategory
from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus
from idraa.models.run_samples import RunSamples
from idraa.models.scenario import Scenario
from tests.conftest import csrf_post


async def _seed_scenario(
    db_session: AsyncSession,
    organization_id: uuid.UUID,
    created_by: uuid.UUID,
    name: str = "run-delete test scenario",
) -> Scenario:
    scenario = Scenario(
        organization_id=organization_id,
        name=name,
        scenario_type=ScenarioType.CUSTOM,
        threat_category=ThreatCategory.RANSOMWARE,
        threat_event_frequency={"distribution": "PERT", "low": 0.1, "mode": 0.5, "high": 2.0},
        vulnerability={"distribution": "PERT", "low": 0.2, "mode": 0.4, "high": 0.6},
        primary_loss={"distribution": "PERT", "low": 50_000, "mode": 250_000, "high": 2_000_000},
        status=EntityStatus.ACTIVE,
        created_by=created_by,
    )
    db_session.add(scenario)
    await db_session.commit()
    await db_session.refresh(scenario)
    return scenario


async def _seed_samples(
    db_session: AsyncSession,
    run: RiskAnalysisRun,
) -> RunSamples:
    row = RunSamples(
        run_id=run.id,
        organization_id=run.organization_id,
        arrays={"base": [1.0, 2.0, 3.0], "residual": [0.5, 1.0, 1.5]},
    )
    db_session.add(row)
    await db_session.commit()
    return row


@pytest.mark.asyncio
async def test_delete_removes_run_and_samples(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_user: object,
    seed_run_factory: object,
) -> None:
    client, org_id = authed_analyst
    scenario = await _seed_scenario(db_session, org_id, seed_user.id)  # type: ignore[attr-defined]
    run = await seed_run_factory(  # type: ignore[operator]
        scenario=scenario,
        status=RunStatus.COMPLETED,
        organization_id=org_id,
    )
    await _seed_samples(db_session, run)
    run_id = run.id

    resp = await csrf_post(
        client, f"/runs/{run_id}/delete", {"confirm": "1"}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/analyses?deleted=1"

    db_session.expire_all()
    assert await db_session.get(RiskAnalysisRun, run_id) is None
    assert await db_session.get(RunSamples, run_id) is None


@pytest.mark.asyncio
async def test_purge_keeps_run_drops_samples(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_user: object,
    seed_run_factory: object,
) -> None:
    client, org_id = authed_analyst
    scenario = await _seed_scenario(db_session, org_id, seed_user.id)  # type: ignore[attr-defined]
    run = await seed_run_factory(  # type: ignore[operator]
        scenario=scenario,
        status=RunStatus.COMPLETED,
        organization_id=org_id,
    )
    await _seed_samples(db_session, run)
    run_id = run.id

    resp = await csrf_post(
        client, f"/runs/{run_id}/purge-samples", {"confirm": "1"}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/runs/{run_id}?purged=1"

    db_session.expire_all()
    assert await db_session.get(RiskAnalysisRun, run_id) is not None  # run survives
    assert await db_session.get(RunSamples, run_id) is None  # samples gone


@pytest.mark.asyncio
async def test_delete_missing_confirm_returns_400(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_user: object,
    seed_run_factory: object,
) -> None:
    client, org_id = authed_analyst
    scenario = await _seed_scenario(db_session, org_id, seed_user.id)  # type: ignore[attr-defined]
    run = await seed_run_factory(  # type: ignore[operator]
        scenario=scenario,
        status=RunStatus.COMPLETED,
        organization_id=org_id,
    )
    run_id = run.id

    resp = await csrf_post(client, f"/runs/{run_id}/delete", {}, follow_redirects=False)
    assert resp.status_code == 400

    db_session.expire_all()
    assert await db_session.get(RiskAnalysisRun, run_id) is not None  # not deleted


@pytest.mark.asyncio
async def test_reviewer_cannot_delete_403(
    authed_reviewer: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_user: object,
    seed_run_factory: object,
) -> None:
    client, org_id = authed_reviewer
    scenario = await _seed_scenario(db_session, org_id, seed_user.id)  # type: ignore[attr-defined]
    run = await seed_run_factory(  # type: ignore[operator]
        scenario=scenario,
        status=RunStatus.COMPLETED,
        organization_id=org_id,
    )
    resp = await csrf_post(
        client, f"/runs/{run.id}/delete", {"confirm": "1"}, follow_redirects=False
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_cross_org_delete_returns_404(
    authed_other_org_analyst: tuple[AsyncClient, uuid.UUID],
    seed_organization: object,
    seed_run_factory: object,
) -> None:
    # Run seeded in seed_organization; the authed analyst belongs to a DIFFERENT org.
    client, _other_org_id = authed_other_org_analyst
    run = await seed_run_factory(  # type: ignore[operator]
        status=RunStatus.COMPLETED,
        organization_id=seed_organization.id,  # type: ignore[attr-defined]
    )
    resp = await csrf_post(
        client, f"/runs/{run.id}/delete", {"confirm": "1"}, follow_redirects=False
    )
    # Cross-org -> 404. The service short-circuits at get_for_org_or_raise
    # BEFORE any delete, so the run row is necessarily untouched.
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_running_without_force_returns_409(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_user: object,
    seed_run_factory: object,
) -> None:
    client, org_id = authed_analyst
    scenario = await _seed_scenario(db_session, org_id, seed_user.id)  # type: ignore[attr-defined]
    run = await seed_run_factory(  # type: ignore[operator]
        scenario=scenario,
        status=RunStatus.RUNNING,
        organization_id=org_id,
    )
    run_id = run.id

    resp = await csrf_post(
        client, f"/runs/{run_id}/delete", {"confirm": "1"}, follow_redirects=False
    )
    assert resp.status_code == 409

    db_session.expire_all()
    assert await db_session.get(RiskAnalysisRun, run_id) is not None  # not deleted

    # force=1 overrides the busy-guard.
    resp2 = await csrf_post(
        client,
        f"/runs/{run_id}/delete",
        {"confirm": "1", "force": "1"},
        follow_redirects=False,
    )
    assert resp2.status_code == 303
    db_session.expire_all()
    assert await db_session.get(RiskAnalysisRun, run_id) is None


async def _count_audit(db_session: AsyncSession, action: str) -> int:
    result = await db_session.execute(
        select(func.count()).select_from(AuditLog).where(AuditLog.action == action)
    )
    return int(result.scalar_one())


@pytest.mark.asyncio
async def test_delete_writes_audit_log(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_user: object,
    seed_run_factory: object,
) -> None:
    client, org_id = authed_analyst
    scenario = await _seed_scenario(db_session, org_id, seed_user.id)  # type: ignore[attr-defined]
    run = await seed_run_factory(  # type: ignore[operator]
        scenario=scenario,
        status=RunStatus.COMPLETED,
        organization_id=org_id,
    )
    await _seed_samples(db_session, run)
    run_id = run.id

    db_session.expire_all()
    before = await _count_audit(db_session, "risk_analysis_run.delete")

    resp = await csrf_post(
        client, f"/runs/{run_id}/delete", {"confirm": "1"}, follow_redirects=False
    )
    assert resp.status_code == 303

    db_session.expire_all()
    assert await _count_audit(db_session, "risk_analysis_run.delete") == before + 1


@pytest.mark.asyncio
async def test_purge_writes_audit_log_and_is_idempotent(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_user: object,
    seed_run_factory: object,
) -> None:
    client, org_id = authed_analyst
    scenario = await _seed_scenario(db_session, org_id, seed_user.id)  # type: ignore[attr-defined]
    run = await seed_run_factory(  # type: ignore[operator]
        scenario=scenario,
        status=RunStatus.COMPLETED,
        organization_id=org_id,
    )
    await _seed_samples(db_session, run)
    run_id = run.id

    db_session.expire_all()
    before = await _count_audit(db_session, "risk_analysis_run.purge_samples")

    resp = await csrf_post(
        client, f"/runs/{run_id}/purge-samples", {"confirm": "1"}, follow_redirects=False
    )
    assert resp.status_code == 303

    db_session.expire_all()
    after_first = await _count_audit(db_session, "risk_analysis_run.purge_samples")
    assert after_first == before + 1

    # Second purge of an already-purged run is an idempotent no-op: no new audit row.
    resp2 = await csrf_post(
        client, f"/runs/{run_id}/purge-samples", {"confirm": "1"}, follow_redirects=False
    )
    assert resp2.status_code == 303

    db_session.expire_all()
    assert await _count_audit(db_session, "risk_analysis_run.purge_samples") == after_first


@pytest.mark.asyncio
async def test_viewer_cannot_delete_403(
    authed_viewer: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_user: object,
    seed_run_factory: object,
) -> None:
    client, org_id = authed_viewer
    scenario = await _seed_scenario(db_session, org_id, seed_user.id)  # type: ignore[attr-defined]
    run = await seed_run_factory(  # type: ignore[operator]
        scenario=scenario,
        status=RunStatus.COMPLETED,
        organization_id=org_id,
    )
    resp = await csrf_post(
        client, f"/runs/{run.id}/delete", {"confirm": "1"}, follow_redirects=False
    )
    assert resp.status_code == 403


async def test_analyses_page_renders_deleted_flash(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
) -> None:
    client, _org_id = authed_analyst
    """#297 follow-up: the delete redirect lands on /analyses (the run-history
    page that hosts the delete buttons), which must render the same
    "Run deleted." flash the dashboard did when it was the landing page."""
    resp = await client.get("/analyses?deleted=1")
    assert resp.status_code == 200
    assert "Run deleted." in resp.text
