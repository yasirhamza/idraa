"""Overlay CRUD routes — admin-only writes; analyst/reviewer read-only.

Mirrors the shape of :mod:`idraa.routes.controls` — uses
``require_user`` for reads and ``require_role(UserRole.ADMIN)`` for
writes. ``ip_address=client_ip(request)`` is threaded into every service
call so AuditLog rows carry the originating client IP (1.1.6.a I2
invariant).

Preamble fold-ins (post-paranoid-review, plan lines 21-78):

- **B7** Form parsing pulls each numeric field out of ``raw`` and runs
  it through ``float()`` / ``int()`` inside an explicit try/except, so
  Pydantic does not silently coerce a stringy ``"not_a_number"`` into a
  validation error with a less informative shape. Mirrors the
  ``_parse_form`` precedent in :mod:`idraa.routes.controls`.
- **B8** Edit form carries a hidden ``expected_version`` input (rendered
  by the template). The POST handler reads it, casts to int, passes to
  :meth:`OverlayService.update`. On
  :class:`OverlayVersionConflictError`, render 409 with a
  "reload-and-retry" message — never 500, never a generic 422.
- **B9 / B10** ``OverlayRepo.get_for_org(id, organization_id)`` is used
  for every detail / edit / deactivate handler. Cross-org IDs return
  None → 404 (NOT 403) to avoid an existence oracle.
- **B11** Forms render ``{{ csrf_field() }}`` (template side; the global
  is :class:`markupsafe.Markup`-typed so no ``|safe`` filter is needed);
  the CSRFMiddleware enforces double-submit on every unsafe method.
- **B13** CSV import is two-step: ``POST /overlays/import`` validates +
  stashes the bytes under a 10-min token and renders preview;
  ``POST /overlays/import/confirm`` re-parses the stored bytes and
  upserts. ``PreviewExpiredError`` → 409 ("preview no longer valid —
  please re-upload"). The single-step CSV-import API never shipped —
  this module wires only the two-step ``validate_csv`` /
  ``apply_validated_preview`` flow per B13.
- **MAX_UPLOAD_BYTES** Both Content-Length pre-check AND post-read
  length check at the upload boundary (Content-Length is forgeable —
  belt-and-suspenders mirrors :mod:`idraa.routes.controls`).
- **render err["msg"] only** Validation errors are flattened to the
  ``msg`` field of each Pydantic error dict — never the full dict-repr
  with ``type``/``input``/``url`` keys, which would leak Pydantic
  internals into rendered HTML.

Transaction commit is owned by the ``get_db`` dependency
(:mod:`idraa.routes.deps`). Handlers never call
``await db.commit()`` directly — same pattern as
:mod:`idraa.routes.organization` / :mod:`idraa.routes.controls`.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.app import templates
from idraa.models.enums import UserRole
from idraa.models.overlay import OverlayDefinition
from idraa.models.user import User
from idraa.repositories.overlay_repo import OverlayRepo
from idraa.routes.deps import (
    MAX_UPLOAD_BYTES,
    client_ip,
    get_db,
    require_recent_auth,
    require_role,
    require_user,
)
from idraa.schemas.overlay import OverlayDeactivateForm, OverlayForm
from idraa.services.audit import log_bulk_export
from idraa.services.flash import build_flash
from idraa.services.org import require_sole_org
from idraa.services.overlays import OverlayService, OverlayVersionConflictError
from idraa.services.overlays_importer import (
    PreviewExpiredError,
    apply_validated_preview,
    generate_template_csv,
    validate_csv,
)
from idraa.utils.csv_export import csv_response

router = APIRouter()


# ---- helpers ---------------------------------------------------------


def _form_ctx(
    user: User,
    form_values: dict[str, Any],
    action: str,
    overlay: OverlayDefinition | None = None,
    errors: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "current_user": user,
        "flash": None,
        "form": form_values,
        "overlay": overlay,
        "action": action,
        "errors": errors or {},
    }


def _default_form() -> dict[str, Any]:
    return {
        "tag": "",
        "display_name": "",
        "frequency_multiplier": "1.0",
        "magnitude_multiplier": "1.0",
        "sources": "",
        "methodology": "",
        "methodology_change_reason": "",
    }


def _form_from_overlay(od: OverlayDefinition) -> dict[str, Any]:
    return {
        "tag": od.tag,
        "display_name": od.display_name,
        "frequency_multiplier": od.frequency_multiplier,
        "magnitude_multiplier": od.magnitude_multiplier,
        "sources": "; ".join(od.sources),
        "methodology": od.methodology,
        "methodology_change_reason": "",
    }


def _parse_overlay_form(raw: dict[str, Any]) -> OverlayForm:
    """Coerce raw form-data into an :class:`OverlayForm` DTO.

    B7 fold-in: numeric fields are pulled out and run through
    :func:`float` explicitly. Pydantic would coerce a string-typed
    ``"1.5"`` cleanly, but ``"not_a_number"`` would surface as a
    Pydantic ``value_error`` whose ``msg`` reads "Input should be a
    valid number, unable to parse string as a number" — informative
    enough, but the explicit-cast pattern keeps the route layer's
    error-shape contract (a single ``msg`` string with a sensible
    column name) consistent with the importer's row-level errors.

    Stripping ``_csrf`` / ``expected_version`` / ``reason`` is required
    because :class:`OverlayForm` has ``extra="forbid"`` — letting them
    through would raise a Pydantic extra-fields error rather than the
    field-specific error the template expects to render. The POST
    handler reads ``expected_version`` separately before this is called.

    KeyError / ValueError / ValidationError bubble to the caller, which
    422-re-renders the form.
    """
    sources_raw = (raw.get("sources") or "").strip()
    sources = [s.strip() for s in sources_raw.split(";") if s.strip()] if sources_raw else []
    return OverlayForm(
        tag=raw["tag"],
        display_name=raw["display_name"],
        frequency_multiplier=float(raw["frequency_multiplier"]),
        magnitude_multiplier=float(raw["magnitude_multiplier"]),
        sources=sources,
        methodology=raw.get("methodology", ""),
        methodology_change_reason=raw.get("methodology_change_reason", ""),
    )


def _flatten_validation_errors(exc: ValidationError) -> dict[str, str]:
    """Flatten Pydantic ValidationError into dict[str, str] (Arch-5).

    Renders ``err["msg"]`` only — never the raw Pydantic dict-repr which
    would leak ``type``/``input``/``url`` keys into rendered HTML.

    When multiple errors share the same field (loc[0]), the last message
    wins — the form can only display one error per field anyway.

    Non-field errors (empty loc) land under ``"_form"`` for the
    form_error_summary banner.
    """
    out: dict[str, str] = {}
    for err in exc.errors():
        loc = str(err["loc"][0]) if err.get("loc") else "_form"
        out[loc] = err["msg"]
    return out


# ---- read paths ------------------------------------------------------


@router.get("/overlays", response_class=HTMLResponse)
async def overlays_list(
    request: Request,
    deactivated: int | None = Query(
        default=None,
        ge=0,
        le=1,
        description=(
            "Issue #154: post-deactivate flash flag. Set to 1 by the "
            "deactivate POST redirect; rendered as a 'success' banner here."
        ),
    ),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),
) -> HTMLResponse:
    org = await require_sole_org(db)
    overlays = await OverlayRepo(db).list_active(organization_id=org.id)
    # Issue #154: post-deactivate flash.
    flash = build_flash("Deactivated overlay.", "success") if deactivated == 1 else None
    return templates.TemplateResponse(
        request,
        "overlays/list.html",
        {"current_user": user, "flash": flash, "overlays": overlays},
    )


# ---- export + import (must come before /{overlay_id} to avoid path-collision) ---


@router.get("/overlays/export.csv", dependencies=[Depends(require_recent_auth)])
async def overlays_export_csv(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),
) -> Response:
    """Stream all active overlays for the current org as a CSV download.

    Plan-gate Arch-2: registered BEFORE /overlays/{overlay_id}.
    Plan-gate Sec-3: scoped by org from require_sole_org.
    """
    org = await require_sole_org(db)
    overlays = await OverlayRepo(db).list_active(organization_id=org.id)
    # #304: bulk egress audit row.
    await log_bulk_export(
        db,
        organization_id=org.id,
        entity_type="overlay",
        fmt="csv",
        count=len(overlays),
        user_id=user.id,
        ip_address=client_ip(request),
    )
    header = [
        "id",
        "tag",
        "display_name",
        "frequency_multiplier",
        "magnitude_multiplier",
        "version",
        "is_active",
    ]
    rows = (
        (
            str(o.id),
            o.tag,
            o.display_name,
            o.frequency_multiplier,
            o.magnitude_multiplier,
            o.version,
            "true" if o.is_active else "false",
        )
        for o in overlays
    )
    return csv_response(filename="overlays.csv", header=header, rows_iter=rows)


@router.get("/overlays/template.csv")
async def overlays_template_csv(
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> Response:
    """Return the bulk-import CSV template (admin-only).

    Admin-only because download exposes the column schema operators are
    expected to populate; while not strictly secret, gating it to admin
    matches the other import endpoints' RBAC posture and avoids casual
    leak of internal schema to non-admin users.
    """
    return Response(
        content=generate_template_csv(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=overlays_template.csv"},
    )


@router.post("/overlays/import")
async def overlays_import(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
    file: UploadFile = File(...),
) -> Response:
    """Step 1 of two-step CSV import: validate, stash, render preview.

    Mirrors ``routes/controls.py:222-234`` for the upload size guard:
    Content-Length is checked BEFORE ``await file.read()`` (so a forged-
    massive file is rejected without spilling into memory), then re-
    checked post-read because Content-Length is client-supplied and
    forgeable.

    Renders the preview page with the storage token + preview rows +
    errors. The route never auto-applies — even a zero-error upload
    waits for explicit confirm via :func:`overlays_import_confirm`.
    """
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
    token, preview, errors = await validate_csv(
        db,
        org_id=org.id,
        user_id=user.id,
        csv_bytes=csv_bytes,
    )
    return templates.TemplateResponse(
        request,
        "overlays/import_preview.html",
        {
            "current_user": user,
            "flash": None,
            "token": token,
            "preview": preview,
            "errors": errors,
        },
    )


@router.post("/overlays/import/confirm")
async def overlays_import_confirm(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> Response:
    """Step 2 of two-step CSV import: apply the rows backed by ``token``.

    On :class:`PreviewExpiredError` (token unknown / expired / cross-
    org), render the 409 expired-preview page rather than letting the
    exception bubble to a 500 — the expected operator response is
    "re-upload to start fresh", not a stack trace.
    """
    # ``request.form()`` types each value as ``str | UploadFile``; the
    # confirm form has no file inputs, so values are always str. Annotate
    # ``raw`` as ``dict[str, Any]`` (matches the ``_parse_form`` precedent
    # in routes/controls.py) so the per-key accesses below stay readable
    # without per-line ``isinstance`` ceremony.
    raw: dict[str, Any] = dict(await request.form())
    token = (raw.get("token") or "").strip()
    if not token:
        raise HTTPException(status_code=422, detail="token required")

    org = await require_sole_org(db)
    try:
        imported, skipped, errors = await apply_validated_preview(
            db,
            token=token,
            org_id=org.id,
            user_id=user.id,
            ip_address=client_ip(request),
        )
    except PreviewExpiredError as exc:
        return templates.TemplateResponse(
            request,
            "overlays/import_expired.html",
            {
                "current_user": user,
                "flash": None,
                # Render the message verbatim — PreviewExpiredError messages
                # are operator-facing strings written by the service layer
                # (no Pydantic-shape leakage risk).
                "message": str(exc),
            },
            status_code=409,
        )

    if errors:
        return templates.TemplateResponse(
            request,
            "overlays/import_result.html",
            {
                "current_user": user,
                "flash": None,
                "imported": imported,
                "skipped": skipped,
                "errors": errors,
            },
        )
    return RedirectResponse("/overlays", status_code=303)


# ---- create ----------------------------------------------------------


@router.get("/overlays/new", response_class=HTMLResponse)
async def overlay_new_get(
    request: Request,
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "overlays/edit.html",
        _form_ctx(user, _default_form(), "/overlays"),
    )


@router.post("/overlays")
async def overlay_create(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> Response:
    raw: dict[str, Any] = dict(await request.form())
    try:
        form = _parse_overlay_form(raw)
    except (ValidationError, KeyError, ValueError) as exc:
        errors: dict[str, str] = (
            _flatten_validation_errors(exc)
            if isinstance(exc, ValidationError)
            else {"_form": str(exc)}
        )
        return templates.TemplateResponse(
            request,
            "overlays/edit.html",
            _form_ctx(user, raw, "/overlays", errors=errors),
            status_code=422,
        )
    org = await require_sole_org(db)
    svc = OverlayService(db)
    od = await svc.create(
        organization_id=org.id,
        user_id=user.id,
        form=form,
        ip_address=client_ip(request),
    )
    return RedirectResponse(f"/overlays/{od.id}", status_code=303)


# ---- view / edit / deactivate ---------------------------------------
# NOTE: routes with /{overlay_id} go LAST so the literal sub-paths
# (/template.csv, /import, /new) match first. FastAPI uses
# registration order, so this ordering matters.


@router.get("/overlays/{overlay_id}", response_class=HTMLResponse)
async def overlay_view(
    overlay_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),
) -> HTMLResponse:
    """Render an overlay's detail page.

    B9/B10 fix: org-scoped lookup. Cross-org IDs return None → 404 (NOT
    403) so we don't leak existence of overlays owned by other orgs.
    """
    org = await require_sole_org(db)
    od = await OverlayRepo(db).get_for_org(overlay_id=overlay_id, organization_id=org.id)
    if od is None:
        raise HTTPException(404)
    return templates.TemplateResponse(
        request,
        "overlays/view.html",
        {"current_user": user, "flash": None, "overlay": od},
    )


@router.get("/overlays/{overlay_id}/edit", response_class=HTMLResponse)
async def overlay_edit_get(
    overlay_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> HTMLResponse:
    """Render the overlay edit form.

    B9/B10 fix: org-scoped lookup, 404 on miss. The hidden
    ``expected_version`` input is templated from ``overlay.version``
    so the next POST carries it for the optimistic-lock check.
    """
    org = await require_sole_org(db)
    od = await OverlayRepo(db).get_for_org(overlay_id=overlay_id, organization_id=org.id)
    if od is None:
        raise HTTPException(404)
    return templates.TemplateResponse(
        request,
        "overlays/edit.html",
        _form_ctx(user, _form_from_overlay(od), f"/overlays/{od.id}/edit", overlay=od),
    )


@router.post("/overlays/{overlay_id}/edit")
async def overlay_edit_post(
    overlay_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> Response:
    """Apply edits to an overlay.

    B8 optimistic-lock: ``expected_version`` is read out of the form,
    cast to ``int`` in a try/except, and passed to
    :meth:`OverlayService.update`. On
    :class:`OverlayVersionConflictError` we render the same edit form
    with status 409 and a reload-and-retry message; on any other
    validation issue we render 422.

    Missing/non-int ``expected_version`` is treated as 422 (form is
    malformed) — the form template carries the field, so its absence
    here means either a hand-crafted POST or a template regression.
    """
    org = await require_sole_org(db)
    od = await OverlayRepo(db).get_for_org(overlay_id=overlay_id, organization_id=org.id)
    if od is None:
        raise HTTPException(404)

    # See ``overlays_import_confirm`` for the ``dict[str, Any]`` rationale —
    # the edit form has no file inputs, so values are always str.
    raw: dict[str, Any] = dict(await request.form())
    expected_version_raw = raw.get("expected_version")
    try:
        expected_version = int(expected_version_raw) if expected_version_raw is not None else None
    except (TypeError, ValueError):
        expected_version = None
    if expected_version is None:
        return templates.TemplateResponse(
            request,
            "overlays/edit.html",
            _form_ctx(
                user,
                raw,
                f"/overlays/{od.id}/edit",
                overlay=od,
                errors={"_form": "expected_version: missing or invalid hidden form field"},
            ),
            status_code=422,
        )

    try:
        form = _parse_overlay_form(raw)
    except (ValidationError, KeyError, ValueError) as exc:
        errs: dict[str, str] = (
            _flatten_validation_errors(exc)
            if isinstance(exc, ValidationError)
            else {"_form": str(exc)}
        )
        return templates.TemplateResponse(
            request,
            "overlays/edit.html",
            _form_ctx(user, raw, f"/overlays/{od.id}/edit", overlay=od, errors=errs),
            status_code=422,
        )

    svc = OverlayService(db)
    try:
        await svc.update(
            overlay=od,
            user_id=user.id,
            form=form,
            expected_version=expected_version,
            ip_address=client_ip(request),
        )
    except OverlayVersionConflictError as exc:
        return templates.TemplateResponse(
            request,
            "overlays/edit.html",
            _form_ctx(
                user,
                raw,
                f"/overlays/{od.id}/edit",
                overlay=od,
                errors={
                    "_form": "Another admin updated this overlay — please reload "
                    "and retry your edit. " + str(exc)
                },
            ),
            status_code=409,
        )
    except ValueError as exc:
        # OverlayService.update raises ValueError on tag-rename — route
        # layer surfaces it as a 422 form-render so the operator can
        # correct the tag back to the original value.
        return templates.TemplateResponse(
            request,
            "overlays/edit.html",
            _form_ctx(user, raw, f"/overlays/{od.id}/edit", overlay=od, errors={"_form": str(exc)}),
            status_code=422,
        )
    return RedirectResponse(f"/overlays/{od.id}", status_code=303)


@router.post(
    "/overlays/{overlay_id}/deactivate",
    dependencies=[Depends(require_recent_auth)],
)
async def overlay_deactivate(
    overlay_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> Response:
    """Mark an overlay inactive.

    Validates the ``reason`` field via :class:`OverlayDeactivateForm`
    (1..500 chars, ``extra="forbid"`` blocks form-field smuggling).
    Empty / oversize reasons surface as 422 with a sensible msg.
    """
    org = await require_sole_org(db)
    od = await OverlayRepo(db).get_for_org(overlay_id=overlay_id, organization_id=org.id)
    if od is None:
        raise HTTPException(404)

    raw: dict[str, Any] = dict(await request.form())
    # Pass only ``reason`` to the form — ``OverlayDeactivateForm`` has
    # ``extra="forbid"`` but we never hand it ``raw``, so other fields
    # (``_csrf``, smuggled extras) can't reach it. ``raw.get("reason",
    # "")`` is str-typed under the dict[str, Any] annotation;
    # OverlayDeactivateForm's reason: str validator catches any non-
    # string content downstream.
    reason_value = raw.get("reason", "")
    try:
        deact = OverlayDeactivateForm(reason=reason_value)
    except ValidationError as exc:
        return templates.TemplateResponse(
            request,
            "overlays/view.html",
            {
                "current_user": user,
                "flash": None,
                "overlay": od,
                "errors": _flatten_validation_errors(exc),
            },
            status_code=422,
        )

    svc = OverlayService(db)
    await svc.deactivate(
        overlay=od,
        user_id=user.id,
        reason=deact.reason,
        ip_address=client_ip(request),
    )
    # Issue #154: query-string flash so the list page can confirm the deactivation.
    return RedirectResponse("/overlays?deactivated=1", status_code=303)
