"""4xx JSON error bodies carry no markup characters on the wire.

Error details are encoded so they are inert when rendered as HTML: ``<``, ``>``
and ``&`` are serialized as JSON ``\\uXXXX`` escapes, while ``response.json()``
yields the identical value for API clients.

Covers: the draft-scenario refusal (a user-authored scenario name is part of
``detail``) on both run-creation routes, the request-validation 422 (which
echoes the rejected input), the legacy adapter's ``int()`` parse error, and
equality of the parsed value between HTMX and plain JSON callers.
"""

from __future__ import annotations

import json
import pathlib
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from fastapi import HTTPException as FastAPIHTTPException
from fastapi.exceptions import RequestValidationError
from httpx import AsyncClient, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request

from idraa.app import (
    _auth_redirect_handler,
    _request_validation_handler,
    _starlette_http_exception_handler,
    create_app,
)
from idraa.models.enums import EntityStatus
from tests.conftest import csrf_post

_NAME = "<b>name</b>"
_EXPECTED_DETAIL = f"scenario '{_NAME}' is a draft — promote it before running an analysis"
_HTMX = {"HX-Request": "true"}
_JSON = {"Accept": "application/json"}


def _assert_inert(response: Response) -> None:
    """The wire bytes carry no markup characters at all."""
    body = response.content
    for ch in (b"<", b">", b"&"):
        assert ch not in body, f"raw {ch!r} on the wire: {body!r}"


async def _draft_scenario(
    seed_scenario_factory: Callable[..., Awaitable[Any]], org_id: uuid.UUID
) -> Any:
    return await seed_scenario_factory(
        name=_NAME, status=EntityStatus.DRAFT, organization_id=org_id
    )


@pytest.mark.asyncio
async def test_4xx_detail_markup_characters_are_encoded_analyses(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    seed_scenario_factory: Callable[..., Awaitable[Any]],
) -> None:
    client, org_id = authed_analyst
    scenario = await _draft_scenario(seed_scenario_factory, org_id)

    response = await csrf_post(
        client,
        "/analyses",
        {"scenario_ids": str(scenario.id), "mc_iterations": "1000"},
        headers=_HTMX,
    )

    assert response.status_code == 422
    _assert_inert(response)
    assert b"<b>" not in response.content
    assert _NAME in response.json()["detail"]
    assert response.json() == {"detail": _EXPECTED_DETAIL}


@pytest.mark.asyncio
async def test_4xx_detail_markup_characters_are_encoded_scenario_run(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    seed_scenario_factory: Callable[..., Awaitable[Any]],
) -> None:
    client, org_id = authed_analyst
    scenario = await _draft_scenario(seed_scenario_factory, org_id)

    response = await csrf_post(
        client,
        f"/scenarios/{scenario.id}/run",
        {"mc_iterations": "1000"},
        headers=_HTMX,
    )

    assert response.status_code == 422
    _assert_inert(response)
    assert b"<b>" not in response.content
    assert _NAME in response.json()["detail"]
    assert response.json() == {"detail": _EXPECTED_DETAIL}


@pytest.mark.asyncio
async def test_json_client_and_htmx_caller_parse_identical_detail(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    seed_scenario_factory: Callable[..., Awaitable[Any]],
) -> None:
    """A plain JSON caller (no HX-Request) sees the same parsed value, same status."""
    client, org_id = authed_analyst
    scenario = await _draft_scenario(seed_scenario_factory, org_id)
    form = {"scenario_ids": str(scenario.id), "mc_iterations": "1000"}

    htmx_resp = await csrf_post(client, "/analyses", form, headers=_HTMX)
    json_resp = await csrf_post(client, "/analyses", form, headers=_JSON)

    assert htmx_resp.status_code == json_resp.status_code == 422
    assert json_resp.headers["content-type"].startswith("application/json")
    assert htmx_resp.json() == json_resp.json() == {"detail": _EXPECTED_DETAIL}
    _assert_inert(json_resp)


@pytest.mark.asyncio
async def test_validation_error_input_markup_characters_are_encoded(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
) -> None:
    """FastAPI's request-validation 422 echoes the rejected input; it is encoded too."""
    client, _ = authed_analyst
    rejected = "<i>x</i>"

    response = await csrf_post(
        client,
        "/analyses",
        {"scenario_ids": str(uuid.uuid4()), "mc_iterations": rejected},
        headers=_HTMX,
    )

    assert response.status_code == 422
    _assert_inert(response)
    detail = response.json()["detail"]
    assert isinstance(detail, list)
    assert any(item.get("input") == rejected for item in detail)


