"""Control CRUD routes.

GET /controls and GET /controls/{id} are open to any authenticated user
(``require_user``); writes are restricted to admin + analyst via
``require_role`` — reviewer/viewer cannot mutate. That's the intended
RBAC split for milestone 1.2.

``ip_address=client_ip(request)`` is threaded into every service write
so the AuditLog row carries the originating client IP — same 1.1.6.a I2
invariant the org/users routes hold. Service writes audit rows
internally; routes do not call ``AuditWriter`` directly.

Transaction commit is owned by the ``get_db`` dependency
(``routes/deps.py::get_db`` + ``db.py::get_session``). Handlers do NOT
call ``await db.commit()`` directly — same pattern as
``routes/organization.py``.
"""

from __future__ import annotations

import logging
import re
import uuid
from types import SimpleNamespace

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.app import templates
from idraa.errors import LibraryEntryNotFoundError, LibraryEntryStatusError
from idraa.models.control import Control
from idraa.models.enums import (
    SUB_FUNCTION_UNITS,
    ControlDomain,
    ControlImplementationStage,
    ControlSource,
    ControlType,
    EntityStatus,
    FairCamSubFunction,
    UserRole,
)
from idraa.models.user import User
from idraa.routes._htmx import is_htmx_request
from idraa.routes.deps import (
    MAX_UPLOAD_BYTES,
    client_ip,
    get_db,
    require_recent_auth,
    require_role,
    require_user,
)
from idraa.schemas.control import ControlForm
from idraa.services import controls as svc
from idraa.services.audit import log_bulk_export
from idraa.services.control_resync import apply_resync, resync_info
from idraa.services.controls_importer import import_csv
from idraa.services.flash import build_flash
from idraa.services.org import require_sole_org
from idraa.utils.csv_export import csv_response

logger = logging.getLogger(__name__)

router = APIRouter()

# Sub-functions exposed in the form dropdown. Excludes virtual functions
# that require derived_from_assignment_id (reserved-but-unused in PR lambda).
_FORM_SUB_FUNCTIONS = [sf for sf in FairCamSubFunction if sf.value != "dsc_corr_misaligned"]

# Server-set fields stripped from form input before Pydantic validation.
# These are owned by the service layer (set from authenticated user_id +
# now()) — analyst form submissions claiming these would be silently
# overridden by create_control / update_control. Pop before model_validate
# to make the contract explicit and prevent confusion.
# derived_from_assignment_id is reserved-but-unused (spec Decision 9 / B-NEW3).
_ASSIGNMENT_SERVER_SET_FIELDS = frozenset(
    {
        "confirmed_by_user_at",
        "measured_by",
        "measured_at",
        "derived_from_assignment_id",
    }
)


def _format_import_flash(
    *,
    imported: int,
    skipped: int,
    zero_cost_count: int,
    unconfirmed_count: int,
) -> str:
    """Build the post-import flash message, dropping zero-count clauses.

    Issue #133: pre-fix the message always rendered both maintenance
    clauses, producing dead "0 controls need annual cost set" text after
    PR #132 made the canonical library fully priced.
    """
    base = f"Imported {imported + skipped} controls ({imported} created, {skipped} skipped)."
    clauses: list[str] = []
    if zero_cost_count > 0:
        clauses.append(f"{zero_cost_count} controls need annual cost set")
    if unconfirmed_count > 0:
        clauses.append(f"{unconfirmed_count} assignments need confirmation")
    if not clauses:
        return base
    return f"{base} {' and '.join(clauses)}."


async def _parse_control_form_dict(request: Request) -> dict[str, object]:
    """Parse FastAPI form data with assignments[N][field] convention.

    HTML forms use repeating bracketed-index field names. Convert to
    nested dict shape that ControlForm.model_validate consumes:
        assignments[0][sub_function]=foo  →  {"assignments": [{"sub_function": "foo"}]}

    Strips server-set fields from each assignment row (defense in depth):
    even if a malicious or confused form submits these, they are dropped
    before Pydantic sees them.
    """
    form = await request.form()
    flat = dict(form)

    # Extract top-level fields
    out: dict[str, object] = {k: v for k, v in flat.items() if not k.startswith("assignments[")}

    # Group assignments[N][...] by index, stripping server-set fields
    assignments: dict[int, dict[str, object]] = {}
    pat = re.compile(r"^assignments\[(\d+)\]\[(\w+)\]$")
    for k, v in flat.items():
        m = pat.match(k)
        if m:
            field = m.group(2)
            if field in _ASSIGNMENT_SERVER_SET_FIELDS:
                continue  # silently drop — server owns these
            idx = int(m.group(1))
            assignments.setdefault(idx, {})[field] = v

    out["assignments"] = [assignments[i] for i in sorted(assignments)]

    # annual_cost passes through to Pydantic as-is when present; empty
    # submissions drop the key so Pydantic's default Decimal("0") fills.
    # Non-numeric strings fall through to Pydantic's ValidationError path
    # (existing 422-error rendering).
    annual_cost_raw = out.pop("annual_cost", None)
    if annual_cost_raw is not None:
        annual_cost_str = str(annual_cost_raw).strip()
        if annual_cost_str:
            out["annual_cost"] = annual_cost_str

    return out


