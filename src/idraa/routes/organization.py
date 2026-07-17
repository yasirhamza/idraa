"""Organization profile editor.

GET renders the sole org's profile form for any authenticated user.
POST applies the edit (admin only), writes a single ``update`` AuditLog
row when anything actually changed, and 303s back to GET.

Transaction commit is owned by the ``get_db`` dependency (see
``routes/deps.py::get_db`` + ``db.py::get_session`` — same 1.1.5.a Q1
pattern used by /setup and /login). Handlers here do NOT call
``await db.commit()`` directly.

IP capture on the audit row matches the 1.1.6 I2 pattern — every
business-row mutation audit carries the originating client IP so the
SOC can correlate activity across rows without joining back through
``AuthSession``. None is acceptable (test transports / no-client
contexts), and the column is nullable to tolerate that.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.app import templates
from idraa.currency import SELECTABLE_CURRENCIES
from idraa.models.enums import (
    IndustryType,
    OrganizationSize,
    RiskAppetite,
    SecurityMaturity,
    UserRole,
)
from idraa.models.organization import Organization
from idraa.models.user import User
from idraa.routes.deps import client_ip, get_db, require_role, require_user
from idraa.schemas.organization import OrganizationForm
from idraa.services.audit import AuditWriter
from idraa.services.flash import build_flash
from idraa.services.fx_rates import FxRateService, is_selectable_currency
from idraa.services.org import compute_org_diff, require_sole_org

router = APIRouter()


async def _selectable_currencies(db: AsyncSession, org_id: Any) -> list[str]:
    """Build the rated-gate selectable currency list: USD + sorted non-USD with active rate."""
    svc = FxRateService(db)
    non_usd: list[str] = []
    for c in SELECTABLE_CURRENCIES:
        if c != "USD" and await svc.active_rate(org_id, c) is not None:
            non_usd.append(c)
    return ["USD", *sorted(non_usd)]


def _template_ctx(
    org: Organization,
    user: User,
    errors: list[str] | None = None,
    flash: dict[str, str | None] | None = None,
    form_data: dict[str, object] | None = None,
    errors_by_field: dict[str, list[str]] | None = None,
    selectable_currencies: list[str] | None = None,
) -> dict[str, object]:
    """Build the template context for organization/form.html.

    On a validation-error re-render, callers pass ``form_data`` (the raw
    POST dict) so the template can preserve the user's typed-in input
    instead of reverting visible fields to the unchanged DB row, and
    ``errors_by_field`` so per-field error messages render next to the
    relevant inputs.

    Arch-5: ``errors_by_field`` is the canonical per-field dict; ``errors``
    (list) is kept for backward-compat with any callers that still build a
    summary list — the template ignores the list form now.

    ``selectable_currencies`` is the rated-gate allowlist: ["USD"] + sorted
    non-USD codes with an active rate.  The template renders a <select> from
    this list (P3 reporting-currency picker).
    """
    return {
        "current_user": user,
        "flash": flash,
        "org": org,
        "industries": list(IndustryType),
        "sizes": list(OrganizationSize),
        "maturities": list(SecurityMaturity),
        "appetites": list(RiskAppetite),
        "errors": errors or [],
        "form_data": form_data,
        # Normalise to dict[str, list[str]] for the template's per-field rendering.
        "errors_by_field": errors_by_field or {},
        # P3: selectable currency codes for the reporting-currency picker.
        # Always includes USD; non-USD codes require an active rate.
        "selectable_currencies": selectable_currencies or ["USD"],
    }


@router.get("/organization", response_class=HTMLResponse)
async def org_get(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),
) -> HTMLResponse:
    org = await require_sole_org(db)
    # Success flash piggybacks on a `?saved=1` query string set by the POST
    # redirect. The project doesn't have session-stored flash; this is the
    # lightest pattern that still gives the user a "Saved" confirmation
    # without breaking POST-redirect-GET. Self-clears on next refresh.
    flash = (
        build_flash("Organization profile saved.", "success")
        if request.query_params.get("saved") == "1"
        else None
    )
    # P3: build the rated-gate selectable list: USD always offered; non-USD
    # codes only if they have an active rate for this org.
    selectable = await _selectable_currencies(db, org.id)
    return templates.TemplateResponse(
        request,
        "organization/form.html",
        _template_ctx(org, user, flash=flash, selectable_currencies=selectable),
    )


@router.post("/organization")
async def org_post(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> Response:
    org = await require_sole_org(db)
    form_data = await request.form()
    raw: dict[str, object] = dict(form_data)
    # Checkbox semantics: HTML sends "on" when checked, omits the key
    # entirely when unchecked. Pydantic's bool coercion rejects "on",
    # so build the bool via presence-check before handing to the schema.
    raw["has_cyber_insurance"] = "has_cyber_insurance" in form_data

    try:
        parsed = OrganizationForm(**raw)  # type: ignore[arg-type]
    except ValidationError as exc:
        msgs: list[str] = []
        errors_by_field: dict[str, list[str]] = {}
        for e in exc.errors():
            msg = str(e["msg"])
            msgs.append(msg)
            field = str(e["loc"][0]) if e["loc"] else "_root"
            errors_by_field.setdefault(field, []).append(msg)
        # Re-build selectable list for the re-render.
        selectable_err = await _selectable_currencies(db, org.id)
        return templates.TemplateResponse(
            request,
            "organization/form.html",
            _template_ctx(
                org,
                user,
                errors=msgs,
                form_data=raw,
                errors_by_field=errors_by_field,
                selectable_currencies=selectable_err,
            ),
            status_code=400,
        )

    # P3 (SECURITY): validate preferred_currency against the rated-gate BEFORE
    # touching the ORM row.  is_selectable_currency rejects markup codes (via
    # is_supported_code's exact-set check) AND unrated codes that would leave
    # the org pointing at a currency with no active conversion rate.
    # This gate is load-bearing for security — the schema's ^[A-Z]{3}$ pattern
    # is defense-in-depth, but the rated-gate here is the real write-path guard.
    if not await is_selectable_currency(db, org.id, parsed.preferred_currency):
        selectable_cur = await _selectable_currencies(db, org.id)
        err_msg = (
            f"Currency '{parsed.preferred_currency}' is not available. "
            "Choose USD or a currency with an active exchange rate."
        )
        return templates.TemplateResponse(
            request,
            "organization/form.html",
            _template_ctx(
                org,
                user,
                errors=[err_msg],
                form_data=raw,
                errors_by_field={"preferred_currency": [err_msg]},
                selectable_currencies=selectable_cur,
            ),
            status_code=400,
        )

    update_dict = parsed.model_dump()
    diff = compute_org_diff(org, update_dict)
    for k, v in update_dict.items():
        setattr(org, k, v)
    if diff:
        await AuditWriter(db).log(
            organization_id=org.id,
            entity_type="organization",
            entity_id=org.id,
            action="update",
            changes=diff,
            user_id=user.id,
            ip_address=client_ip(request),
        )
    # Transaction commit owned by get_db dependency.
    # Carry a success signal through the redirect so GET can render the
    # "Saved" flash. The query string is self-clearing (refresh drops it).
    return RedirectResponse("/organization?saved=1", status_code=303)
