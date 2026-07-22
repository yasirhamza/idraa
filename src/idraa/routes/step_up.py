"""Step-up ("sudo mode") challenge + verify routes (P2).

GET /auth/step-up renders the challenge; POST /auth/step-up/verify accepts a
TOTP code or recovery code for strong-factor users, or the account password
for users with NO strong factor yet. Password is NEVER offered to a
strong-factor account — that would collapse step-up assurance to
knowledge-only, which is exactly what a phished-password attacker holds.
The factor-less password path also prevents an enrollment deadlock: the
enrollment endpoints are themselves step-up-gated.

On success the CURRENT AuthSession.reauthenticated_at is stamped and the
user is 303'd back to `next`. Interrupted POST actions are NOT replayed —
the user re-triggers them inside the fresh window.

Passkey step-up is the ceremony pair /auth/step-up/passkey/* (Task 2).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.app import templates
from idraa.models._types import now_utc
from idraa.models.mfa import WebAuthnCredential
from idraa.models.session import AuthSession
from idraa.models.user import User
from idraa.routes.deps import (
    client_ip,
    current_session,
    get_db,
    require_user,
    safe_next,
)
from idraa.services.audit import AuditWriter
from idraa.services.auth import (
    is_locked,
    register_failed_login,
    reset_login_throttle,
    verify_user_password,
)
from idraa.services.mfa_enrollment import user_has_strong_factor
from idraa.services.second_factor import verify_totp_or_recovery

router = APIRouter()


async def _challenge_context(
    db: AsyncSession, user: User, next_url: str, error: str | None
) -> dict[str, object]:
    passkey_count = await db.scalar(
        select(func.count())
        .select_from(WebAuthnCredential)
        .where(WebAuthnCredential.user_id == user.id)
    )
    return {
        "current_user": user,
        "error": error,
        "next": next_url,
        "has_strong_factor": await user_has_strong_factor(db, user.id),
        "has_passkeys": bool(passkey_count),
    }


@router.get("/auth/step-up", response_class=HTMLResponse)
async def step_up_get(
    request: Request,
    next: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),
) -> HTMLResponse:
    ctx = await _challenge_context(db, user, safe_next(next), error=None)
    return templates.TemplateResponse(request, "auth/step_up.html", ctx)


@router.post("/auth/step-up/verify")
async def step_up_verify(
    request: Request,
    code: str = Form(default="", max_length=32),
    password: str = Form(default="", max_length=1024),
    next: str | None = Form(default=None, max_length=2048),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),
    sess: AuthSession | None = Depends(current_session),
) -> Response:
    target = safe_next(next)
    # Rebind BOTH state objects into THIS handler's db session before any
    # mutation (P1 detached-instance convention — see routes/mfa.py).
    user = await db.get(User, user.id) or user
    live_sess = await db.get(AuthSession, sess.id) if sess is not None else None
    if live_sess is None:
        return RedirectResponse("/login", status_code=303)

    async def _render_error() -> Response:
        # ONE generic body for wrong-code, wrong-password, AND locked —
        # no lockout oracle (mirrors /login's anti-enumeration posture).
        ctx = await _challenge_context(db, user, target, error="Invalid code or password")
        return templates.TemplateResponse(request, "auth/step_up.html", ctx, status_code=400)

    async def _fail() -> Response:
        await AuditWriter(db).log(
            organization_id=user.organization_id,
            entity_type="user",
            entity_id=user.id,
            action="user.step_up_failed",
            changes={},
            user_id=user.id,
            ip_address=client_ip(request),
        )
        register_failed_login(user)
        if is_locked(user):  # this miss just tripped the lock -> audit
            await AuditWriter(db).log(
                organization_id=user.organization_id,
                entity_type="user",
                entity_id=user.id,
                action="user.login_locked_out",
                changes={},
                user_id=user.id,
                ip_address=client_ip(request),
            )
        return await _render_error()

    if is_locked(user):
        # Already locked: generic bounce with NO audit row and NO counter
        # bump — mirrors /login/mfa's locked short-circuit. Auditing here
        # would let a hostile session-holder grow the append-only audit_log
        # without bound (plan-gate Sec-N3; the 2026-06-29 outage was
        # SQLite-volume exhaustion).
        return await _render_error()

    method: str | None = None
    if await user_has_strong_factor(db, user.id):
        if code:
            method = await verify_totp_or_recovery(db, user, code, ip_address=client_ip(request))
        # NOTE: password deliberately ignored for strong-factor accounts.
    elif password and verify_user_password(user, password):
        method = "password"

    if method is None:
        return await _fail()

    reset_login_throttle(user)
    live_sess.reauthenticated_at = now_utc()
    await AuditWriter(db).log(
        organization_id=user.organization_id,
        entity_type="session",
        entity_id=live_sess.id,
        action="user.step_up",
        changes={"method": method},
        user_id=user.id,
        ip_address=client_ip(request),
    )
    return RedirectResponse(target, status_code=303)
