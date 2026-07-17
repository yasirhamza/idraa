"""Routes for RiskAnalysisRun: detail, status fragment, cancel, history.

All routes are organization-scoped via the require_user/require_role
dependencies (org accessed via user.organization_id). Reviewer role is
read-only (cannot trigger or cancel) — enforced at dependency level.

Spec §8.1.

RBAC summary:
- GET /runs/{id}              require_user  (analyst, reviewer, admin)
- GET /runs/{id}/status       require_user  (analyst, reviewer, admin)
- POST /runs/{id}/cancel      require_role(ANALYST, ADMIN) — reviewer gets 403
- GET /scenarios/{id}/runs    require_user  (analyst, reviewer, admin)
- GET /analyses/new           require_role(ANALYST, ADMIN) — unified new-analysis form
- POST /analyses              require_role(ANALYST, ADMIN) — create + dispatch run
- GET /scenarios/{id}/run/new 303 redirect to /analyses/new (PR xi adapter)
- POST /scenarios/{id}/run    adapter: wraps as 1-element list, calls create_and_dispatch
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from idraa.app import templates
from idraa.config import get_settings
from idraa.errors import (
    RunBusyError,
    RunNotFoundError,
    RunValidationError,
    ScenarioNotFoundError,
)
from idraa.formatting import utc_isoformat
from idraa.models.enums import (
    FairCamSubFunction,
    UserRole,
)
from idraa.models.organization import Organization
from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType
from idraa.models.scenario import Scenario
from idraa.models.user import User
from idraa.repositories.run_repo import RunRepo
from idraa.repositories.scenario_repo import ScenarioRepo
from idraa.routes.deps import (
    client_ip,
    get_db,
    require_role,
    require_user,
)
from idraa.services.aggregate_run_view_model import build_aggregate_display_results
from idraa.services.audit import log_bulk_export
from idraa.services.dashboard_view_model import appetite_strip
from idraa.services.flash import build_flash
from idraa.services.fx_rates import FxRateService
from idraa.services.org import require_sole_org
from idraa.services.reporting_currency import resolve_reporting_currency
from idraa.services.retention import maybe_sweep_opportunistic
from idraa.services.run_view_model import build_display_results
from idraa.services.runs import RunService
from idraa.utils.csv_export import csv_response

logger = logging.getLogger(__name__)

router = APIRouter()


async def _resolve_run_rc(
    run: Any, db: AsyncSession, org: Organization
) -> Any:  # returns ReportingCurrency
    """Resolve the ReportingCurrency for a run + org.

    Loads the active FX rate only when the run lacks a pinned snapshot AND
    the org's preferred currency is non-USD — the common path (USD org or
    pinned run) never hits the DB.
    """
    snap = getattr(run, "presentation_fx_snapshot", None)
    code = getattr(org, "preferred_currency", "USD") or "USD"
    active_rate = None
    if code != "USD" and not snap:
        active_rate = await FxRateService(db).active_rate(org.id, code)
    return resolve_reporting_currency(run, org, active_rate)


# Issue #131 (M-I1 / M-N2): the 6 sub-functions whose ``unit_type`` changed
# at #131 (ELAPSED_TIME → PROBABILITY). V2 snapshots persisting assignments
# on these sub-functions are subject to post-#131 re-interpretation;
# always-PROBABILITY sub-functions are not. Single source of truth used by
# the log filter (_emit_v2_snapshot_read_log) AND the banner-condition
# helper (_v2_snapshot_has_reclassified_sub_function), so the audit log and
# the operator-facing banner cannot drift out of sync.
_RECLASSIFIED_SUB_FUNCTIONS_131: frozenset[FairCamSubFunction] = frozenset(
    {
        FairCamSubFunction.LEC_RESP_RESILIENCE,
        FairCamSubFunction.VMC_ID_THREAT_INTELLIGENCE,
        FairCamSubFunction.VMC_ID_CONTROL_MONITORING,
        FairCamSubFunction.VMC_CORR_TREATMENT_SELECTION,
        FairCamSubFunction.DSC_ID_MISALIGNED,
        FairCamSubFunction.DSC_CORR_MISALIGNED,
    }
)


def _v2_snapshot_has_reclassified_sub_function(
    controls_snapshot: list[dict[str, Any]] | None,
) -> bool:
    """Return True iff any V2 envelope carries an assignment on a #131-reclassified
    sub-function (M-N2).

    Tightens the banner condition: V2 snapshots whose assignments only touch
    always-PROBABILITY sub-functions have no #131 re-interpretation drift and
    must NOT trigger the banner. Symmetric to the log-filter behaviour in
    ``_emit_v2_snapshot_read_log`` — both consume
    ``_RECLASSIFIED_SUB_FUNCTIONS_131`` as the single source of truth.
    """
    if not controls_snapshot:
        return False
    for c in controls_snapshot:
        if not isinstance(c, dict) or c.get("snapshot_version") != 2:
            continue
        for a in c.get("assignments") or []:
            if not isinstance(a, dict):
                continue
            sf_value = a.get("sub_function")
            if not isinstance(sf_value, str):
                continue
            try:
                sf = FairCamSubFunction(sf_value)
            except ValueError:
                # Tampered / unknown sub_function string — skip without raising.
                continue
            if sf in _RECLASSIFIED_SUB_FUNCTIONS_131:
                return True
    return False


def _emit_v2_snapshot_read_log(
    run: Any,
    user: User,
    controls_snapshot: list[dict[str, Any]] | None,
) -> None:
    """Issue #131 T6.5 (Sec3-I1 + CR4-B1): structured log for V2 snapshot reads.

    Tamper-evident server-side audit signal — DOM-suppression of the
    template banner cannot hide this. Volume is bounded by historical V2
    run count (not by user traffic), so emitting on every V2 read is safe.

    The log message uses positional ``%s`` interpolation per stdlib
    ``logging`` semantics; structured fields go in ``extra={}`` (NOT as
    keyword args, which raise TypeError on stdlib loggers). Matches the
    canonical project pattern at routes/controls.py:266. (CR4-B1)

    Filter on ``_RECLASSIFIED_SUB_FUNCTIONS_131`` (M-I1): the previous
    ``UnitType.PROBABILITY`` filter over-reported by matching every
    always-PROBABILITY sub-function. Only the 6 sub-functions whose
    unit_type CHANGED at #131 carry post-#131 re-interpretation risk.
    """
    if not controls_snapshot:
        return

    # Materialise the V2 subset once so we don't iterate twice.
    v2_entries = [
        c for c in controls_snapshot if isinstance(c, dict) and c.get("snapshot_version") == 2
    ]
    if not v2_entries:
        return

    reclassified_subfns: set[str] = set()
    for c in v2_entries:
        for a in c.get("assignments") or []:
            if not isinstance(a, dict):
                continue
            sf_value = a.get("sub_function")
            if not isinstance(sf_value, str):
                continue
            try:
                sf = FairCamSubFunction(sf_value)
            except ValueError:
                # Tampered / unknown sub_function string — skip without raising.
                continue
            if sf in _RECLASSIFIED_SUB_FUNCTIONS_131:
                # Capture only the sub-functions whose unit_type CHANGED at
                # #131 (the re-interpretation surface). M-I1.
                reclassified_subfns.add(sf.value)

    logger.info(
        "snapshot_v2_read run_id=%s user_id=%s",
        run.id,
        user.id,
        extra={
            "run_id": str(run.id),
            "user_id": str(user.id),
            "reclassified_sub_functions": sorted(reclassified_subfns),
        },
    )


# Plan-gate Arch-2: registered BEFORE /runs/{run_id} GET so the UUID path-
# segment parser does not shadow the literal string "control-matrix.csv".
@router.get("/runs/{run_id}/control-matrix.csv")
async def get_aggregate_matrix_csv(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),
) -> Response:
    """Per-scenario control attribution matrix as a CSV download.

    Shapley semantics: each cell is a control's Shapley marginal contribution —
    its fair share of the scenario's modeled risk reduction. Cells SUM to the
    scenario / column total (Shapley efficiency). A 'Scenario total' column is
    appended; M-1's no-row-total rationale is superseded by Shapley efficiency
    (cells now sum meaningfully).

    Plan-gate M-2: cells iterated as dict-per-cell ({control_id, value}).

    RBAC: any authenticated org user (same as /runs/{id} GET).
    """
    service = RunService(db)
    try:
        run = await service.get_for_org(user.organization_id, run_id)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404) from exc

    if run.run_type != RunType.AGGREGATE:
        raise HTTPException(
            status_code=400,
            detail="control-matrix.csv is only available for AGGREGATE runs.",
        )

    # Resolve reporting currency once — cells are already converted in the builder.
    org = await db.get(Organization, user.organization_id)
    if org is None:
        raise HTTPException(status_code=404)
    rc = await _resolve_run_rc(run, db, org)
    currency_code = rc.code

    display_results = build_aggregate_display_results(run, rc=rc)
    if display_results is None:
        raise HTTPException(
            status_code=404,
            detail="Run has no simulation results (still pending or failed).",
        )

    matrix = display_results["per_scenario_control_matrix"]
    controls = matrix.get("controls", [])
    rows = matrix.get("rows", [])
    # Mean+typical side-by-side (2026-07-04): "basis" defaults to "typical" for
    # legacy blobs / pre-mean-basis matrices (no "basis" key). Legacy runs keep
    # today's single-column-per-control shape byte-identical; mean-basis runs
    # (every run executed after the mean-basis chain landed) gain a paired
    # typical-case column per control, explicitly labeled.
    _matrix_basis = matrix.get("basis", "typical")

    control_names = [c["control_name"] for c in controls]

    def _cell_value(cell: dict[str, Any], key: str = "value") -> str:
        # Plan-gate M-2: cells are dict-per-cell ({control_id, value}).
        # None (absent attribution) → blank field; genuine 0.0 → "0.00".
        v = cell.get(key)
        return "" if v is None else f"{float(v):.2f}"

    def _control_total(c: dict[str, Any], key: str = "total_reduction") -> str:
        v = c.get(key)
        return "" if v is None else f"{float(v):.2f}"

    if _matrix_basis == "mean":
        header = ["Scenario"]
        for name in control_names:
            header.append(f"{name} (average $)")
            header.append(f"{name} (typical $)")
        header.append("Scenario total (average $)")

        def _rows() -> Any:
            for row in rows:
                cell_strs: list[str] = []
                primary_vals: list[float] = []
                for c in row.get("cells", []):
                    avg_s = _cell_value(c, "value")
                    cell_strs.append(avg_s)
                    cell_strs.append(_cell_value(c, "value_typical"))
                    if avg_s != "":
                        primary_vals.append(float(avg_s))
                row_total = f"{sum(primary_vals):.2f}" if primary_vals else ""
                yield (row["scenario_name"], *cell_strs, row_total)
            # Per-control totals row with grand-total cell (Shapley efficiency,
            # primary/average chain only — the typical chain is informational).
            col_cells: list[str] = []
            primary_totals: list[float] = []
            for c in controls:
                avg_s = _control_total(c, "total_reduction")
                col_cells.append(avg_s)
                col_cells.append(_control_total(c, "total_reduction_typical"))
                if avg_s != "":
                    primary_totals.append(float(avg_s))
            grand_total = f"{sum(primary_totals):.2f}" if primary_totals else ""
            yield ("Total per control", *col_cells, grand_total)
    else:
        header = ["Scenario", *control_names, "Scenario total"]

        def _rows() -> Any:
            for row in rows:
                cell_strs = [_cell_value(c) for c in row.get("cells", [])]
                non_none = [float(s) for s in cell_strs if s != ""]
                row_total = f"{sum(non_none):.2f}" if non_none else ""
                yield (row["scenario_name"], *cell_strs, row_total)
            # Per-control totals row with grand-total cell (Shapley efficiency).
            col_totals = [_control_total(c) for c in controls]
            non_none_totals = [float(s) for s in col_totals if s != ""]
            grand_total = f"{sum(non_none_totals):.2f}" if non_none_totals else ""
            yield ("Total per control", *col_totals, grand_total)

    # P3: preamble labels use reporting currency code (cells already converted in builder).
    if _matrix_basis == "mean":
        preamble = [
            f"Per-scenario control attribution — Shapley marginal contributions ({currency_code}).",
            "Each control has two columns: (average $) is the mean-basis fair share, directly",
            "comparable to the Monte-Carlo mean headline; (typical $) is the paired typical-case",
            "(median-like) fair share, which for skewed losses runs below the average column.",
            "Cells SUM to the scenario / column total (Shapley efficiency) on the average column;",
            "the typical column is informational and does not drive the totals.",
            "Rows/columns with blank cells show a partial total over attributed controls only, not the full scenario reduction.",
        ]
    else:
        preamble = [
            f"Per-scenario control attribution — Shapley marginal contributions ({currency_code}).",
            "Each cell is a control's fair share of the scenario's modeled risk reduction;",
            "cells SUM to the scenario / column total (Shapley efficiency). Representative-value point estimate (per-distribution: PERT mode, lognormal median) -",
            "for right-skewed losses this runs systematically below the Monte-Carlo headline.",
            "Rows/columns with blank cells show a partial total over attributed controls only, not the full scenario reduction.",
        ]
    if matrix.get("unavailable"):
        preamble.append(
            "Per-control attribution unavailable for this run (predates Shapley attribution or exceeded attribution limits)."
        )

    return csv_response(
        filename=f"run-{run_id}-control-matrix.csv",
        header=header,
        rows_iter=_rows(),
        preamble=preamble,
    )


def _build_display_context(
    run: RiskAnalysisRun,
    org: Organization | None,
    rc: Any,
) -> dict[str, Any]:
    """Build the display view-model + reporting-currency-converted cost summary.

    Shared by the full detail page (``get_run_detail``) and the status-poll
    fragment (``get_run_status_fragment``) so a poll-completion render matches
    a fresh page load exactly — the cost summary now feeds the verdict strip,
    which both handlers render (plan-gate Arch-I1). Extracting it here prevents
    a third divergent copy of the rc/loss_tolerance/cost conversion.

    ``rc`` is the pre-resolved reporting-currency converter; ``org`` may be
    None (fragment fallback), in which case loss_tolerance conversion is
    skipped but cost fields still convert through ``rc``.
    """
    if run.run_type == RunType.SINGLE:
        display_results: Any = build_display_results(run, rc=rc)
    else:
        display_results = build_aggregate_display_results(run, rc=rc)

    if (
        isinstance(display_results, dict)
        and org is not None
        and org.loss_tolerance_amount is not None
        and org.loss_tolerance_probability is not None
    ):
        # P3: convert the loss_tolerance amount to reporting currency.
        # probability is a ratio — NOT money — and must NOT be converted.
        display_results["loss_tolerance"] = {
            "amount": rc.convert(float(org.loss_tolerance_amount)),
            "probability": float(org.loss_tolerance_probability),
        }

    # P1 #547 / #545: verdict strip from the SAME converted curve + converted
    # tolerance the dashboard posture verdict uses — never raw samples (the #294
    # rule forbids raw-sample loads on these paths). Both args None-safe, so this
    # is unconditional; the strip is None (and elides) when either is absent.
    if isinstance(display_results, dict):
        display_results["appetite_strip"] = appetite_strip(
            display_results.get("dual_lec"), display_results.get("loss_tolerance")
        )

    # P3: convert cost_summary dollar fields to reporting currency.
    # aggregate_roi is dimensionless — do NOT convert.
    _cost_raw = (run.simulation_results or {}).get("cost_summary")
    converted_cost: dict[str, Any] | None = None
    if _cost_raw is not None:
        converted_cost = dict(_cost_raw)
        for _k in ("total_annual_cost", "total_risk_reduction", "net_benefit"):
            _v = converted_cost.get(_k)
            if _v is not None:
                converted_cost[_k] = rc.convert(float(_v))

    return {"display_results": display_results, "converted_cost_summary": converted_cost}


@router.get("/runs/{run_id}", response_class=HTMLResponse)
async def get_run_detail(
    run_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),
    purged: int | None = Query(
        default=None,
        ge=0,
        le=1,
        description=(
            "#297: post-purge-samples flash flag. Set to 1 by the purge "
            "POST redirect; rendered as a 'success' banner here."
        ),
    ),
) -> HTMLResponse:
    """Full run detail page. Read-only RBAC: any authenticated org user.

    The view-model is built by ``services.run_view_model.build_display_results``
    which strips raw ``simulation_results`` sample arrays before template
    render to avoid 20MB-scope bloat in the Jinja context.
    """
    service = RunService(db)
    try:
        run = await service.get_for_org(user.organization_id, run_id)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404) from exc

    # P3: resolve reporting currency once — builders use it, templates only format.
    org = await db.get(Organization, user.organization_id)
    if org is None:
        raise HTTPException(status_code=404)
    rc = await _resolve_run_rc(run, db, org)

    if run.run_type == RunType.SINGLE:
        if run.scenario_id is None:
            raise HTTPException(status_code=404)
        scenario = await ScenarioRepo(db).get_for_org(
            organization_id=user.organization_id,
            scenario_id=run.scenario_id,
        )
        if scenario is None:
            raise HTTPException(status_code=404)
        snapshot_ids = sorted(str(c["control_id"]) for c in run.controls_snapshot)
        scenario_ids = sorted(str(c.id) for c in scenario.mitigating_controls)
        controls_overridden = snapshot_ids != scenario_ids
        scenarios = None
    else:  # AGGREGATE
        scenarios_uuids = [uuid.UUID(s) for s in (run.aggregate_scenario_ids or [])]
        scenarios = await ScenarioRepo(db).fetch_by_ids_for_org(
            user.organization_id,
            scenarios_uuids,
        )
        scenario = None
        controls_overridden = False

    # Anti-drift (plan-gate Arch-I1): the display view-model + converted cost
    # summary are built by the shared helper so the status-poll fragment renders
    # an identical verdict strip on poll-completion.
    _ctx = _build_display_context(run, org, rc)
    display_results = _ctx["display_results"]
    converted_cost = _ctx["converted_cost_summary"]

    # Issue #131 T6.5 (Sec3-I1 + CR4-B1): emit a structured log entry whenever
    # a V2 snapshot is surfaced to a user. This is the tamper-evident audit
    # signal for the post-#131 re-interpretation — independent of the
    # DOM banner the template renders.
    _emit_v2_snapshot_read_log(run, user, run.controls_snapshot)

    # M-N2: banner fires only when the V2 snapshot carries an assignment on a
    # post-#131 reclassified sub-function. Always-PROBABILITY V2 snapshots
    # have no re-interpretation drift and must not trigger the banner.
    has_reinterpreted_v2_snapshot = _v2_snapshot_has_reclassified_sub_function(
        run.controls_snapshot
    )

    flash = build_flash("Sample arrays purged.", "success") if purged == 1 else None
    # Resolve the creator to a human label — the raw created_by UUID is an
    # operator-hostile display (pre-existing UI bug found in the 2026-07-03
    # Playwright sweep). Missing user (deleted account) degrades to no label.
    creator_label = None
    if run.created_by:
        _creator = await db.get(User, run.created_by)
        if _creator is not None:
            creator_label = _creator.full_name or _creator.email
    return templates.TemplateResponse(
        request,
        "runs/detail.html",
        {
            "current_user": user,
            "flash": flash,
            "run": run,
            "scenario": scenario,
            "scenarios": scenarios,
            "display_results": display_results,
            "controls_overridden": controls_overridden,
            "has_reinterpreted_v2_snapshot": has_reinterpreted_v2_snapshot,
            "converted_cost_summary": converted_cost,
            "creator_label": creator_label,
        },
    )


@router.get("/runs/{run_id}/status", response_class=HTMLResponse)
async def get_run_status_fragment(
    run_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),
) -> HTMLResponse:
    """HTMX status-poll fragment.

    Self-stopping: the template omits ``hx-trigger`` once the run reaches
    a terminal state (COMPLETED / CANCELLED / FAILED), preventing the poll
    from running indefinitely after the run is done.

    On the COMPLETED transition the fragment includes the results panel, so
    it must carry ``display_results`` — without it the user watched the
    poll flip to "Completed" over an EMPTY panel until a manual refresh
    (gap caught by tests/e2e/test_run_execution_e2e.py). Built only on the
    terminal render: non-terminal polls stay cheap, and the self-stopping
    fragment means the build runs once per completion, not per poll.
    """
    service = RunService(db)
    try:
        run = await service.get_for_org(user.organization_id, run_id)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404) from exc

    display_results = None
    converted_cost: dict[str, Any] | None = None
    if run.status == RunStatus.COMPLETED:
        # COMPLETED-with-simulation_results=None (legacy purged rows, #297)
        # degrades gracefully: the builder returns None and the panel's
        # {% if display_results %} renders empty — same as the detail page.

        # P3: resolve reporting currency — must match get_run_detail for
        # intra-web parity (status fragment renders the SAME results panel).
        org = await db.get(Organization, user.organization_id)
        if org is not None:
            rc = await _resolve_run_rc(run, db, org)
        else:
            from idraa.services.run_view_model import _USD_IDENTITY

            rc = _USD_IDENTITY

        # Anti-drift (plan-gate Arch-I1): SAME helper as get_run_detail, so a
        # poll-completion render carries converted_cost_summary (→ verdict-strip
        # cost/ROI cells) identically to a fresh page load.
        _ctx = _build_display_context(run, org, rc)
        display_results = _ctx["display_results"]
        converted_cost = _ctx["converted_cost_summary"]

    return templates.TemplateResponse(
        request,
        "runs/_status_poll.html",
        {
            "current_user": user,
            "run": run,
            "display_results": display_results,
            "converted_cost_summary": converted_cost,
        },
    )


@router.post("/runs/{run_id}/cancel")
async def post_cancel_run(
    run_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ANALYST, UserRole.ADMIN)),
) -> Response:
    """Cancel a non-terminal run.

    RBAC: ``require_role(ANALYST, ADMIN)`` rejects reviewer with 403 at
    dependency level — the handler body never runs for reviewers.

    ``RunService.cancel`` is idempotent: if the run is already in a terminal
    state it is returned unchanged (no error). The status-poll fragment is
    returned so HTMX can swap the status region inline.
    """
    service = RunService(db)
    try:
        run = await service.cancel(
            organization_id=user.organization_id,
            run_id=run_id,
            cancelled_by=user.id,
        )
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404) from exc

    return templates.TemplateResponse(
        request,
        "runs/_status_poll.html",
        {
            "current_user": user,
            "run": run,
        },
    )


def _confirmed(value: str | None) -> bool:
    """Truthy-check the mandatory ``confirm`` form field (#297).

    Accepts the typical HTML form values for a checked checkbox / set
    hidden input ("1", "on", "true", "yes"). Absent / falsey -> the route
    raises HTTP 400 (operator did not confirm the destructive action).
    """
    return (value or "").strip().lower() in {"1", "on", "true", "yes"}


@router.post("/runs/{run_id}/delete")
async def post_delete_run(
    run_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ANALYST, UserRole.ADMIN)),
    confirm: str | None = Form(default=None),
    force: str | None = Form(default=None),
) -> Response:
    """Hard-delete a run row; ON DELETE CASCADE removes its run_samples row (#297).

    RBAC: ``require_role(ANALYST, ADMIN)`` rejects reviewer with 403 at the
    dependency level. CSRF: middleware-validated automatically via the
    ``_csrf`` form field.

    Mandatory confirm: absent/falsey ``confirm`` -> 400. ``force`` (optional
    hidden field) overrides the in-flight guard for QUEUED / RUNNING runs.

    Error mapping: ``RunBusyError`` -> 409, ``RunNotFoundError`` -> 404.
    Success -> 303 redirect to the dashboard ``/?deleted=1`` (there is no
    ``GET /runs`` route — run lists live under the dashboard + scenario
    HTMX partials).
    """
    if not _confirmed(confirm):
        raise HTTPException(status_code=400, detail="confirm: missing or falsey")

    service = RunService(db)
    try:
        await service.delete_run(
            run_id,
            org_id=user.organization_id,
            user_id=user.id,
            force=_confirmed(force),
        )
    except RunBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404) from exc

    return RedirectResponse(url="/?deleted=1", status_code=303)


@router.post("/runs/{run_id}/purge-samples")
async def post_purge_run_samples(
    run_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ANALYST, UserRole.ADMIN)),
    confirm: str | None = Form(default=None),
) -> Response:
    """Delete just the run_samples row; keep the run + summary (#297).

    RBAC: ``require_role(ANALYST, ADMIN)`` — reviewer rejected at the
    dependency level. CSRF: middleware-validated via ``_csrf``.

    Mandatory confirm: absent/falsey ``confirm`` -> 400. The service is
    idempotent — purging already-purged samples is a silent no-op.

    Error mapping: ``RunNotFoundError`` -> 404. Success -> 303 redirect
    back to the run detail page with ``?purged=1``.
    """
    if not _confirmed(confirm):
        raise HTTPException(status_code=400, detail="confirm: missing or falsey")

    service = RunService(db)
    try:
        await service.purge_samples(
            run_id,
            org_id=user.organization_id,
            user_id=user.id,
        )
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404) from exc

    return RedirectResponse(url=f"/runs/{run_id}?purged=1", status_code=303)


async def _build_run_display_rows(
    db: AsyncSession,
    org: Organization,
    rows: list[Any],
) -> list[dict[str, Any]]:
    """Build the per-run display dicts (run + converted residual ALE + symbol)
    shared by the per-scenario history fragment and the org-wide /analyses index.

    Decision: use each run's pinned presentation_fx_snapshot when available (so
    historical runs show the rate active at run time), falling back to the
    current active rate for un-pinned runs. The active rate is loaded once
    (N+1-free — one org active rate covers all un-pinned runs).
    """
    from babel.numbers import get_currency_symbol as _get_sym

    from idraa.currency import APP_LOCALE

    _pref_code = getattr(org, "preferred_currency", "USD") or "USD"
    _active_rate = None
    if _pref_code != "USD":
        _active_rate = await FxRateService(db).active_rate(org.id, _pref_code)

    run_display_rows: list[dict[str, Any]] = []
    for run_row in rows:
        _rc = resolve_reporting_currency(run_row, org, _active_rate)
        _sr = run_row.simulation_results
        _ale_usd: float | None = None
        if _sr and run_row.status.value == "completed":
            # SINGLE runs store residual_risk at the top level; AGGREGATE runs
            # store aggregate_with_controls. Read whichever is present so the
            # global index shows a residual ALE for both run types.
            _resid = _sr.get("residual_risk") or _sr.get("aggregate_with_controls") or {}
            _ale_raw = _resid.get("annualized_loss_expectancy")
            if _ale_raw is not None:
                _ale_usd = float(_ale_raw)
        _ale_converted = _rc.convert(_ale_usd) if _ale_usd is not None else None
        _sym = _get_sym(_rc.code, locale=APP_LOCALE)
        run_display_rows.append(
            {
                "run": run_row,
                "ale": _ale_converted,
                "currency_symbol": _sym,
            }
        )
    return run_display_rows


@router.get("/scenarios/{scenario_id}/runs", response_class=HTMLResponse)
async def get_scenario_run_history(
    scenario_id: uuid.UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),
    page: int = 1,
    page_size: int = Query(default=10, ge=1, le=100),
) -> HTMLResponse:
    """Paginated run history fragment for a scenario.

    Returns ``_history_list.html`` (an HTMX partial). Scenario IDOR is
    guarded implicitly via ``RunRepo.list_for_scenario`` which filters by
    ``organization_id`` — runs for a cross-org scenario_id yield an empty
    list (not 404), matching the read-only nature of a list endpoint.
    """
    service = RunService(db)
    rows, total = await service.list_history(
        organization_id=user.organization_id,
        scenario_id=scenario_id,
        page=page,
        page_size=page_size,
    )

    # P3: per-run currency conversion, shared with the org-wide /analyses index.
    org = await require_sole_org(db)
    run_display_rows = await _build_run_display_rows(db, org, rows)

    # Opportunistic, throttled retention sweep — runs AFTER the response (own
    # session, atomic per-interval throttle). org_id pinned to the authed user
    # (Arch-N4), not the path param.
    background_tasks.add_task(
        maybe_sweep_opportunistic, get_settings(), org_id=user.organization_id
    )
    return templates.TemplateResponse(
        request,
        "runs/_history_list.html",
        {
            "current_user": user,
            "runs": rows,
            "run_display_rows": run_display_rows,
            "total": total,
            "page": page,
            "page_size": page_size,
            "scenario_id": scenario_id,
        },
    )


# ---- PR xi: unified /analyses/new form + POST + legacy adapters ------


@router.get("/analyses", response_class=HTMLResponse)
async def list_analyses(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),
    page: int = 1,
    page_size: int = Query(default=20, ge=1, le=100),
) -> HTMLResponse:
    """Org-wide historical analysis-runs index (full page).

    Lists every run for the org (SINGLE + AGGREGATE, all statuses), newest
    first, paginated. Org-scoped via ``list_history_for_org`` (no cross-org
    leakage). The sidebar "Analyses" item points here; "New analysis" + CSV
    export are reachable from this page.
    """
    page = max(1, page)
    service = RunService(db)
    rows, total = await service.list_history_for_org(
        organization_id=user.organization_id,
        page=page,
        page_size=page_size,
    )
    org = await require_sole_org(db)
    run_display_rows = await _build_run_display_rows(db, org, rows)

    background_tasks.add_task(
        maybe_sweep_opportunistic, get_settings(), org_id=user.organization_id
    )
    return templates.TemplateResponse(
        request,
        "analyses/index.html",
        {
            "current_user": user,
            "run_display_rows": run_display_rows,
            "total": total,
            "page": page,
            "page_size": page_size,
        },
    )


@router.get("/analyses/export.csv")
async def analyses_export_csv(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),
) -> Response:
    """Stream all runs for the current org as a CSV download.

    Plan-gate Arch-2: registered BEFORE /analyses/new so the literal
    "export.csv" path is matched without ambiguity.
    Plan-gate Sec-3: scoped by org from require_sole_org.
    """
    org = await require_sole_org(db)
    repo = RunRepo(db)
    runs = await repo.list_recent_for_org(org.id, limit=10_000)
    # #304: bulk egress audit row.
    await log_bulk_export(
        db,
        organization_id=org.id,
        entity_type="risk_analysis_run",
        fmt="csv",
        count=len(runs),
        user_id=user.id,
        ip_address=client_ip(request),
    )
    header = ["id", "name", "run_type", "status", "mc_iterations", "created_at"]
    rows = (
        (
            str(r.id),
            r.name or "",
            r.run_type.value if hasattr(r.run_type, "value") else str(r.run_type),
            r.status.value if hasattr(r.status, "value") else str(r.status),
            r.mc_iterations,
            utc_isoformat(r.created_at),
        )
        for r in runs
    )
    return csv_response(filename="analyses.csv", header=header, rows_iter=rows)


@router.get("/analyses/new", response_class=HTMLResponse)
async def get_new_analysis_form(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ANALYST, UserRole.ADMIN)),
    prefill_scenario_id: uuid.UUID | None = None,
) -> HTMLResponse:
    """Unified new-analysis form. Multi-select scenarios; 1 -> SINGLE, 2+ -> AGGREGATE.

    RBAC: require_role(ANALYST, ADMIN) — reviewer rejected at dependency level.
    Alpine.js three-state controls picker (manuallyChecked / manuallyUnchecked
    overlay over the union of mitigating_controls for selected scenarios).
    """
    org_id = user.organization_id

    # Fetch scenarios with mitigating_controls eagerly loaded for the read-only
    # info panel (issue #89). list_for_org does not load the relationship.
    _scenario_stmt = (
        select(Scenario)
        .where(Scenario.organization_id == org_id)
        .options(selectinload(Scenario.mitigating_controls))
        .order_by(Scenario.updated_at.desc())
    )
    scenarios = list((await db.execute(_scenario_stmt)).scalars().all())

    # Embed per-scenario mitigating_controls map for Alpine.js reactivity.
    scenario_to_mitigating = {
        str(s.id): [str(c.id) for c in s.mitigating_controls] for s in scenarios
    }
    # Issue #89: build a UUID→display-name map covering scenarios + the controls
    # they reference. Used by the read-only info panel for chip labels. We don't
    # need the org-wide control list anymore — the panel only displays controls
    # that are configured on at least one scenario.
    name_by_id: dict[str, str] = {}
    for s in scenarios:
        name_by_id[str(s.id)] = s.name
        for c in s.mitigating_controls:
            name_by_id[str(c.id)] = c.name

    # Auto-generate a default run name so the operator never has to type one.
    # User feedback (2026-05-09): "analysis run names should be generated."
    from datetime import datetime

    default_run_name = f"Run {datetime.now():%Y-%m-%d %H:%M}"

    return templates.TemplateResponse(
        request,
        "analyses/new.html",
        {
            "current_user": user,
            "scenarios": scenarios,
            "scenario_to_mitigating": scenario_to_mitigating,
            "name_by_id": name_by_id,
            "prefill_scenario_ids": [str(prefill_scenario_id)] if prefill_scenario_id else [],
            "default_mc_iterations": get_settings().mc_iterations_default,
            "max_mc_iterations": get_settings().mc_iterations_max,
            # #508: the form's high-fidelity cost-warning trigger reads the same
            # server-side threshold as the concurrency cap, so an env override
            # can't desync the UI warning from the server gate.
            "high_fidelity_threshold": get_settings().high_fidelity_iterations_threshold,
            "default_random_seed": 42,
            "default_run_name": default_run_name,
        },
    )


@router.post("/analyses")
async def post_create_analysis(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ANALYST, UserRole.ADMIN)),
    scenario_ids: list[uuid.UUID] = Form(...),
    mc_iterations: int = Form(...),
    name: str | None = Form(default=None, max_length=200),
    random_seed: int = Form(default=42),
) -> Response:
    """Create + dispatch a run; return HX-Redirect to /runs/{id}.

    Issue #89: per-run override removed. Controls always derived from each
    scenario's own ``mitigating_controls``. A stray ``control_ids`` field
    in the form payload is silently ignored by FastAPI Form extraction.

    run_type discriminated by len(scenario_ids):
    - 1 -> SINGLE
    - 2+ -> AGGREGATE (per-scenario controls frozen on run row)

    CSRF: middleware-validated automatically via _csrf form field.
    """
    # UAT 2026-05-21 (issue #212): bound mc_iterations server-side so a
    # forged or hand-crafted form submission can't OOM-kill the worker.
    # The form's max= attribute is client-side only and trivially bypassed.
    iter_max = get_settings().mc_iterations_max
    if mc_iterations < 100 or mc_iterations > iter_max:
        raise HTTPException(
            status_code=422,
            detail=(
                f"mc_iterations must be between 100 and {iter_max:,} on this "
                f"deployment. Raise the MC_ITERATIONS_MAX env var to lift the cap."
            ),
        )
    service = RunService(db)
    try:
        run = await service.create_and_dispatch(
            organization_id=user.organization_id,
            scenario_ids=scenario_ids,
            mc_iterations_override=mc_iterations,
            created_by=user.id,
            background_tasks=background_tasks,
            name=name,
            random_seed=random_seed,
        )
    except ScenarioNotFoundError as exc:
        raise HTTPException(status_code=404) from exc
    except RunValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return Response(headers={"HX-Redirect": f"/runs/{run.id}"}, status_code=204)


@router.get(
    "/scenarios/{scenario_id}/run/new",
    response_class=RedirectResponse,
    status_code=303,
)
async def get_legacy_trigger_redirect(scenario_id: uuid.UUID) -> RedirectResponse:
    """PR xi: legacy modal trigger redirects to unified form (303 — GET)."""
    return RedirectResponse(
        url=f"/analyses/new?prefill_scenario_id={scenario_id}",
        status_code=303,
    )


@router.post("/scenarios/{scenario_id}/run")
async def post_legacy_trigger_adapter(
    scenario_id: uuid.UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ANALYST, UserRole.ADMIN)),
    name: str | None = Form(default=None, max_length=200),
) -> Response:
    """PR xi adapter: legacy POST reads form fields server-side, calls
    create_and_dispatch with scenario_ids=[scenario_id] (1-element list
    discriminates to SINGLE).

    Issue #89: per-run control override removed. A stray ``control_ids``
    field in the form payload is silently ignored.

    Per R2-NB1: background_tasks BEFORE Depends-defaulted args to prevent
    Python SyntaxError (non-default arg after default arg).
    """
    form = await request.form()
    try:
        raw_iters = form.get("mc_iterations")
        if raw_iters is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "mc_iterations is required (legacy adapter; PR π removed Scenario default)."
                ),
            )
        mc_iterations = int(str(raw_iters))
        raw_seed = form.get("random_seed")
        random_seed = 42 if raw_seed is None else int(str(raw_seed))
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    service = RunService(db)
    try:
        run = await service.create_and_dispatch(
            organization_id=user.organization_id,
            scenario_ids=[scenario_id],
            mc_iterations_override=mc_iterations,
            created_by=user.id,
            background_tasks=background_tasks,
            name=name,
            random_seed=random_seed,
        )
    except ScenarioNotFoundError as exc:
        raise HTTPException(status_code=404) from exc
    except RunValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return Response(
        status_code=204,
        headers={"HX-Redirect": f"/runs/{run.id}"},
    )
