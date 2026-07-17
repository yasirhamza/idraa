"""Data-contract iteration test for the entry_meta channel in scenario import (PR ρ rule).

When _validate_rows grew a parallel entry_meta list (Task 7), that list must
remain aligned with forms across all N rows — a future [0]/[-1] optimization
would silently drop per-row currency/rate and assign the wrong metadata.
Build N ≥ 3 rows with DISTINCT (entry_currency, entry_rate) pairs, run the
full parse→validate→apply path, and assert ALL N rows persist the correct
entry_currency and entry_rate.

Mirror tests/contracts/test_scenario_import_iteration.py for the harness shape.
SAR + EUR rates must be seeded so is_selectable_currency returns True for them.
USD needs no rate (is_selectable_currency("USD") is always True).
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

import pytest
from sqlalchemy import select

from idraa.models.scenario import Scenario
from idraa.services.fx_rates import FxRateService
from idraa.services.scenario_import import apply_validated_preview, validate_upload


def _scenario_obj(name: str, currency: str, rate: str) -> dict:
    """Build a minimal JSON scenario object with entry_currency + entry_rate."""
    obj: dict = {
        "name": name,
        "threat_category": "malware",
        "threat_event_frequency": {"distribution": "PERT", "low": 1, "mode": 2, "high": 3},
        "vulnerability": {"distribution": "PERT", "low": 0.1, "mode": 0.2, "high": 0.3},
        "primary_loss": {"distribution": "PERT", "low": 10, "mode": 20, "high": 30},
    }
    if currency:
        obj["entry_currency"] = currency
    if rate:
        obj["entry_rate"] = rate
    return obj


@pytest.mark.asyncio
async def test_entry_meta_all_rows_persist_correct_currency_rate(
    db_session, organization, admin_user
) -> None:
    """N=3 rows with distinct (currency, rate); all N must persist in the DB."""
    fx = FxRateService(db_session)
    await fx.upsert_rate(
        organization.id, "SAR", Decimal("3.75"), dt.date(2026, 6, 15), "SAMA", user_id=None
    )
    await fx.upsert_rate(
        organization.id, "EUR", Decimal("0.92"), dt.date(2026, 6, 15), "ECB", user_id=None
    )
    await db_session.commit()

    rows = [
        _scenario_obj("IterMeta-USD", "USD", ""),  # row 0: USD, no rate
        _scenario_obj("IterMeta-SAR", "SAR", "3.75"),  # row 1: SAR / 3.75
        _scenario_obj("IterMeta-EUR", "EUR", "0.92"),  # row 2: EUR / 0.92
    ]
    data = json.dumps(rows).encode()

    token, _p, _e = await validate_upload(
        db_session,
        org_id=organization.id,
        user_id=admin_user.id,
        data=data,
        filename="s.json",
        content_type="application/json",
    )
    imported, skipped, errors = await apply_validated_preview(
        db_session,
        token=token,
        org_id=organization.id,
        user=admin_user,
    )
    assert errors == [], f"unexpected apply errors: {errors}"
    assert imported == 3, f"expected 3 imported, got {imported}"
    assert skipped == 0, f"expected 0 skipped, got {skipped}"

    # Fetch and assert per-row metadata — all N must survive (iteration guard).
    names = ["IterMeta-USD", "IterMeta-SAR", "IterMeta-EUR"]
    result = await db_session.execute(
        select(Scenario)
        .where(Scenario.organization_id == organization.id, Scenario.name.in_(names))
        .order_by(Scenario.name)
    )
    db_rows = {r.name: r for r in result.scalars().all()}

    assert set(db_rows.keys()) == set(names), f"missing rows: {set(names) - set(db_rows.keys())}"

    usd_row = db_rows["IterMeta-USD"]
    assert usd_row.entry_currency == "USD"
    assert usd_row.entry_rate is None  # blank entry_rate → None

    sar_row = db_rows["IterMeta-SAR"]
    assert sar_row.entry_currency == "SAR"
    assert sar_row.entry_rate == Decimal("3.75000000")

    eur_row = db_rows["IterMeta-EUR"]
    assert eur_row.entry_currency == "EUR"
    assert eur_row.entry_rate == Decimal("0.92000000")
