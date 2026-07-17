"""ADMIN-gated FX rate-admin routes (Task 5, P2).

GET  /fx-rates  — list active rates + inline add form
POST /fx-rates  — upsert a rate (CSRF-protected, audited, IP-logged)
Both endpoints require ADMIN role.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.app import templates
from idraa.currency import SELECTABLE_CURRENCIES
from idraa.models.enums import UserRole
from idraa.models.fx_rate import FxRate
from idraa.models.user import User
from idraa.routes.deps import client_ip, get_db, require_role
from idraa.schemas.fx_rate import FxRateForm
from idraa.services.fx_rates import FxRateService, InvalidRateError
from idraa.services.org import require_sole_org

router = APIRouter()


@router.get("/fx-rates", response_class=HTMLResponse)
async def fx_rates_list(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> HTMLResponse:
    org = await require_sole_org(db)
    rows = (
        (
            await db.execute(
                select(FxRate).where(
                    FxRate.organization_id == org.id,
                    FxRate.is_active.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    offered = sorted(c for c in SELECTABLE_CURRENCIES if c != "USD")
    return templates.TemplateResponse(
        request,
        "fx_rates/list.html",
        {"rates": rows, "offered": offered, "current_user": user},
    )


@router.post("/fx-rates")
async def fx_rates_post(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> Response:
    org = await require_sole_org(db)
    raw = dict(await request.form())
    offered = sorted(c for c in SELECTABLE_CURRENCIES if c != "USD")
    try:
        parsed = FxRateForm(**raw)  # type: ignore[arg-type]
    except ValidationError:
        return templates.TemplateResponse(
            request,
            "fx_rates/form.html",
            {"errors": True, "form": raw, "offered": offered},
            status_code=400,
        )
    try:
        await FxRateService(db).upsert_rate(
            org.id,
            parsed.code,
            parsed.usd_rate,
            parsed.as_of_date,
            parsed.source,
            user_id=user.id,
            ip_address=client_ip(request),
        )
    except InvalidRateError as exc:
        return templates.TemplateResponse(
            request,
            "fx_rates/form.html",
            {"errors": True, "form": raw, "offered": offered, "error_msg": str(exc)},
            status_code=400,
        )
    return RedirectResponse("/fx-rates?saved=1", status_code=303)
