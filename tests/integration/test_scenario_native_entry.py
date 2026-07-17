"""Task 3: Scenario CREATE converts loss→USD, sets entry_currency/entry_rate metadata."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.scenario import Scenario
from idraa.services.fx_rates import FxRateService
from tests.conftest import csrf_post  # the project's CSRF POST helper


def _form(**over: str) -> dict[str, str]:
    base = {
        "name": "OT ransomware (SAR)",
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


@pytest.mark.asyncio
async def test_scenario_entered_in_sar_is_stored_in_usd(
    authed_admin, db_session: AsyncSession
) -> None:
    client, org_id = authed_admin
    await FxRateService(db_session).upsert_rate(
        org_id, "SAR", Decimal("3.75"), dt.date(2026, 6, 14), "SAMA", user_id=None
    )
    await db_session.commit()
    resp = await csrf_post(client, "/scenarios", _form(), follow_redirects=False)
    assert resp.status_code in (302, 303), resp.text
    row = (
        await db_session.execute(select(Scenario).where(Scenario.name == "OT ransomware (SAR)"))
    ).scalar_one()
    assert float(row.primary_loss["low"]) == pytest.approx(1_000_000)
    assert float(row.primary_loss["high"]) == pytest.approx(4_000_000)
    assert float(row.vulnerability["low"]) == pytest.approx(0.2)  # untouched
    assert float(row.threat_event_frequency["low"]) == pytest.approx(0.1)  # untouched
    assert row.entry_currency == "SAR"
    assert row.entry_rate == Decimal("3.75000000")


@pytest.mark.asyncio
async def test_unrated_currency_rejected_not_stored(authed_admin, db_session: AsyncSession) -> None:
    client, _ = authed_admin
    # EUR has no rate configured → not selectable → reject, do not store.
    resp = await csrf_post(
        client, "/scenarios", _form(name="bad", entry_currency="EUR"), follow_redirects=False
    )
    assert resp.status_code == 422
    row = (
        await db_session.execute(select(Scenario).where(Scenario.name == "bad"))
    ).scalar_one_or_none()
    assert row is None


# ── Fix B: non-numeric loss on non-USD CREATE → 422 not 500 ──────────────────


@pytest.mark.asyncio
async def test_non_numeric_loss_non_usd_create_returns_422(
    authed_admin, db_session: AsyncSession
) -> None:
    """pl_low='abc' with entry_currency=SAR must return 422 (not 500), scenario not stored."""
    from idraa.services.fx_rates import FxRateService

    client, org_id = authed_admin
    await FxRateService(db_session).upsert_rate(
        org_id, "SAR", Decimal("3.75"), dt.date(2026, 6, 14), "SAMA", user_id=None
    )
    await db_session.commit()

    resp = await csrf_post(
        client,
        "/scenarios",
        _form(name="NonNumericLoss", pl_low="abc"),
        follow_redirects=False,
    )
    assert resp.status_code == 422, (
        f"non-numeric pl_low with SAR currency must return 422, got {resp.status_code}"
    )
    stored = (
        await db_session.execute(select(Scenario).where(Scenario.name == "NonNumericLoss"))
    ).scalar_one_or_none()
    assert stored is None, "scenario with non-numeric loss must not be stored"
