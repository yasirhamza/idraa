"""Routes for /reports, /reports/run/{run_id} (T8 #351 unified route),
and /reports/executive/{run_id} (308 legacy alias).

Route surface:
  GET /reports                   — list page (COMPLETED runs of both types)
  GET /reports/export.csv        — CSV export (aggregate runs, all statuses)
  GET /reports/run/{run_id}      — download PDF for COMPLETED SINGLE or AGGREGATE
  GET /reports/executive/{run_id}— 308 → /reports/run/{run_id} (legacy alias)

Validation at the route boundary (Q10=A):
  • 404 for not-found / cross-org / non-COMPLETED / wrong combination
  • 500 for COMPLETED+simulation_results=None (data-integrity bug)

Filename construction (T8(c)):
  idraa-run-report-{org_slug}-{run_slug}-{YYYY-MM-DD}.pdf
  Both slugs sanitise to [a-zA-Z0-9_-]{1,40} preventing Content-Disposition
  header injection. Empty/all-underscore slugs fall back to 'org' / 'run'.

Export audit (T8(d)):
  Successful download writes exactly one `report.exported` AuditLog row
  (entity_type='risk_analysis_run', action='report.exported', changes contain
  bytes_written). Log-and-continue: audit failure never aborts the PDF response.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.app import templates
from idraa.db import get_session
from idraa.formatting import utc_isoformat
from idraa.models.organization import Organization
from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus
from idraa.models.user import User
from idraa.repositories.run_repo import RunRepo
from idraa.routes.deps import client_ip, get_db, require_user
from idraa.services.audit import AuditWriter, log_bulk_export
from idraa.services.org import require_sole_org
from idraa.services.pdf_report import render_executive_pdf
from idraa.services.reports import build_executive_pdf_data
from idraa.services.verification_workbook import build_verification_workbook
from idraa.utils.csv_export import csv_response

_XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

logger = logging.getLogger(__name__)
router = APIRouter()


_FILENAME_SLUG = re.compile(r"[^a-zA-Z0-9_-]")


def _slug(raw: str, fallback: str, maxlen: int = 40) -> str:
    """Sanitise *raw* to a Content-Disposition-safe slug.

    Non-alphanumeric/dash/underscore chars are replaced with underscores, the
    result is truncated to *maxlen*, and an all-underscore string (e.g. from a
    CJK name) falls back to *fallback* so the filename never has a double-dash.
    """
    s = _FILENAME_SLUG.sub("_", raw)[:maxlen]
    return fallback if s.strip("_") == "" else s


def _build_filename(run: RiskAnalysisRun, org: Organization) -> str:
    """Construct a Content-Disposition-safe filename.

    Format: idraa-run-report-{org_slug}-{run_slug}-{YYYY-MM-DD}.pdf

    T8(c) extends the pre-T8 pattern (which used only the org slug) to also
    include a slugified run-name component, giving operators a self-describing
    filename in their downloads directory.

    Caller (download_run_pdf) has validated run.status == COMPLETED, so
    run.completed_at is not None on the live path. The 'undated' fallback is
    dead-code-by-contract; defensive only.
    """
    org_slug = _slug(str(org.name), fallback="org")
    run_name = str(run.name or "")
    run_slug = _slug(run_name, fallback="run")
    completed_at = run.completed_at
    date = "undated" if completed_at is None else completed_at.strftime("%Y-%m-%d")
    return f"idraa-run-report-{org_slug}-{run_slug}-{date}.pdf"


@router.get("/reports", response_class=HTMLResponse)
async def list_reports(
    request: Request,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    org = await require_sole_org(db)
    repo = RunRepo(db)
    # T8(e): show COMPLETED runs of both types (SINGLE + AGGREGATE) so operators
    # can download PDFs for single-scenario analyses alongside aggregate runs.
    runs = await repo.list_completed_for_org(org.id, limit=50)
    return templates.TemplateResponse(
        request,
        "reports/list.html",
        {"current_user": user, "runs": runs, "org": org},
    )


@router.get("/reports/export.csv")
async def reports_export_csv(
    request: Request,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Stream aggregate runs list as CSV download.

    Plan-gate Arch-2: registered BEFORE /reports/executive/{run_id} so
    "export.csv" is matched as a literal path, not forwarded to the UUID handler.
    Plan-gate Sec-3: scoped by org from require_sole_org.
    """
    org = await require_sole_org(db)
    repo = RunRepo(db)
    runs = await repo.list_aggregate_for_org(org.id, limit=10_000)
    # #304: bulk egress audit row; run_type filter distinguishes this view
    # from /analyses/export.csv (same entity_type).
    await log_bulk_export(
        db,
        organization_id=org.id,
        entity_type="risk_analysis_run",
        fmt="csv",
        count=len(runs),
        user_id=user.id,
        ip_address=client_ip(request),
        filters={"run_type": "aggregate"},
    )
    header = ["id", "name", "status", "created_at"]
    rows = (
        (
            str(r.id),
            r.name or "",
            r.status.value if hasattr(r.status, "value") else str(r.status),
            utc_isoformat(r.created_at),
        )
        for r in runs
    )
    return csv_response(filename="reports.csv", header=header, rows_iter=rows)


