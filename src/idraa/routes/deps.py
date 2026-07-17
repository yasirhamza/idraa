"""Common FastAPI dependencies: db session, current_user, role check."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.db import get_session
from idraa.models.enums import UserRole
from idraa.models.session import AuthSession
from idraa.models.user import User

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
