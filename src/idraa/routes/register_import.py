"""Register import routes — staged multi-step upload (admin-only).

Epic #34 P1c Task 4 (upload / sheet-pick / column-map). Later tasks in the
same PR extend this module with the value-bind step (Task 5), preview +
convert (Task 6), and converter-aware copy (Task 8).

Flow (full-page 303 redirects threading the opaque ``token`` in the path —
the app-wide wizard precedent; no HTMX step-nav precedent exists to mirror,
per the plan's scope-drift log):

    GET  /register-import                      upload form
    POST /register-import                      stage_upload -> 303 sheet|columns
    GET  /register-import/{token}/sheet         xlsx multi-sheet picker
    POST /register-import/{token}/sheet         set_sheet -> 303 columns
    GET  /register-import/{token}/columns       header -> target mapping form
    POST /register-import/{token}/columns       set_column_map -> 303 bind (Task 5)

RBAC: every route is ``require_role(UserRole.ADMIN)`` (Global Constraints).
CSRF is enforced by the global CSRFMiddleware on every unsafe method here —
these routes are NOT exempted.

``PreviewExpiredError`` (unknown / expired / cross-org / wrong-flow token)
renders the register-import-specific 409 expired page (Task 4 plan-gate
amendment Spec-I1) — mirrors ``routes/scenario_import.py``'s posture exactly,
just with its own template (the existing ``scenarios/import_expired.html``
siblings are entity-worded, not generically reusable).

``MAX_UPLOAD_BYTES`` guard on ``POST /register-import`` is belt-and-suspenders
across THREE layers (Task 4 plan-gate amendment Sec-I3): a forgeable
Content-Length pre-check here (mirrors ``routes/scenario_import.py:88-97``),
a post-read length check here, AND ``RegisterImportService.stage_upload``'s
own post-read check (the one that actually holds for a chunked/streamed
upload with no, or a lying, Content-Length header).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.app import templates
from idraa.errors import ValidationError
from idraa.models.enums import UserRole
from idraa.models.user import User
from idraa.routes.deps import MAX_UPLOAD_BYTES, get_db, require_role
from idraa.services.flash import build_flash
from idraa.services.register_import import (
    PreviewExpiredError,
    RegisterImportService,
)
from idraa.services.register_import_parsers import list_sheet_names

router = APIRouter(tags=["register-import"])

# Human-friendly labels for the 8 column-map targets, in a UX-sensible order
# (score-relevant fields first). Values are exactly `TARGETS` (Task 4).
_TARGET_OPTIONS: list[tuple[str, str]] = [
    ("title", "Title"),
    ("likelihood", "Likelihood"),
    ("impact", "Impact"),
    ("category", "Category"),
    ("description", "Description"),
    ("owner", "Owner"),
    ("carry_along", "Carry along (kept in the scenario description)"),
    ("ignore", "Ignore"),
]
# Kept in sync with `TARGETS` by test_register_import_routes.py's
# `test_target_options_match_targets` (no runtime assert — S101).


def _expired_response(request: Request, user: User, exc: PreviewExpiredError) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "register_import/import_expired.html",
        {"current_user": user, "flash": None, "message": str(exc)},
        status_code=status.HTTP_409_CONFLICT,
    )


def _upload_fmt(entity_type: str) -> str:
    """Recover ``"xlsx"``/``"csv"`` from a resolved preview row's
    ``entity_type`` (``"register:<fmt>"``) — trivial enough (the format is
    the literal suffix this module's own ``stage_upload`` wrote) that it
    doesn't warrant reaching into ``register_import``'s private helper."""
    return entity_type.rsplit(":", 1)[-1]


# ---- step 1: upload ----------------------------------------------------


@router.get("/register-import", response_class=HTMLResponse)
async def register_import_get(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> HTMLResponse:
    profiles = await RegisterImportService(db).list_profiles(user.organization_id)
    return templates.TemplateResponse(
        request,
        "register_import/upload.html",
        {"current_user": user, "flash": None, "profiles": profiles},
    )


@router.post("/register-import")
async def register_import_post(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
    file: UploadFile = File(...),
) -> Response:
    # Sec-I3 layer 1/3: forgeable Content-Length pre-check, BEFORE the body
    # is read at all (mirrors routes/scenario_import.py:88-97).
    content_length = request.headers.get("content-length")
    if (
        content_length is not None
        and content_length.isdigit()
        and int(content_length) > MAX_UPLOAD_BYTES
    ):
        raise HTTPException(status_code=413, detail="Upload too large (max 5 MB)")
    data = await file.read()
    # Sec-I3 layer 2/3: post-read check — holds even when Content-Length was
    # absent or understated. Layer 3/3 is stage_upload's own check below.
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Upload too large (max 5 MB)")

    svc = RegisterImportService(db)
    profiles = await svc.list_profiles(user.organization_id)

    # Sec-N (Task 1 amendment, applies here): UploadFile.filename is
    # `str | None` — reject None/empty at the route, before staging, so
    # `stage_upload`'s `filename: str` parameter is never handed a None.
    filename = (file.filename or "").strip()
    if not filename:
        return templates.TemplateResponse(
            request,
            "register_import/upload.html",
            {
                "current_user": user,
                "flash": build_flash("a filename is required", "error"),
                "profiles": profiles,
            },
            status_code=422,
        )

    try:
        staged = await svc.stage_upload(
            organization_id=user.organization_id,
            filename=filename,
            content_type=file.content_type,
            data=data,
            user=user,
        )
    except ValidationError as exc:
        return templates.TemplateResponse(
            request,
            "register_import/upload.html",
            {
                "current_user": user,
                "flash": build_flash(str(exc), "error"),
                "profiles": profiles,
            },
            status_code=422,
        )

    if staged.fmt == "xlsx" and staged.sheet_names is not None and len(staged.sheet_names) > 1:
        return RedirectResponse(
            f"/register-import/{staged.token}/sheet", status_code=status.HTTP_303_SEE_OTHER
        )
    return RedirectResponse(
        f"/register-import/{staged.token}/columns", status_code=status.HTTP_303_SEE_OTHER
    )


# ---- step 2: sheet pick (xlsx multi-sheet only) -------------------------


@router.get("/register-import/{token}/sheet", response_class=HTMLResponse)
async def register_import_sheet_get(
    request: Request,
    token: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> Response:
    svc = RegisterImportService(db)
    try:
        preview = await svc.get_staged(organization_id=user.organization_id, token=token)
    except PreviewExpiredError as exc:
        return _expired_response(request, user, exc)

    if _upload_fmt(preview.entity_type) != "xlsx":
        raise HTTPException(status_code=422, detail="sheet selection only applies to xlsx uploads")
    try:
        sheet_names = list_sheet_names(preview.csv_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    filename = (preview.state_json or {}).get("filename")
    return templates.TemplateResponse(
        request,
        "register_import/sheet.html",
        {
            "current_user": user,
            "flash": None,
            "token": token,
            "filename": filename,
            "sheet_options": [(s, s) for s in sheet_names],
        },
    )


@router.post("/register-import/{token}/sheet")
async def register_import_sheet_post(
    request: Request,
    token: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
    sheet_name: str = Form(...),
) -> Response:
    svc = RegisterImportService(db)
    try:
        await svc.set_sheet(
            organization_id=user.organization_id, token=token, sheet_name=sheet_name
        )
    except PreviewExpiredError as exc:
        return _expired_response(request, user, exc)
    except ValidationError as exc:
        preview = await svc.get_staged(organization_id=user.organization_id, token=token)
        sheet_names = list_sheet_names(preview.csv_bytes)
        filename = (preview.state_json or {}).get("filename")
        return templates.TemplateResponse(
            request,
            "register_import/sheet.html",
            {
                "current_user": user,
                "flash": build_flash(str(exc), "error"),
                "token": token,
                "filename": filename,
                "sheet_options": [(s, s) for s in sheet_names],
            },
            status_code=422,
        )
    return RedirectResponse(
        f"/register-import/{token}/columns", status_code=status.HTTP_303_SEE_OTHER
    )


# ---- step 3: column map --------------------------------------------------


@router.get("/register-import/{token}/columns", response_class=HTMLResponse)
async def register_import_columns_get(
    request: Request,
    token: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> Response:
    svc = RegisterImportService(db)
    try:
        preview = await svc.get_staged(organization_id=user.organization_id, token=token)
        headers = await svc.get_headers(organization_id=user.organization_id, token=token)
    except PreviewExpiredError as exc:
        return _expired_response(request, user, exc)

    state = preview.state_json or {}
    return templates.TemplateResponse(
        request,
        "register_import/column_map.html",
        {
            "current_user": user,
            "flash": None,
            "token": token,
            "filename": state.get("filename"),
            "headers": headers,
            "column_map": state.get("column_map") or {},
            "targets": _TARGET_OPTIONS,
        },
    )


@router.post("/register-import/{token}/columns")
async def register_import_columns_post(
    request: Request,
    token: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> Response:
    raw = await request.form()
    column_map: dict[str, str] = {}
    i = 0
    while f"header_{i}" in raw:
        header = str(raw[f"header_{i}"])
        target = str(raw.get(f"target_{i}", "ignore"))
        column_map[header] = target
        i += 1

    svc = RegisterImportService(db)
    try:
        await svc.set_column_map(
            organization_id=user.organization_id, token=token, column_map=column_map
        )
    except PreviewExpiredError as exc:
        return _expired_response(request, user, exc)
    except ValidationError as exc:
        preview = await svc.get_staged(organization_id=user.organization_id, token=token)
        state = preview.state_json or {}
        return templates.TemplateResponse(
            request,
            "register_import/column_map.html",
            {
                "current_user": user,
                "flash": build_flash(str(exc), "error"),
                "token": token,
                "filename": state.get("filename"),
                "headers": await svc.get_headers(organization_id=user.organization_id, token=token),
                "column_map": column_map,
                "targets": _TARGET_OPTIONS,
            },
            status_code=422,
        )
    return RedirectResponse(f"/register-import/{token}/bind", status_code=status.HTTP_303_SEE_OTHER)