def _extract_form_assignments(form_dict: dict) -> list[dict]:  # type: ignore[type-arg]
    """For re-rendering form on validation error — extract assignment dicts."""
    raw: object = form_dict.get("assignments", [])
    return raw if isinstance(raw, list) else []


def _format_errors(exc: ValidationError) -> dict[str, str | list[str]]:
    """Pydantic ValidationError → flat per-field error dict for templates.

    Model-level validator errors (raised from ``@model_validator(mode="after")``)
    are promoted to _global so they render above the assignments fieldset
    rather than alongside a per-field error.

    Implementation note (paranoid-review-verified): Pydantic v2 emits
    ``loc=()`` (empty tuple) for ``model_validator(mode="after")`` raised
    ValueErrors — NOT ``("assignments",)`` even though the validator
    inspects the assignments list. Use empty-tuple loc + value_error type
    for the cross-field promotion check.
    """
    out: dict[str, str | list[str]] = {}
    global_msgs: list[str] = []
    for err in exc.errors():
        loc_path = err["loc"]
        msg = err["msg"]
        # Model-level validator (cross-field): loc=() + err type "value_error"
        # → promote to _global so it doesn't get rendered under an empty key.
        if not loc_path and err["type"].startswith("value_error"):
            global_msgs.append(msg)
            continue
        loc = ".".join(str(p) for p in loc_path)
        out[loc] = msg
    if global_msgs:
        out["_global"] = global_msgs
    return out


def _render_form_with_errors(
    request: Request,
    *,
    user: User,
    control: Control | None,
    form_data: dict,  # type: ignore[type-arg]
    errors: dict,  # type: ignore[type-arg]
    existing_assignments: list,  # type: ignore[type-arg]
    status_code: int = 422,
) -> Response:
    return templates.TemplateResponse(
        request,
        "controls/form.html",
        {
            "current_user": user,
            "control": control,
            "form_data": form_data,
            "existing_assignments": existing_assignments,
            "errors": errors,
            "type_choices": list(ControlType),
            "status_choices": list(EntityStatus),
            "stage_choices": list(ControlImplementationStage),
            "sub_function_choices": _FORM_SUB_FUNCTIONS,
            "flash": None,
        },
        status_code=status_code,
    )


def _maintenance_response(request: Request | None = None) -> Response:
    """Per-request 503 factory. Accepts request=None for importer/migration callsites.

    Branches on HTMX-vs-non-HTMX:
      - HTMX request (HX-Request: true): renders the fragment-only
        controls/_maintenance.html and sets HX-Reswap: outerHTML so HTMX
        replaces the matched element with the fragment.
      - Non-HTMX request (direct browser nav): renders a full-page response
        wrapping the same alert in the base layout.

    (spec §9.1, §B-NEW1; F18 hygiene fix)
    """
    if request is None:
        return Response(status_code=503, content=b"Controls maintenance in progress")
    if is_htmx_request(request):
        response = templates.TemplateResponse(
            request,
            "controls/_maintenance.html",
            {},
            status_code=503,
        )
        response.headers["HX-Reswap"] = "outerHTML"
        return response
    # Non-HTMX direct nav: full-page response.
    return templates.TemplateResponse(
        request,
        "controls/maintenance_unavailable.html",
        {},
        status_code=503,
    )


