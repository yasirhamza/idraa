"""Task 4: Scenario form — entry-currency selector (create) / read-only provenance (edit)
+ coherence note.

GET /scenarios/new with a SAR rate seeded → 200, selector present, SAR option present,
coherence disclosure present.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.services.fx_rates import FxRateService


@pytest.mark.asyncio
async def test_new_scenario_form_shows_entry_currency_selector(
    authed_admin, db_session: AsyncSession
) -> None:
    client, org_id = authed_admin
    await FxRateService(db_session).upsert_rate(
        org_id, "SAR", Decimal("3.75"), dt.date(2026, 6, 14), "SAMA", user_id=None
    )
    await db_session.commit()
    resp = await client.get("/scenarios/new")
    assert resp.status_code == 200
    assert 'name="entry_currency"' in resp.text
    assert "SAR" in resp.text
    # Coherence disclosure must be present.
    assert "control costs" in resp.text.lower() and "usd" in resp.text.lower()
