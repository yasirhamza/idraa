"""Account security: MFA enrollment + management."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.app import templates
from idraa.config import get_settings
from idraa.models._types import now_utc
from idraa.models.mfa import RecoveryCode, UserTotp, WebAuthnCredential
from idraa.models.user import User
from idraa.routes.deps import client_ip, get_db, require_user
from idraa.services import totp as totp_service
from idraa.services.audit import AuditWriter
from idraa.services.auth import (
    clear_totp_pending_cookie,
    load_totp_pending,
    set_totp_pending_cookie,
)
from idraa.services.mfa_crypto import (
    encrypt_totp_secret,
    generate_recovery_codes,
    hash_recovery_code,
)
from idraa.services.mfa_enrollment import maybe_stamp_enrolled

router = APIRouter()


async def _security_context(db: AsyncSession, user: User) -> dict[str, object]:
    passkeys = (
        (await db.execute(select(WebAuthnCredential).where(WebAuthnCredential.user_id == user.id)))
        .scalars()
        .all()
    )
    totp = (await db.execute(select(UserTotp).where(UserTotp.user_id == user.id))).scalars().first()
    recovery_remaining = len(
        [
            r
            for r in (await db.execute(select(RecoveryCode).where(RecoveryCode.user_id == user.id)))
            .scalars()
            .all()
            if r.used_at is None
        ]
    )
    return {
        "current_user": user,
        "passkeys": passkeys,
        "totp_confirmed": bool(totp and totp.confirmed_at),
        "recovery_remaining": recovery_remaining,
        "enrolled": user.mfa_enrolled_at is not None,
    }


@router.get("/account/security", response_class=HTMLResponse)
async def security_page(
    request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(require_user)
) -> HTMLResponse:
    ctx = await _security_context(db, user)
    return templates.TemplateResponse(request, "account/security.html", ctx)


@router.get("/account/security/totp/enroll", response_class=HTMLResponse)
async def totp_enroll_get(
    request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(require_user)
) -> Response:
    # Already confirmed → just render (no re-provision).
    confirmed = (
        (
            await db.execute(
                select(UserTotp).where(
                    UserTotp.user_id == user.id, UserTotp.confirmed_at.is_not(None)
                )
            )
        )
        .scalars()
        .first()
    )
    if confirmed is not None:
        return templates.TemplateResponse(
            request, "account/_totp.html", {"already": True, "qr_svg": "", "current_user": user}
        )
    # Provision a fresh secret; stash it in a SIGNED cookie — NO DB write on GET.
    secret = totp_service.provision_secret()
    uri = totp_service.totp_uri(secret, user.email, get_settings().totp_issuer)
    resp = templates.TemplateResponse(
        request,
        "account/_totp.html",
        {
            "already": False,
            "qr_svg": totp_service.totp_qr_svg(uri),
            "secret": secret,
            "current_user": user,
        },
    )
    set_totp_pending_cookie(resp, secret)
    return resp


@router.post("/account/security/totp/enroll")
async def totp_enroll_post(
    request: Request,
    code: str = Form(..., max_length=10),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),
) -> Response:
    # `user` (from require_user) was loaded by SessionMiddleware's own now-closed
    # session — mutating it and committing THIS handler's `db` session (a
    # different AsyncSession instance) would silently no-op the write. Rebind
    # to a `db`-tracked instance before any mutation reaches maybe_stamp_enrolled.
    user = await db.get(User, user.id) or user
    signed = request.cookies.get("rf_totp_pending")
    secret = load_totp_pending(signed) if signed else None
    if secret is None:
        return RedirectResponse("/account/security/totp/enroll", status_code=303)
    if not totp_service.verify_totp(secret, code):
        ctx = await _security_context(db, user)
        ctx["error"] = "That code didn't match. Try again."
        return templates.TemplateResponse(request, "account/security.html", ctx, status_code=400)
    # Confirmed — persist the ENCRYPTED secret now (first TOTP DB write).
    existing = (
        (await db.execute(select(UserTotp).where(UserTotp.user_id == user.id))).scalars().first()
    )
    if existing is None:
        db.add(
            UserTotp(
                user_id=user.id,
                secret_encrypted=encrypt_totp_secret(secret),
                confirmed_at=now_utc(),
            )
        )
    else:
        existing.secret_encrypted = encrypt_totp_secret(secret)
        existing.confirmed_at = now_utc()
    await AuditWriter(db).log(
        organization_id=user.organization_id,
        entity_type="user",
        entity_id=user.id,
        action="user.mfa_totp_enroll",
        changes={},
        user_id=user.id,
        ip_address=client_ip(request),
    )
    await maybe_stamp_enrolled(db, user)
    resp = RedirectResponse("/account/security", status_code=303)
    clear_totp_pending_cookie(resp)
    return resp


@router.post("/account/security/recovery-codes/generate", response_class=HTMLResponse)
async def recovery_codes_generate(
    request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(require_user)
) -> HTMLResponse:
    # See the identical rebind in totp_enroll_post — `user` from require_user
    # is detached from this handler's `db` session; mutating it directly would
    # not be persisted by this session's commit.
    user = await db.get(User, user.id) or user
    # Replace any prior codes (regenerate invalidates the old set).
    for old in (
        (await db.execute(select(RecoveryCode).where(RecoveryCode.user_id == user.id)))
        .scalars()
        .all()
    ):
        await db.delete(old)
    codes = generate_recovery_codes()
    for c in codes:
        db.add(RecoveryCode(user_id=user.id, code_hash=hash_recovery_code(c)))
    await AuditWriter(db).log(
        organization_id=user.organization_id,
        entity_type="user",
        entity_id=user.id,
        action="user.recovery_codes_generated",
        changes={"count": len(codes)},
        user_id=user.id,
        ip_address=client_ip(request),
    )
    await maybe_stamp_enrolled(db, user)
    return templates.TemplateResponse(
        request,
        "account/security.html",
        {**(await _security_context(db, user)), "shown_recovery_codes": codes},
    )
