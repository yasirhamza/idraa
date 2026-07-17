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
- POST ``/logout`` deletes the current AuthSession row, writes a ``logout``
  AuditLog row (only when a user is attributed — stale-cookie logouts have
  no user context), clears the cookie, and 303-redirects to /login.

Commit is owned by the ``get_db`` dependency (see
``routes/deps.py::get_db`` + ``db.py::get_session``): the context manager
auto-commits on successful exit. Handlers here do NOT call
``await db.commit()`` directly.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.app import templates
from idraa.models.session import AuthSession
from idraa.models.user import User
from idraa.routes.deps import (
    client_ip,
    current_session,
    current_user,
    get_db,
)
from idraa.services.audit import AuditWriter
from idraa.services.auth import (
    clear_session_cookie,
    create_session,
    load_user_by_email,
    set_session_cookie,
    verify_user_password,
)

router = APIRouter()


def _safe_next(raw: str | None) -> str:
    """Sanitize a ``?next=`` redirect target.

    Returns ``raw`` only when it is a same-origin absolute path: must start
    with a single ``/`` and NOT with ``//`` (protocol-relative URLs like
    ``//evil.example`` are an open-redirect vector — the browser treats them
    as ``https://evil.example``). Anything else falls back to ``/``.
    """
    if raw and raw.startswith("/") and not raw.startswith("//"):
        return raw
    return "/"


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
    if not verify_user_password(user, password):
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

    # user is non-None here: verify_user_password returns False for None.
    # Narrow for mypy without an extra assert; the branch above returned.
    assert user is not None  # noqa: S101

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
