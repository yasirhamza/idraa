"""Request-framing guard (HTTP desync defence in depth, advisory GHSA-46jj-823j-mjj9)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from idraa.middleware.request_framing import RequestFramingMiddleware, framing_violation


def _scope(method: str, headers: dict[str, str] | None = None, version: str = "1.1") -> dict:
    return {
        "type": "http",
        "method": method,
        "http_version": version,
        "headers": [(k.encode(), v.encode()) for k, v in (headers or {}).items()],
    }


@pytest.mark.parametrize(
    "method,headers,version,expected",
    [
        ("GET", {}, "1.1", None),
        ("GET", {"content-length": "0"}, "1.1", None),
        ("POST", {"content-length": "12"}, "1.1", None),
        ("POST", {"transfer-encoding": "chunked"}, "1.1", None),
        ("PATCH", {"content-length": "3"}, "1.1", None),
        ("OPTIONS", {}, "1.1", None),
        ("GET", {"content-length": "5"}, "1.1", 400),
        ("HEAD", {"content-length": "5"}, "1.1", 400),
        ("OPTIONS", {"content-length": "1"}, "1.1", 400),
        ("GET", {"transfer-encoding": "chunked"}, "1.1", 400),
        ("POST", {"transfer-encoding": "chunked"}, "1.0", 400),
        ("TRACE", {}, "1.1", 501),
        ("CONNECT", {}, "1.1", 501),
        ("PROPFIND", {}, "1.1", 501),
    ],
)
def test_framing_violation(method, headers, version, expected) -> None:
    result = framing_violation(_scope(method, headers, version))
    assert (result[0] if result else None) == expected


async def _echo(scope, receive, send) -> None:
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


@pytest.fixture
async def client():
    transport = ASGITransport(app=RequestFramingMiddleware(_echo))
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        yield ac


async def test_get_with_body_is_refused_and_closes(client) -> None:
    r = await client.request("GET", "/", content=b"smuggled")
    assert r.status_code == 400
    assert r.headers["connection"] == "close"


async def test_unknown_method_is_501_with_allow(client) -> None:
    r = await client.request("PROPFIND", "/")
    assert r.status_code == 501
    assert "POST" in r.headers["allow"] and "TRACE" not in r.headers["allow"]


async def test_ordinary_requests_pass(client) -> None:
    assert (await client.get("/")).status_code == 200
    assert (await client.post("/", content=b"a=1")).status_code == 200


async def test_app_wires_the_guard_outermost() -> None:
    """The real app refuses a GET body before routing, auth or CSRF run."""
    from idraa.app import create_app

    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://t") as ac:
        r = await ac.request("GET", "/healthz", content=b"x")
        assert r.status_code == 400
        assert (await ac.get("/healthz")).status_code == 200
