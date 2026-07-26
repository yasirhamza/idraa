"""PR2 D13/D18/D19 capacity-bound epic: import producer (Task 4b), route level.

Complements tests/unit/test_scenario_import_capacity_bound.py's pure
``_validate_rows`` coverage with a real-HTTP round trip through
``routes/scenario_import.py`` -- the surface whose ``require_sole_org`` call
sites (lines 99/135 pre-Task-4b) were replaced with
``db.get(Organization, user.organization_id)``. These tests exercise that
replacement end to end: upload -> preview -> confirm -> stored scenario.
"""

from __future__ import annotations

import re
import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.organization import Organization
from idraa.models.scenario import Scenario
from tests.conftest import csrf_post

_CSV_HEADER = (
    "name,description,scenario_type,threat_category,threat_actor_type,attack_vector,"
    "asset_class,version,status,distribution,tef_low,tef_mode,tef_high,vuln_low,vuln_mode,"
    "vuln_high,pl_dist,pl_low,pl_mode,pl_high,sl_low,sl_mode,sl_high\n"
)


def _catastrophic_csv(name: str) -> str:
    # p5=100, p95=10000 -> a valid lognormal PL; CSV cannot express `max`.
    return _CSV_HEADER + (
        f"{name},,custom,ransomware,cybercriminals,,systems,1.0,active,PERT,"
        "0.1,0.5,2,0.2,0.35,0.6,lognormal,100,,10000,,,\n"
    )


async def _set_annual_revenue(db: AsyncSession, org_id: uuid.UUID, revenue: str | None) -> None:
    org = await db.get(Organization, org_id)
    assert org is not None
    org.annual_revenue = Decimal(revenue) if revenue is not None else None
    await db.commit()


@pytest.mark.asyncio
async def test_csv_catastrophic_import_mints_capacity_max_end_to_end(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_admin
    await _set_annual_revenue(db_session, org_id, "50000000")  # capacity_k=1.0 -> max=5e7

    pr = await csrf_post(
        client,
        "/scenarios/import",
        {},
        files={"file": ("s.csv", _catastrophic_csv("CapMint"), "text/csv")},
    )
    assert pr.status_code == 200
    assert "create" in pr.text.lower()

    m = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", pr.text)
    assert m, "expected a preview-token UUID in the preview body"
    token = m.group(0)

    cr = await csrf_post(
        client,
        "/scenarios/import/confirm",
        {"token": token},
        follow_redirects=False,
    )
    assert cr.status_code in (200, 303)

    scenario = (
        await db_session.execute(select(Scenario).where(Scenario.name == "CapMint"))
    ).scalar_one()
    assert scenario.primary_loss["distribution"] == "lognormal"
    assert scenario.primary_loss["max"] == pytest.approx(50_000_000.0)


@pytest.mark.asyncio
async def test_csv_catastrophic_import_blocks_with_d18_when_revenue_unset(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_admin
    await _set_annual_revenue(db_session, org_id, None)  # create_org() default, made explicit

    pr = await csrf_post(
        client,
        "/scenarios/import",
        {},
        files={"file": ("s.csv", _catastrophic_csv("CapBlocked"), "text/csv")},
    )
    assert pr.status_code == 200
    # The D18 pinned copy (reused verbatim from Task 4a) -- substrings
    # without an apostrophe so Jinja's autoescape can't break the match.
    assert "annual revenue" in pr.text
    assert "expert form with an explicit cap" in pr.text

    rows = (
        (await db_session.execute(select(Scenario).where(Scenario.name == "CapBlocked")))
        .scalars()
        .all()
    )
    assert rows == []  # never staged as create-able, never created