@router.get("/reports/run/{run_id}")
async def download_run_pdf(
    request: Request,
    run_id: uuid.UUID,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """T8(a): unified PDF download for COMPLETED SINGLE and AGGREGATE runs.

    Q10=A uniform 404 for not-found / IDOR / non-COMPLETED.
    500 for COMPLETED-but-empty-results (data-integrity bug).
    Cache-Control: private, no-store on the PDF response.
    T8(d): writes one report.exported AuditLog row on success (log-and-continue).
    """
    org = await require_sole_org(db)
    repo = RunRepo(db)
    run = await repo.get_for_org(org.id, run_id)

    # Q10=A uniform 404 for not-found / IDOR / non-COMPLETED.
    if run is None:
        raise HTTPException(status_code=404)
    if run.status != RunStatus.COMPLETED:
        raise HTTPException(status_code=404)

    # Q10=A 500 for COMPLETED-but-empty-results: data-integrity bug.
    if run.simulation_results is None:
        logger.error(
            "Run report PDF: run %s is COMPLETED but simulation_results is None",
            run.id,
        )
        raise HTTPException(status_code=500)

    client = client_ip(request)

    # L4 (riskflow#564): throttle + budget-count the heavy PDF export BEFORE
    # spending CPU, sharing the sliding-window budget with the CSV exports. The
    # report.exported action below does NOT match the limiter's `%.export`
    # predicate, so this path was previously un-throttled. Over budget ->
    # ExportRateLimitedError (over budget) -> 429. FAIL-CLOSED on the
    # throttle/budget row, matching the CSV export path and the "audit_log health
    # gates bulk egress" stance: a heavy export must not proceed un-throttled and
    # un-counted. (The DETAILED report.exported row below stays log-and-continue —
    # T8(d) — so a hiccup writing that non-load-bearing egress detail does not
    # block a legit download.) Committed now because this handler releases the
    # request connection mid-flight (below), bypassing get_db's terminal commit.
    await log_bulk_export(
        db,
        organization_id=org.id,
        entity_type="risk_analysis_run",
        fmt="pdf",
        count=1,
        user_id=user.id,
        ip_address=client,
        filters={"kind": "report_pdf"},
    )
    await db.commit()

    data = await build_executive_pdf_data(db, run, org)
    filename = _build_filename(run, org)
    org_id = org.id
    user_id = user.id
    run_pk = run.id

    # M1 (riskflow#563): release the pooled DB connection BEFORE the CPU-bound
    # reportlab render + response stream — neither touches the DB. Holding the
    # connection across a multi-second build exhausted the pool (size 5 + overflow
    # 10) under repeated taps and 500'd concurrent requests incl. login (prod
    # outage 2026-06-15); the sibling verification-xlsx path was fixed then, this
    # one wasn't. expire_on_commit=False keeps already-loaded columns readable on
    # the detached objects for the filename/audit.
    db.expunge_all()
    await db.close()

    # CPU-bound reportlab multiBuild -> thread: never blocks the event loop and
    # holds no DB connection.
    pdf_bytes = await asyncio.to_thread(render_executive_pdf, data)

    # T8(d): detailed per-download egress audit on a FRESH short-lived connection
    # (the request session is closed) — log-and-continue so audit failure never
    # aborts the download.
    try:
        async with get_session() as audit_db:
            await AuditWriter(audit_db).log(
                organization_id=org_id,
                entity_type="risk_analysis_run",
                entity_id=run_pk,
                action="report.exported",
                changes={"bytes_written": [None, len(pdf_bytes)]},
                user_id=user_id,
                ip_address=client,
            )
    except Exception:
        logger.error(
            "report.exported audit write failed for run %s — continuing",
            run_pk,
            exc_info=True,
        )

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, no-store",
        },
    )


def _build_xlsx_filename(run: RiskAnalysisRun, org: Organization) -> str:
    """Build the verification-workbook download filename.

    Mirror _build_filename's date source for pdf/xlsx consistency (Sec-NTH-2):
    completed_at with an 'undated' fallback (dead-code-by-contract on the
    COMPLETED path the caller guarantees).

    Format: idraa-verification-{org_slug}-{run_slug}-{YYYY-MM-DD}.xlsx
    """
    org_slug = _slug(str(org.name), fallback="org")
    run_slug = _slug(str(run.name or ""), fallback="run")
    completed_at = run.completed_at
    date = "undated" if completed_at is None else completed_at.strftime("%Y-%m-%d")
    return f"idraa-verification-{org_slug}-{run_slug}-{date}.xlsx"


