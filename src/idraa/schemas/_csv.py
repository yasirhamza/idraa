"""Shared CSV-to-list Pydantic validator for form list fields.

Centralizes the item-count and per-item-length caps introduced in Phase
1.1.8 I3 (originally applied only to OrganizationForm). Phase 1.2.5
extracts it here so new schemas (ControlForm, Scenario in 1.3+) don't
re-introduce the uncapped bypass.
"""

from __future__ import annotations

_MAX_CSV_ITEMS = 50
_MAX_CSV_ITEM_LEN = 120


def split_csv(v: str | list[str] | None) -> list[str]:
    if v is None:
        return []
    parts = v if isinstance(v, list) else v.split(",")
    cleaned = [p.strip() for p in parts if p and p.strip()]
    if len(cleaned) > _MAX_CSV_ITEMS:
        raise ValueError(f"too many items (max {_MAX_CSV_ITEMS})")
    for item in cleaned:
        if len(item) > _MAX_CSV_ITEM_LEN:
            raise ValueError(f"item too long (max {_MAX_CSV_ITEM_LEN} chars)")
    return cleaned
