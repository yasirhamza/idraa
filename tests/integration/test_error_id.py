"""idraa#72 (fix 4) — diagnosable 500s: correlation id on response + log.

The #72 investigation dead-ended because Fly's log buffer had rotated by the
time the owner-reported 500 was investigated — no traceback, no way to tie
the user's screenshot to a server-side event. The unhandled-exception handler
now mints a short error id, puts it in the response body AND an X-Error-Id
header, and logs the full traceback under the same id — so a user report
carrying the id can be matched to logs (or its absence proven) even later.
"""

from __future__ import annotations

import logging
import re

import pytest
from httpx import ASGITransport, AsyncClient

from idraa import config


@pytest.mark.asyncio
async def test_unhandled_500_carries_error_id(
    monkeypatch: pytest.MonkeyPatch, db_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("DATABASE_URL", db_url)
    config.reset_for_tests()
    from idraa.app import create_app

    app = create_app()

    # Registered under /healthz/ so the setup-guard allowlist admits it on an
    # empty DB — the test targets the exception handler, not the guard.
    @app.get("/healthz/boom-test-route")
    async def _boom() -> None:
        raise RuntimeError("intentional test explosion")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    with caplog.at_level(logging.ERROR):
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/healthz/boom-test-route")

    assert r.status_code == 500
    m = re.search(r"Error ID: ([0-9a-f]{12})", r.text)
    assert m, f"no error id in 500 body: {r.text!r}"
    error_id = m.group(1)
    assert r.headers.get("X-Error-Id") == error_id
    # No internal detail may leak to the client (pre-existing contract).
    assert "intentional test explosion" not in r.text
    assert "RuntimeError" not in r.text

    # The server-side log line carries the SAME id (+ the traceback via
    # exc_info), so a user-reported id is greppable to a full stack.
    matching = [
        rec
        for rec in caplog.records
        if error_id in rec.getMessage() and rec.levelno >= logging.ERROR
    ]
    assert matching, "no ERROR log record carries the error id"
    assert any(rec.exc_info for rec in matching), "log record lost the traceback"


@pytest.mark.asyncio
async def test_error_ids_are_unique_per_failure(
    monkeypatch: pytest.MonkeyPatch, db_url: str
) -> None:
    monkeypatch.setenv("DATABASE_URL", db_url)
    config.reset_for_tests()
    from idraa.app import create_app

    app = create_app()

    @app.get("/healthz/boom-test-route")
    async def _boom() -> None:
        raise RuntimeError("boom")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r1 = await c.get("/healthz/boom-test-route")
        r2 = await c.get("/healthz/boom-test-route")
    assert r1.headers["X-Error-Id"] != r2.headers["X-Error-Id"]