@router.get("/controls", response_class=HTMLResponse)
async def controls_list(
    request: Request,
    domain: str | None = Query(
        default=None,
        description=(
            "Filter by FAIR-CAM domain (loss_event / variance_management / decision_support)."
        ),
    ),
    source: str | None = Query(
        default=None,
        description="Filter by control provenance (custom / library_derived).",
    ),
    imported: int | None = Query(
        default=None,
        ge=0,
        le=10_000,
        description=(
            "Issue #152: post-import flash counter. Set by POST /controls/import "
            "redirect; rendered as a 'success' banner here. Bounded matches "
            "controls_importer.MAX_CSV_ROWS."
        ),
    ),
    skipped: int | None = Query(
        default=None,
        ge=0,
        le=10_000,
        description="Issue #152: post-import flash counter (skipped row count).",
    ),
    deleted: int | None = Query(
        default=None,
        ge=0,
        le=1,
        description=(
            "Issue #154: post-delete flash flag. Set to 1 by the soft-delete "
            "POST redirect; rendered as a 'success' banner here."
        ),
    ),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),
) -> HTMLResponse:
    org = await require_sole_org(db)
    # Optional ?domain=<slug> filter (issue #90). Declared as a FastAPI Query
    # parameter (untyped string) for OpenAPI visibility, BUT validated
    # manually against ControlDomain so we can:
    #   - return a generic 400 "unknown domain" instead of FastAPI's default
    #     422 (which would echo the offending value in the error response;
    #     plan-gate fix Sec-I3 prohibits that),
    #   - log the offending input server-side at INFO level, truncated to
    #     32 chars for ops debugging without unbounded log growth.
    domain_filter: ControlDomain | None = None
    if domain:
        try:
            domain_filter = ControlDomain(domain)
        except ValueError as err:
            logger.info(
                "controls.list: unknown domain query param",
                extra={"q_domain": domain[:32]},
            )
            raise HTTPException(status_code=400, detail="unknown domain") from err

    # Optional ?source=<slug> provenance filter (P2b Task 9). Same manual-
    # validation pattern as ?domain= above: generic 400 that does NOT echo
    # the offending value, with a truncated INFO log for ops.
    source_filter: ControlSource | None = None
    if source:
        try:
            source_filter = ControlSource(source)
        except ValueError as err:
            logger.info(
                "controls.list: unknown source query param",
                extra={"q_source": source[:32]},
            )
            raise HTTPException(status_code=400, detail="unknown source") from err

    controls = await svc.list_controls(
        db, org_id=org.id, domain=domain_filter, source=source_filter
    )

    # Issue #152: query-string flash for post-import feedback. Both counts
    # must be present (defends against partial / handcrafted URLs). Uses the
    # same _format_import_flash helper as the library-import flow so the
    # message format is consistent across both import paths.
    flash = None
    if imported is not None and skipped is not None:
        from idraa.services.controls_maintenance import maintenance_summary

        summary = await maintenance_summary(db, org.id)
        flash_msg = _format_import_flash(
            imported=imported,
            skipped=skipped,
            zero_cost_count=summary.zero_cost_controls_count,
            unconfirmed_count=summary.unconfirmed_assignments_count,
        )
        flash = build_flash(
            flash_msg,
            "success" if imported > 0 or skipped == 0 else "warning",
            href="/controls/maintenance" if summary.unconfirmed_assignments_count > 0 else None,
            href_text="Open Maintenance" if summary.unconfirmed_assignments_count > 0 else None,
        )
    elif deleted == 1:
        # Issue #154: post-delete flash. The list page already shows the
        # deleted item is gone — this banner is positive confirmation.
        flash = build_flash("Deleted control.", "success")

    return templates.TemplateResponse(
        request,
        "controls/list.html",
        {
            "current_user": user,
            "flash": flash,
            "controls": controls,
            "ControlSource": ControlSource,
            "active_source": source_filter.value if source_filter else None,
        },
    )


@router.get("/controls/new", response_class=HTMLResponse)
async def control_new_get(
    request: Request,
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.ANALYST)),
    db: AsyncSession = Depends(get_db),
) -> Response:
    org = await require_sole_org(db)
    _ = org  # org fetched to validate sole-org invariant; not needed in template
    return templates.TemplateResponse(
        request,
        "controls/form.html",
        {
            "current_user": user,
            "control": None,
            "form_data": None,
            "existing_assignments": [],
            "errors": {},
            "type_choices": list(ControlType),
            "status_choices": list(EntityStatus),
            "stage_choices": list(ControlImplementationStage),
            "sub_function_choices": _FORM_SUB_FUNCTIONS,
            "flash": None,
        },
    )


