"""Common FastAPI dependencies: db session, current_user, role check."""

from __future__ import annotations

import ipaddress
from collections.abc import AsyncIterator, Callable
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.config import get_settings
from idraa.db import get_session
from idraa.errors import StepUpRequired
from idraa.models.enums import StepUpCategory, UserRole
from idraa.models.session import AuthSession
from idraa.models.user import User
from idraa.services.auth import is_step_up_fresh

MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB — caps bulk-import uploads across all endpoints


async def get_db() -> AsyncIterator[AsyncSession]:
    """Request-scoped session. OWNS the terminal commit.

    ``get_session()`` auto-commits on successful handler exit and rolls
    back on any unhandled exception — handlers do NOT need a trailing
    ``await db.commit()``.

    Convention (transaction-ownership, whole-project-eval hygiene): an
    explicit ``db.commit()`` inside a handler is reserved for MID-REQUEST
    visibility needs — e.g. the row must be durably visible to another
    session before the handler returns (background-task dispatch reading
    via its own engine session, multi-step wizard flushes that survive a
    later in-handler failure). A ``db.commit()`` as the last data
    statement before building the response is redundant with this
    dependency — harmless, but don't add new ones.
    """
    async with get_session() as db:
        yield db


def current_user(request: Request) -> User | None:
    return getattr(request.state, "user", None)


def current_session(request: Request) -> AuthSession | None:
    return getattr(request.state, "session", None)


def client_ip(request: Request) -> str | None:
    """Return the client IP from request.client, or None if unavailable.

    request.client is None in some ASGI test contexts (bare Request objects
    constructed without a transport) and in explicit-no-client scenarios.
    Handlers that persist the IP should tolerate None — the audit / session
    rows accept nullable ip_address for this reason.

    NOT proxy-aware. Returns the DIRECT peer IP; behind a reverse proxy
    this is the proxy, not the user. Honoring X-Forwarded-For / Forwarded
    requires a trusted-proxy allowlist gated in Settings — do NOT let this
    helper silently read those headers without a trust boundary.

    Not a FastAPI dependency — a simple free function. Handlers call it as
    ``client_ip(request)``, not ``Depends(client_ip)``. Factoring out of
    ``routes/setup.py`` and ``routes/auth.py`` so the third/fourth copy
    of the ``request.client.host if request.client else None`` idiom
    doesn't land in future phases.
    """
    return request.client.host if request.client else None


def _normalize_ip(ip: str) -> str:
    """IPv6 -> /64 network address (defeats free /64 rotation); IPv4 unchanged."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if isinstance(addr, ipaddress.IPv6Address):
        return str(ipaddress.ip_network(f"{ip}/64", strict=False).network_address)
    return ip


def resolve_throttle_source(request: Request, *, surface: str) -> str | None:
    """Trusted client IP for throttling, namespaced by ``surface`` (e.g. "login").

    Returns ``None`` (throttle no-ops) when no configured strategy positively
    yields a client IP. NEVER falls back to ``request.client.host`` — behind the
    prod edge that is the spoofable leftmost X-Forwarded-For, not the client.
    """
    s = get_settings()
    ip: str | None = None
    if s.trusted_client_ip_header:  # shape 1: dedicated header
        val = request.headers.get(s.trusted_client_ip_header)
        if val:
            ip = val.split(",")[0].strip()  # single-valued; split is defensive
    elif s.trusted_proxy_count > 0:  # shape 2: XFF + known hop count
        parts = [
            p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()
        ]
        # N trusted proxies each APPEND one entry; the client is the LEFTMOST of
        # those N rightmost == parts[len - N] (Werkzeug ProxyFix / nginx real_ip).
        # A client can only PREPEND forged entries to the LEFT of that boundary.
        idx = len(parts) - s.trusted_proxy_count
        if 0 <= idx < len(parts):
            ip = parts[idx]
    if ip is None:
        return None
    return f"{surface}:{_normalize_ip(ip)}"


def require_user(user: User | None = Depends(current_user)) -> User:
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user


def require_role(*roles: UserRole) -> Callable[[User], User]:
    """Build a FastAPI dependency that enforces a role allowlist.

    Usage::

        @router.get("/users", dependencies=[Depends(require_role(UserRole.ADMIN))])
        async def list_users(...): ...
    """

    def _checker(user: User = Depends(require_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
        return user

    return _checker


def safe_next(raw: str | None) -> str:
    """Sanitize a ``?next=`` redirect target (relocated from routes/auth.py).

    Returns ``raw`` only when it is a same-origin absolute path: must start
    with a single ``/`` and NOT with ``//`` or ``/\\`` (browsers normalize a
    leading backslash to a forward slash for special schemes, so ``/\\evil``
    is an equivalent protocol-relative open-redirect vector to ``//evil``).
    Anything else falls back to ``/``.
    """
    if raw and raw.startswith("/") and raw[1:2] not in ("/", "\\"):
        return raw
    return "/"


def _step_up_next(request: Request) -> str:
    """The URL the user should land on after passing the step-up challenge.

    GET targets round-trip themselves (path + query — safe to re-issue).
    POST targets cannot be replayed by a redirect, so fall back to the
    same-origin Referer path (the page holding the form/button); the user
    re-triggers the action, which then passes the fresh check.
    """
    if request.method == "GET":
        target = request.url.path
        if request.url.query:
            target = f"{target}?{request.url.query}"
        return safe_next(target)
    ref = request.headers.get("referer", "")
    parts = urlsplit(ref)
    if parts.netloc and parts.netloc != request.url.netloc:
        return "/"
    target = parts.path or "/"
    if parts.query:
        target = f"{target}?{parts.query}"
    return safe_next(target)


def require_recent_auth(
    request: Request,
    user: User | None = Depends(current_user),
    sess: AuthSession | None = Depends(current_session),
) -> None:
    """Step-up ("sudo mode") gate for sensitive actions.

    Wire as a ROUTE-DECORATOR dependency so it runs before handler params::

        @router.post("/x/delete", dependencies=[Depends(require_recent_auth)])

    Anonymous callers get the same 401 as require_user (-> /login redirect
    via _auth_redirect_handler). Stale sessions raise StepUpRequired, which
    app.py::_step_up_handler turns into the /auth/step-up challenge.
    """
    if user is None or sess is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    if not is_step_up_fresh(sess):
        raise StepUpRequired(next_url=_step_up_next(request))


def require_step_up(
    category: StepUpCategory,
) -> Callable[[Request, User | None, AuthSession | None], None]:
    """Per-category step-up gate. Wire as a route dependency:
    @router.get("/x/export.csv", dependencies=[Depends(require_step_up(StepUpCategory.EXPORTS))])
    """

    def _dep(
        request: Request,
        user: User | None = Depends(current_user),
        sess: AuthSession | None = Depends(current_session),
    ) -> None:
        if user is None or sess is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
            )
        from idraa.services.security_settings import step_up_required

        if step_up_required(category) and not is_step_up_fresh(sess):
            raise StepUpRequired(next_url=_step_up_next(request))

    return _dep
