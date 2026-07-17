"""Path-driven breadcrumb generator.

Walks `request.url.path` to produce a list of `(label, href)` tuples. The last
item's href is None (current page). Labels are looked up in `_LABELS` first;
unknown segments title-case their slug (`bar-baz` -> `Bar Baz`).

UUID segments collapse to a generic "Detail" label and a clickable href back
to the entity's index.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import Request

_LABELS: dict[str, str] = {
    "controls": "Controls",
    "scenarios": "Scenarios",
    "analyses": "Analyses",
    "reports": "Reports",
    "library": "Library",
    "overlays": "Overlays",
    "organization": "Organization",
    "users": "Users",
    "setup": "Setup",
    "maintenance": "Maintenance",
    "new": "New",
    "edit": "Edit",
    "import": "Import",
    "export": "Export",
    "duplicate": "Duplicate",
}

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _humanize(segment: str) -> str:
    return segment.replace("-", " ").replace("_", " ").title()


def breadcrumb_for(request: Request) -> list[tuple[str, str | None]]:
    """Return breadcrumb tuples for the request's path. Final item has href=None."""
    path = request.url.path.rstrip("/") or "/"
    if path == "/":
        return [("Home", None)]

    parts = path.lstrip("/").split("/")
    crumbs: list[tuple[str, str | None]] = [("Home", "/")]
    accumulated = ""
    for i, seg in enumerate(parts):
        accumulated += "/" + seg
        is_last = i == len(parts) - 1
        label = "Detail" if _UUID_RE.match(seg) else _LABELS.get(seg, _humanize(seg))
        crumbs.append((label, None if is_last else accumulated))
    return crumbs