@router.post("/controls/new")
async def control_new_post(
    request: Request,
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.ANALYST)),
    db: AsyncSession = Depends(get_db),
) -> Response:
    org = await require_sole_org(db)
    form_dict = await _parse_control_form_dict(request)
    try:
        form = ControlForm.model_validate(form_dict)
    except ValidationError as e:
        # Log validation failures in dev/UAT so we can see what fields the
        # user is missing even when the template-side error rendering is
        # incomplete. INFO level keeps it visible without flooding prod.
        formatted = _format_errors(e)
        logger.info(
            "control_new validation failed org=%s user=%s errors=%s",
            org.id,
            user.id,
            formatted,
        )
        return _render_form_with_errors(
            request,
            user=user,
            control=None,
            form_data=form_dict,
            errors=formatted,
            existing_assignments=_extract_form_assignments(form_dict),
            status_code=422,
        )

    try:
        control = await svc.create_control(
            db,
            org_id=org.id,
            user_id=user.id,
            form=form,
            ip_address=client_ip(request),
        )
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        # Do NOT leak str(e.orig) — that includes raw DB internals (table
        # names, constraint names, SQL fragments) per CWE-209. Emit a fixed
        # human-friendly message; log the underlying error server-side for
        # ops debugging.
        logger.warning(
            "Control create IntegrityError org=%s user=%s: %s",
            org.id,
            user.id,
            e.orig,
        )
        return _render_form_with_errors(
            request,
            user=user,
            control=None,
            form_data=form_dict,
            errors={
                "_global": [
                    "A control with this name already exists, or another constraint was violated."
                ]
            },
            existing_assignments=_extract_form_assignments(form_dict),
            status_code=409,
        )

    if "HX-Request" in request.headers:
        return Response(status_code=204, headers={"HX-Redirect": f"/controls/{control.id}"})
    return RedirectResponse(f"/controls/{control.id}", status_code=303)


@router.get("/controls/import", response_class=HTMLResponse)
async def controls_import_get(
    request: Request,
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "controls/import.html",
        {"current_user": user, "flash": None},
    )


@router.post("/controls/import")
async def controls_import_post(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
    file: UploadFile = File(...),
) -> Response:
    # Reject oversized uploads before spilling into memory. Content-Length
    # is client-supplied and forgeable — the post-read length check below
    # catches liars.
    content_length = request.headers.get("content-length")
    if (
        content_length is not None
        and content_length.isdigit()
        and int(content_length) > MAX_UPLOAD_BYTES
    ):
        raise HTTPException(status_code=413, detail="Upload too large (max 5 MB)")
    csv_bytes = await file.read()
    if len(csv_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Upload too large (max 5 MB)")
    org = await require_sole_org(db)
    imported, skipped = await import_csv(
        db,
        org_id=org.id,
        user_id=user.id,
        csv_bytes=csv_bytes,
        ip_address=client_ip(request),
    )
    # Transaction commit owned by get_db dependency.
    # Issue #152: piggyback the (imported, skipped) counts on the redirect
    # query string so GET /controls can render a flash. Matches the
    # query-string flash pattern at routes/organization.py:82-90 ("project
    # doesn't have session-stored flash; this is the lightest pattern
    # that still gives the user a 'Saved' confirmation without breaking
    # POST-redirect-GET. Self-clears on next refresh.")
    return RedirectResponse(f"/controls?imported={imported}&skipped={skipped}", status_code=303)


@router.get("/controls/maintenance", response_class=HTMLResponse)
async def controls_maintenance(
    request: Request,
    confirmed: int | None = Query(
        default=None,
        ge=0,
        le=1,
        description=(
            "Issue #154: post-confirm flash flag (non-HTMX callers only). "
            "HTMX callers receive an HX-Trigger event instead."
        ),
    ),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),
) -> Response:
    """List unconfirmed assignments + $0-cost controls. (issue #87, spec §B-NEW1)"""
    from idraa.models.control_function_assignment import ControlFunctionAssignment
    from idraa.models.enums import subfunction_to_domain
    from idraa.services.controls_maintenance import maintenance_summary

    org = await require_sole_org(db)
    summary = await maintenance_summary(db, org.id)

    groups: dict[str, list[ControlFunctionAssignment]] = {}
    for a in summary.unconfirmed_assignments:
        # Each assignment belongs to exactly ONE FAIR-CAM domain -- the domain of
        # its OWN sub-function -- NOT every domain its (possibly multi-domain)
        # parent control spans. The prior `for d in a.control.domains` form
        # cross-posted each assignment into every section the control touched, so
        # a multi-domain control's rows appeared duplicated across the LEC/VMC/DSC
        # tables. Grouping per-assignment still satisfies #90 (a multi-domain
        # control surfaces under each domain it spans -- via its respective
        # assignments) without duplicating any single row. The template iterates
        # fixed scalar keys ("loss_event", "variance_management", "decision_support").
        groups.setdefault(subfunction_to_domain(a.sub_function).value, []).append(a)

    # Issue #154: non-HTMX post-confirm flash. HTMX callers get an
    # HX-Trigger event from the confirm route; non-HTMX callers land here
    # via a 303 with ?confirmed=1 and need a visible banner.
    flash = build_flash("Assignment confirmed.", "success") if confirmed == 1 else None

    return templates.TemplateResponse(
        request,
        "controls/maintenance.html",
        {
            "current_user": user,
            "flash": flash,
            "groups": groups,
            "zero_cost_controls": summary.zero_cost_controls,
            "zero_cost_count": summary.zero_cost_controls_count,
            "unconfirmed_count": summary.unconfirmed_assignments_count,
            "total_count": summary.total_needs_attention,
            "sub_function_units": SUB_FUNCTION_UNITS,
        },
    )


