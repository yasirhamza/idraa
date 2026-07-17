"""Dashboard — landing page (omicron-1).

Q5=R1: static server-render on GET. No HTMX polling.
Q6=A2: require_user only; template gates CTAs on role via the codified
       `templates/macros/rbac.html` macros.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.app import templates
from idraa.config import get_settings
from idraa.models.user import User
from idraa.routes.deps import get_db, require_user
from idraa.services.dashboard import build_dashboard
from idraa.services.flash import build_flash
from idraa.services.org import require_sole_org
from idraa.services.retention import maybe_sweep_opportunistic

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),
    deleted: int | None = Query(
        default=None,
        ge=0,
        le=1,
        description=(
            "#297: post-run-delete flash flag. Set to 1 by the run delete "
            "POST redirect; rendered as a 'success' banner here."
        ),
    ),
) -> HTMLResponse:
    org = await require_sole_org(db)
    data = await build_dashboard(db, org)
    # Opportunistic, throttled retention sweep — runs AFTER the response (own
    # session, atomic per-interval throttle). org_id pinned to the authed user
    # (Arch-N4), not a query/path param.
    background_tasks.add_task(
        maybe_sweep_opportunistic, get_settings(), org_id=user.organization_id
    )
    flash = build_flash("Run deleted.", "success") if deleted == 1 else None
    return templates.TemplateResponse(
        request,
        "dashboard/index.html",
        {"current_user": user, "flash": flash, "data": data},
    )
