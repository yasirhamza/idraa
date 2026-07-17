"""Load the current session + user onto request.state from the signed cookie.

Runs INSIDE CSRFMiddleware on the wire (order set in ``app.py``):

    request  -> SecurityHeaders -> CSRF -> Session -> route
    response <- SecurityHeaders <- CSRF <- Session <- route

Rationale: session lookup hits the DB, so CSRF should reject forgeries
BEFORE we burn a DB round trip.

Observability: a non-empty cookie whose signature fails ``unsign_session_id``
is logged at WARNING. That is the attacker-facing "tampered cookie" signal
the SOC wants to see. The "no cookie at all" path is silent — that's just
an anonymous user.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from idraa.db import get_session
from idraa.models.user import User
from idraa.services.auth import SESSION_COOKIE, load_active_session, unsign_session_id

log = logging.getLogger(__name__)


class SessionMiddleware(BaseHTTPMiddleware):
    """Reads ``idraa_session`` cookie, populates request.state.user + request.state.session."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.state.user = None
        request.state.session = None

        signed = request.cookies.get(SESSION_COOKIE)
        if signed:
            session_id = unsign_session_id(signed)
            if session_id is None:
                # Forged/tampered/truncated cookie. Log once; fall through as anon.
                log.warning("rejected session cookie with invalid signature or payload")
            else:
                # Absolute session expiry: load_active_session checks expires_at but does NOT
                # extend it on use. 14d TTL is set at create_session time and does not slide.
                # Rationale: simpler mental model for a small-team internal tool; stolen-cookie
                # blast radius is bounded by SESSION_TTL. Revisit if the user base grows.
                async with get_session() as db:
                    sess = await load_active_session(db, session_id)
                    if sess is not None:
                        user = await db.get(User, sess.user_id)
                        if user is not None and user.is_active:
                            request.state.user = user
                            request.state.session = sess

        response: Response = await call_next(request)
        return response