@router.post("/controls/{control_id}/duplicate")
async def control_duplicate(
    request: Request,
    control_id: uuid.UUID,
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.ANALYST)),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Clone an existing control and redirect to the new control's edit page.

    Duplicates the source control (including assignments) via
    duplicate_control, then 303-redirects to /controls/{clone.id}/edit
    so the user can rename and adjust the copy. (spec §F10 Step 1)
    """
    org = await require_sole_org(db)
    source = await svc.get_control(db, control_id)
    if source is None or source.organization_id != org.id or source.status == EntityStatus.DELETED:
        raise HTTPException(status_code=404)

    clone = await svc.duplicate_control(
        db, control=source, user_id=user.id, ip_address=client_ip(request)
    )
    await db.commit()

    return RedirectResponse(f"/controls/{clone.id}/edit", status_code=303)


@router.post("/controls/library/{entry_id}/adopt", response_class=HTMLResponse)
async def control_adopt_from_library(
    entry_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.ANALYST)),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Adopt (clone-snapshot) a published library entry into a new editable control.

    Analyst+ ({ADMIN, ANALYST}); reviewer/viewer rejected (403). The new control
    lands with source=LIBRARY_DERIVED + a library_pin and UNCONFIRMED assignments;
    we redirect to its detail page so the org reviews/tunes/confirms them.
    """
    org = await require_sole_org(db)
    # P2c: optional wizard-return. Validate to the wizard tx grammar (UUID4) BEFORE
    # adopting, so a malformed tx fails fast with a clean 400 and adopts nothing
    # (Sec-I1/Sec-N1). The wizard's only consumer (scenarios.py:_resolve_tx) does
    # uuid.UUID(tx_str) unguarded — a tx passing a loose allowlist but not a real
    # UUID would adopt, redirect, then 500 there. Match the UUID grammar exactly.
    form = await request.form()
    from_wizard_tx = form.get("from_wizard_tx")
    if from_wizard_tx is not None and from_wizard_tx != "":
        if not isinstance(from_wizard_tx, str):
            raise HTTPException(status_code=400, detail="invalid tx")
        try:
            # Normalize to canonical 8-4-4-4-12 hex. uuid.UUID() also accepts the
            # urn:uuid:/{...} forms; str() canonicalizes so the redirect value is
            # always pure hex+hyphen (no :,{,} ever reach the path).
            from_wizard_tx = str(uuid.UUID(from_wizard_tx))
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid tx") from None
    else:
        from_wizard_tx = None
    try:
        control = await svc.adopt_from_library(
            db,
            org_id=org.id,
            user_id=user.id,
            entry_id=entry_id,
            version=None,  # Spec-6: latest published; no version-pinning UI this phase
            ip_address=client_ip(request),
        )
        await db.commit()
    except (LibraryEntryNotFoundError, LibraryEntryStatusError) as exc:
        # Sec-I1: catch BOTH — constant 404, no status-vs-existence oracle.
        # Catching both survives any future TOCTOU re-fetch that raises the
        # status error.
        raise HTTPException(status_code=404, detail="Library entry not available") from exc
    # tx already validated + canonicalized to pure hex+hyphen → interpolated only
    # into the query-value position of a fixed, server-authored path. Not an open
    # redirect (a canonical UUID can contain no /,?,#,:,&,{ or CR/LF).
    target = (
        f"/scenarios/new/wizard/step/5?tx={from_wizard_tx}"
        if from_wizard_tx
        else f"/controls/{control.id}"
    )
    if request.headers.get("HX-Request"):
        resp = Response(status_code=204)
        resp.headers["HX-Redirect"] = target
        return resp
    return RedirectResponse(target, status_code=303)


