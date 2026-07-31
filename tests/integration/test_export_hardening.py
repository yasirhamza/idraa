"""Export hardening parity (idraa#107 + idraa#110 + their review pass).

1. #107(1) — the THREE pre-category export routes (`/scenarios/{id}/export`,
   `/runs/{id}/control-matrix.csv`, `/library/entries/{id}/export` — the
   third surfaced by the review) gain the EXPORTS step-up gate their sibling
   exports already carry, plus audit rows so per-id exports consume the
   export budget (``log_bulk_export`` IS the rate limiter).
2. #107(2) — /healthz exposes the security-settings cache state as a
   TRI-state: "warm" (overrides loaded), "empty" (warm completed, no
   settings row saved yet — normal), "cold" (never warmed / boot warm
   FAILED — env-fallback policy in effect, the actionable signal).
   Deliberately DB-free: healthz must stay cheap during the boot-write
   window (#72).
3. #110(1) — bulk-export audit rows record the TRUSTED client IP (edge
   header machinery from routes/deps.py) instead of ``request.client``,
   falling back to it when no trust strategy is configured.
4. #110(2) — the shared csv_response helper, the scenario JSON export, and
   the library bundle export emit ``Cache-Control: private, no-store``,
   matching the PDF-report and samples-export precedents.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import idraa.services.security_settings as security_settings
from idraa import config
from idraa.models.audit_log import AuditLog
from tests.integration.test_step_up_categories import _apply, _make_stale

# ---------------------------------------------------------------------------
# 1. Step-up gates on the two pre-category export routes (#107 item 1)


_PRE_CATEGORY_ROUTES = [
    "/scenarios/{id}/export",
    "/runs/{id}/control-matrix.csv",
    "/library/entries/{id}/export",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("url", _PRE_CATEGORY_ROUTES)
async def test_pre_category_export_routes_gate_on_exports(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession, url: str
) -> None:
    """EXPORTS on + stale session -> 303 to step-up, never the handler.

    The require_step_up dependency runs BEFORE the handler, so a dummy id
    303s at the gate (same technique as test_step_up_categories).
    """
    client, org_id = authed_admin
    await _apply(db_session, org_id, step_up_exports=True)
    await _make_stale(db_session, client)
    r = await client.get(url.format(id=uuid.uuid4()), follow_redirects=False)
    assert r.status_code == 303
    assert "/auth/step-up" in r.headers["location"]


@pytest.mark.asyncio
@pytest.mark.parametrize("url", _PRE_CATEGORY_ROUTES)
async def test_exports_off_leaves_routes_open(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession, url: str
) -> None:
    """Category OFF: the routes must not gate (404 for the dummy id, not 303)."""
    client, org_id = authed_admin
    await _apply(db_session, org_id, step_up_exports=False)
    await _make_stale(db_session, client)
    r = await client.get(url.format(id=uuid.uuid4()), follow_redirects=False)
    assert r.status_code == 404  # reached the handler; unknown id


# ---------------------------------------------------------------------------
# 2. /healthz warm-state signal (#107 item 2)


@pytest.mark.asyncio
async def test_healthz_reports_security_settings_cache_state(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """Tri-state signal: cold = never warmed / warm failed; empty = warmed,
    no SecuritySettings row (NORMAL until an admin first saves settings);
    warm = snapshot loaded. Two-state warm/cold would report "cold" forever
    on a healthy install that never wrote a settings row, teaching operators
    to ignore the field."""
    client, org_id = authed_admin

    security_settings.invalidate()
    r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["security_settings_cache"] == "cold"

    # Warmed successfully but no row exists -> "empty", NOT "cold".
    security_settings._warmed = True
    r = await client.get("/healthz")
    assert r.json()["security_settings_cache"] == "empty"

    # _apply persists a SecuritySettings row and loads the cache.
    await _apply(db_session, org_id, step_up_exports=False)
    r = await client.get("/healthz")
    assert r.json()["security_settings_cache"] == "warm"


# ---------------------------------------------------------------------------
# 3. Trusted client IP in bulk-export audit rows (#110 item 1)


async def _export_audit_ip(
    db_session: AsyncSession, client: AsyncClient, headers: dict[str, str]
) -> str | None:
    r = await client.get("/scenarios/export", headers=headers)
    assert r.status_code == 200
    rows = (
        (await db_session.execute(select(AuditLog).where(AuditLog.action == "scenario.export")))
        .scalars()
        .all()
    )
    assert len(rows) == 1, f"expected exactly one scenario.export row, got {len(rows)}"
    return rows[0].ip_address


@pytest.mark.asyncio
async def test_bulk_export_audit_records_trusted_header_ip(
    admin_client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With a trusted header configured, the audit row records the header IP."""
    monkeypatch.setenv("TRUSTED_CLIENT_IP_HEADER", "Fly-Client-IP")
    config.reset_for_tests()
    try:
        ip = await _export_audit_ip(db_session, admin_client, {"Fly-Client-IP": "203.0.113.7"})
    finally:
        monkeypatch.delenv("TRUSTED_CLIENT_IP_HEADER")
        config.reset_for_tests()
    assert ip == "203.0.113.7"


