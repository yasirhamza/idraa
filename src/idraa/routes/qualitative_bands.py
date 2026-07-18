"""Org-band admin CRUD. RBAC: admin-only at every endpoint (epic #34 P1c Task 7).

Mirrors ``routes/library_overrides.py`` exactly for routes/RBAC/422-flash/
409-optimistic-lock/delete-confirm — the write surface is a thin CRUD over
``QualitativeBandService`` (Task 3/4 of P1b). Two structural differences from
``library_overrides``:

- No ``entry_id``/parent-entity concept: a band override stands alone,
  identified by (kind, label) at creation and by ``band_id`` thereafter.
- The service folds "duplicate active override" into a plain
  ``ValidationError`` (see ``create_org_band``'s docstring) rather than a
  dedicated ``AlreadyExistsError`` — so create-duplicate maps to 422, not a
  separate 409 branch like ``library_overrides.create_override``.

Spec: docs/superpowers/specs/2026-07-18-qualitative-register-converter-design.md §2.
Plan: docs/superpowers/plans/2026-07-18-import-ui-p1c.md Task 7 (+ amendment:
form fields route through the ``form_field`` macro — no raw inputs, no
inheriting ``library/overrides/form.html``'s allowlisted raw PERT grid).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.app import templates
from idraa.errors import NotFoundError, QualitativeBandVersionConflictError, ValidationError
from idraa.models.enums import UserRole
from idraa.models.user import User
from idraa.routes.deps import client_ip, get_db, require_role
from idraa.services.flash import build_flash
from idraa.services.qualitative_bands import QualitativeBandService

router = APIRouter(tags=["qualitative-bands"])

KIND_OPTIONS: list[tuple[str, str]] = [
    ("frequency", "Frequency (events/year)"),
    ("magnitude", "Magnitude (USD)"),
]


# ---- read paths ------------------------------------------------------


@router.get("/qualitative-bands", response_class=HTMLResponse)
async def list_bands(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> HTMLResponse:
    """EFFECTIVE table: canonical rows read-only, org rows edit/delete.

    ``effective_bands`` supplies the merged (kind, label) -> values view
    (org overrides win); ``list_org_bands`` is queried separately to recover
    each org row's own ``id`` (``EffectiveBand`` carries no id — see its
    docstring) so edit/delete links can be built. A canonical row with no
    matching org override has ``band_id=None`` -> no edit/delete affordance.
    """
    svc = QualitativeBandService(db)
    effective = await svc.effective_bands(user.organization_id)
    canonical_rows = await svc.repo.list_canonical()
    org_rows = await svc.repo.list_org_bands(user.organization_id)

    org_by_key = {(b.kind, b.label): b for b in org_rows}
    sort_order_by_key = {(b.kind, b.label): b.sort_order for b in canonical_rows}

    bands: list[dict[str, Any]] = []
    for key, eb in effective.items():
        org_band = org_by_key.get(key)
        bands.append(
            {
                "kind": eb.kind,
                "label": eb.label,
                "low": eb.low,
                "mode": eb.mode,
                "high": eb.high,
                "source": eb.source,
                "source_version": eb.source_version,
                "band_id": str(org_band.id) if org_band is not None else None,
                # Canonical order for matched labels; org-only novel labels
                # (no canonical counterpart) sort after, alphabetically.
                "sort_order": sort_order_by_key.get(key, 999),
            }
        )
    bands.sort(key=lambda b: (b["kind"], b["sort_order"], b["label"]))

    return templates.TemplateResponse(
        request,
        "qualitative_bands/list.html",
        {"current_user": user, "flash": None, "bands": bands},
    )


@router.get("/qualitative-bands/new", response_class=HTMLResponse)
async def new_band_form(
    request: Request,
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "qualitative_bands/form.html",
        {
            "current_user": user,
            "flash": None,
            "band": None,
            "form": None,
            "kind_options": KIND_OPTIONS,
        },
    )


# ---- create ------------------------------------------------------------


@router.post("/qualitative-bands")
async def create_band(
    request: Request,
    kind: str = Form(...),
    label: str = Form(...),
    low: float = Form(...),
    mode: float = Form(...),
    high: float = Form(...),
    reason: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> Response:
    svc = QualitativeBandService(db)
    try:
        await svc.create_org_band(
            organization_id=user.organization_id,
            kind=kind,
            label=label,
            low=low,
            mode=mode,
            high=high,
            reason=reason,
            user=user,
            ip_address=client_ip(request),
        )
        await db.commit()
    except ValidationError as exc:
        # Folds BOTH ordinary field-validation failures AND the "an active
        # org band already exists for this (kind, label)" case — the service
        # does not distinguish them with a dedicated conflict exception (see
        # module docstring). Both map to 422 here.
        await db.rollback()
        return templates.TemplateResponse(
            request,
            "qualitative_bands/form.html",
            {
                "current_user": user,
                "flash": build_flash(str(exc), "error"),
                "band": None,
                "form": {
                    "kind": kind,
                    "label": label,
                    "low": low,
                    "mode": mode,
                    "high": high,
                    "reason": reason,
                },
                "kind_options": KIND_OPTIONS,
            },
            status_code=422,
        )
    return RedirectResponse(url="/qualitative-bands", status_code=status.HTTP_303_SEE_OTHER)


# ---- edit / update -------------------------------------------------------
# NOTE: routes with /{band_id} go AFTER the literal sub-path (/new) so the
# literal sub-path matches first — mirrors routes/library_overrides.py.


@router.get("/qualitative-bands/{band_id}/edit", response_class=HTMLResponse)
async def edit_band_form(
    request: Request,
    band_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> HTMLResponse:
    # IDOR guard: get_org_band's WHERE clause is org-scoped (repo-level
    # closure — see QualitativeMappingRepo.get_org_band docstring). Missing,
    # tombstoned, and cross-org band_ids are all indistinguishable "not
    # found" -> 404 (never 403, never leaking existence).
    svc = QualitativeBandService(db)
    band = await svc.repo.get_org_band(user.organization_id, band_id)
    if band is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "qualitative_bands/form.html",
        {
            "current_user": user,
            "flash": None,
            "band": band,
            "form": None,
            "kind_options": KIND_OPTIONS,
        },
    )


@router.post("/qualitative-bands/{band_id}")
async def update_band(
    request: Request,
    band_id: uuid.UUID,
    low: float = Form(...),
    mode: float = Form(...),
    high: float = Form(...),
    reason: str = Form(...),
    expected_row_version: int = Form(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> Response:
    svc = QualitativeBandService(db)
    try:
        await svc.update_org_band(
            organization_id=user.organization_id,
            band_id=band_id,
            low=low,
            mode=mode,
            high=high,
            reason=reason,
            expected_row_version=expected_row_version,
            user=user,
            ip_address=client_ip(request),
        )
        await db.commit()
    except NotFoundError:
        # Missing / tombstoned / cross-org — same existence-hiding posture
        # as the GET edit-form path.
        raise HTTPException(status_code=404) from None
    except ValidationError as exc:
        await db.rollback()
        band_current = await svc.repo.get_org_band(user.organization_id, band_id)
        if band_current is None:
            raise HTTPException(status_code=404) from None
        return templates.TemplateResponse(
            request,
            "qualitative_bands/form.html",
            {
                "current_user": user,
                "flash": build_flash(str(exc), "error"),
                "band": band_current,
                "form": None,
                "kind_options": KIND_OPTIONS,
            },
            status_code=422,
        )
    except QualitativeBandVersionConflictError as exc:
        await db.rollback()
        # Re-fetch with the current row_version so the re-rendered form's
        # hidden expected_row_version reflects the winning writer's state —
        # the operator can review + resubmit against the latest version.
        band_current = await svc.repo.get_org_band(user.organization_id, band_id)
        if band_current is None:
            raise HTTPException(status_code=404) from None
        return templates.TemplateResponse(
            request,
            "qualitative_bands/form.html",
            {
                "current_user": user,
                "flash": build_flash(str(exc), "error"),
                "band": band_current,
                "form": None,
                "kind_options": KIND_OPTIONS,
            },
            status_code=409,
        )
    return RedirectResponse(
        url=f"/qualitative-bands/{band_id}/edit",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# ---- delete --------------------------------------------------------------


@router.post("/qualitative-bands/{band_id}/delete")
async def delete_band(
    request: Request,
    band_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> Response:
    svc = QualitativeBandService(db)
    try:
        await svc.delete_org_band(
            organization_id=user.organization_id,
            band_id=band_id,
            user=user,
            ip_address=client_ip(request),
        )
        await db.commit()
    except NotFoundError:
        raise HTTPException(status_code=404) from None
    return RedirectResponse(url="/qualitative-bands", status_code=status.HTTP_303_SEE_OTHER)