@router.post("/controls/{control_id}/assignments/{assignment_id}/confirm")
async def confirm_assignment_route(
    control_id: uuid.UUID,
    assignment_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.ANALYST)),
) -> Response:
    """Confirm a ControlFunctionAssignment — set confirmed_by_user_at = now().

    Verifies the assignment belongs to control_id (404 if mismatched).
    Re-confirmation is permitted: each confirm stamps a fresh audit row
    capturing the prior confirmed_by_user_at so the trail is non-misleading.

    HTMX callers: receive 200 + empty body + HX-Trigger: confirmationDone.
    Issue #159: previously returned 204, but HTMX 1.9.x silently skips the
    swap on 204 (regardless of hx-swap directive), so the clicked
    confirm row stayed visible until manual refresh. Returning 200 with
    empty body lets hx-swap="outerHTML" remove the row cleanly. HX-Trigger
    is preserved for any downstream listeners (badge refresh, etc.).
    Non-HTMX callers: receive 303 redirect to /controls/maintenance.
    (spec §7.2, §5.3, OQ4)
    """
    from idraa.models.control_function_assignment import ControlFunctionAssignment

    org = await require_sole_org(db)
    control = await svc.get_control(db, control_id)
    if control is None or control.organization_id != org.id:
        raise HTTPException(status_code=404, detail="Assignment not found")

    assignment = await db.get(ControlFunctionAssignment, assignment_id)
    if assignment is None or assignment.control_id != control_id:
        raise HTTPException(status_code=404, detail="Assignment not found")

    await svc.confirm_assignment(
        db,
        assignment=assignment,
        user_id=user.id,
        ip_address=client_ip(request),
    )

    if is_htmx_request(request):
        return Response(
            content=b"",
            status_code=200,
            headers={"HX-Trigger": "confirmationDone"},
        )
    # Issue #154: non-HTMX (JS-disabled / curl-style) callers need a flash
    # since they hit a fresh page-load, not an HX-Trigger event. Matches
    # the ?saved=1 pattern at routes/organization.py:82-90.
    return RedirectResponse("/controls/maintenance?confirmed=1", status_code=303)


@router.get("/controls/_assignment_row", response_class=HTMLResponse)
async def control_assignment_row_partial(
    request: Request,
    index: int = Query(0, ge=0, le=100),  # Sec-N2: defensive upper bound on caller-supplied index
    sub_function: str | None = None,
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.ANALYST)),
) -> Response:
    """HTMX partial: render a single assignment row.

    Two call paths:

    * "+ Add assignment" click — no ``sub_function`` query param, returns a
      blank row with the legacy default capability widget (spec §F10 Step 3).
    * Sub-function ``<select>`` change — ``sub_function`` query param drives
      unit-aware widget rendering via the ``unit_input`` macro. PR μ.1b
      (#129 T5) adds this path so the capability input reflects the
      sub-function's UnitType (PROBABILITY / ELAPSED_TIME / CURRENCY /
      PERCENT_REDUCTION) the instant the user picks a sub-function.

    Unknown ``sub_function`` → 400. Avoids the FastAPI 500 path that a
    bare enum coercion would produce on garbage input.
    """
    assignment: SimpleNamespace | None = None
    if sub_function is not None:
        try:
            sf_enum = FairCamSubFunction(sub_function)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="unknown sub_function") from exc
        # Stub assignment whose .sub_function attribute drives the macro's
        # SUB_FUNCTION_UNITS dispatch. capability_value=None renders an
        # empty input; coverage/reliability defaults match the blank-row
        # convention from form.html (assignment=None branch uses 0.8).
        assignment = SimpleNamespace(
            sub_function=sf_enum,
            capability_value=None,
            coverage=0.8,
            reliability=0.8,
        )

    return templates.TemplateResponse(
        request,
        "controls/_assignment_row.html",
        {
            "index": index,
            "assignment": assignment,
            "sub_function_choices": _FORM_SUB_FUNCTIONS,
        },
    )


