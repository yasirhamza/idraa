"""JSON responses whose bytes carry no markup characters.

Error details are encoded so they are inert when rendered as HTML (HTMX swaps
4xx response bodies into the page). Error handlers therefore build their JSON
with ``HtmlSafeJSONResponse``: ``<``, ``>`` and ``&`` are written as the JSON
escapes ``\\u003c``, ``\\u003e`` and ``\\u0026``. Every JSON parser decodes
those back, so ``response.json()`` yields the identical value; only the wire
bytes differ, and only when a string contained one of the three characters.
"""

from __future__ import annotations

from typing import Any

from starlette.responses import JSONResponse

# Applied to the serialized bytes. Structural JSON characters are only
# ``{}[],:"`` plus literals and numbers, so ``<``, ``>`` and ``&`` can occur
# only inside a string (or key). A backslash always reaches the output as an
# escaped pair, so the run of backslashes before one of these characters is
# even-length and the ``\u00XX`` escape inserted here stays a valid escape.
_ESCAPES = {
    b"&": b"\\u0026",
    b"<": b"\\u003c",
    b">": b"\\u003e",
}


class HtmlSafeJSONResponse(JSONResponse):
    """``JSONResponse`` that never emits a raw ``<``, ``>`` or ``&``."""

    def render(self, content: Any) -> bytes:
        body = super().render(content)
        for raw, escaped in _ESCAPES.items():
            body = body.replace(raw, escaped)
        return body
