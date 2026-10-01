"""Threat Agent Library service (spec 2026-09-30-threat-agent-library section 4-5)."""  # ASCII: ruff RUF002

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def choices_from_rows(rows: Iterable[Any]) -> list[tuple[str, str]]:
    return [(r.slug, r.name) for r in rows]