@router.get("/controls/export.csv", dependencies=[Depends(require_recent_auth)])
async def controls_export_csv(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),
) -> Response:
    """Stream all controls for the current org as a CSV download.

    Plan-gate Arch-2: registered BEFORE /controls/{control_id} so FastAPI's
    declaration-order match does not route "export.csv" to the UUID parser.

    Plan-gate Sec-3: scoped by org from require_sole_org (same single-org
    invariant used throughout this module) — cross-org IDOR is not possible
    because list_controls takes an explicit org_id predicate applied BEFORE
    any JOIN (see services/controls.py).
    """
    org = await require_sole_org(db)
    controls = await svc.list_controls(db, org_id=org.id)
    # #304: bulk egress audit row.
    await log_bulk_export(
        db,
        organization_id=org.id,
        entity_type="control",
        fmt="csv",
        count=len(controls),
        user_id=user.id,
        ip_address=client_ip(request),
    )
    header = ["id", "name", "type", "status", "implementation_stage", "domains", "sub_function"]
    rows = (
        (
            str(c.id),
            c.name,
            c.type.value if hasattr(c.type, "value") else str(c.type),
            c.status.value if hasattr(c.status, "value") else str(c.status),
            c.implementation_stage.value
            if hasattr(c.implementation_stage, "value")
            else str(c.implementation_stage),
            "|".join(
                sorted(d.value if hasattr(d, "value") else str(d) for d in (c.domains or set()))
            ),
            "|".join(
                sorted(
                    a.sub_function.value
                    if hasattr(a.sub_function, "value")
                    else str(a.sub_function)
                    for a in (c.assignments or [])
                )
            ),
        )
        for c in controls
    )
    return csv_response(filename="controls.csv", header=header, rows_iter=rows)


@router.get("/controls/nist-suggest", response_class=HTMLResponse)
async def control_nist_suggest(
    request: Request,
    nist_csf_functions: str = "",
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.ANALYST)),
) -> HTMLResponse:
    """#144 — HTMX partial: crosswalk-grounded FAIR-CAM function suggestions
    for the NIST CSF codes typed into the control form. Informational
    (validate-not-derive): assignments remain the analyst's explicit choice.

    Declared BEFORE the /controls/{control_id} routes so the literal path
    segment wins over the UUID path-param match.
    """
    from idraa.schemas._csv import split_csv
    from idraa.services.crosswalk import CrosswalkService, MultipleVersionsError

    codes = split_csv(nist_csf_functions)
    svc_cw = CrosswalkService(db)
    try:
        known = set(await svc_cw.codes_for("nist_csf")) if codes else set()
        recognized = [c for c in codes if c in known]
        unknown = [c for c in codes if c not in known]
        suggested = sorted(
            fn.value for fn in await svc_cw.faircam_functions_for("nist_csf", recognized)
        )
    except MultipleVersionsError:
        # A 2nd seeded nist_csf version makes version-less lookups ambiguous —
        # degrade to no suggestions on this informational panel rather than 500
        # (the crosswalk gate itself pins versioned behavior).
        recognized, unknown, suggested = [], [], []
    return templates.TemplateResponse(
        request,
        "controls/_nist_suggest.html",
        {"recognized": recognized, "unknown": unknown, "suggested": suggested},
    )


@router.get("/controls/{control_id}", response_class=HTMLResponse)
async def control_detail(
    control_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),
) -> HTMLResponse:
    org = await require_sole_org(db)
    c = await svc.get_control(db, control_id)
    if c is None or c.organization_id != org.id or c.status is EntityStatus.DELETED:
        raise HTTPException(404)
    resync = await resync_info(db, c)  # #438: None for custom controls
    return templates.TemplateResponse(
        request,
        "controls/detail.html",
        {"current_user": user, "flash": None, "c": c, "resync": resync},
    )


