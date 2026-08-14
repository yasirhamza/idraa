"""Offline OpenAPI schema extraction for the DAST harness.

``build_schema`` pulls the schema straight out of ``app.openapi()`` — an
in-process call, no HTTP request, no ``/setup`` redirect — so schema
extraction is fully decoupled from whatever target Schemathesis is later
pointed at (design doc §4). The ``servers`` entry is injected after the fact
so the same extraction works regardless of which ephemeral port the
orchestrator (Task 5) happens to bind, and ``config.EXCLUDED_PATH_REGEX`` is
applied to drop the heavy + session/account-mutating surface (design doc §7)
before the schema ever reaches Schemathesis.

Run standalone to dump the filtered schema as JSON to stdout:

    uv run python -m security.dast.extract_openapi --base-url http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from typing import Any

from security.dast.config import EXCLUDED_PATH_REGEX

from idraa.app import app

_EXCLUDED_PATH_RE = re.compile(EXCLUDED_PATH_REGEX)


def build_schema(base_url: str) -> dict[str, Any]:
    """Return a filtered, servers-injected OpenAPI schema dict.

    ``FastAPI.openapi()`` caches and returns the SAME dict object on every
    call, so this deep-copies before mutating — an in-place edit here would
    otherwise leak into the app's own cached schema (and, in a process that
    also serves ``/api/openapi.json``, into that route's response).

    Paths are matched with ``re.search`` against the OpenAPI path *template*
    (e.g. ``/scenarios/{scenario_id}/delete``), not a concrete request URL —
    the ``EXCLUDED_PATH_REGEX`` suffix alternatives (``/delete$`` etc.) rely
    on this to drop parameterized destructive routes, not just literal
    prefixes.
    """
    schema: dict[str, Any] = copy.deepcopy(app.openapi())
    schema["servers"] = [{"url": base_url}]
    schema["paths"] = {
        path: operations
        for path, operations in schema.get("paths", {}).items()
        if not _EXCLUDED_PATH_RE.search(path)
    }
    return schema


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help=(
            "Ephemeral local target URL injected into the schema's `servers` "
            "entry. This harness never targets a deployed environment — keep "
            "this pointed at 127.0.0.1."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    schema = build_schema(args.base_url)
    json.dump(schema, sys.stdout)


if __name__ == "__main__":
    main()
