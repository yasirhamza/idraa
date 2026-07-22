"""Login + logout routes.

Behaviour summary:

- GET ``/login`` renders the sign-in form. Rendered even when the request
  already has a valid session — that's mildly weird UX but not a bug; the
  plan intentionally does NOT add a logged-in shortcut here (1.1.5-style
  bounces are the only approved pattern for that). Fix in a later phase
  with an explicit decision record.
- POST ``/login`` verifies credentials via ``verify_user_password`` (timing-
  safe), creates an AuthSession, writes a ``login`` AuditLog row, and
  attaches the signed ``idraa_session`` cookie via ``set_session_cookie``.
  Bad-credentials path re-renders the form with status=400.
  If the account already has a strong 2nd factor (``user_has_strong_factor``),
  POST ``/login`` instead renders ``auth/mfa_challenge.html`` with a signed
  ``rf_mfa_pending`` cookie — no session is minted yet.
- POST ``/login/mfa`` verifies the pending user's TOTP code or a recovery
  code (recovery only walked when the input is recovery-code-shaped, so a
  wrong 6-digit guess never pays the Argon2 cost of the recovery loop) and
  completes the session on success.
- POST ``/login/passkey/options`` / ``/login/passkey/verify`` are the
  usernameless WebAuthn login ceremony (see ``static/js/webauthn.js``).
- A minimal per-account login throttle (idraa#81 slice) tracks
  ``failed_login_count`` / ``locked_until`` on ``User``; it is reset ONLY on
  full-auth success (no strong factor pending, or the 2nd factor verifies) —
  never on a bare password match while a 2nd factor is still pending, or an
  attacker with the password could wipe the ``/login/mfa`` rate limit at will.
- POST ``/logout`` deletes the current AuthSession row, writes a ``logout``
  AuditLog row (only when a user is attributed — stale-cookie logouts have
  no user context), clears the cookie, and 303-redirects to /login.

Commit is owned by the ``get_db`` dependency (see
``routes/deps.py::get_db`` + ``db.py::get_session``): the context manager
auto-commits on successful exit. Handlers here do NOT call
``await db.commit()`` directly.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.app import templates
from idraa.models._types import now_utc
from idraa.models.mfa import RecoveryCode, UserTotp, WebAuthnCredential
from idraa.models.session import AuthSession
from idraa.models.user import User
from idraa.routes.deps import (
    client_ip,
    current_session,
    current_user,
    get_db,
)
from idraa.services import totp as totp_service
from idraa.services import webauthn_service
from idraa.services.audit import AuditWriter
from idraa.services.auth import (
    clear_mfa_pending_cookie,
    clear_session_cookie,
    clear_webauthn_challenge_cookie,
    create_session,
    is_locked,
    load_mfa_pending,
    load_user_by_email,
    load_webauthn_challenge,
    register_failed_login,
    reset_login_throttle,
    set_mfa_pending_cookie,
    set_session_cookie,
    set_webauthn_challenge_cookie,
    verify_user_password,
)
from idraa.services.mfa_crypto import decrypt_totp_secret, verify_recovery_code
from idraa.services.mfa_enrollment import user_has_strong_factor

router = APIRouter()


def _safe_next(raw: str | None) -> str:
    """Sanitize a ``?next=`` redirect target.

    Returns ``raw`` only when it is a same-origin absolute path: must start
    with a single ``/`` and NOT with ``//`` or ``/\\`` (browsers normalize a
    leading backslash to a forward slash for special schemes, so ``/\\evil``
    is an equivalent protocol-relative open-redirect vector to ``//evil``).
    Anything else falls back to ``/``.
    """
    if raw and raw.startswith("/") and raw[1:2] not in ("/", "\\"):
        return raw
    return "/"


def _json_err(msg: str) -> Response:
    return Response(
        content=json.dumps({"error": msg}), status_code=400, media_type="application/json"
    )


@router.get("/login", response_class=HTMLResponse)
async def login_get(request: Request, next: str | None = None) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "auth/login.html",
        {
            "current_user": current_user(request),
            "flash": None,
            "form": {},
            "error": None,
            "next": _safe_next(next),
        },
    )


@router.post("/login")
async def login_post(
    request: Request,
    email: str = Form(..., max_length=255),
    password: str = Form(..., max_length=1024),
    next: str | None = Form(default=None, max_length=2048),
    db: AsyncSession = Depends(get_db),
) -> Response:
    # next arrives as a hidden field on the login form (round-tripped from
    # GET /login?next=<path>); fall back to the ?next= query param so a direct
    # POST to /login?next=<path> also works. Re-sanitize server-side: the value
    # is attacker-controllable, so trusting the GET-time validation alone would
    # let a crafted POST smuggle an open-redirect target.
    safe_next = _safe_next(next or request.query_params.get("next"))
    user = await load_user_by_email(db, email)
    # verify_user_password always runs one Argon2 hash even when user is
    # None — prevents "does this email exist?" timing-based enumeration.
    # No min_length on password here: /setup enforces min_length=8 at
    # creation, and defense-in-depth should not reject on login what the
    # system already accepted on creation.
    password_ok = verify_user_password(user, password)
    if user is None or not password_ok:
        if user is not None and not is_locked(user):
            # Count only a real, unlocked user's miss — an already-locked
            # user's repeated wrong guesses shouldn't keep extending the
            # lockout window past auth_lockout_seconds.
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
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            {
                "current_user": None,
                "flash": None,
                "form": {"email": email},
                "error": "Invalid email or password",
                "next": safe_next,
            },
            status_code=400,
        )

    if is_locked(user):  # correct password but locked -> still deny
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            {
                "current_user": None,
                "flash": None,
                "form": {"email": email},
                "error": "Invalid email or password",
                "next": safe_next,
            },
            status_code=400,
        )

    if await user_has_strong_factor(db, user.id):
        # Correct password but a 2nd factor is still required. Do NOT reset
        # the throttle here — password-verify is NOT full auth, and
        # resetting would let an attacker who has the password wipe the
        # /login/mfa rate limit at will (the exact hole B1 closes). Reset
        # happens only on full-auth success (below, and in login_mfa_post).
        resp = templates.TemplateResponse(
            request,
            "auth/mfa_challenge.html",
            {"current_user": None, "error": None, "next": safe_next},
        )
        set_mfa_pending_cookie(resp, user.id)
        return resp

    # No strong factor (pre-enrollment / migration) -> password IS full auth
    # here; reset the throttle, then mint a session as before (the
    # enrollment interstitial traps the user on the next request).
    reset_login_throttle(user)

    sess = await create_session(db, user.id, ip=client_ip(request))
    # datetime.now(UTC) — matches the aware-UTC invariant enforced at
    # create_session time. GH #7 tracks a UtcDateTime TypeDecorator that
    # would enforce this at column level; do NOT use utcnow() (naive).
    user.last_login_at = datetime.now(UTC)
    audit = AuditWriter(db)
    await audit.log(
        organization_id=user.organization_id,
        entity_type="session",
        entity_id=sess.id,
        action="login",
        changes={},
        user_id=user.id,
        ip_address=client_ip(request),
    )
    # Transaction commit owned by get_db dependency.

    response = RedirectResponse(safe_next, status_code=303)
    set_session_cookie(response, sess.id)
    return response


@router.post("/login/mfa")
async def login_mfa_post(
    request: Request,
    code: str = Form(..., max_length=32),
    next: str | None = Form(default=None, max_length=2048),
    db: AsyncSession = Depends(get_db),
) -> Response:
    signed = request.cookies.get("rf_mfa_pending")
    user_id = load_mfa_pending(signed) if signed else None
    safe_next = _safe_next(next or request.query_params.get("next"))
    if user_id is None:
        return RedirectResponse("/login", status_code=303)
    user = await db.get(User, user_id)
    if user is None or not user.is_active or is_locked(user):
        return RedirectResponse("/login", status_code=303)

    code = code.strip()
    method: str | None = None
    totp = (
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
    if totp and totp_service.verify_totp(decrypt_totp_secret(totp.secret_encrypted), code):
        method = "totp"
    # Only walk the recovery Argon2 loop when the input is recovery-code-shaped
    # — a wrong TOTP guess must NOT cost up to 10 Argon2 verifies (CPU-DoS
    # amplifier).
    if method is None and re.fullmatch(r"[0-9a-f]{5}-[0-9a-f]{5}", code):
        for rc in (
            (
                await db.execute(
                    select(RecoveryCode).where(
                        RecoveryCode.user_id == user.id, RecoveryCode.used_at.is_(None)
                    )
                )
            )
            .scalars()
            .all()
        ):
            if verify_recovery_code(code, rc.code_hash):
                rc.used_at = now_utc()
                method = "recovery"
                await AuditWriter(db).log(
                    organization_id=user.organization_id,
                    entity_type="user",
                    entity_id=user.id,
                    action="user.recovery_code_used",
                    changes={},
                    user_id=user.id,
                    ip_address=client_ip(request),
                )
                break

    if method is None:
        # Audit EVERY failed 2nd-factor attempt (not just the one that trips
        # the lock) — this is the feature's core detection signal: a phished-
        # password attacker stuck at the MFA wall is otherwise invisible until
        # the 5th miss. Bounded by the lockout itself (<= auth_max_failed_logins
        # rows per lockout window).
        await AuditWriter(db).log(
            organization_id=user.organization_id,
            entity_type="user",
            entity_id=user.id,
            action="user.login_mfa_failed",
            changes={},
            user_id=user.id,
            ip_address=client_ip(request),
        )
        register_failed_login(user)  # counts toward lockout (B1)
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
        return templates.TemplateResponse(
            request,
            "auth/mfa_challenge.html",
            {"current_user": None, "error": "Invalid code", "next": safe_next},
            status_code=400,
        )

    reset_login_throttle(user)
    sess = await create_session(db, user.id, ip=client_ip(request))
    user.last_login_at = datetime.now(UTC)
    await AuditWriter(db).log(
        organization_id=user.organization_id,
        entity_type="session",
        entity_id=sess.id,
        action="user.login_mfa",
        changes={"method": method},
        user_id=user.id,
        ip_address=client_ip(request),
    )
    resp = RedirectResponse(safe_next, status_code=303)
    set_session_cookie(resp, sess.id)
    clear_mfa_pending_cookie(resp)
    return resp


@router.post("/login/passkey/options")
async def login_passkey_options(request: Request) -> Response:
    options_json, challenge = webauthn_service.authentication_options()
    resp = Response(content=options_json, media_type="application/json")
    set_webauthn_challenge_cookie(resp, challenge)
    return resp


@router.post("/login/passkey/verify")
async def login_passkey_verify(
    request: Request, payload: dict[str, Any] = Body(...), db: AsyncSession = Depends(get_db)
) -> Response:
    signed = request.cookies.get("rf_webauthn_challenge")
    challenge = load_webauthn_challenge(signed) if signed else None
    if challenge is None:
        return _json_err("challenge expired")
    credential = payload.get("credential")
    if not isinstance(credential, dict) or not credential.get("rawId"):
        return _json_err("malformed credential")
    raw_id = webauthn_service.parse_raw_id(credential)
    cred = (
        (
            await db.execute(
                select(WebAuthnCredential).where(WebAuthnCredential.credential_id == raw_id)
            )
        )
        .scalars()
        .first()
    )
    if cred is None:
        return _json_err("unknown credential")
    try:
        new_count = webauthn_service.verify_authentication(
            credential, challenge, cred.public_key, cred.sign_count
        )
    except Exception as exc:  # any bad/tampered assertion -> 400, not 500
        return _json_err(f"verification failed: {type(exc).__name__}")
    if not webauthn_service.sign_count_ok(cred.sign_count, new_count):
        return _json_err("counter")
    cred.sign_count = new_count
    cred.last_used_at = now_utc()
    user = await db.get(User, cred.user_id)
    if user is None or not user.is_active:
        return _json_err("inactive")
    sess = await create_session(db, user.id, ip=client_ip(request))
    user.last_login_at = datetime.now(UTC)
    await AuditWriter(db).log(
        organization_id=user.organization_id,
        entity_type="session",
        entity_id=sess.id,
        action="user.login_passkey",
        changes={},
        user_id=user.id,
        ip_address=client_ip(request),
    )
    resp = Response(content='{"next":"/"}', media_type="application/json")
    set_session_cookie(resp, sess.id)
    clear_webauthn_challenge_cookie(resp)
    return resp


@router.post("/logout")
async def logout_post(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(current_user),
    sess: AuthSession | None = Depends(current_session),
) -> Response:
    if sess is not None:
        await db.delete(sess)
        # Only audit a logout when we have a user to attribute it to. A
        # stale-cookie logout (session row missing its user for any reason,
        # or request.state.user didn't hydrate) has no one to blame — don't
        # write a misleading "logout by ???" row.
        #
        # Intentionally no audit write when sess exists but user is None
        # (deactivated-user case, or stale cookie that unsigns to a valid
        # session but whose user is now inactive). Rationale: AuditLog is
        # append-only and an anon-writable path would amplify cheap rows.
        # The deactivation itself will be audited by the users-admin route
        # (1.1.9), which is the appropriate scope for "user X deactivated;
        # N live sessions terminated".
        if user is not None:
            audit = AuditWriter(db)
            await audit.log(
                organization_id=user.organization_id,
                entity_type="session",
                entity_id=sess.id,
                action="logout",
                changes={},
                user_id=user.id,
                ip_address=client_ip(request),
            )
        # Transaction commit owned by get_db dependency.

    response = RedirectResponse("/login", status_code=303)
    clear_session_cookie(response)
    return response
