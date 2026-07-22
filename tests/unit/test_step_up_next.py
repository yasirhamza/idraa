"""Unit tests for routes.deps._step_up_next — the step-up return-URL builder.

Covers the branches the integration catalog test exercises only indirectly:
the POST Referer fallback, its cross-origin rejection, and the GET
path+query round-trip. The open-redirect guarantee itself lives in
``safe_next`` (tested via the login suite); these pin that ``_step_up_next``
routes every input THROUGH ``safe_next`` and rejects a foreign Referer
before it can even reach it.
"""

from __future__ import annotations

from starlette.requests import Request

from idraa.routes.deps import _step_up_next


def _request(method: str, path: str, query: str = "", *, host: str, referer: str | None) -> Request:
    headers = [(b"host", host.encode())]
    if referer is not None:
        headers.append((b"referer", referer.encode()))
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "headers": headers,
        "scheme": "https",
        "server": (host, 443),
    }
    return Request(scope)


def test_get_round_trips_path_and_query() -> None:
    req = _request("GET", "/users/export.csv", "format=full", host="app.example", referer=None)
    assert _step_up_next(req) == "/users/export.csv?format=full"


def test_post_uses_same_origin_referer_path() -> None:
    req = _request(
        "POST",
        "/users/x/delete",
        host="app.example",
        referer="https://app.example/users?page=2",
    )
    assert _step_up_next(req) == "/users?page=2"


def test_post_cross_origin_referer_collapses_to_root() -> None:
    req = _request(
        "POST",
        "/users/x/delete",
        host="app.example",
        referer="https://evil.example/users",
    )
    assert _step_up_next(req) == "/"


def test_post_missing_referer_collapses_to_root() -> None:
    req = _request("POST", "/users/x/delete", host="app.example", referer=None)
    assert _step_up_next(req) == "/"


def test_post_protocol_relative_referer_path_is_sanitized() -> None:
    # A same-origin Referer whose PATH is itself an open-redirect vector
    # (//evil) must still collapse to "/" via the trailing safe_next.
    req = _request(
        "POST",
        "/users/x/delete",
        host="app.example",
        referer="https://app.example//evil.example/phish",
    )
    assert _step_up_next(req) == "/"
