"""Admin security settings — tri-state per-org overrides for MFA policy,
step-up window, and per-category step-up gating (idraa#85 admin knobs).

GET renders the effective values (env default merged with any override)
plus which fields are currently overridden. POST is STRICT-REJECT: an
unrecognized value (unknown ``mfa_policy``, negative/non-int window, a
category value outside ``{"", "on", "off"}``) 400s and re-renders with NO
partial write — a typo must never silently clear an existing override.
Fields absent from the POST body are left untouched (partial-update
semantics); a field present as ``""`` is an EXPLICIT clear-to-follow-env
(stores ``NULL``).

Write ordering (plan-gate landmine — do not reorder): read the prior row
-> upsert via dialect ``ON CONFLICT DO UPDATE`` (handles the concurrent
first-write race) -> one audit row covering every ACTUALLY-changed field
(incl. ``value -> None`` and ``None -> value``) -> ``await db.commit()``
(explicit; the ``get_db`` mid-request-durability carve-out — see
``routes/deps.py::get_db``) -> THEN ``load_security_settings`` to swap the
process-wide cache. If the commit raises, the handler raises before the
reload, so the cache keeps the prior *committed* snapshot. The reload
itself is wrapped in try/except-log: a committed-but-reload-failed write
leaves the cache briefly stale rather than raising a 500 for an already
-durable write.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.app import templates
from idraa.models._types import now_utc
from idraa.models.enums import StepUpCategory, UserRole
from idraa.models.security_settings import SecuritySettings
from idraa.models.user import User
from idraa.routes.deps import client_ip, get_db, require_role, require_step_up
from idraa.services.audit import AuditWriter
from idraa.services.flash import build_flash
from idraa.services.org import require_sole_org
from idraa.services.security_settings import (
    effective_mfa_policy,
    effective_step_up_window,
    load_security_settings,
    step_up_required,
)

router = APIRouter()
logger = logging.getLogger(__name__)

_MFA_POLICIES = {"required", "optional"}

# (form field name, StepUpCategory, display label) — drives both the GET
# view-model loop and the POST parse loop so the two never drift apart.
_CATEGORIES: list[tuple[str, StepUpCategory, str]] = [
    ("step_up_exports", StepUpCategory.EXPORTS, "Exports"),
    ("step_up_destructive", StepUpCategory.DESTRUCTIVE, "Destructive actions"),
    ("step_up_admin", StepUpCategory.ADMIN, "Admin actions"),
    ("step_up_credentials", StepUpCategory.CREDENTIALS, "Credential changes"),
]


class _ValidationError(ValueError):
    """A single out-of-set form value. Message is shown verbatim to the admin."""


def _parse_mfa_policy(raw: str) -> str | None:
    if raw == "":
        return None
    if raw not in _MFA_POLICIES:
        raise _ValidationError(f"Unknown mfa_policy: {raw!r}")
    return raw


def _parse_window(raw: str) -> int | None:
    if raw == "":
        return None
    try:
        value = int(raw)
    except ValueError:
        raise _ValidationError("step_up_window_seconds must be a whole number") from None
    if value < 0:
        raise _ValidationError("step_up_window_seconds must be >= 0")
    return value


def _parse_category(field: str, raw: str) -> bool | None:
    if raw == "":
        return None
    if raw == "on":
        return True
    if raw == "off":
        return False
    raise _ValidationError(f"Unknown value for {field}: {raw!r}")


def _select_value(v: bool | None) -> str:
    """Tri-state override -> the same ""/"on"/"off" sentinel the form uses."""
    if v is None:
        return ""
    return "on" if v else "off"


async def _context(
    request: Request,
    me: User,
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    error: str | None = None,
    saved: bool = False,
) -> dict[str, Any]:
    row = (
        await db.execute(select(SecuritySettings).where(SecuritySettings.organization_id == org_id))
    ).scalar_one_or_none()
    categories = [
        {
            "field": field,
            "label": label,
            "value": _select_value(getattr(row, field) if row is not None else None),
            "effective": step_up_required(category),
        }
        for field, category, label in _CATEGORIES
    ]
    return {
        "current_user": me,
        "is_admin": True,
        "flash": build_flash("Security settings saved.", "success") if saved else None,
        "error": error,
        "mfa_policy_value": row.mfa_policy if row is not None else "",
        "step_up_window_seconds_value": row.step_up_window_seconds if row is not None else None,
        "categories": categories,
        "effective_mfa_policy": effective_mfa_policy(),
        "effective_step_up_window_seconds": effective_step_up_window(),
    }


@router.get("/settings/security", response_class=HTMLResponse)
async def security_settings_get(
    request: Request,
    db: AsyncSession = Depends(get_db),
    me: User = Depends(require_role(UserRole.ADMIN)),
) -> HTMLResponse:
    org = await require_sole_org(db)
    ctx = await _context(request, me, db, org.id, saved=request.query_params.get("saved") == "1")
    return templates.TemplateResponse(request, "settings/security.html", ctx)


@router.post("/settings/security", dependencies=[Depends(require_step_up(StepUpCategory.ADMIN))])
async def security_settings_post(
    request: Request,
    db: AsyncSession = Depends(get_db),
    me: User = Depends(require_role(UserRole.ADMIN)),
) -> Response:
    org = await require_sole_org(db)
    form = await request.form()

    # Step 3.1: parse only the fields PRESENT in the body (partial-update
    # semantics) with the ""-sentinel. Any out-of-set value strictly
    # rejects the WHOLE submission with no partial write.
    parsed: dict[str, object] = {}
    try:
        if "mfa_policy" in form:
            parsed["mfa_policy"] = _parse_mfa_policy(str(form["mfa_policy"]))
        if "step_up_window_seconds" in form:
            parsed["step_up_window_seconds"] = _parse_window(str(form["step_up_window_seconds"]))
        for field, _category, _label in _CATEGORIES:
            if field in form:
                parsed[field] = _parse_category(field, str(form[field]))
    except _ValidationError as exc:
        ctx = await _context(request, me, db, org.id, error=str(exc))
        return templates.TemplateResponse(request, "settings/security.html", ctx, status_code=400)

    if not parsed:
        return RedirectResponse("/settings/security?saved=1", status_code=303)

    # Step 3.2: read prior values BEFORE the upsert so the diff reflects
    # the pre-write state (a fresh org with no row -> every field is None).
    prior_row = (
        await db.execute(select(SecuritySettings).where(SecuritySettings.organization_id == org.id))
    ).scalar_one_or_none()
    prior = {
        field: getattr(prior_row, field) if prior_row is not None else None for field in parsed
    }

    dialect = db.bind.dialect.name if db.bind is not None else "sqlite"
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as _insert
    else:
        from sqlalchemy.dialects.sqlite import insert as _insert  # type: ignore[assignment]

    stmt = _insert(SecuritySettings).values(organization_id=org.id, **parsed)
    stmt = stmt.on_conflict_do_update(
        index_elements=["organization_id"],
        # updated_at explicitly in set_: onupdate does NOT fire on an
        # ON CONFLICT SET (same footgun documented in services/login_throttle.py).
        set_={**parsed, "updated_at": now_utc()},
    )
    await db.execute(stmt)
    row = (
        await db.execute(select(SecuritySettings).where(SecuritySettings.organization_id == org.id))
    ).scalar_one()

    # Step 3.3: one audit row covering every ACTUALLY-changed field,
    # including value->None and None->value.
    changes: dict[str, list[object]] = {
        field: [prior[field], new_value]
        for field, new_value in parsed.items()
        if prior[field] != new_value
    }
    if changes:
        await AuditWriter(db).log(
            organization_id=org.id,
            entity_type="security_settings",
            entity_id=row.id,
            action="security_settings.changed",
            changes=changes,
            user_id=me.id,
            ip_address=client_ip(request),
        )

    # Step 3.4: durability BEFORE the cache swap. If commit raises, we
    # never reach the reload -> cache keeps the prior committed snapshot.
    await db.commit()
    try:
        await load_security_settings(db, org.id)
    except Exception:
        logger.exception(
            "security_settings cache reload failed after a committed write "
            "(org_id=%s); cache retains the prior committed snapshot until "
            "the next successful reload",
            org.id,
        )

    return RedirectResponse("/settings/security?saved=1", status_code=303)
