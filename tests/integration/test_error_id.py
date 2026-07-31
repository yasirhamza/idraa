"""idraa#72 (fix 4) — diagnosable 500s: correlation id on response + log.

The #72 investigation dead-ended because Fly's log buffer had rotated by the
time the owner-reported 500 was investigated — no traceback, no way to tie
the user's screenshot to a server-side event. Every sanctioned 500 shape now
mints a short error id, puts it in the response body AND an X-Error-Id
header, and logs the full traceback under the same id.

Two covered paths (both through app._internal_error_response):
- truly uncaught exceptions (the Exception handler);
- raised ``HTTPException(5xx)`` (review finding I2 — these previously shipped
  ``exc.detail``, an internal string, to the client via the JSON fallback,
  with no id).

Test-harness notes (review findings B1/B2):
- Routes are registered under ``/login/`` — a setup-guard ALLOWLIST DIR
  PREFIX — so an empty schema-less DB never intercepts the request
  (``/healthz`` is exact-match only; a subpath 500s inside the guard itself,
  which is a DIFFERENT exception than the one under test).
- A ``reached`` flag proves the route body executed — without it the
  assertions pass vacuously against a guard-raised exception.
- ``db.reset_for_tests()`` + ``config.reset_for_tests()`` run in try/finally:
  the setup-guard lazily caches ``db._engine`` against this test's tmp DB,
  and a leaked engine breaks the NEXT client-fixture test (the documented
  #108 leak mode).
"""

from __future__ import annotations

import contextlib
import logging
import re
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

import idraa.db as db
from idraa import config


@contextlib.asynccontextmanager
async def _fresh_app_client(db_url: str) -> AsyncIterator[tuple[AsyncClient, object]]:
    """A bare app + client on an isolated DB, with engine-leak hygiene."""
    from idraa.app import create_app

    try:
        config.reset_for_tests()
        db.reset_for_tests()
        app = create_app()
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, app
    finally:
        db.reset_for_tests()
        config.reset_for_tests()


@pytest.mark.asyncio
async def test_unhandled_500_carries_error_id(
    monkeypatch: pytest.MonkeyPatch, db_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("DATABASE_URL", db_url)
    reached: list[bool] = []

    async with _fresh_app_client(db_url) as (client, app):

        @app.get("/login/boom-test-route")  # /login/ = allowlist dir prefix
        async def _boom() -> None:
            reached.append(True)
            raise RuntimeError("intentional test explosion")

        with caplog.at_level(logging.ERROR):
            r = await client.get("/login/boom-test-route")

    assert reached, "route body never executed — test would be vacuous (B2)"
    assert r.status_code == 500
    m = re.search(r"Error ID: ([0-9a-f]{12})", r.text)
    assert m, f"no error id in 500 body: {r.text!r}"
    error_id = m.group(1)
    assert r.headers.get("X-Error-Id") == error_id
    # No internal detail may leak to the client (pre-existing contract).
    assert "intentional test explosion" not in r.text
    assert "RuntimeError" not in r.text

    matching = [
        rec
        for rec in caplog.records
        if error_id in rec.getMessage() and rec.levelno >= logging.ERROR
    ]
    assert matching, "no ERROR log record carries the error id"
    assert any(rec.exc_info for rec in matching), "log record lost the traceback"


@pytest.mark.asyncio
async def test_http_500_exception_gets_id_and_leaks_no_detail(
    monkeypatch: pytest.MonkeyPatch, db_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Review I2: HTTPException(5xx) must not ship exc.detail to the client.

    The finalize path raises HTTPException(500, "re-estimate draft missing
    its row-version capture") and the reports routes raise bare 500s — these
    used to hit the JSON fallback (detail leaked, no id).
    """
    from fastapi import HTTPException

    monkeypatch.setenv("DATABASE_URL", db_url)
    reached: list[bool] = []

    async with _fresh_app_client(db_url) as (client, app):

        @app.get("/login/http500-test-route")
        async def _internal() -> None:
            reached.append(True)
            raise HTTPException(500, "internal secret detail")

        with caplog.at_level(logging.ERROR):
            r = await client.get("/login/http500-test-route")

    assert reached
    assert r.status_code == 500
    assert "internal secret detail" not in r.text  # the leak this fix closes
    m = re.search(r"Error ID: ([0-9a-f]{12})", r.text)
    assert m
    assert r.headers.get("X-Error-Id") == m.group(1)
    # The detail is preserved SERVER-side for diagnosis.
    joined = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "internal secret detail" in joined
    assert m.group(1) in joined


@pytest.mark.asyncio
async def test_http_4xx_detail_passthrough_unchanged(
    monkeypatch: pytest.MonkeyPatch, db_url: str
) -> None:
    """4xx HTTPExceptions keep their JSON detail contract (no drift)."""
    from fastapi import HTTPException

    monkeypatch.setenv("DATABASE_URL", db_url)

    async with _fresh_app_client(db_url) as (client, app):

        @app.get("/login/http404-test-route")
        async def _notfound() -> None:
            raise HTTPException(404, "thing not found")

        r = await client.get("/login/http404-test-route")

    assert r.status_code == 404
    assert r.json() == {"detail": "thing not found"}
    assert "X-Error-Id" not in r.headers


@pytest.mark.asyncio
async def test_error_ids_are_unique_per_failure(
    monkeypatch: pytest.MonkeyPatch, db_url: str
) -> None:
    monkeypatch.setenv("DATABASE_URL", db_url)

    async with _fresh_app_client(db_url) as (client, app):

        @app.get("/login/boom-test-route")
        async def _boom() -> None:
            raise RuntimeError("boom")

        r1 = await client.get("/login/boom-test-route")
        r2 = await client.get("/login/boom-test-route")

    assert r1.headers["X-Error-Id"] != r2.headers["X-Error-Id"]
