"""(a) converter linearity across currencies; (b) two scenarios entered in
different currencies both store USD. AGGREGATE summation over USD scenarios is
covered by the existing aggregate tests — this pins the entry→USD precondition."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.scenario import Scenario
from idraa.services.fx_rates import FxRateService
from idraa.services.scenario_currency import convert_loss_inputs_to_usd
from tests.conftest import csrf_post


def test_converter_linearity_across_currencies() -> None:
    a = convert_loss_inputs_to_usd({"pl_low": "3750000"}, "SAR", Decimal("3.75"))
    b = convert_loss_inputs_to_usd({"pl_low": "920000"}, "EUR", Decimal("0.92"))
    c = convert_loss_inputs_to_usd({"pl_low": "1000000"}, "USD", Decimal("1"))
    assert sum(float(d["pl_low"]) for d in (a, b, c)) == pytest.approx(3_000_000)


@pytest.mark.asyncio
async def test_two_scenarios_two_currencies_both_store_usd(
    authed_admin, db_session: AsyncSession
) -> None:
    client, org_id = authed_admin
    svc = FxRateService(db_session)
    await svc.upsert_rate(org_id, "SAR", Decimal("3.75"), dt.date(2026, 6, 14), "x", user_id=None)
    await db_session.commit()

    def _f(name: str, cur: str, low: str, mode: str, high: str) -> dict[str, str]:
        return {
            "name": name,
            "scenario_type": "custom",
            "threat_category": "ransomware",
            "entry_currency": cur,
            "tef_dist": "pert",
            "tef_low": "0.1",
            "tef_mode": "0.3",
            "tef_high": "0.5",
            "vuln_low": "0.2",
            "vuln_mode": "0.5",
            "vuln_high": "0.8",
            "pl_dist": "pert",
            "pl_low": low,
            "pl_mode": mode,
            "pl_high": high,
            "sl_dist": "pert",
            "sl_low": "",
            "sl_mode": "",
            "sl_high": "",
        }

    await csrf_post(
        client,
        "/scenarios",
        _f("sar-one", "SAR", "3750000", "7500000", "15000000"),
        follow_redirects=False,
    )
    await csrf_post(
        client,
        "/scenarios",
        _f("usd-one", "USD", "1000000", "2000000", "4000000"),
        follow_redirects=False,
    )
    rows = {
        r.name: r
        for r in (
            await db_session.execute(
                select(Scenario).where(Scenario.name.in_(["sar-one", "usd-one"]))
            )
        ).scalars()
    }
    assert float(rows["sar-one"].primary_loss["low"]) == pytest.approx(
        1_000_000
    )  # 3.75M SAR → 1M USD
    assert float(rows["usd-one"].primary_loss["low"]) == pytest.approx(1_000_000)