@router.get("/controls/{control_id}/resync", response_class=HTMLResponse)
async def control_resync_review(
    control_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.ANALYST)),
) -> HTMLResponse:
    """#438 — the re-sync review page: what changed in the library entry vs
    what the analyst changed locally, BEFORE the destructive apply."""
    org = await require_sole_org(db)
    c = await svc.get_control(db, control_id)
    if c is None or c.organization_id != org.id or c.status is EntityStatus.DELETED:
        raise HTTPException(404)
    info = await resync_info(db, c)
    if info is None or not info.stale:
        # Nothing to review — bounce back to the control.
        return RedirectResponse(f"/controls/{control_id}", status_code=303)  # type: ignore[return-value]
    return templates.TemplateResponse(
        request,
        "controls/resync.html",
        {"current_user": user, "flash": None, "c": c, "info": info},
    )


@router.post("/controls/{control_id}/resync", response_class=HTMLResponse)
async def control_resync_apply(
    control_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.ANALYST)),
) -> Response:
    """#438 — apply the re-sync (the review page is the consent step)."""
    org = await require_sole_org(db)
    c = await svc.get_control(db, control_id)
    if c is None or c.organization_id != org.id or c.status is EntityStatus.DELETED:
        raise HTTPException(404)
    try:
        flagged = await apply_resync(db, c, user_id=user.id, ip_address=client_ip(request))
        await db.commit()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RedirectResponse(
        f"/controls/{control_id}?resynced=1&stale_runs={flagged}", status_code=303
    )


@router.get("/controls/{control_id}/edit", response_class=HTMLResponse)
async def control_edit_get(
    control_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.ANALYST)),
    db: AsyncSession = Depends(get_db),
) -> Response:
    org = await require_sole_org(db)
    control = await svc.get_control(db, control_id)
    if (
        control is None
        or control.organization_id != org.id
        or control.status == EntityStatus.DELETED
    ):
        raise HTTPException(status_code=404)

    return templates.TemplateResponse(
        request,
        "controls/form.html",
        {
            "current_user": user,
            "control": control,
            "form_data": None,
            "existing_assignments": list(control.assignments or []),
            "errors": {},
            "type_choices": list(ControlType),
            "status_choices": list(EntityStatus),
            "stage_choices": list(ControlImplementationStage),
            "sub_function_choices": _FORM_SUB_FUNCTIONS,
            "flash": None,
        },
    )


@router.post("/controls/{control_id}/edit")
async def control_edit_post(
    control_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.ANALYST)),
    db: AsyncSession = Depends(get_db),
) -> Response:
    org = await require_sole_org(db)
    control = await svc.get_control(db, control_id)
    if (
        control is None
        or control.organization_id != org.id
        or control.status == EntityStatus.DELETED
    ):
        raise HTTPException(status_code=404)

    form_dict = await _parse_control_form_dict(request)
    try:
        form = ControlForm.model_validate(form_dict)
    except ValidationError as e:
        return _render_form_with_errors(
            request,
            user=user,
            control=control,
            form_data=form_dict,
            errors=_format_errors(e),
            existing_assignments=_extract_form_assignments(form_dict),
            status_code=422,
        )

    try:
        await svc.update_control(
            db,
            control=control,
            user_id=user.id,
            form=form,
            ip_address=client_ip(request),
        )
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        logger.warning(
            "Control update IntegrityError org=%s control=%s user=%s: %s",
            org.id,
            control.id,
            user.id,
            e.orig,
        )
        return _render_form_with_errors(
            request,
            user=user,
            control=control,
            form_data=form_dict,
            errors={
                "_global": [
                    "A control with this name already exists, or another constraint was violated."
                ]
            },
            existing_assignments=_extract_form_assignments(form_dict),
            status_code=409,
        )

    if "HX-Request" in request.headers:
        return Response(status_code=204, headers={"HX-Redirect": f"/controls/{control.id}"})
    return RedirectResponse(f"/controls/{control.id}", status_code=303)


@router.post("/controls/{control_id}/delete", dependencies=[Depends(require_recent_auth)])
async def control_delete(
    control_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.ANALYST)),
) -> Response:
    org = await require_sole_org(db)
    c = await svc.get_control(db, control_id)
    if c is None or c.organization_id != org.id or c.status is EntityStatus.DELETED:
        raise HTTPException(404)
    await svc.soft_delete_control(
        db,
        c,
        user_id=user.id,
        ip_address=client_ip(request),
    )
    # Transaction commit owned by get_db dependency.
    # Issue #154: query-string flash so the list page can confirm the delete.
    return RedirectResponse("/controls?deleted=1", status_code=303)