@pytest.mark.asyncio
async def test_legacy_adapter_parse_error_markup_characters_are_encoded(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    seed_scenario_factory: Callable[..., Awaitable[Any]],
) -> None:
    """The legacy adapter's int() parse failure echoes the submitted text in ``detail``."""
    client, org_id = authed_analyst
    scenario = await seed_scenario_factory(name="plain-name", organization_id=org_id)
    rejected = "<u>7</u>"

    response = await csrf_post(
        client,
        f"/scenarios/{scenario.id}/run",
        {"mc_iterations": rejected},
        headers=_HTMX,
    )

    assert response.status_code == 422
    _assert_inert(response)
    assert rejected in response.json()["detail"]


@pytest.mark.asyncio
async def test_plain_detail_status_and_bytes_unchanged(
    authed_reviewer: tuple[AsyncClient, uuid.UUID],
) -> None:
    """A detail with no markup characters is byte-for-byte what the stock handler sent."""
    client, _ = authed_reviewer

    response = await csrf_post(
        client,
        "/analyses",
        {"scenario_ids": str(uuid.uuid4()), "mc_iterations": "1000"},
        headers=_HTMX,
    )

    assert response.status_code == 403
    assert response.content == b'{"detail":"Forbidden"}'
    assert response.headers["content-type"] == "application/json"


def _bare_request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": []})


@pytest.mark.asyncio
async def test_framework_http_exception_handler_encodes_markup_characters() -> None:
    """Router/multipart-raised (bare Starlette) exceptions get the same encoding."""
    response = await _starlette_http_exception_handler(
        _bare_request(), StarletteHTTPException(400, detail="<b>&x")
    )

    assert response.status_code == 400
    body = bytes(response.body)
    assert b"<" not in body and b"&" not in body
    assert json.loads(body) == {"detail": "<b>&x"}


@pytest.mark.asyncio
async def test_framework_http_exception_handler_keeps_bodyless_statuses_empty() -> None:
    response = await _starlette_http_exception_handler(_bare_request(), StarletteHTTPException(304))

    assert response.status_code == 304
    assert bytes(response.body) == b""


@pytest.mark.asyncio
async def test_unknown_path_404_body_unchanged(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
) -> None:
    client, _ = authed_analyst

    response = await client.get("/no-such-path-here", headers=_HTMX)

    assert response.status_code == 404
    assert response.content == b'{"detail":"Not Found"}'


@pytest.mark.asyncio
async def test_framework_http_exception_5xx_gets_the_generic_body_and_error_id() -> None:
    """A bare Starlette 5xx takes the same generic-body + error-id path as every other 500."""
    response = await _starlette_http_exception_handler(
        _bare_request(), StarletteHTTPException(503, detail="internal <b> detail")
    )

    assert response.status_code == 500
    body = bytes(response.body)
    assert b"internal" not in body
    assert b"<b>" not in body
    assert body.startswith(b"Internal Server Error\nError ID: ")
    assert response.headers["x-error-id"] == body.rsplit(b" ", 1)[1].decode()


def test_error_handlers_are_registered_for_each_exception_class() -> None:
    """Each JSON error producer is wired to its encoding handler (registry check)."""
    handlers = create_app().exception_handlers

    assert handlers[StarletteHTTPException] is _starlette_http_exception_handler
    assert handlers[RequestValidationError] is _request_validation_handler
    assert handlers[FastAPIHTTPException] is _auth_redirect_handler


@pytest.mark.asyncio
async def test_router_405_keeps_allow_header_and_plain_json_body(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
) -> None:
    """The router's own 405 goes through the framework handler: header and body preserved."""
    client, _ = authed_analyst

    response = await client.get(f"/scenarios/{uuid.uuid4()}/run", headers=_HTMX)

    assert response.status_code == 405
    assert "POST" in response.headers["allow"]
    assert response.content == b'{"detail":"Method Not Allowed"}'
    assert response.headers["content-type"] == "application/json"


def test_sme_save_alert_shows_the_parsed_detail() -> None:
    """The one client that read an error body as text shows ``detail``, not escapes."""
    root = pathlib.Path(__file__).resolve().parent.parent.parent
    template = (
        root / "src/idraa/templates/scenarios/wizard/_fair_params_form_inner.html"
    ).read_text(encoding="utf-8")

    assert "alert('Could not save SME: ' + (await resp.text()))" not in template
    assert "JSON.parse(msg).detail" in template
    assert "alert('Could not save SME: ' + msg)" in template
