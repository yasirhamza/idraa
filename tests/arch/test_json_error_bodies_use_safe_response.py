"""Permanent guard: JSON bodies are built by ``HtmlSafeJSONResponse``.

Error details are encoded so they are inert when rendered as HTML
(``idraa.utils.safe_json``). That only holds while every JSON error body comes
from ``HtmlSafeJSONResponse``, so this guard fails when ``src/idraa``

* references a raw ``JSONResponse`` / ``ORJSONResponse`` / ``UJSONResponse``
  anywhere outside ``utils/safe_json.py`` (import, call, ``response_class=``), or
* builds a ``Response(...)`` (any ``*Response`` call) with a JSON ``media_type``
  at a site that is not accounted for in ``_HAND_BUILT_JSON``.

``_HAND_BUILT_JSON`` pins an exact per-file count of the hand-built JSON
responses that exist today, each with the reason it is not an error-detail body
(WebAuthn/passkey ``fetch()`` endpoints with constant messages, and file
downloads). Exact equality is deliberate: a NEW site fails until it either uses
``HtmlSafeJSONResponse`` or is added here with a reason, and a removed site
forces the entry to shrink. Scoped to ``src/idraa`` only.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
_SRC = _ROOT / "src" / "idraa"
_SAFE_MODULE = "src/idraa/utils/safe_json.py"

_RAW_JSON_CLASSES = frozenset({"JSONResponse", "ORJSONResponse", "UJSONResponse"})

# file -> number of hand-built JSON ``*Response(..., media_type="...json...")`` calls.
_HAND_BUILT_JSON: dict[str, int] = {
    # WebAuthn / passkey endpoints called with fetch() from static/js/webauthn.js
    # (never an HTMX swap): credential options, success markers and {"error": <constant>}.
    "src/idraa/routes/auth.py": 4,
    "src/idraa/routes/mfa.py": 3,
    "src/idraa/routes/step_up.py": 4,
    # 200 file downloads served with an attachment Content-Disposition.
    "src/idraa/routes/library_import.py": 1,
    "src/idraa/routes/scenario_import.py": 1,
    "src/idraa/services/library_bundle_export.py": 1,
    "src/idraa/services/scenario_export.py": 1,
}


def _module_string_constants(tree: ast.Module) -> dict[str, str]:
    """Module-level ``NAME = "literal"`` assignments (resolves ``media_type=NAME``)."""
    out: dict[str, str] = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            out[node.targets[0].id] = node.value.value
    return out


def _call_name(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def scan_source(source: str) -> tuple[list[int], list[int]]:
    """Return (lines referencing a raw JSON response class, lines of JSON-media calls)."""
    tree = ast.parse(source)
    constants = _module_string_constants(tree)
    raw_refs: list[int] = []
    json_media: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if any(alias.name in _RAW_JSON_CLASSES for alias in node.names):
                raw_refs.append(node.lineno)
        elif isinstance(node, ast.Name):
            if node.id in _RAW_JSON_CLASSES:
                raw_refs.append(node.lineno)
        elif isinstance(node, ast.Attribute):
            if node.attr in _RAW_JSON_CLASSES:
                raw_refs.append(node.lineno)
        elif isinstance(node, ast.Call):
            name = _call_name(node)
            if name is None or not name.endswith("Response") or name == "HtmlSafeJSONResponse":
                continue
            media: ast.expr | None = None
            for kw in node.keywords:
                if kw.arg == "media_type":
                    media = kw.value
            if media is None and len(node.args) >= 4:
                media = node.args[3]  # Response(content, status_code, headers, media_type)
            if isinstance(media, ast.Name):
                text = constants.get(media.id)
            elif isinstance(media, ast.Constant) and isinstance(media.value, str):
                text = media.value
            else:
                text = None
            if text is not None and "json" in text.lower():
                json_media.append(node.lineno)
    return sorted(set(raw_refs)), sorted(set(json_media))


def _scan_tree() -> dict[str, tuple[list[int], list[int]]]:
    results: dict[str, tuple[list[int], list[int]]] = {}
    for path in sorted(_SRC.rglob("*.py")):
        rel = path.relative_to(_ROOT).as_posix()
        results[rel] = scan_source(path.read_text(encoding="utf-8"))
    return results


def test_no_raw_json_response_outside_the_safe_module() -> None:
    offenders = {
        rel: lines for rel, (lines, _) in _scan_tree().items() if lines and rel != _SAFE_MODULE
    }
    assert offenders == {}, (
        "raw JSON response class referenced outside utils/safe_json.py "
        f"(use HtmlSafeJSONResponse): {offenders}"
    )


def test_hand_built_json_responses_are_all_accounted_for() -> None:
    found = {
        rel: len(lines) for rel, (_, lines) in _scan_tree().items() if lines and rel != _SAFE_MODULE
    }
    assert found == _HAND_BUILT_JSON, (
        "hand-built JSON Response(...) sites differ from _HAND_BUILT_JSON: build error "
        "bodies with HtmlSafeJSONResponse, or add the site here with its reason. "
        f"found={found}"
    )


def test_the_safe_module_is_the_one_place_that_wraps_the_raw_class() -> None:
    raw_refs, _ = _scan_tree()[_SAFE_MODULE]
    assert raw_refs, f"{_SAFE_MODULE} no longer references the class it subclasses"


@pytest.mark.parametrize(
    ("source", "raw", "media"),
    [
        ("from fastapi.responses import JSONResponse\n", 1, 0),
        ("from starlette.responses import JSONResponse as J\n", 1, 0),
        ("import fastapi.responses as r\nx = r.JSONResponse({})\n", 1, 0),
        ("x = JSONResponse({'a': 1})\n", 1, 0),
        ("@router.get('/x', response_class=JSONResponse)\ndef f(): ...\n", 1, 0),
        ("from fastapi.responses import ORJSONResponse\n", 1, 0),
        ("x = Response(content='{}', media_type='application/json')\n", 0, 1),
        ("x = Response('{}', 400, None, 'application/problem+json')\n", 0, 1),
        ("M = 'application/json'\nx = Response(content='{}', media_type=M)\n", 0, 1),
        ("x = PlainTextResponse('hi', status_code=400)\n", 0, 0),
        ("x = Response(content=b'', media_type='text/csv')\n", 0, 0),
        ("X = 'application/vnd.ms-excel'\ny = Response(b'', media_type=X)\n", 0, 0),
        ("x = Response(b'', media_type=some_call())\n", 0, 0),
        ("x = HtmlSafeJSONResponse({'detail': 'd'}, status_code=422)\n", 0, 0),
        ("from idraa.utils.safe_json import HtmlSafeJSONResponse\n", 0, 0),
    ],
)
def test_detector_on_snippets(source: str, raw: int, media: int) -> None:
    raw_lines, media_lines = scan_source(source)
    assert (len(raw_lines), len(media_lines)) == (raw, media)
