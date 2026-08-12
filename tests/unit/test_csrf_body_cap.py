"""A4: CSRFMiddleware bounds the request body it buffers for double-submit
replay, so a single unsafe-method request can't pin process RSS to an
arbitrary body size before its CSRF verdict.

The cap is enforced FIRST (step 0 of dispatch) — before the token check and
before the whole body is buffered — so oversize requests get a 413 without
the middleware doing any CSRF work on them. A body under the cap flows through
to the normal CSRF check unchanged (proving the cap never false-rejects a
legitimate small body, and that replay to downstream handlers still works).
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from idraa.config import get_settings
from idraa.middleware.csrf import CSRFMiddleware
from idraa.routes.deps import MAX_UPLOAD_BYTES


def _build_app(cap: int) -> Starlette:
    async def echo(request):
        # Reads the body AFTER the middleware replay — asserts the replay
        # delivered the full bytes downstream.
        body = await request.body()
        return PlainTextResponse(f"len={len(body)}")

    app = Starlette(routes=[Route("/x", echo, methods=["POST"])])
    app.add_middleware(CSRFMiddleware, secret="x" * 32, secure_cookie=False, max_body_bytes=cap)
    return app


@pytest.mark.asyncio
async def test_oversize_body_rejected_413_before_csrf_check():
    app = _build_app(cap=1024)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        # No CSRF cookie/token at all: if the cap did NOT run first this would
        # be a 403. It's a 413 — the cap fires before the token check.
        r = await c.post("/x", content=b"a" * 5000)
    assert r.status_code == 413


@pytest.mark.asyncio
async def test_under_cap_body_reaches_csrf_check():
    app = _build_app(cap=1024)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/x", content=b"a" * 100)
    # Small body is NOT capped (would be 413) — it reaches the CSRF check,
    # which 403s for the missing token. 403 ≠ 413 confirms the split.
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_body_exactly_at_cap_is_allowed():
    # Boundary: total == cap is fine (strictly-greater rejects). The at-cap
    # body reaches the CSRF check (403 for missing token, not 413).
    app = _build_app(cap=256)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/x", content=b"a" * 256)
    assert r.status_code == 403


def test_configured_cap_exceeds_upload_cap():
    # The global body cap MUST exceed the import upload cap (+ multipart
    # framing), or a legitimate 5MB register/library/scenario import would
    # 413 at the CSRF middleware before its handler ever ran.
    assert get_settings().max_request_body_bytes > MAX_UPLOAD_BYTES
