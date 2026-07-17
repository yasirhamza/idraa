"""#304 — bulk-egress audit rows on every bulk export endpoint.

Bulk egress of org data (or the shared canonical catalogs) to a file is a
data-movement event worth a lightweight audit row (count + format + user +
ip). One row per download, action ``<entity_type>.export``, with
``entity_id`` set to the organization id (the exported SET has no single
entity row — documented convention on ``log_bulk_export``).
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.audit_log import AuditLog


async def _audit_rows(db_session: AsyncSession, action: str) -> list[AuditLog]:
    return list(
        (await db_session.execute(select(AuditLog).where(AuditLog.action == action)))
        .scalars()
        .all()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "action", "fmt"),
    [
        ("/scenarios/export", "scenario.export", "csv"),
        ("/scenarios/export?format=json", "scenario.export", "json"),
        ("/analyses/export.csv", "risk_analysis_run.export", "csv"),
        ("/reports/export.csv", "risk_analysis_run.export", "csv"),
        ("/controls/export.csv", "control.export", "csv"),
        ("/overlays/export.csv", "overlay.export", "csv"),
        ("/library/export.csv", "library_bundle.export", "csv"),
        ("/library/export", "library_bundle.export", "json"),
        ("/controls/library/export.csv", "control_library.export", "csv"),
        ("/users/export.csv", "user.export", "csv"),
    ],
)
async def test_bulk_export_writes_audit_row(
    admin_client: AsyncClient,
    db_session: AsyncSession,
    path: str,
    action: str,
    fmt: str,
) -> None:
    r = await admin_client.get(path)
    assert r.status_code == 200, f"{path} -> {r.status_code}"

    rows = await _audit_rows(db_session, action)
    assert len(rows) == 1, f"expected exactly one {action} audit row, got {len(rows)}"
    row = rows[0]
    # changes carry the [None, value] pair convention used repo-wide.
    assert row.changes["format"] == [None, fmt]
    count = row.changes["count"]
    assert count[0] is None and isinstance(count[1], int) and count[1] >= 0
    assert row.user_id is not None
    # Pin the documented entity_id convention: the org id stands in for the
    # exported set (no single entity row exists for bulk egress).
    assert row.entity_id == row.organization_id


@pytest.mark.asyncio
async def test_scenario_export_audit_records_status_filter(
    admin_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """The honored ?status= filter is recorded so the audit row answers
    "WHAT subset left the building", not just "something did"."""
    r = await admin_client.get("/scenarios/export?status=active")
    assert r.status_code == 200

    rows = await _audit_rows(db_session, "scenario.export")
    assert len(rows) == 1
    filters = rows[0].changes.get("filters")
    assert filters is not None and filters[1] == {"status": "active"}


@pytest.mark.asyncio
async def test_single_entity_export_writes_no_bulk_audit_row(
    admin_client: AsyncClient,
    db_session: AsyncSession,
    seed_library_entry: object,
) -> None:
    """Per-entry exports are NOT bulk egress — no bulk audit row (issue #304
    scopes the audit to bulk endpoints; single-entity reads mirror the UI)."""
    r = await admin_client.get(f"/library/entries/{seed_library_entry.id}/export")  # type: ignore[attr-defined]
    assert r.status_code == 200
    rows = await _audit_rows(db_session, "library_bundle.export")
    assert rows == []


@pytest.mark.asyncio
async def test_export_cadence_cap_returns_429(
    admin_client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#357: the Nth+1 export inside the window is refused with 429 +
    Retry-After, BEFORE any data egresses or another audit row lands."""
    from idraa.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "export_rate_limit_count", 2)
    monkeypatch.setattr(settings, "export_rate_limit_window_seconds", 3600)

    assert (await admin_client.get("/controls/export.csv")).status_code == 200
    assert (await admin_client.get("/scenarios/export")).status_code == 200

    r = await admin_client.get("/users/export.csv")
    assert r.status_code == 429
    assert r.headers["Retry-After"] == "3600"
    assert "rate limit" in r.text.lower()
    # The refused request wrote NO audit row (checked across all export
    # actions): the cap bounds the bloat vector instead of feeding it.
    rows = await _audit_rows(db_session, "user.export")
    assert rows == []


@pytest.mark.asyncio
async def test_export_cadence_cap_zero_disables(
    admin_client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from idraa.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "export_rate_limit_count", 0)

    for _ in range(3):
        assert (await admin_client.get("/controls/export.csv")).status_code == 200
    assert len(await _audit_rows(db_session, "control.export")) == 3


@pytest.mark.asyncio
async def test_audit_watermark_warns_on_export(
    admin_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """#357: crossing the audit_log row-count watermark emits a WARNING on
    the export path (alert-only; the export itself still succeeds)."""
    from idraa.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "audit_log_watermark_rows", 1)

    # First export writes row 1; the SECOND export sees total >= 1 pre-insert.
    assert (await admin_client.get("/controls/export.csv")).status_code == 200
    with caplog.at_level("WARNING", logger="idraa.services.audit"):
        assert (await admin_client.get("/controls/export.csv")).status_code == 200
    assert any("watermark" in rec.message for rec in caplog.records)
