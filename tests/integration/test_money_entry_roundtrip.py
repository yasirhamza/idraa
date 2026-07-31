"""Comma-formatted POST round-trips for every Excel-like money surface.

Owner UAT 2026-08-01 rollout + its review (finding I5): every converted
surface posts comma-grouped values now, and B1-B6 showed that a surface
whose server parse misses the benign sanitize fails silently (dropped
override values) or loudly (422 on legitimate saves). One POST-with-commas
per named surface is the minimum regression bar; each test asserts the
PERSISTED value, not just the status code.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import csrf_post

_PERT_TEF_VULN = {
    "tef_dist": "pert",
    "tef_low": "0.1",
    "tef_mode": "0.5",
    "tef_high": "2.0",
    "vuln_low": "0.2",
    "vuln_mode": "0.4",
    "vuln_high": "0.6",
}


@pytest.mark.asyncio
async def test_org_money_fields_accept_commas(
    authed_admin: tuple[AsyncClient, Any], db_session: AsyncSession
) -> None:
    from idraa.models.organization import Organization

    client, org_id = authed_admin
    r = await csrf_post(
        client,
        "/organization",
        {
            "name": "Acme Commas",
            "industry_type": "healthcare",
            "organization_size": "large",
            "annual_revenue": "5,000,000,000",
            "annual_security_budget": "3,500,000",
            "loss_tolerance_amount": "$2,000,000",
            "risk_appetite": "moderate",
            "security_maturity": "defined",
            "preferred_currency": "USD",
            "preferred_language": "en",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    org = (await db_session.execute(select(Organization))).scalars().first()
    assert org is not None
    assert org.annual_revenue == Decimal("5000000000")
    assert org.annual_security_budget == Decimal("3500000")
    assert org.loss_tolerance_amount == Decimal("2000000")


@pytest.mark.asyncio
async def test_control_cost_and_currency_capability_accept_commas(
    authed_admin: tuple[AsyncClient, Any], db_session: AsyncSession
) -> None:
    from idraa.models.control import Control

    client, _ = authed_admin
    r = await csrf_post(
        client,
        "/controls/new",
        {
            "name": "Comma Cost Control",
            "description": "money round-trip",
            "domain": "loss_event",
            "type": "administrative",
            "status": "active",
            "version": "1.0",
            "annual_cost": "12,500",
            # LEC_RESP_LOSS_REDUCTION is a CURRENCY ($/event) capability.
            "assignments[0][sub_function]": "lec_resp_loss_reduction",
            "assignments[0][capability_value]": "5,000",
            "assignments[0][coverage]": "0.8",
            "assignments[0][reliability]": "0.8",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    control = (
        (await db_session.execute(select(Control).where(Control.name == "Comma Cost Control")))
        .scalars()
        .one()
    )
    assert control.annual_cost == Decimal("12500")
    assert control.assignments[0].capability_value == 5000.0


@pytest.mark.asyncio
async def test_expert_scenario_losses_accept_commas(
    authed_analyst: tuple[AsyncClient, Any], db_session: AsyncSession
) -> None:
    """PL lognormal + pl_max + inline SL (review B3/B4/B5 regression pins)."""
    from idraa.models.scenario import Scenario

    client, _ = authed_analyst
    r = await csrf_post(
        client,
        "/scenarios",
        {
            "name": "Comma Losses",
            "threat_category": "ransomware",
            **_PERT_TEF_VULN,
            "pl_dist": "lognormal",
            "pl_low": "1,000,000",
            "pl_high": "20,000,000",
            "pl_max": "30,000,000",
            "sl_dist": "pert",
            "sl_low": "10,000",
            "sl_mode": "50,000",
            "sl_high": "250,000",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    s = (
        (await db_session.execute(select(Scenario).where(Scenario.name == "Comma Losses")))
        .scalars()
        .one()
    )
    assert s.primary_loss["max"] == 30000000.0
    assert s.secondary_loss == {
        "distribution": "PERT",
        "low": 10000.0,
        "mode": 50000.0,
        "high": 250000.0,
    }


@pytest.mark.asyncio
async def test_library_override_losses_accept_commas(
    admin_client: AsyncClient, db_session: AsyncSession, seed_library_entry: Any
) -> None:
    """Also the B1/B2 regression pin: correct field NAMES + real server parse
    (the Annotated BeforeValidator shipped as a no-op; a silent None-override
    was the failure mode)."""
    from idraa.models.scenario_library import ScenarioLibraryOverride

    r = await csrf_post(
        admin_client,
        "/library/overrides",
        {
            "entry_id": str(seed_library_entry.id),
            "pl_low": "100,000",
            "pl_mode": "750,000",
            "pl_high": "5,000,000",
            "sl_low": "10,000",
            "sl_mode": "25,000",
            "sl_high": "100,000",
            "reason": "comma round-trip regression pin",
        },
    )
    assert r.status_code in (200, 303), r.text
    ov = (await db_session.execute(select(ScenarioLibraryOverride))).scalars().one()
    assert ov.primary_loss == {
        "distribution": "PERT",
        "low": 100000.0,
        "mode": 750000.0,
        "high": 5000000.0,
    }
    assert ov.secondary_loss["high"] == 100000.0


@pytest.mark.asyncio
async def test_qualitative_magnitude_band_accepts_commas(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Review B6: the bands form got the money input via form_field's DYNAMIC
    input_type — the surface a static grep missed."""
    from idraa.models.qualitative_mapping import QualitativeMappingOrgBand

    r = await csrf_post(
        admin_client,
        "/qualitative-bands",
        {
            "kind": "magnitude",
            "label": "high",
            "low": "1,000,000",
            "mode": "5,000,000",
            "high": "$25,000,000",
            "reason": "comma round-trip regression pin",
        },
        follow_redirects=False,
    )
    assert r.status_code in (200, 303), r.text
    band = (await db_session.execute(select(QualitativeMappingOrgBand))).scalars().one()
    assert band.low == 1000000.0
    assert band.mode == 5000000.0
    assert band.high == 25000000.0


def test_pin_quantile_parser_accepts_commas() -> None:
    """Pin-panel p50/p95 parse (the last converted surface): benign sanitize
    before float; letters still raise (never-launder)."""
    import pytest as _pytest

    from idraa.routes.scenario_loss_pin import _parse_pin_quantile

    assert _parse_pin_quantile("1,000,000", field_name="pin_p50") == 1_000_000.0
    assert _parse_pin_quantile("$2 500 000", field_name="pin_p95") == 2_500_000.0
    with _pytest.raises(ValueError, match="pin_p50"):
        _parse_pin_quantile("1.5M", field_name="pin_p50")