@pytest.mark.asyncio
async def test_bulk_export_audit_ignores_unconfigured_header(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    """No trust strategy configured: a spoofed header must NOT be recorded.

    Asserts equality against the known ASGI test peer (httpx ASGITransport
    sets client=("127.0.0.1", 123)) — an inequality assert would pass for
    any buggy value.
    """
    ip = await _export_audit_ip(db_session, admin_client, {"Fly-Client-IP": "198.51.100.99"})
    assert ip == "127.0.0.1"  # the direct peer, never the spoofed header


# ---------------------------------------------------------------------------
# 3b. Audit-row parity on the newly gated single-item exports (#107/#110 review)


@pytest.mark.asyncio
async def test_library_entry_export_audited_and_no_store(
    admin_client: AsyncClient, db_session: AsyncSession, seed_library_entry: object
) -> None:
    """Single-entry library export: audit row (budget-counted) + no-store.

    Review finding I2: without the audit row, enumerating entry ids pulls the
    catalog one entry at a time with zero *.export rows and zero consumption
    of the export budget (log_bulk_export IS the limiter).
    """
    entry = seed_library_entry
    r = await admin_client.get(f"/library/entries/{entry.id}/export")
    assert r.status_code == 200
    assert r.headers.get("Cache-Control") == "private, no-store"
    rows = (
        (
            await db_session.execute(
                select(AuditLog).where(AuditLog.action == "library_bundle.export")
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].changes["count"][1] == 1  # changes store [old, new] pairs
    assert rows[0].changes["filters"][1]["entry_id"] == str(entry.id)


@pytest.mark.asyncio
async def test_scenario_single_export_writes_audit_row(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """Single-scenario export: audit row with the scenario id in filters."""
    from idraa.models.enums import EntityStatus
    from tests.integration.test_scenario_export_routes import _seed_scenario

    client, org_id = authed_admin
    s = await _seed_scenario(
        db_session, org_id=org_id, name="Audit Row Scenario", status=EntityStatus.ACTIVE
    )
    r = await client.get(f"/scenarios/{s.id}/export")
    assert r.status_code == 200
    rows = (
        (await db_session.execute(select(AuditLog).where(AuditLog.action == "scenario.export")))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].changes["count"][1] == 1  # changes store [old, new] pairs
    assert rows[0].changes["filters"][1]["scenario_id"] == str(s.id)


# ---------------------------------------------------------------------------
# 4. Cache-Control on CSV/JSON export responses (#110 item 2)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/scenarios/export",  # shared csv_response helper
        "/scenarios/export?format=json",  # scenario JSON export
        "/controls/export.csv",  # another csv_response consumer
    ],
)
async def test_export_responses_are_no_store(admin_client: AsyncClient, path: str) -> None:
    r = await admin_client.get(path)
    assert r.status_code == 200
    assert r.headers.get("Cache-Control") == "private, no-store"