@router.get("/reports/run/{run_id}/verification.xlsx")
async def download_verification_workbook(
    request: Request,
    run_id: uuid.UUID,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Independent in-Excel Monte Carlo workbook for a COMPLETED run.

    Auth/org-scoping/audit mirror download_run_pdf (uniform 404; 500 on empty).
    """
    org = await require_sole_org(db)
    repo = RunRepo(db)
    run = await repo.get_for_org(org.id, run_id)

    # Q10=A uniform 404 for not-found / IDOR / non-COMPLETED.
    if run is None or run.status != RunStatus.COMPLETED:
        raise HTTPException(status_code=404)

    # 500 for COMPLETED-but-empty-results: data-integrity bug.
    if run.simulation_results is None:
        logger.error(
            "Verification xlsx: run %s COMPLETED but simulation_results is None",
            run.id,
        )
        raise HTTPException(status_code=500)

    client = client_ip(request)

    # L4 (riskflow#564): throttle + budget-count the heavy xlsx export BEFORE the
    # CPU build, sharing the sliding-window budget with the CSV exports (the
    # report.exported action below does NOT match the limiter's `%.export`
    # predicate). ExportRateLimitedError -> 429; FAIL-CLOSED on the budget row
    # (CSV-consistent; the detailed report.exported write below stays
    # log-and-continue). Committed now because this handler releases the request
    # connection below, bypassing get_db's terminal commit.
    await log_bulk_export(
        db,
        organization_id=org.id,
        entity_type="risk_analysis_run",
        fmt="xlsx",
        count=1,
        user_id=user.id,
        ip_address=client,
        filters={"kind": "verification_xlsx"},
    )
    await db.commit()

    # Capture what we need, then RELEASE the pooled DB connection BEFORE the
    # CPU-bound build AND the ~750 KB response stream — neither touches the DB.
    # Holding the request's connection across the build + (slow-mobile) body
    # stream is what exhausted the pool (size 5 + overflow 10) under repeated
    # taps: every other request, incl. login, then waited the 30s QueuePool
    # timeout and 500'd (prod outage 2026-06-15). expire_on_commit=False keeps
    # the already-loaded scalar/JSON columns readable on the detached objects, so
    # the threaded build can still read run/org after the session is closed.
    org_id = org.id
    user_id = user.id
    db.expunge_all()
    await db.close()

    # GIL-bound xlsxwriter LET build → thread so it doesn't block the event loop.
    # The connection is already back in the pool, so even if the build is slow it
    # cannot starve other requests of a DB connection.
    # Help-link base from the serving request (idraa.app / fly.dev / per-tester
    # hosts / localhost all self-describe) — never a hardcoded domain (OSS rule).
    xlsx_bytes = await asyncio.to_thread(
        build_verification_workbook,
        run,
        org,
        base_url=str(request.base_url).rstrip("/"),
    )
    filename = _build_xlsx_filename(run, org)

    # Export audit on a FRESH short-lived connection (the request session is
    # closed) — log-and-continue so audit failure never aborts the download.
    try:
        async with get_session() as audit_db:
            await AuditWriter(audit_db).log(
                organization_id=org_id,
                entity_type="risk_analysis_run",
                entity_id=run_id,
                action="report.exported",
                # `format` discriminator distinguishes pdf vs xlsx egress in audit;
                # no AuditLog payload-shape contract test forbids the extra key
                # (verified: none in tests/contracts/). Sec-NTH-1 accepted.
                changes={"bytes_written": [None, len(xlsx_bytes)], "format": [None, "xlsx"]},
                user_id=user_id,
                ip_address=client,
            )
            # get_session() auto-commits on clean exit.
    except Exception:
        logger.error(
            "report.exported (xlsx) audit write failed for run %s — continuing",
            run_id,
            exc_info=True,
        )

    return Response(
        content=xlsx_bytes,
        media_type=_XLSX_MEDIA,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/reports/executive/{run_id}")
async def legacy_executive_alias(
    run_id: uuid.UUID,
    user: User = Depends(require_user),
) -> RedirectResponse:
    """T8(b): 308 Permanent Redirect to the unified /reports/run/{run_id} route.

    No DB lookup, no audit write — unconditional redirect after FastAPI's
    path-type validation (uuid.UUID) guarantees a well-formed UUID.
    308 (not 301/302) preserves the HTTP method for any client that might
    POST to the old URL, and signals to HTTP caches that the redirect is
    permanent so they can update bookmarks.
    """
    return RedirectResponse(
        url=f"/reports/run/{run_id}",
        status_code=308,
    )
