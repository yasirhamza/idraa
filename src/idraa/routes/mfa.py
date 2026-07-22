"""Account security: MFA enrollment + management."""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.app import templates
from idraa.config import get_settings
from idraa.models._types import now_utc
from idraa.models.mfa import RecoveryCode, UserTotp, WebAuthnCredential
from idraa.models.user import User
from idraa.routes.deps import client_ip, get_db, require_recent_auth, require_user
from idraa.services import totp as totp_service
from idraa.services import webauthn_service
from idraa.services.audit import AuditWriter
from idraa.services.auth import (
    clear_totp_pending_cookie,
    clear_webauthn_challenge_cookie,
    load_totp_pending,
    load_webauthn_challenge,
    set_totp_pending_cookie,
    set_webauthn_challenge_cookie,
)
from idraa.services.mfa_crypto import (
    encrypt_totp_secret,
    generate_recovery_codes,
    hash_recovery_code,
)
from idraa.services.mfa_enrollment import (
    credential_views,
    maybe_stamp_enrolled,
    maybe_unstamp_enrolled,
)

router = APIRouter()


def _json_error(msg: str, status: int = 400) -> Response:
    return Response(
        content=json.dumps({"error": msg}), status_code=status, media_type="application/json"
    )


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
        "passkeys": credential_views(passkeys),
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


@router.get(
    "/account/security/totp/enroll",
    response_class=HTMLResponse,
    dependencies=[Depends(require_recent_auth)],
)
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


@router.post("/account/security/totp/enroll", dependencies=[Depends(require_recent_auth)])
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


@router.post(
    "/account/security/recovery-codes/generate",
    response_class=HTMLResponse,
    dependencies=[Depends(require_recent_auth)],
)
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


@router.post("/account/security/passkey/options", dependencies=[Depends(require_recent_auth)])
async def passkey_register_options(
    request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(require_user)
) -> Response:
    # Read-only (no mutation of `user`) — no rebind needed here, unlike
    # passkey_register_verify / passkey_delete below.
    existing = [
        c.credential_id
        for c in (
            await db.execute(
                select(WebAuthnCredential).where(WebAuthnCredential.user_id == user.id)
            )
        )
        .scalars()
        .all()
    ]
    options_json, challenge = webauthn_service.registration_options(
        user.id, user.email, user.full_name, existing
    )
    resp = Response(content=options_json, media_type="application/json")
    set_webauthn_challenge_cookie(resp, challenge)
    return resp


@router.post("/account/security/passkey/verify", dependencies=[Depends(require_recent_auth)])
async def passkey_register_verify(
    request: Request,
    payload: dict[str, Any] = Body(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),
) -> Response:
    # See the rebind comment on totp_enroll_post — `user` from require_user is
    # detached from this handler's `db` session; maybe_stamp_enrolled below
    # mutates user.mfa_enrolled_at, which would silently no-op without this.
    user = await db.get(User, user.id) or user
    signed = request.cookies.get("rf_webauthn_challenge")
    challenge = load_webauthn_challenge(signed) if signed else None
    if challenge is None:
        return _json_error("challenge expired")
    try:
        reg = webauthn_service.verify_registration(payload["credential"], challenge)
    except Exception as exc:  # any bad/tampered ceremony → 400, not 500
        return _json_error(f"verification failed: {type(exc).__name__}")
    nickname = (payload.get("nickname") or "Passkey")[:64]
    cred = WebAuthnCredential(
        user_id=user.id,
        credential_id=reg.credential_id,
        public_key=reg.public_key,
        sign_count=reg.sign_count,
        aaguid=reg.aaguid,
        transports=reg.transports,
        nickname=nickname,
    )
    db.add(cred)
    try:
        await db.flush()  # surface a duplicate credential_id as IntegrityError, not a 500
    except IntegrityError:
        await db.rollback()
        return _json_error("credential already registered")
    await AuditWriter(db).log(
        organization_id=user.organization_id,
        entity_type="webauthn_credential",
        entity_id=cred.id,
        action="webauthn_credential.create",
        changes={"nickname": nickname},
        user_id=user.id,
        ip_address=client_ip(request),
    )
    await maybe_stamp_enrolled(db, user)
    resp = Response(content='{"ok":true}', media_type="application/json")
    clear_webauthn_challenge_cookie(resp)
    return resp


@router.post(
    "/account/security/passkey/{cred_id}/delete",
    dependencies=[Depends(require_recent_auth)],
)
async def passkey_delete(
    cred_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),
) -> Response:
    # See the rebind comment on totp_enroll_post — `user` from require_user is
    # detached from this handler's `db` session; maybe_unstamp_enrolled below
    # mutates user.mfa_enrolled_at, which would silently no-op without this.
    user = await db.get(User, user.id) or user
    cred = (
        (
            await db.execute(
                select(WebAuthnCredential).where(
                    WebAuthnCredential.id == cred_id, WebAuthnCredential.user_id == user.id
                )
            )
        )
        .scalars()
        .first()
    )
    if cred is not None:
        cred_pk = cred.id
        await db.delete(cred)
        await db.flush()  # make the delete visible to maybe_unstamp_enrolled's count
        await AuditWriter(db).log(
            organization_id=user.organization_id,
            entity_type="webauthn_credential",
            entity_id=cred_pk,
            action="webauthn_credential.delete",
            changes={},
            user_id=user.id,
            ip_address=client_ip(request),
        )
        # I4: if that was the last strong factor, clear enrollment so the
        # interstitial re-fires (don't silently downgrade to password-only).
        await maybe_unstamp_enrolled(db, user)
    return RedirectResponse("/account/security", status_code=303)
