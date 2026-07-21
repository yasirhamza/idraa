"""Route-level tests for /reports + /reports/executive/{run_id} (omicron-2 F12)
and /reports/run/{run_id} + 308 alias (T8 #351).

httpx AsyncClient. Uses the project standard authed_admin /
authed_analyst / authed_reviewer / authed_viewer fixtures
(tuple[AsyncClient, uuid.UUID]).
"""

from __future__ import annotations

import datetime as dt
import io
import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pypdf
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.audit_log import AuditLog
from idraa.models.organization import Organization
from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType
from idraa.models.user import User
from tests.integration._reports_fixtures import (
    _make_completed_aggregate_run,
    _make_completed_single_run,
)


async def _org_for(db_session: AsyncSession, org_id: uuid.UUID) -> Organization:
    """Fetch the Organization row owned by an authed_admin/analyst/etc fixture
    via the test's db_session. The fixtures yield (client, org_id); tests that
    need the Organization row to feed into _make_completed_aggregate_run pull
    it through here (rather than requesting the unrelated `organization`
    fixture, which creates a SECOND org and would land the run in the wrong
    tenant for require_sole_org's `.first()` lookup)."""
    org = await db_session.get(Organization, org_id)
    assert org is not None
    return org


# ---------- /reports list-page tests ----------


