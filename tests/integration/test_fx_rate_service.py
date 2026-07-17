from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.audit_log import AuditLog
from idraa.models.organization import Organization
from idraa.services.fx_rates import (
    FxRateService,
    InvalidRateError,
    RateNotFoundError,
)


async def _org(db: AsyncSession) -> uuid.UUID:
    org = Organization(name="Acme", industry_type="manufacturing", organization_size="medium")
    db.add(org)
    await db.flush()
    return org.id


@pytest.mark.asyncio
async def test_usd_is_identity(db_session: AsyncSession) -> None:
    org_id = await _org(db_session)
    svc = FxRateService(db_session)
    assert await svc.to_usd(Decimal("100"), "USD", org_id) == Decimal("100")
    assert await svc.from_usd(Decimal("100"), "USD", org_id) == Decimal("100")


@pytest.mark.asyncio
async def test_entry_to_usd_divides_and_usd_to_reporting_multiplies(
    db_session: AsyncSession,
) -> None:
    org_id = await _org(db_session)
    svc = FxRateService(db_session)
    await svc.upsert_rate(
        org_id, "SAR", Decimal("3.75"), dt.date(2026, 6, 14), "SAMA", user_id=None
    )
    assert await svc.to_usd(Decimal("3750000"), "SAR", org_id) == Decimal("1000000")
    assert await svc.from_usd(Decimal("1000000"), "SAR", org_id) == Decimal("3750000")


@pytest.mark.asyncio
async def test_unrated_currency_raises(db_session: AsyncSession) -> None:
    org_id = await _org(db_session)
    svc = FxRateService(db_session)
    with pytest.raises(RateNotFoundError):
        await svc.to_usd(Decimal("100"), "EUR", org_id)


@pytest.mark.asyncio
async def test_upsert_supersedes_prior_and_bumps_version(
    db_session: AsyncSession,
) -> None:
    org_id = await _org(db_session)
    svc = FxRateService(db_session)
    await svc.upsert_rate(
        org_id, "SAR", Decimal("3.75"), dt.date(2026, 6, 14), "SAMA", user_id=None
    )
    r2 = await svc.upsert_rate(
        org_id, "SAR", Decimal("3.76"), dt.date(2026, 6, 15), "SAMA", user_id=None
    )
    assert r2.version == 2
    active = await svc.active_rate(org_id, "SAR")
    assert active is not None and active.usd_rate == Decimal("3.76")
    assert active.is_active is True


@pytest.mark.asyncio
async def test_out_of_range_and_usd_rates_rejected(db_session: AsyncSession) -> None:
    org_id = await _org(db_session)
    svc = FxRateService(db_session)
    today = dt.date(2026, 6, 14)
    with pytest.raises(InvalidRateError):
        await svc.upsert_rate(org_id, "SAR", Decimal("0.0000001"), today, "x", user_id=None)
    with pytest.raises(InvalidRateError):
        await svc.upsert_rate(org_id, "SAR", Decimal("100001"), today, "x", user_id=None)
    with pytest.raises(InvalidRateError):
        await svc.upsert_rate(org_id, "USD", Decimal("1"), today, "x", user_id=None)


@pytest.mark.asyncio
async def test_audit_row_records_prior_rate_as_pair(db_session: AsyncSession) -> None:
    org_id = await _org(db_session)
    svc = FxRateService(db_session)
    today = dt.date(2026, 6, 14)
    await svc.upsert_rate(org_id, "SAR", Decimal("3.75"), today, "SAMA", user_id=None)
    await svc.upsert_rate(org_id, "SAR", Decimal("3.76"), today, "SAMA", user_id=None)
    rows = (
        (await db_session.execute(select(AuditLog).where(AuditLog.entity_type == "fx_rate")))
        .scalars()
        .all()
    )
    update_changes = [r.changes for r in rows if r.action == "update"]
    assert update_changes, "no update audit row written"
    assert update_changes[0]["usd_rate"] == ["3.75", "3.76"]


@pytest.mark.asyncio
async def test_audit_round_number_rate_not_scientific_notation(
    db_session: AsyncSession,
) -> None:
    org_id = await _org(db_session)
    svc = FxRateService(db_session)
    today = dt.date(2026, 6, 14)
    # A round-number rate must serialize plainly, not as "1E+3" (audit readability).
    await svc.upsert_rate(org_id, "JPY", Decimal("1000"), today, "BOJ", user_id=None)
    r2 = await svc.upsert_rate(org_id, "JPY", Decimal("1500"), today, "BOJ", user_id=None)
    rows = (
        (await db_session.execute(select(AuditLog).where(AuditLog.entity_type == "fx_rate")))
        .scalars()
        .all()
    )
    update_changes = [r.changes for r in rows if r.action == "update"]
    assert update_changes[0]["usd_rate"] == ["1000", "1500"]
    assert r2.usd_rate == Decimal("1500")
