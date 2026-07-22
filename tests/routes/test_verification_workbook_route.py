"""Route-level tests for GET /reports/run/{run_id}/verification.xlsx (T12).

Mirrors the PDF route's auth/org-scoping/audit pattern (download_run_pdf,
tests/integration/test_reports_routes.py). Reuses the project-standard
authed_admin / seed_organization_factory / db_session fixtures
(tests/conftest.py) and the COMPLETED-run seed helpers
(tests/integration/_reports_fixtures.py).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.audit_log import AuditLog
from idraa.models.organization import Organization
from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType
from tests.integration._reports_fixtures import _make_completed_single_run

_XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


async def _org_for(db_session: AsyncSession, org_id: uuid.UUID) -> Organization:
    org = await db_session.get(Organization, org_id)
    assert org is not None
    return org


# ---------- 200 happy path ----------


async def test_verification_xlsx_streams_for_completed_single(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """COMPLETED SINGLE run → 200 with xlsx Content-Type, attachment
    Content-Disposition (.xlsx filename), Cache-Control private, no-store."""
    client, org_id = authed_admin
    organization = await _org_for(db_session, org_id)
    run = await _make_completed_single_run(db_session, organization, name="verify-q3")
    await db_session.commit()

    r = await client.get(f"/reports/run/{run.id}/verification.xlsx")
    assert r.status_code == 200
    assert r.headers["content-type"] == _XLSX_MEDIA
    assert r.headers["cache-control"] == "private, no-store"
    cd = r.headers["content-disposition"]
    assert cd.startswith("attachment;")
    assert ".xlsx" in cd
    assert "idraa-verification-" in cd
    # Filename carries the run-name slug + completed_at date (2026-05-07).
    assert "verify-q3" in cd or "verify_q3" in cd
    assert "2026-05-07" in cd
    # Real .xlsx (zip) magic bytes.
    assert r.content[:2] == b"PK"


# ---------- 404 (three distinct cases) ----------


async def test_verification_xlsx_not_found_returns_404(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    """Non-existent run_id → 404."""
    client, _ = authed_admin
    r = await client.get(f"/reports/run/{uuid.uuid4()}/verification.xlsx")
    assert r.status_code == 404


async def test_verification_xlsx_cross_org_idor_returns_404(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_organization_factory: Any,
) -> None:
    """Run owned by a DIFFERENT org → 404 (IDOR prevention)."""
    other_org = await seed_organization_factory(name="OtherInc-vwb")
    run = await _make_completed_single_run(db_session, other_org, name="not-yours")
    await db_session.commit()
    client, _ = authed_admin
    r = await client.get(f"/reports/run/{run.id}/verification.xlsx")
    assert r.status_code == 404


@pytest.mark.parametrize(
    "status",
    [
        RunStatus.QUEUED,
        RunStatus.RUNNING,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    ],
)
async def test_verification_xlsx_non_completed_returns_404(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    status: RunStatus,
) -> None:
    """Non-COMPLETED run → 404 (uniform with not-found / IDOR)."""
    client, org_id = authed_admin
    run = RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=org_id,
        run_type=RunType.AGGREGATE,
        status=status,
        scenario_id=None,
        aggregate_scenario_ids=[str(uuid.uuid4()), str(uuid.uuid4())],
        mc_iterations=1000,
        inputs_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        controls_snapshot=[],
        control_ids_used=[],
    )
    db_session.add(run)
    await db_session.commit()
    r = await client.get(f"/reports/run/{run.id}/verification.xlsx")
    assert r.status_code == 404


# ---------- 500 (COMPLETED but no results) ----------


async def test_verification_xlsx_simulation_results_none_returns_500(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """COMPLETED but simulation_results=None → 500 (data-integrity bug)."""
    client, org_id = authed_admin
    run = RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=org_id,
        run_type=RunType.AGGREGATE,
        status=RunStatus.COMPLETED,
        scenario_id=None,
        aggregate_scenario_ids=[str(uuid.uuid4()), str(uuid.uuid4())],
        mc_iterations=1000,
        inputs_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        controls_snapshot=[],
        control_ids_used=[],
        completed_at=dt.datetime.now(dt.UTC),
        simulation_results=None,
    )
    db_session.add(run)
    await db_session.commit()
    r = await client.get(f"/reports/run/{run.id}/verification.xlsx")
    assert r.status_code == 500


# ---------- audit ----------


async def test_verification_xlsx_writes_one_audit_row(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Successful download writes exactly one report.exported AuditLog row
    with the xlsx format discriminator + byte count."""
    client, org_id = authed_admin
    organization = await _org_for(db_session, org_id)
    run = await _make_completed_single_run(db_session, organization, name="audit-vwb")
    await db_session.commit()
    run_id = run.id

    r = await client.get(f"/reports/run/{run_id}/verification.xlsx")
    assert r.status_code == 200

    db_session.expire_all()
    rows = (
        (
            await db_session.execute(
                select(AuditLog)
                .where(AuditLog.action == "report.exported")
                .where(AuditLog.entity_id == run_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1, f"Expected exactly 1 report.exported row, got {len(rows)}"
    row = rows[0]
    assert row.organization_id == org_id
    assert row.entity_type == "risk_analysis_run"
    assert row.user_id is not None
    assert row.changes["format"][1] == "xlsx"
    assert isinstance(row.changes["bytes_written"][1], int)
    assert row.changes["bytes_written"][1] > 0


# ---------- regression: connection released before the build (prod outage 2026-06-15) ----------


async def test_releases_db_connection_before_build(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The request's DB connection MUST be released before the CPU-bound build +
    file stream — holding it across them exhausted the pool under repeated taps
    (QueuePool 30s timeout -> app-wide 500s). Guard the call order so a refactor
    can't re-introduce the connection hold."""
    import idraa.routes.reports as reports_mod

    order: list[str] = []
    real_close = AsyncSession.close

    async def spy_close(self: AsyncSession) -> None:  # type: ignore[no-untyped-def]
        order.append("close")
        await real_close(self)

    def spy_build(run: Any, org: Any, *, base_url: str = "") -> bytes:
        order.append("build")
        return b"PK\x03\x04stub"

    monkeypatch.setattr(AsyncSession, "close", spy_close)
    monkeypatch.setattr(reports_mod, "build_verification_workbook", spy_build)

    client, org_id = authed_admin
    organization = await _org_for(db_session, org_id)
    run = await _make_completed_single_run(db_session, organization, name="conn-rel")
    await db_session.commit()

    r = await client.get(f"/reports/run/{run.id}/verification.xlsx")
    assert r.status_code == 200
    assert "build" in order and "close" in order
    # The request session is closed (connection returned to the pool) BEFORE the
    # build runs.
    assert order.index("close") < order.index("build"), (
        f"connection must be released before the build; got order={order}"
    )


# ---------- workbook-labels PR: help-link base derives from the serving request ----------


async def test_route_threads_request_base_url_to_builder(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The route must pass the serving request's base URL to the builder so the
    in-sheet help link matches the deployment host (idraa.app / per-tester
    instances / localhost) instead of a hardcoded domain."""
    import idraa.routes.reports as reports_mod

    seen: list[str] = []

    def spy_build(run: Any, org: Any, *, base_url: str = "") -> bytes:
        seen.append(base_url)
        return b"PK\x03\x04stub"

    monkeypatch.setattr(reports_mod, "build_verification_workbook", spy_build)

    client, org_id = authed_admin
    organization = await _org_for(db_session, org_id)
    run = await _make_completed_single_run(db_session, organization, name="base-url")
    await db_session.commit()

    r = await client.get(f"/reports/run/{run.id}/verification.xlsx")
    assert r.status_code == 200
    assert len(seen) == 1
    # Non-empty, scheme-qualified, no trailing slash — exactly what the sheet
    # concatenates with /help/control-value-robustness.
    assert seen[0].startswith("http"), seen
    assert not seen[0].endswith("/"), seen