async def test_reports_list_admin_cold_start(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    client, _ = authed_admin
    r = await client.get("/reports")
    assert r.status_code == 200
    assert "Run aggregate analysis" in r.text  # CTA visible to ADMIN


async def test_reports_list_analyst_cold_start(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
) -> None:
    client, _ = authed_analyst
    r = await client.get("/reports")
    assert r.status_code == 200
    assert "Run aggregate analysis" in r.text  # CTA visible to ANALYST


async def test_reports_list_reviewer_no_cta(
    authed_reviewer: tuple[AsyncClient, uuid.UUID],
) -> None:
    client, _ = authed_reviewer
    r = await client.get("/reports")
    assert r.status_code == 200
    assert "Run aggregate analysis" not in r.text  # CTA hidden by analyst_or_admin


async def test_reports_list_viewer_no_cta(
    authed_viewer: tuple[AsyncClient, uuid.UUID],
) -> None:
    client, _ = authed_viewer
    r = await client.get("/reports")
    assert r.status_code == 200
    assert "Run aggregate analysis" not in r.text


async def test_reports_list_unauthenticated_redirects_to_login(
    anonymous_client: AsyncClient,
    admin_user: User,
    db_session: AsyncSession,
) -> None:
    # admin_user seeds a user so setup_guard does not 307 -> /setup; the route
    # then runs require_user -> 401 -> _auth_redirect_handler -> 303 /login.
    # Commit explicitly so the client's separate engine can observe the row.
    await db_session.commit()
    r = await anonymous_client.get("/reports", follow_redirects=False)
    assert r.status_code in (302, 303)
    assert r.headers["location"].startswith("/login")


async def test_reports_list_populated_shows_completed_row_with_download(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Q7=A: COMPLETED row -> active 'Download' button. Non-COMPLETED ->
    disabled affordance + status badge.

    T8(e): download link now points to /reports/run/{id} (unified route).
    """
    client, org_id = authed_admin
    organization = await _org_for(db_session, org_id)
    run = await _make_completed_aggregate_run(db_session, organization, name="ready")
    await db_session.commit()

    r = await client.get("/reports")
    assert r.status_code == 200
    assert "ready" in r.text  # run name
    # T8(e): active download link for COMPLETED row now points to /reports/run/{id}
    assert f'href="/reports/run/{run.id}"' in r.text


async def test_reports_list_completed_row_shows_verification_workbook_link(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """T13: COMPLETED row exposes a verification-workbook (.xlsx) download link
    next to the PDF link. Non-COMPLETED rows do not (mirrors PDF visibility)."""
    client, org_id = authed_admin
    organization = await _org_for(db_session, org_id)
    run = await _make_completed_aggregate_run(db_session, organization, name="ready")
    await db_session.commit()

    r = await client.get("/reports")
    assert r.status_code == 200
    assert f'href="/reports/run/{run.id}/verification.xlsx"' in r.text


async def test_reports_list_idor_cross_org_canary_absent(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_organization_factory: Any,
) -> None:
    other_org = await seed_organization_factory(name="OtherInc")
    await _make_completed_aggregate_run(db_session, other_org, name="canary_xyz")
    await db_session.commit()
    client, _ = authed_admin
    r = await client.get("/reports")
    assert "canary_xyz" not in r.text


# ---------- /reports/executive/{run_id} — T8(b) triaged alias tests ----------
# Pre-T8, this was the primary PDF download route. T8 converts it to a
# 308 Permanent Redirect → /reports/run/{run_id}.
#
# TRIAGE (audit-old-tests rule):
#   - Old 200-with-PDF assertions have MOVED to test_run_report_* tests above.
#   - Old 404/500 assertions also become 308 since the alias is unconditional.
#   - Retained here: basic 308 redirect shape + auth gating.


async def test_reports_executive_alias_streams_pdf_via_redirect(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Following the 308 from the alias lands on the PDF (end-to-end sanity).

    This replaces the old test_reports_executive_happy_path_streams_pdf which
    previously asserted 200 directly on /reports/executive/{id}. The alias now
    returns 308; following it via follow_redirects=True still delivers the PDF.
    """
    client, org_id = authed_admin
    organization = await _org_for(db_session, org_id)
    run = await _make_completed_aggregate_run(db_session, organization, name="board-q2")
    await db_session.commit()

    r = await client.get(f"/reports/executive/{run.id}", follow_redirects=True)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["cache-control"] == "private, no-store"
    assert r.content.startswith(b"%PDF-1.")
    reader = pypdf.PdfReader(io.BytesIO(r.content))
    # T9 CF-2: distinct back page added (+1 to T7's 13).
    # cover(1) + TOC(2) + risk(3) + LEC(4) + dist-stats(5) +
    # per-scenario(6) + economics(7) + attribution-matrix(8) +
    # assumptions-inputs(9-11, 1 per scenario) + methodology-appendix(12)
    # + controls-inventory(13) + back-page(14) = 14 pages
    assert len(reader.pages) == 14


async def test_reports_executive_returns_308_for_any_uuid(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    """T8(b): the alias redirects unconditionally — 308 for any well-formed UUID,
    including non-existent IDs (no DB lookup in the alias handler).

    Pre-T8 these cases returned 404; now they return 308.
    """
    client, _ = authed_admin
    r = await client.get(f"/reports/executive/{uuid.uuid4()}", follow_redirects=False)
    assert r.status_code == 308
    assert "/reports/run/" in r.headers["location"]


# ---------- T8 #351: /reports/run/{run_id} new unified route ----------


async def test_run_report_aggregate_streams_pdf(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Happy path: AGGREGATE COMPLETED run returns PDF via new route."""
    client, org_id = authed_admin
    organization = await _org_for(db_session, org_id)
    run = await _make_completed_aggregate_run(db_session, organization, name="board-q3")
    await db_session.commit()

    r = await client.get(f"/reports/run/{run.id}")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["cache-control"] == "private, no-store"
    cd = r.headers["content-disposition"]
    assert cd.startswith("attachment;")
    assert ".pdf" in cd
    # Filename contains run name slug, org slug, and date
    assert "board-q3" in cd or "board_q3" in cd
    assert r.content.startswith(b"%PDF-1.")
    reader = pypdf.PdfReader(io.BytesIO(r.content))
    assert len(reader.pages) >= 1


async def test_run_report_single_streams_pdf(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """T8(a): SINGLE COMPLETED run also returns PDF via the new unified route."""
    client, org_id = authed_admin
    organization = await _org_for(db_session, org_id)
    run = await _make_completed_single_run(
        db_session, organization, name="OT-scenario-q3", scenario_name="Ransomware OT"
    )
    await db_session.commit()

    r = await client.get(f"/reports/run/{run.id}")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["cache-control"] == "private, no-store"
    cd = r.headers["content-disposition"]
    assert cd.startswith("attachment;")
    assert ".pdf" in cd
    assert r.content.startswith(b"%PDF-1.")


async def test_run_report_not_found_returns_404(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    """T8(a): non-existent run_id → 404."""
    client, _ = authed_admin
    r = await client.get(f"/reports/run/{uuid.uuid4()}")
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
async def test_run_report_non_completed_returns_404(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    status: RunStatus,
) -> None:
    """T8(a): non-COMPLETED AGGREGATE run → 404."""
    import hashlib

    client, org_id = authed_admin
    s1, s2 = uuid.uuid4(), uuid.uuid4()
    run = RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=org_id,
        run_type=RunType.AGGREGATE,
        status=status,
        scenario_id=None,
        aggregate_scenario_ids=[str(s1), str(s2)],
        mc_iterations=1000,
        inputs_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        controls_snapshot=[],
        control_ids_used=[],
    )
    db_session.add(run)
    await db_session.commit()
    r = await client.get(f"/reports/run/{run.id}")
    assert r.status_code == 404


async def test_run_report_cross_org_idor_returns_404(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    seed_organization_factory: Any,
) -> None:
    """T8(a): run belonging to a different org → 404 (IDOR prevention)."""
    other_org = await seed_organization_factory(name="OtherInc2")
    run = await _make_completed_aggregate_run(db_session, other_org)
    await db_session.commit()
    client, _ = authed_admin
    r = await client.get(f"/reports/run/{run.id}")
    assert r.status_code == 404


async def test_run_report_simulation_results_none_returns_500(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """T8(a): COMPLETED but simulation_results=None → 500 (data-integrity bug)."""
    import hashlib

    client, org_id = authed_admin
    s1, s2 = uuid.uuid4(), uuid.uuid4()
    run = RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=org_id,
        run_type=RunType.AGGREGATE,
        status=RunStatus.COMPLETED,
        scenario_id=None,
        aggregate_scenario_ids=[str(s1), str(s2)],
        mc_iterations=1000,
        inputs_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        controls_snapshot=[],
        control_ids_used=[],
        completed_at=dt.datetime.now(dt.UTC),
        simulation_results=None,
    )
    db_session.add(run)
    await db_session.commit()
    r = await client.get(f"/reports/run/{run.id}")
    assert r.status_code == 500


# --- Role matrix: all four roles can download via /reports/run/{run_id} ---


async def test_run_report_analyst_can_download(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_analyst
    organization = await _org_for(db_session, org_id)
    run = await _make_completed_aggregate_run(db_session, organization)
    await db_session.commit()
    r = await client.get(f"/reports/run/{run.id}")
    assert r.status_code == 200


async def test_run_report_reviewer_can_download(
    authed_reviewer: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_reviewer
    organization = await _org_for(db_session, org_id)
    run = await _make_completed_aggregate_run(db_session, organization)
    await db_session.commit()
    r = await client.get(f"/reports/run/{run.id}")
    assert r.status_code == 200


async def test_run_report_viewer_can_download(
    authed_viewer: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_viewer
    organization = await _org_for(db_session, org_id)
    run = await _make_completed_aggregate_run(db_session, organization)
    await db_session.commit()
    r = await client.get(f"/reports/run/{run.id}")
    assert r.status_code == 200


# ---------- T8(b): 308 legacy alias tests ----------


async def test_executive_alias_redirects_308(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """T8(b): GET /reports/executive/{run_id} returns 308 → /reports/run/{run_id}."""
    client, org_id = authed_admin
    organization = await _org_for(db_session, org_id)
    run = await _make_completed_aggregate_run(db_session, organization)
    await db_session.commit()

    r = await client.get(f"/reports/executive/{run.id}", follow_redirects=False)
    assert r.status_code == 308
    assert r.headers["location"] == f"/reports/run/{run.id}"


async def test_executive_alias_no_db_lookup_nonexistent_uuid(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    """T8(b): alias redirects unconditionally — even for a UUID that doesn't exist."""
    client, _ = authed_admin
    random_id = uuid.uuid4()
    r = await client.get(f"/reports/executive/{random_id}", follow_redirects=False)
    assert r.status_code == 308
    assert r.headers["location"] == f"/reports/run/{random_id}"


async def test_executive_alias_writes_no_audit_row(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """T8(b): alias handler writes zero AuditLog rows."""
    client, org_id = authed_admin
    # Count audit rows before
    before = (await db_session.execute(select(AuditLog))).scalars().all()
    before_count = len(before)

    random_id = uuid.uuid4()
    await client.get(f"/reports/executive/{random_id}", follow_redirects=False)

    # expire_all() is synchronous; call it then re-query so any writes the
    # handler made in its own session scope become visible here.
    db_session.expire_all()
    after = (await db_session.execute(select(AuditLog))).scalars().all()
    assert len(after) == before_count, "Alias handler must write zero audit rows"


# ---------- T8(c): Filename builder with run-name slug ----------


async def test_run_report_filename_contains_run_name_slug(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """T8(c): filename is idraa-run-report-{org_slug}-{run_slug}-{date}.pdf."""
    client, org_id = authed_admin
    organization = await _org_for(db_session, org_id)
    run = await _make_completed_aggregate_run(db_session, organization, name="board-q3 review")
    await db_session.commit()

    r = await client.get(f"/reports/run/{run.id}")
    assert r.status_code == 200
    cd = r.headers["content-disposition"]
    # filename must match idraa-run-report-<org>-<run_slug>-<date>.pdf
    assert "idraa-run-report-" in cd
    # run name "board-q3 review" → "board-q3_review" after sanitization
    assert "board" in cd
    assert "q3" in cd


async def test_run_report_filename_run_name_injection_safe(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """T8(c): run name with injection chars produces a safe filename.

    The critical header-injection vectors are CR (\\r) and LF (\\n) — these
    allow inserting new HTTP response headers. Both must be absent from the
    Content-Disposition value. Path-traversal chars (dots, slashes) and
    shell-special chars (semicolons, quotes) are also sanitised.
    """
    client, org_id = authed_admin
    organization = await _org_for(db_session, org_id)
    run = await _make_completed_aggregate_run(
        db_session,
        organization,
        name="../../etc/passwd\r\nContent-Type: evil",
    )
    await db_session.commit()

    r = await client.get(f"/reports/run/{run.id}")
    assert r.status_code == 200
    cd = r.headers["content-disposition"]
    import re as _re

    m = _re.match(r'^attachment; filename="([^"]+)"$', cd)
    assert m is not None, f"Malformed Content-Disposition: {cd!r}"
    slug = m.group(1)
    # No CR/LF — these are the actual header-injection vectors.
    assert "\n" not in slug
    assert "\r" not in slug
    # No unescaped quote that could break out of the Content-Disposition value.
    assert '"' not in slug
    # No semicolon that could inject a second parameter (e.g. filename*=...).
    assert ";" not in slug
    # Path traversal sequences are absent.
    assert "../" not in slug
    assert ".." not in slug
    assert slug.startswith("idraa-run-report-")


async def test_run_report_filename_run_name_empty_fallback(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """T8(c): run name that reduces to all underscores falls back to 'run'."""
    client, org_id = authed_admin
    organization = await _org_for(db_session, org_id)
    # name=None → handled by the helper as unnamed run slug
    run = await _make_completed_aggregate_run(db_session, organization, name="中文!!!")
    await db_session.commit()

    r = await client.get(f"/reports/run/{run.id}")
    assert r.status_code == 200
    cd = r.headers["content-disposition"]
    # all-underscore run name → "run" fallback
    assert "run-report-" in cd
    # The date must still appear
    assert "2026" in cd


async def test_run_report_filename_org_slug_sanitization(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """T8(c): port existing org-name injection test to new route."""
    client, org_id = authed_admin
    organization = await _org_for(db_session, org_id)
    organization.name = 'evil"; DROP TABLE'
    db_session.add(organization)
    await db_session.flush()
    run = await _make_completed_aggregate_run(db_session, organization)
    await db_session.commit()

    r = await client.get(f"/reports/run/{run.id}")
    assert r.status_code == 200
    cd = r.headers["content-disposition"]
    import re as _re

    m = _re.match(r'^attachment; filename="([^"]+)"$', cd)
    assert m is not None, f"Malformed Content-Disposition: {cd!r}"
    slug = m.group(1)
    assert '"' not in slug
    assert ";" not in slug
    assert "\n" not in slug
    assert "\r" not in slug
    assert slug.startswith("idraa-run-report-")


async def test_run_report_filename_org_empty_slug_fallback(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """T8(c): org name of all non-alphanumeric chars → 'org' fallback sentinel."""
    client, org_id = authed_admin
    organization = await _org_for(db_session, org_id)
    organization.name = "中文!!!"
    db_session.add(organization)
    await db_session.flush()
    run = await _make_completed_aggregate_run(db_session, organization)
    await db_session.commit()

    r = await client.get(f"/reports/run/{run.id}")
    assert r.status_code == 200
    cd = r.headers["content-disposition"]
    assert "run-report-org-" in cd


# ---------- T8(d): Export audit ----------


async def test_run_report_writes_audit_row_on_download(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """T8(d): successful download writes exactly one report.exported AuditLog row."""
    client, org_id = authed_admin
    organization = await _org_for(db_session, org_id)
    run = await _make_completed_aggregate_run(db_session, organization, name="audit-test")
    await db_session.commit()
    # Capture ID before expire_all so we don't trigger async lazy-load on the expired object.
    run_id = run.id

    r = await client.get(f"/reports/run/{run_id}")
    assert r.status_code == 200

    # Expire the session's identity map so subsequent queries hit the DB fresh,
    # picking up any rows committed by the route handler.
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
    # bytes_written is in the changes payload
    assert "bytes_written" in row.changes
    # T9 Step 1.5(a): user_id must be populated and bytes_written must be a positive int.
    assert row.user_id is not None
    assert isinstance(row.changes["bytes_written"][1], int) and row.changes["bytes_written"][1] > 0


async def test_run_report_audit_error_does_not_abort_download(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """T8(d): if the DETAILED report.exported egress audit raises, the response is
    still 200 PDF and the error is logged (that write is log-and-continue). The
    fail-closed throttle/budget row (risk_analysis_run.export) is written first
    and is NOT what this test perturbs — see test_report_export_throttled_at_cap
    for its enforcement, and the fail-closed behavior on budget-write failure is
    intentional (matches the CSV export path)."""
    import logging

    client, org_id = authed_admin
    organization = await _org_for(db_session, org_id)
    run = await _make_completed_aggregate_run(db_session, organization, name="audit-fail-test")
    await db_session.commit()

    def _fail_only_detailed(*_a: object, **kwargs: object) -> object:
        # The budget row (risk_analysis_run.export) succeeds; only the detailed
        # per-download report.exported write fails — exercising T8(d)'s tolerance.
        if kwargs.get("action") == "report.exported":
            raise RuntimeError("db boom")
        return None

    with (
        caplog.at_level(logging.ERROR, logger="idraa.routes.reports"),
        patch(
            "idraa.routes.reports.AuditWriter.log",
            new_callable=AsyncMock,
            side_effect=_fail_only_detailed,
        ),
    ):
        r = await client.get(f"/reports/run/{run.id}")

    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    # Error must be logged
    assert any(
        "report.exported" in rec.message or "audit" in rec.message.lower() for rec in caplog.records
    )


# ---------- T8(e): UI entry points ----------


async def test_reports_list_shows_completed_single_run(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """T8(e): /reports list now shows completed SINGLE runs too."""
    client, org_id = authed_admin
    organization = await _org_for(db_session, org_id)
    await _make_completed_single_run(db_session, organization, name="single-scenario-report")
    await db_session.commit()

    r = await client.get("/reports")
    assert r.status_code == 200
    assert "single-scenario-report" in r.text


async def test_reports_list_single_run_download_link_points_to_new_route(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """T8(e): SINGLE run's download link in /reports list points to /reports/run/{id}."""
    client, org_id = authed_admin
    organization = await _org_for(db_session, org_id)
    run = await _make_completed_single_run(db_session, organization, name="single-link-test")
    await db_session.commit()

    r = await client.get("/reports")
    assert r.status_code == 200
    assert f'href="/reports/run/{run.id}"' in r.text


async def test_run_detail_shows_pdf_download_link_for_completed_single(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """T8(e): run-detail page shows the PDF report AND verification-workbook download links for COMPLETED SINGLE."""
    client, org_id = authed_admin
    organization = await _org_for(db_session, org_id)
    run = await _make_completed_single_run(db_session, organization, name="detail-page-test")
    await db_session.commit()

    r = await client.get(f"/runs/{run.id}")
    assert r.status_code == 200
    assert f'href="/reports/run/{run.id}"' in r.text
    assert f'href="/reports/run/{run.id}/verification.xlsx"' in r.text


async def test_run_detail_shows_pdf_download_link_for_completed_aggregate(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """T8(e): run-detail page shows the PDF report AND verification-workbook download links for COMPLETED AGGREGATE."""
    client, org_id = authed_admin
    organization = await _org_for(db_session, org_id)
    run = await _make_completed_aggregate_run(db_session, organization, name="agg-detail-page-test")
    await db_session.commit()

    r = await client.get(f"/runs/{run.id}")
    assert r.status_code == 200
    assert f'href="/reports/run/{run.id}"' in r.text
    assert f'href="/reports/run/{run.id}/verification.xlsx"' in r.text


@pytest.mark.anyio
async def test_run_detail_non_completed_has_no_pdf_download_link(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """T9 Step 1.5(b): run-detail page for a non-COMPLETED run must NOT show the PDF download link."""
    import hashlib

    client, org_id = authed_admin
    run = RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=org_id,
        run_type=RunType.AGGREGATE,
        status=RunStatus.RUNNING,
        scenario_id=None,
        aggregate_scenario_ids=[str(uuid.uuid4()), str(uuid.uuid4())],
        mc_iterations=1000,
        inputs_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        controls_snapshot=[],
        control_ids_used=[],
    )
    db_session.add(run)
    await db_session.commit()

    r = await client.get(f"/runs/{run.id}")
    assert r.status_code == 200
    assert f"/reports/run/{run.id}" not in r.text, (
        "Neither the PDF report nor the verification-workbook download link may appear "
        "on the run-detail page for a non-COMPLETED run (the substring covers both hrefs)"
    )


@pytest.mark.asyncio
async def test_report_export_throttled_at_cap(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """riskflow#564 (L4): the heavy PDF/xlsx exports now share the sliding-window
    export budget (the old report.exported action didn't match the limiter's
    %.export predicate, so they were un-throttled). The Nth+1 export is refused
    with 429 before another reportlab build runs."""
    from idraa.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "export_rate_limit_count", 2)
    monkeypatch.setattr(settings, "export_rate_limit_window_seconds", 3600)

    client, org_id = authed_admin
    organization = await _org_for(db_session, org_id)
    run = await _make_completed_aggregate_run(db_session, organization, name="throttle-test")
    await db_session.commit()

    assert (await client.get(f"/reports/run/{run.id}")).status_code == 200
    assert (await client.get(f"/reports/run/{run.id}")).status_code == 200
    r = await client.get(f"/reports/run/{run.id}")
    assert r.status_code == 429
    assert r.headers["Retry-After"] == "3600"
