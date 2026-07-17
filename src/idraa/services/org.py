"""Single-organization helpers for phase 1.

Phase 1 pins every business row to ONE organization (see CLAUDE.md —
``organization_id`` column exists on every table from day 1 but is hard-
coded). These helpers fetch / assert that sole row. Promote to tenant-
aware lookups (``get_org_for_user``) when multi-tenancy ships.

``require_sole_org`` raises ``RuntimeError`` rather than ``HTTPException``
because in practice ``setup_guard`` middleware redirects to ``/setup``
before any /organization request reaches this code path when the DB is
empty. The RuntimeError is a defensive "should-not-happen" signal, not a
user-facing 4xx — if it fires, the request flow is broken upstream.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.organization import Organization


async def get_sole_org(db: AsyncSession) -> Organization | None:
    return (await db.execute(select(Organization))).scalars().first()


async def require_sole_org(db: AsyncSession) -> Organization:
    org = await get_sole_org(db)
    if org is None:
        raise RuntimeError("No organization set up")
    return org


def compute_org_diff(before: Organization, new: dict[str, object]) -> dict[str, list[object]]:
    """Return a ``{field: [before, after]}`` dict for fields that changed.

    Values are run through ``_jsonable`` so the result is safe to persist
    in the AuditLog.changes JSON column (Decimals → float, UUIDs → str,
    StrEnum → its string value).
    """
    diff: dict[str, list[object]] = {}
    for field, after in new.items():
        prev = getattr(before, field, None)
        if prev != after:
            diff[field] = [_jsonable(prev), _jsonable(after)]
    return diff


def _jsonable(v: object) -> object:
    if isinstance(v, uuid.UUID):
        return str(v)
    try:
        import decimal

        if isinstance(v, decimal.Decimal):
            return float(v)
    except ImportError:
        pass
    if hasattr(v, "value"):
        return v.value  # enums
    return v
