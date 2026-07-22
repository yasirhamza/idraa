"""Admin override CRUD. RBAC: admin-only at every endpoint per spec §8.2.

Spec §8.1. Mirrors routes/calibration_overrides.py preamble:
- Depends(get_db) injects the AsyncSession.
- require_role(UserRole.ADMIN) enforces RBAC; cross-org IDs return 404.
- ip_address=client_ip(request) threads the originating IP to AuditWriter.
- {{ csrf_field() }} rendered in every form template (paranoid review #15).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.app import templates
from idraa.errors import (
    FAIRCAMValidationError,
    IDORError,
    LibraryOverrideAlreadyExistsError,
    LibraryOverrideVersionConflictError,
)
from idraa.models.enums import UserRole
from idraa.models.scenario_library import ScenarioLibraryOverride
from idraa.models.user import User
from idraa.routes.deps import client_ip, get_db, require_recent_auth, require_role
from idraa.services.flash import build_flash
from idraa.services.scenario_library import (
    OverrideDraft,
    ScenarioLibraryService,
)

router = APIRouter(tags=["library-overrides"])


# ---- helpers ---------------------------------------------------------


def _parse_distribution(
    low: float | None, mode: float | None, high: float | None
) -> dict[str, Any] | None:
    """Build a PERT dict from three optional floats; return None if any are missing.

    Form-layer typing makes FastAPI return 422 on non-numeric input
    (e.g. tef_low="abc") before this function is reached. No exception
    guard required.
    """
    if low is None or mode is None or high is None:
        return None
    return {
        "distribution": "PERT",
        "low": low,
        "mode": mode,
        "high": high,
    }


# ---- read paths ------------------------------------------------------


@router.get("/library/overrides", response_class=HTMLResponse)
async def list_overrides(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> HTMLResponse:
    stmt = select(ScenarioLibraryOverride).where(
        ScenarioLibraryOverride.organization_id == user.organization_id,
        ScenarioLibraryOverride.deleted_at.is_(None),
    )
    rows = (await db.execute(stmt)).scalars().all()
    return templates.TemplateResponse(
        request,
        "library/overrides/list.html",
        {"current_user": user, "flash": None, "overrides": rows},
    )


@router.get("/library/overrides/new", response_class=HTMLResponse)
async def new_override_form(
    request: Request,
    entry_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> HTMLResponse:
    svc = ScenarioLibraryService(db)
    entry = await svc.repo.get_latest_published_by_id(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="library entry not found")
    return templates.TemplateResponse(
        request,
        "library/overrides/form.html",
        {"current_user": user, "flash": None, "entry": entry, "override": None},
    )


# ---- create ----------------------------------------------------------


@router.post("/library/overrides")
async def create_override(
    request: Request,
    entry_id: uuid.UUID = Form(...),
    tef_low: float | None = Form(None),
    tef_mode: float | None = Form(None),
    tef_high: float | None = Form(None),
    vuln_low: float | None = Form(None),
    vuln_mode: float | None = Form(None),
    vuln_high: float | None = Form(None),
    pl_low: float | None = Form(None),
    pl_mode: float | None = Form(None),
    pl_high: float | None = Form(None),
    sl_low: float | None = Form(None),
    sl_mode: float | None = Form(None),
    sl_high: float | None = Form(None),
    reason: str = Form(...),
    methodology_change_reason: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> Response:
    svc = ScenarioLibraryService(db)
    draft = OverrideDraft(
        threat_event_frequency=_parse_distribution(tef_low, tef_mode, tef_high),
        vulnerability=_parse_distribution(vuln_low, vuln_mode, vuln_high),
        primary_loss=_parse_distribution(pl_low, pl_mode, pl_high),
        secondary_loss=_parse_distribution(sl_low, sl_mode, sl_high),
    )
    try:
        await svc.create_override(
            entry_id=entry_id,
            organization_id=user.organization_id,
            draft=draft,
            reason=reason,
            user=user,
            methodology_change_reason=methodology_change_reason,
            ip_address=client_ip(request),
        )
        await db.commit()
        return RedirectResponse(
            url=f"/library/entries/{entry_id}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except LibraryOverrideAlreadyExistsError:
        await db.rollback()
        return templates.TemplateResponse(
            request,
            "library/overrides/form.html",
            {
                "current_user": user,
                "flash": build_flash(
                    "Override already exists for this entry; use Edit instead.",
                    "error",
                ),
                "entry_id": entry_id,
                "override": None,
            },
            status_code=409,
        )
    except FAIRCAMValidationError as exc:
        # #333: service-level distribution gate (non-finite legs, sigma bound,
        # vuln ∈ [0,1]). Validation raises before any row write; rollback
        # unwinds nothing but keeps the session clean for the re-render.
        await db.rollback()
        return templates.TemplateResponse(
            request,
            "library/overrides/form.html",
            {
                "current_user": user,
                "flash": build_flash(str(exc), "error"),
                "entry_id": entry_id,
                "override": None,
            },
            status_code=422,
        )


# ---- edit / update ---------------------------------------------------
# NOTE: routes with /{override_id} go AFTER the literal sub-paths (/new)
# so the literal sub-path matches first. FastAPI uses registration order.


@router.get("/library/overrides/{override_id}/edit", response_class=HTMLResponse)
async def edit_override_form(
    request: Request,
    override_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> HTMLResponse:
    # IDOR guard at GET: scope the lookup to the caller's org.
    # Cross-org IDs return None -> 404 (NOT 403) to avoid leaking existence.
    # Mirrors routes/calibration_overrides.py B9/B10.
    o = (
        await db.execute(
            select(ScenarioLibraryOverride).where(
                ScenarioLibraryOverride.id == override_id,
                ScenarioLibraryOverride.organization_id == user.organization_id,
            )
        )
    ).scalar_one_or_none()
    if o is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "library/overrides/form.html",
        {"current_user": user, "flash": None, "override": o},
    )


@router.post("/library/overrides/{override_id}")
async def update_override(
    request: Request,
    override_id: uuid.UUID,
    tef_low: float | None = Form(None),
    tef_mode: float | None = Form(None),
    tef_high: float | None = Form(None),
    vuln_low: float | None = Form(None),
    vuln_mode: float | None = Form(None),
    vuln_high: float | None = Form(None),
    pl_low: float | None = Form(None),
    pl_mode: float | None = Form(None),
    pl_high: float | None = Form(None),
    sl_low: float | None = Form(None),
    sl_mode: float | None = Form(None),
    sl_high: float | None = Form(None),
    reason: str = Form(...),
    methodology_change_reason: str | None = Form(None),
    expected_version: int = Form(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> Response:
    svc = ScenarioLibraryService(db)
    draft = OverrideDraft(
        threat_event_frequency=_parse_distribution(tef_low, tef_mode, tef_high),
        vulnerability=_parse_distribution(vuln_low, vuln_mode, vuln_high),
        primary_loss=_parse_distribution(pl_low, pl_mode, pl_high),
        secondary_loss=_parse_distribution(sl_low, sl_mode, sl_high),
    )
    try:
        await svc.update_override(
            override_id=override_id,
            organization_id=user.organization_id,
            draft=draft,
            reason=reason,
            methodology_change_reason=methodology_change_reason,
            user=user,
            expected_version=expected_version,
            ip_address=client_ip(request),
        )
        await db.commit()
    except IDORError:
        # Cross-org write: surface as 404 to avoid leaking existence.
        raise HTTPException(status_code=404) from None
    except FAIRCAMValidationError as exc:
        # #333: service-level distribution gate — validation runs before any
        # mutation, so the row keeps its prior values + version.
        await db.rollback()
        o_current = (
            await db.execute(
                select(ScenarioLibraryOverride).where(
                    ScenarioLibraryOverride.id == override_id,
                    ScenarioLibraryOverride.organization_id == user.organization_id,
                )
            )
        ).scalar_one_or_none()
        if o_current is None:
            raise HTTPException(status_code=404) from None
        return templates.TemplateResponse(
            request,
            "library/overrides/form.html",
            {
                "current_user": user,
                "flash": build_flash(str(exc), "error"),
                "override": o_current,
            },
            status_code=422,
        )
    except LibraryOverrideVersionConflictError as exc:
        await db.rollback()
        # Re-fetch with current row_version so the form re-binds and
        # the user can resubmit with the latest expected_version.
        o_current = (
            await db.execute(
                select(ScenarioLibraryOverride).where(
                    ScenarioLibraryOverride.id == override_id,
                    ScenarioLibraryOverride.organization_id == user.organization_id,
                )
            )
        ).scalar_one_or_none()
        if o_current is None:
            raise HTTPException(status_code=404) from None
        return templates.TemplateResponse(
            request,
            "library/overrides/form.html",
            {
                "current_user": user,
                "flash": build_flash(str(exc), "error"),
                "override": o_current,
            },
            status_code=409,
        )
    return RedirectResponse(
        url=f"/library/overrides/{override_id}/edit",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# ---- delete ----------------------------------------------------------


@router.post(
    "/library/overrides/{override_id}/delete",
    dependencies=[Depends(require_recent_auth)],
)
async def delete_override(
    request: Request,
    override_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> Response:
    svc = ScenarioLibraryService(db)
    try:
        await svc.delete_override(
            override_id=override_id,
            organization_id=user.organization_id,
            user=user,
            ip_address=client_ip(request),
        )
        await db.commit()
    except IDORError:
        raise HTTPException(status_code=404) from None
    return RedirectResponse(
        url="/library/overrides",
        status_code=status.HTTP_303_SEE_OTHER,
    )
