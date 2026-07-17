"""Pins the documented FX direction so a future sign/division flip fails loudly.
usd_rate = code per USD  =>  to_usd divides, from_usd multiplies."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.organization import Organization
from idraa.services.fx_rates import FxRateService


@pytest.mark.asyncio
async def test_round_trip_is_identity_within_tolerance(
    db_session: AsyncSession,
) -> None:
    org = Organization(name="Acme", industry_type="manufacturing", organization_size="medium")
    db_session.add(org)
    await db_session.flush()
    svc = FxRateService(db_session)
    cases = {"SAR": Decimal("3.75"), "EUR": Decimal("0.92"), "KWD": Decimal("0.307")}
    for code, rate in cases.items():
        await svc.upsert_rate(org.id, code, rate, dt.date(2026, 6, 14), "x", user_id=None)
    # Decimal division is not exact for non-terminating quotients (e.g.
    # 1,000,000 / 3.75 rounds at 28 sig-figs), so round-trip is identity only
    # to a tolerance — exactly what design Predictability rule 4 says. Asserting
    # bit-exact equality would be a latent flaky-test foot-gun.
    tol = Decimal("0.0000001")
    for code in cases:
        usd = Decimal("1000000")
        back1 = await svc.to_usd(await svc.from_usd(usd, code, org.id), code, org.id)
        assert abs(back1 - usd) <= tol, f"{code} USD->code->USD drift: {back1}"
        amt = Decimal("3750000")
        back2 = await svc.from_usd(await svc.to_usd(amt, code, org.id), code, org.id)
        assert abs(back2 - amt) <= tol, f"{code} code->USD->code drift: {back2}"
