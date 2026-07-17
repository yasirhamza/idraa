from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.organization import Organization
from idraa.services.fx_rates import FxRateService, is_selectable_currency


async def _org(db: AsyncSession) -> uuid.UUID:
    org = Organization(name="Acme", industry_type="manufacturing", organization_size="medium")
    db.add(org)
    await db.flush()
    return org.id


@pytest.mark.asyncio
async def test_usd_always_selectable(db_session: AsyncSession) -> None:
    org_id = await _org(db_session)
    assert await is_selectable_currency(db_session, org_id, "USD") is True


@pytest.mark.asyncio
async def test_rated_currency_selectable_unrated_not(db_session: AsyncSession) -> None:
    org_id = await _org(db_session)
    await FxRateService(db_session).upsert_rate(
        org_id, "SAR", Decimal("3.75"), dt.date(2026, 6, 14), "SAMA", user_id=None
    )
    assert await is_selectable_currency(db_session, org_id, "SAR") is True
    assert await is_selectable_currency(db_session, org_id, "EUR") is False


@pytest.mark.asyncio
async def test_offered_but_malformed_not_selectable(db_session: AsyncSession) -> None:
    org_id = await _org(db_session)
    assert await is_selectable_currency(db_session, org_id, "ZZZ") is False
    assert await is_selectable_currency(db_session, org_id, "<b>") is False
