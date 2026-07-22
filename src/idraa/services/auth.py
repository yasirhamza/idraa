"""Password hashing + signed-cookie session helpers."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from itsdangerous import BadData, URLSafeSerializer, URLSafeTimedSerializer
from passlib.context import CryptContext
from passlib.exc import UnknownHashError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from idraa.config import get_settings
from idraa.models.session import AuthSession
from idraa.models.user import User

_pwd_ctx = CryptContext(schemes=["argon2"], deprecated="auto")

SESSION_COOKIE = "idraa_session"
SESSION_TTL = timedelta(days=14)


def hash_password(plain: str) -> str:
    return _pwd_ctx.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _pwd_ctx.verify(plain, hashed)
    except UnknownHashError:
        return False


# Pre-computed at import time so wrong-username paths still pay the Argon2 cost.
# Not a real secret — it's hashed once at import, never used as a credential.
_DUMMY_PW_HASH = hash_password("unused-enumeration-blocker")


def verify_user_password(user: User | None, plain: str) -> bool:
    """Timing-safe password check.

    Always runs exactly one Argon2 verify so that "user does not exist" and
    "wrong password" take the same wall-clock time. Return False for a
    missing, inactive, or wrong-password user.
    """
    if user is None or not user.is_active:
        verify_password(plain, _DUMMY_PW_HASH)
        return False
    return verify_password(plain, user.password_hash)


# Salt convention: every signed-payload type MUST use a distinct salt. Reusing
# "rf-session" for a password-reset or email-confirm signer would mean a leaked
# reset link is a valid session cookie. New types get their own salt:
# "rf-reset", "rf-email-confirm", "rf-csv-export", etc.
def _serializer() -> URLSafeSerializer:
    return URLSafeSerializer(get_settings().session_secret, salt="rf-session")


def sign_session_id(session_id: uuid.UUID) -> str:
    return _serializer().dumps(str(session_id))


def unsign_session_id(signed: str) -> uuid.UUID | None:
    try:
        raw: str = _serializer().loads(signed)
    except BadData:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


# --- Short-lived signed MFA tokens. Each type gets its OWN salt (see the
# salt-convention note above _serializer) so a leaked/expired token of one
# kind can never validate as another. Timed (not plain) serializers, since
# these represent an in-progress login/enrollment step that must expire. ---
def _mfa_pending_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().session_secret, salt="rf-mfa-pending")


def sign_mfa_pending(user_id: uuid.UUID) -> str:
    return _mfa_pending_serializer().dumps(str(user_id))


def load_mfa_pending(token: str, max_age: int = 300) -> uuid.UUID | None:
    try:
        raw: str = _mfa_pending_serializer().loads(token, max_age=max_age)
    except BadData:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


def _webauthn_challenge_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().session_secret, salt="rf-webauthn-challenge")


def sign_webauthn_challenge(challenge_b64url: str) -> str:
    return _webauthn_challenge_serializer().dumps(challenge_b64url)


def load_webauthn_challenge(token: str, max_age: int = 300) -> str | None:
    try:
        value: str = _webauthn_challenge_serializer().loads(token, max_age=max_age)
    except BadData:
        return None
    return value


def _totp_pending_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().session_secret, salt="rf-totp-pending")


def sign_totp_pending(secret: str) -> str:
    return _totp_pending_serializer().dumps(secret)


def load_totp_pending(token: str, max_age: int = 600) -> str | None:
    try:
        value: str = _totp_pending_serializer().loads(token, max_age=max_age)
    except BadData:
        return None
    return value


# --- Short-lived MFA cookie helpers (DRY: one place derives the secure flag,
# mirroring set_session_cookie — plan-gate: prevents a missed secure= flag). ---
def _secure() -> bool:
    return get_settings().environment == "prod"


def set_mfa_pending_cookie(response: Response, user_id: uuid.UUID) -> None:
    response.set_cookie(
        "rf_mfa_pending",
        sign_mfa_pending(user_id),
        max_age=300,
        httponly=True,
        samesite="lax",
        secure=_secure(),
        path="/",
    )


def clear_mfa_pending_cookie(response: Response) -> None:
    response.delete_cookie("rf_mfa_pending", path="/")


def set_webauthn_challenge_cookie(response: Response, challenge_b64url: str) -> None:
    response.set_cookie(
        "rf_webauthn_challenge",
        sign_webauthn_challenge(challenge_b64url),
        max_age=300,
        httponly=True,
        samesite="lax",
        secure=_secure(),
        path="/",
    )


def clear_webauthn_challenge_cookie(response: Response) -> None:
    response.delete_cookie("rf_webauthn_challenge", path="/")


def set_totp_pending_cookie(response: Response, secret: str) -> None:
    response.set_cookie(
        "rf_totp_pending",
        sign_totp_pending(secret),
        max_age=600,
        httponly=True,
        samesite="lax",
        secure=_secure(),
        path="/",
    )


def clear_totp_pending_cookie(response: Response) -> None:
    response.delete_cookie("rf_totp_pending", path="/")


# --- Step-up WebAuthn challenge: DISTINCT salt + cookie from the login/
# registration ceremony (P2 plan-gate Sec-N1). Same payload shape, different
# PURPOSE: a challenge minted for the anonymous login ceremony must never
# validate for an authenticated step-up re-verify, per the salt convention
# above _serializer. ---
def _webauthn_stepup_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().session_secret, salt="rf-webauthn-stepup")


def sign_webauthn_stepup_challenge(challenge_b64url: str) -> str:
    return _webauthn_stepup_serializer().dumps(challenge_b64url)


def load_webauthn_stepup_challenge(token: str, max_age: int = 300) -> str | None:
    try:
        value: str = _webauthn_stepup_serializer().loads(token, max_age=max_age)
    except BadData:
        return None
    return value


def set_webauthn_stepup_challenge_cookie(response: Response, challenge_b64url: str) -> None:
    response.set_cookie(
        "rf_webauthn_stepup",
        sign_webauthn_stepup_challenge(challenge_b64url),
        max_age=300,
        httponly=True,
        samesite="lax",
        secure=_secure(),
        path="/",
    )


def clear_webauthn_stepup_challenge_cookie(response: Response) -> None:
    response.delete_cookie("rf_webauthn_stepup", path="/")


async def create_session(db: AsyncSession, user_id: uuid.UUID, ip: str | None) -> AuthSession:
    now = datetime.now(UTC)
    # Invariant: load_active_session assumes expires_at is UTC-aware (see auth.py
    # tz-reattach comment). Guard the only current write path so naive datetimes
    # can never land in the column.
    assert now.tzinfo is UTC, "create_session must persist UTC-aware datetimes"  # noqa: S101
    sess = AuthSession(
        id=uuid.uuid4(),
        user_id=user_id,
        created_at=now,
        last_seen_at=now,
        expires_at=now + SESSION_TTL,
        ip_address=ip,
        reauthenticated_at=now,  # login IS a re-auth (design §Step-up)
    )
    db.add(sess)
    return sess


def set_session_cookie(response: Response, session_id: uuid.UUID) -> None:
    """Attach a signed idraa_session cookie to the outgoing response.

    Mirrors CSRFMiddleware's precedent: Secure is gated on environment=="prod"
    because dev/test use http:// where a Secure cookie would be silently dropped.
    samesite="lax" permits login-via-external-link (OAuth, email-confirm flows)
    to carry the cookie; httponly=True blocks JS from reading it; path="/" so
    the cookie applies to every path including the ones that will carry
    authenticated API calls in later phases.
    """
    settings = get_settings()
    response.set_cookie(
        SESSION_COOKIE,
        sign_session_id(session_id),
        httponly=True,
        samesite="lax",
        secure=(settings.environment == "prod"),
        max_age=int(SESSION_TTL.total_seconds()),
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    """Expire the idraa_session cookie on the outgoing response.

    Mirrors set_session_cookie's attribute set: Starlette's delete_cookie
    echoes `path` and nothing else by default; browsers may treat a mismatch
    on other attributes (SameSite, Secure) as a non-matching cookie and
    leave the original in place. Future subdomain / cross-origin
    deployments make this stricter — encode every attribute once here.
    """
    settings = get_settings()
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        samesite="lax",
        secure=(settings.environment == "prod"),
        httponly=True,
    )


async def load_active_session(db: AsyncSession, session_id: uuid.UUID) -> AuthSession | None:
    sess = await db.get(AuthSession, session_id)
    if sess is None:
        return None
    # SQLite's aiosqlite driver strips tzinfo on DateTime(timezone=True) columns
    # when a row is first read by a session that did NOT originate it (cross-engine
    # / cross-connection read). Our write path always stores UTC-aware datetimes
    # via create_session, so a naive value here is known to be UTC — re-attach
    # tzinfo before comparing rather than crashing on
    # "can't compare offset-naive and offset-aware datetimes". Postgres is
    # unaffected (its timestamptz column carries UTC through the driver).
    expires_at = sess.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= datetime.now(UTC):
        return None
    return sess


# --- Minimal login throttle (idraa#81 slice, plan-gate B1). Per-account only
# (no per-IP dimension yet — full idraa#81 management UI/admin unlock/per-IP
# throttle stays P3). ---
def is_locked(user: User) -> bool:
    lu = user.locked_until
    if lu is None:
        return False
    if lu.tzinfo is None:  # aiosqlite may strip tzinfo on cross-connection read
        lu = lu.replace(tzinfo=UTC)
    return lu > datetime.now(UTC)


def register_failed_login(user: User) -> None:
    settings = get_settings()
    user.failed_login_count += 1
    if (
        settings.auth_max_failed_logins
        and user.failed_login_count >= settings.auth_max_failed_logins
    ):
        user.locked_until = datetime.now(UTC) + timedelta(seconds=settings.auth_lockout_seconds)


def reset_login_throttle(user: User) -> None:
    user.failed_login_count = 0
    user.locked_until = None


def is_step_up_fresh(sess: AuthSession) -> bool:
    """True when the session's last re-auth is inside the step-up window.

    max_age == 0 disables step-up (operator opt-out, mirrors
    auth_max_failed_logins). A NULL reauthenticated_at (pre-P2 row) is
    stale — fail closed, the user re-verifies once and gets stamped.
    """
    max_age = get_settings().auth_step_up_max_age_seconds
    if max_age == 0:
        return True
    ra = sess.reauthenticated_at
    if ra is None:
        return False
    if ra.tzinfo is None:  # aiosqlite may strip tzinfo on cross-connection read
        ra = ra.replace(tzinfo=UTC)
    return datetime.now(UTC) - ra <= timedelta(seconds=max_age)


async def load_user_by_email(db: AsyncSession, email: str) -> User | None:
    # Normalize email: lowercase + strip whitespace. Trailing spaces from form
    # input would otherwise false-mismatch the (organization_id, email) unique
    # constraint at insert time and the equality lookup here.
    result = await db.execute(select(User).where(User.email == email.lower().strip()))
    return result.scalars().first()
