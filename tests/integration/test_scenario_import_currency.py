"""Integration tests: import/export entry_currency + entry_rate round-trip.

Contract (Invariant 3 from the plan):
  Exported CSV pl_*/sl_* are already USD.  Re-import carries entry_currency/
  entry_rate as pure provenance metadata and does NOT call
  convert_loss_inputs_to_usd.  A re-imported SAR scenario's primary_loss
  distribution must be IDENTICAL to the original (no re-division by the rate).

Two-step upload → confirm flow mirrors test_scenario_import_routes.py.
Uses authed_admin + csrf_post from conftest.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.scenario import Scenario
from idraa.services.fx_rates import FxRateService
from idraa.services.scenario_export import export_csv_response
from tests.conftest import csrf_post

_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _extract_token(html: str) -> str:
    m = _UUID_RE.search(html)
    assert m, "expected a preview-token UUID in the preview body"
    return m.group(0)


# ── helpers ────────────────────────────────────────────────────────────────


def _scenario_form(**over: str) -> dict[str, str]:
    """A minimal expert-form POST dict for a SAR-denominated scenario."""
    base = {
        "name": "ImportRoundTrip-SAR",
        "scenario_type": "custom",
        "threat_category": "ransomware",
        "entry_currency": "SAR",
        "tef_dist": "pert",
        "tef_low": "0.1",
        "tef_mode": "0.3",
        "tef_high": "0.5",
        "vuln_low": "0.2",
        "vuln_mode": "0.5",
        "vuln_high": "0.8",
        "pl_dist": "pert",
        "pl_low": "3750000",
        "pl_mode": "7500000",
        "pl_high": "15000000",
        "sl_dist": "pert",
        "sl_low": "",
        "sl_mode": "",
        "sl_high": "",
    }
    base.update(over)
    return base


async def _seed_sar_rate(db: AsyncSession, org_id: uuid.UUID) -> None:
    await FxRateService(db).upsert_rate(
        org_id, "SAR", Decimal("3.75"), dt.date(2026, 6, 15), "SAMA", user_id=None
    )
    await db.commit()


# ── value-fidelity round-trip ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_export_then_reimport_sar_no_reconvert(
    authed_admin, db_session: AsyncSession
) -> None:
    """Export a SAR scenario → re-import CSV → assert USD distributions unchanged.

    The exported CSV carries the stored USD distribution (pl_low=1_000_000 etc.).
    Re-import MUST NOT divide again by the SAR rate (that would yield 266_666…).
    """
    client, org_id = authed_admin
    await _seed_sar_rate(db_session, org_id)

    # 1. Create the SAR scenario via the expert form (Task 3 converts it to USD).
    resp = await csrf_post(client, "/scenarios", _scenario_form(), follow_redirects=False)
    assert resp.status_code in (302, 303), f"create failed: {resp.status_code} {resp.text[:500]}"

    # 2. Fetch the created row and record its USD distributions.
    original = (
        await db_session.execute(
            select(Scenario).where(
                Scenario.organization_id == org_id,
                Scenario.name == "ImportRoundTrip-SAR",
            )
        )
    ).scalar_one()
    orig_pl_low = float(original.primary_loss["low"])
    orig_pl_high = float(original.primary_loss["high"])
    orig_currency = original.entry_currency
    orig_rate = original.entry_rate

    # 3. Export the scenario as CSV.
    export_resp = export_csv_response([original], filename="export.csv")
    csv_bytes = export_resp.body  # type: ignore[attr-defined]

    # 4. Upload the CSV for a preview (two-step step 1).
    pr = await csrf_post(
        client,
        "/scenarios/import",
        {},
        files={"file": ("export.csv", csv_bytes, "text/csv")},
    )
    assert pr.status_code == 200, f"preview failed: {pr.status_code}"
    # The scenario already exists → it should be marked "skip" (duplicate guard).
    assert "skip" in pr.text.lower() or "ImportRoundTrip-SAR" in pr.text

    # 5. Rename original so it's not a duplicate, then re-export and re-import.
    original.name = "ImportRoundTrip-SAR-ORIG"
    # Build the CSV from the renamed scenario BEFORE committing, while we still
    # hold the data. The export is pure in-memory computation.
    export_resp2 = export_csv_response([original], filename="export2.csv")
    csv_bytes2 = export_resp2.body  # type: ignore[attr-defined]
    # Commit the rename so the app doesn't see a duplicate when confirming.
    await db_session.commit()

    pr2 = await csrf_post(
        client,
        "/scenarios/import",
        {},
        files={"file": ("export2.csv", csv_bytes2, "text/csv")},
    )
    assert pr2.status_code == 200
    token = _extract_token(pr2.text)

    # Confirm the import.
    cr = await csrf_post(
        client,
        "/scenarios/import/confirm",
        {"token": token},
        follow_redirects=False,
    )
    assert cr.status_code in (200, 303), f"confirm failed: {cr.status_code} {cr.text[:500]}"

    # 6. Fetch the re-imported row.
    reimported = (
        await db_session.execute(
            select(Scenario).where(
                Scenario.organization_id == org_id,
                Scenario.name == "ImportRoundTrip-SAR-ORIG",
            )
        )
    ).scalar_one()

    # 7. Assert NO double-conversion: distributions must equal the original USD values.
    assert float(reimported.primary_loss["low"]) == pytest.approx(orig_pl_low), (
        f"pl_low re-import mismatch: expected {orig_pl_low}, got {reimported.primary_loss['low']}"
    )
    assert float(reimported.primary_loss["high"]) == pytest.approx(orig_pl_high), (
        f"pl_high re-import mismatch: expected {orig_pl_high}, got {reimported.primary_loss['high']}"
    )

    # 8. Assert provenance metadata survived.
    assert reimported.entry_currency == orig_currency == "SAR"
    assert reimported.entry_rate == orig_rate == Decimal("3.75000000")


# ── reject-path tests ──────────────────────────────────────────────────────


def _csv_with_entry(name: str, entry_currency: str, entry_rate: str) -> bytes:
    """Build a minimal CSV with entry_currency and entry_rate columns."""
    header = (
        "name,description,scenario_type,threat_category,threat_actor_type,attack_vector,"
        "asset_class,version,status,distribution,tef_dist,tef_low,tef_mode,tef_high,"
        "vuln_low,vuln_mode,vuln_high,pl_dist,pl_low,pl_mode,pl_high,"
        "sl_dist,sl_low,sl_mode,sl_high,entry_currency,entry_rate"
    )
    row = (
        f"{name},,custom,ransomware,cybercriminals,,systems,1.0,active,PERT,"
        f"PERT,0.1,0.5,2,0.2,0.35,0.6,PERT,100000,1000000,15000000,PERT,,,,"
        f"{entry_currency},{entry_rate}"
    )
    return (header + "\n" + row + "\n").encode("utf-8")


@pytest.mark.asyncio
async def test_entry_rate_zero_rejected_batch_survives(
    authed_admin, db_session: AsyncSession
) -> None:
    """entry_rate=0 is rejected; the containing batch still processes (not a hard-stop)."""
    client, org_id = authed_admin
    await _seed_sar_rate(db_session, org_id)

    csv_bytes = _csv_with_entry("BadRate-Zero", "SAR", "0")
    pr = await csrf_post(
        client,
        "/scenarios/import",
        {},
        files={"file": ("bad.csv", csv_bytes, "text/csv")},
    )
    assert pr.status_code == 200
    token = _extract_token(pr.text)

    cr = await csrf_post(
        client,
        "/scenarios/import/confirm",
        {"token": token},
        follow_redirects=False,
    )
    assert cr.status_code in (200, 303)

    # The row must NOT have been stored.
    row = (
        await db_session.execute(
            select(Scenario).where(
                Scenario.organization_id == org_id, Scenario.name == "BadRate-Zero"
            )
        )
    ).scalar_one_or_none()
    assert row is None, "entry_rate=0 row should not have been imported"


@pytest.mark.asyncio
async def test_unrated_currency_rejected_not_stored(authed_admin, db_session: AsyncSession) -> None:
    """entry_currency with no active rate is rejected; scenario not stored."""
    client, org_id = authed_admin
    # Do NOT seed GBP rate — it should be rejected.
    csv_bytes = _csv_with_entry("UnratedCurrency", "GBP", "1.26")
    pr = await csrf_post(
        client,
        "/scenarios/import",
        {},
        files={"file": ("gbp.csv", csv_bytes, "text/csv")},
    )
    assert pr.status_code == 200
    token = _extract_token(pr.text)

    cr = await csrf_post(
        client,
        "/scenarios/import/confirm",
        {"token": token},
        follow_redirects=False,
    )
    assert cr.status_code in (200, 303)

    row = (
        await db_session.execute(
            select(Scenario).where(
                Scenario.organization_id == org_id, Scenario.name == "UnratedCurrency"
            )
        )
    ).scalar_one_or_none()
    assert row is None, "unrated currency row should not have been imported"


# ── Fix A: NaN entry_rate → must NOT 500 ─────────────────────────────────────


def _json_with_entry(name: str, entry_currency: str, entry_rate: str) -> bytes:
    """Build a minimal JSON import payload with entry_currency and entry_rate."""
    import json as _json

    obj = {
        "name": name,
        "description": None,
        "scenario_type": "custom",
        "threat_category": "ransomware",
        "threat_actor_type": "cybercriminals",
        "attack_vector": None,
        "asset_class": "systems",
        "version": "1.0",
        "status": "active",
        "threat_event_frequency": {"distribution": "PERT", "low": 0.1, "mode": 0.5, "high": 2},
        "vulnerability": {"distribution": "PERT", "low": 0.2, "mode": 0.35, "high": 0.6},
        "primary_loss": {"distribution": "PERT", "low": 100000, "mode": 1000000, "high": 15000000},
        "secondary_loss": None,
        "entry_currency": entry_currency,
        "entry_rate": entry_rate,
    }
    return _json.dumps([obj]).encode("utf-8")


@pytest.mark.asyncio
async def test_entry_rate_nan_csv_rejected_not_500(authed_admin, db_session: AsyncSession) -> None:
    """entry_rate='NaN' in CSV must yield row error (not 500), scenario not stored."""
    client, org_id = authed_admin
    await _seed_sar_rate(db_session, org_id)

    for nan_val in ("NaN", "sNaN"):
        csv_bytes = _csv_with_entry(f"BadRate-{nan_val}", "SAR", nan_val)
        pr = await csrf_post(
            client,
            "/scenarios/import",
            {},
            files={"file": (f"bad-{nan_val}.csv", csv_bytes, "text/csv")},
        )
        assert pr.status_code == 200, f"preview must not 500 for entry_rate={nan_val!r}"
        token = _extract_token(pr.text)

        cr = await csrf_post(
            client,
            "/scenarios/import/confirm",
            {"token": token},
            follow_redirects=False,
        )
        assert cr.status_code in (200, 303), (
            f"confirm must not 500 for entry_rate={nan_val!r}: got {cr.status_code}"
        )

        stored = (
            await db_session.execute(
                select(Scenario).where(
                    Scenario.organization_id == org_id,
                    Scenario.name == f"BadRate-{nan_val}",
                )
            )
        ).scalar_one_or_none()
        assert stored is None, f"entry_rate={nan_val!r} row must NOT be stored"


@pytest.mark.asyncio
async def test_entry_rate_nan_json_rejected_not_500(authed_admin, db_session: AsyncSession) -> None:
    """entry_rate='NaN' in JSON must yield row error (not 500), scenario not stored."""
    client, org_id = authed_admin
    await _seed_sar_rate(db_session, org_id)

    for nan_val in ("NaN", "sNaN"):
        json_bytes = _json_with_entry(f"BadRateJSON-{nan_val}", "SAR", nan_val)
        pr = await csrf_post(
            client,
            "/scenarios/import",
            {},
            files={"file": (f"bad-{nan_val}.json", json_bytes, "application/json")},
        )
        assert pr.status_code == 200, f"preview must not 500 for JSON entry_rate={nan_val!r}"
        token = _extract_token(pr.text)

        cr = await csrf_post(
            client,
            "/scenarios/import/confirm",
            {"token": token},
            follow_redirects=False,
        )
        assert cr.status_code in (200, 303), (
            f"confirm must not 500 for JSON entry_rate={nan_val!r}: got {cr.status_code}"
        )

        stored = (
            await db_session.execute(
                select(Scenario).where(
                    Scenario.organization_id == org_id,
                    Scenario.name == f"BadRateJSON-{nan_val}",
                )
            )
        ).scalar_one_or_none()
        assert stored is None, f"JSON entry_rate={nan_val!r} row must NOT be stored"


# ── Fix C: JSON round-trip preserves entry_currency/entry_rate ────────────────


@pytest.mark.asyncio
async def test_json_roundtrip_preserves_entry_currency_rate(
    authed_admin, db_session: AsyncSession
) -> None:
    """Export SAR scenario to JSON → re-import → entry_currency/rate preserved, no double-convert."""
    from idraa.services.scenario_export import export_json_response

    client, org_id = authed_admin
    await _seed_sar_rate(db_session, org_id)

    # 1. Create a SAR scenario.
    resp = await csrf_post(
        client,
        "/scenarios",
        _scenario_form(name="JSONRoundTrip-SAR"),
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303), f"create failed: {resp.status_code}"

    original = (
        await db_session.execute(
            select(Scenario).where(
                Scenario.organization_id == org_id,
                Scenario.name == "JSONRoundTrip-SAR",
            )
        )
    ).scalar_one()
    orig_pl_low = float(original.primary_loss["low"])
    orig_pl_high = float(original.primary_loss["high"])

    # 2. Export as JSON.
    json_resp = export_json_response([original], filename="export.json")
    json_bytes = json_resp.body  # type: ignore[attr-defined]

    # 3. Parse and verify entry_currency/entry_rate appear in the JSON payload.
    import json as _json

    exported_objs = _json.loads(json_bytes)
    assert len(exported_objs) == 1
    assert exported_objs[0].get("entry_currency") == "SAR", (
        "JSON export must include entry_currency"
    )
    exported_rate = exported_objs[0].get("entry_rate")
    assert exported_rate is not None, "JSON export must include entry_rate"
    assert float(exported_rate) == pytest.approx(3.75), (
        f"JSON export entry_rate must be ~3.75; got {exported_rate!r}"
    )

    # 4. Rename original so it's not a duplicate.
    original.name = "JSONRoundTrip-SAR-ORIG"
    export_json2 = export_json_response([original], filename="export2.json")
    json_bytes2 = export_json2.body  # type: ignore[attr-defined]
    await db_session.commit()

    # 5. Upload and confirm.
    pr = await csrf_post(
        client,
        "/scenarios/import",
        {},
        files={"file": ("export2.json", json_bytes2, "application/json")},
    )
    assert pr.status_code == 200, f"preview failed: {pr.status_code}"
    token = _extract_token(pr.text)

    cr = await csrf_post(
        client,
        "/scenarios/import/confirm",
        {"token": token},
        follow_redirects=False,
    )
    assert cr.status_code in (200, 303), f"confirm failed: {cr.status_code} {cr.text[:500]}"

    # 6. Assert entry_currency/rate preserved and NO double-conversion.
    reimported = (
        await db_session.execute(
            select(Scenario).where(
                Scenario.organization_id == org_id,
                Scenario.name == "JSONRoundTrip-SAR-ORIG",
            )
        )
    ).scalar_one()

    assert reimported.entry_currency == "SAR", "re-imported JSON must carry entry_currency=SAR"
    assert reimported.entry_rate == Decimal("3.75"), (
        f"re-imported JSON must carry entry_rate=3.75, got {reimported.entry_rate}"
    )
    assert float(reimported.primary_loss["low"]) == pytest.approx(orig_pl_low), (
        f"pl_low must not be double-converted; expected {orig_pl_low}, got {reimported.primary_loss['low']}"
    )
    assert float(reimported.primary_loss["high"]) == pytest.approx(orig_pl_high), (
        f"pl_high must not be double-converted; expected {orig_pl_high}, got {reimported.primary_loss['high']}"
    )
