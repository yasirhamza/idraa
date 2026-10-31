"""Unit tests for ``HtmlSafeJSONResponse``: JSON bodies free of markup characters.

The serialized bytes must contain no ``<``, ``>`` or ``&``, while parsing them
must yield the identical Python value (the characters become JSON ``\\uXXXX``
escapes, which every JSON parser decodes back).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from starlette.responses import JSONResponse

from idraa.utils.safe_json import HtmlSafeJSONResponse


def _body(content: Any) -> bytes:
    return bytes(HtmlSafeJSONResponse(content).body)


def test_render_encodes_markup_characters_and_round_trips() -> None:
    original = {"detail": "<b>&x"}

    body = _body(original)

    for ch in (b"<", b">", b"&"):
        assert ch not in body
    assert json.loads(body) == original


def test_render_encodes_with_lowercase_unicode_escapes() -> None:
    assert _body({"detail": "<>&"}) == b'{"detail":"\\u003c\\u003e\\u0026"}'


def test_render_covers_keys_nested_values_and_lists() -> None:
    original = {
        "a<b": ["<x>", {"k&v": "&amp;"}, 3, None, True],
        "detail": [{"input": "<i>", "loc": ["body", "mc_iterations"], "msg": "a > b"}],
    }

    body = _body(original)

    for ch in (b"<", b">", b"&"):
        assert ch not in body
    assert json.loads(body) == original


@pytest.mark.parametrize(
    "text",
    [
        "\\&",  # backslash directly before an encoded character
        "\\\\<",
        '"<"',
        "line\n<tab>\t&",
        "snowman ☃ <b>",
        "emoji \U0001f600 &",
        "",
        "plain text with no markup characters",
    ],
)
def test_round_trip_is_exact_for_awkward_strings(text: str) -> None:
    original = {"detail": text}

    body = bytes(HtmlSafeJSONResponse(original).body)

    assert json.loads(body) == original
    for ch in (b"<", b">", b"&"):
        assert ch not in body


def test_bytes_identical_to_stock_response_when_no_markup_characters() -> None:
    content = {"detail": "Forbidden", "n": 1.5, "u": "☃"}

    assert bytes(HtmlSafeJSONResponse(content).body) == bytes(JSONResponse(content).body)


def test_status_headers_and_media_type_are_preserved() -> None:
    response = HtmlSafeJSONResponse(
        {"detail": "<b>"}, status_code=422, headers={"Retry-After": "7"}
    )

    assert response.status_code == 422
    assert response.headers["retry-after"] == "7"
    assert response.media_type == "application/json"
    assert response.headers["content-type"] == "application/json"
    assert response.headers["content-length"] == str(len(response.body))


def test_non_finite_numbers_still_rejected_like_the_stock_response() -> None:
    with pytest.raises(ValueError):
        HtmlSafeJSONResponse({"detail": float("inf")})
