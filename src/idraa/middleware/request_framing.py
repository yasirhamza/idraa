"""Request-framing guard — HTTP desync defence in depth (advisory GHSA-46jj-823j-mjj9).

The back-end parser (uvicorn + httptools) already rejects every classic
smuggling gadget (dual Content-Length, CL.TE, TE.CL, obfuscated TE, bare LF).
Two shapes it accepts are harmless on their own but become desync gadgets if
the front-end proxy (Fly's edge) frames them differently:

- a body on GET / HEAD / OPTIONS (accepted and consumed today), and
- ``Transfer-Encoding`` on an HTTP/1.0 request (chunked framing does not
  exist in 1.0, so the two hops may disagree on where the request ends).

Following PortSwigger's back-end mitigation, this OUTERMOST pure-ASGI layer
rejects both with 400 and answers any method outside the allow-list with 405
(+ ``Allow``; a 5xx would read as a server error to monitoring and the DAST job),
before any other middleware reads the request. Every rejection carries
``Connection: close`` so an unread body can never be parsed as the next
request on a kept-alive connection. Browsers, HTMX and the platform health
probe never send any of these shapes, so legitimate traffic is unaffected.
"""

from __future__ import annotations

import logging

from starlette.types import ASGIApp, Receive, Scope, Send

ALLOWED_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
_BODYLESS_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

logger = logging.getLogger(__name__)


def framing_violation(scope: Scope) -> tuple[int, bytes] | None:
    """(status, reason) when the request must be refused, else None."""
    method = scope["method"]
    if method not in ALLOWED_METHODS:
        return 405, b"Method not allowed"
    has_te = False
    content_length: bytes | None = None
    for name, value in scope["headers"]:
        if name == b"transfer-encoding":
            has_te = True
        elif name == b"content-length":
            content_length = value.strip()
    if has_te and scope.get("http_version") == "1.0":
        return 400, b"Transfer-Encoding is not valid on HTTP/1.0"
    if method in _BODYLESS_METHODS and (has_te or content_length not in (None, b"0")):
        return 400, b"Request body not allowed for this method"
    return None


class RequestFramingMiddleware:
    """Refuse ambiguous request framing before the rest of the stack runs."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        violation = framing_violation(scope)
        if violation is None:
            await self.app(scope, receive, send)
            return
        status, reason = violation
        # One line per refusal: the guard is outermost, so a false positive would
        # otherwise block a request shape with no trace in the app log.
        logger.warning(
            "request_framing refused %r %r (HTTP/%r): %s",
            scope["method"],
            scope.get("path", ""),
            scope.get("http_version", "?"),
            reason.decode(),
        )
        headers = [
            (b"content-type", b"text/plain; charset=utf-8"),
            (b"connection", b"close"),
            (b"x-content-type-options", b"nosniff"),
        ]
        if status == 405:
            headers.append((b"allow", ", ".join(sorted(ALLOWED_METHODS)).encode()))
        body = b"" if scope["method"] == "HEAD" else reason
        headers.append((b"content-length", str(len(body)).encode()))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})
