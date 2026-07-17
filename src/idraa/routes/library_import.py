"""Library-bundle import routes — two-step JSON upload (admin-only).

Mirrors :mod:`idraa.routes.scenario_import`'s two-step import flow:
``POST /library/import`` validates + stashes the bytes under a 10-min token and
renders the preview; ``POST /library/import/confirm`` re-parses the stored bytes
and inserts the non-duplicate valid entries into the GLOBAL library catalog.
``PreviewExpiredError`` (unknown / expired / cross-org token) → 409
expired-preview page, never a 500.

RBAC: every route is ``require_role(UserRole.ADMIN)`` — the form, the template
download, the upload POST, and the confirm POST. Imports mutate the GLOBAL
scenario-library catalog every org sees, so admin-only is stricter than the
analyst+ browse surface. CSRF is enforced by the global CSRFMiddleware on both
unsafe methods (the upload multipart POST and the form-encoded confirm POST),
matching the scenario-import / overlays posture exactly — these routes are NOT
exempted.

``MAX_UPLOAD_BYTES`` guard is belt-and-suspenders: a forgeable Content-Length
pre-check AND a post-read length check, mirroring routes/scenario_import.py.
Transaction commit is owned by the ``get_db`` dependency.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.app import templates
from idraa.models.enums import UserRole
from idraa.models.user import User
from idraa.routes.deps import (
    MAX_UPLOAD_BYTES,
    client_ip,
    get_db,
    require_role,
)
from idraa.services.library_bundle_import import (
    PreviewExpiredError,
    apply_validated_preview,
    generate_template_json,
    validate_upload,
)
from idraa.services.org import require_sole_org

router = APIRouter()


@router.get("/library/import", response_class=HTMLResponse)
async def library_import_get(
    request: Request,
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "library/import.html", {"current_user": user, "flash": None}
    )


@router.get("/library/import/template.json")
async def library_import_template_json(
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> Response:
    return Response(
        content=generate_template_json(),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=library_bundle_template.json"},
    )


@router.post("/library/import")
async def library_import_post(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
    file: UploadFile = File(...),
) -> Response:
    content_length = request.headers.get("content-length")
    if (
        content_length is not None
        and content_length.isdigit()
        and int(content_length) > MAX_UPLOAD_BYTES
    ):
        raise HTTPException(status_code=413, detail="Upload too large (max 5 MB)")
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Upload too large (max 5 MB)")

    org = await require_sole_org(db)
    token, preview, errors = await validate_upload(
        db,
        org_id=org.id,
        user_id=user.id,
        data=data,
    )
    return templates.TemplateResponse(
        request,
        "library/import_preview.html",
        {
            "current_user": user,
            "flash": None,
            "token": token,
            "preview": preview,
            "errors": errors,
        },
    )


@router.post("/library/import/confirm")
async def library_import_confirm(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> Response:
    # The confirm form has no file inputs, so ``request.form()`` values are
    # always str — annotate ``raw`` as ``dict[str, Any]`` (matches the
    # routes/scenario_import.py precedent) to keep the per-key accesses readable.
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
            user=user,
            ip_address=client_ip(request),
        )
    except PreviewExpiredError as exc:
        return templates.TemplateResponse(
            request,
            "library/import_expired.html",
            {"current_user": user, "flash": None, "message": str(exc)},
            status_code=409,
        )

    if errors:
        return templates.TemplateResponse(
            request,
            "library/import_result.html",
            {
                "current_user": user,
                "flash": None,
                "imported": imported,
                "skipped": skipped,
                "errors": errors,
            },
        )
    return RedirectResponse("/library", status_code=303)
