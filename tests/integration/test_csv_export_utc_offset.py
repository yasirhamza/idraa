"""Regression for issue #266 sub-item D3.

CSV exports render TimestampMixin columns (``created_at`` / ``updated_at``)
via ``.isoformat()``. On SQLite, ``DateTime(timezone=True)`` reads back NAIVE,
so ``.isoformat()`` omits the ``+00:00`` UTC offset. The export side must
render those timestamps as UTC-aware ISO-8601 strings regardless of dialect.
"""

from __future__ import annotations

import csv
import io
import uuid

from httpx import AsyncClient


async def test_users_export_csv_created_at_has_utc_offset(
    authed_admin: tuple[AsyncClient, uuid.UUID],
) -> None:
    """The seeded admin's ``created_at`` cell must carry a UTC offset."""
    client, _ = authed_admin
    resp = await client.get("/users/export.csv")
    assert resp.status_code == 200

    reader = csv.DictReader(io.StringIO(resp.text))
    rows = list(reader)
    assert rows, "expected at least the seeded admin user in the export"
    for row in rows:
        created_at = row["created_at"]
        assert created_at, "created_at cell must not be empty"
        assert "+00:00" in created_at, (
            f"created_at must render with a UTC offset, got {created_at!r}"
        )
