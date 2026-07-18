"""tests/integration/test_qualitative_converter_integration.py — epic #34 P1b.

The converter has no UI yet (P1c); "integration" here means: run
``QualitativeConverterService.convert`` at the service layer against the
same DB the HTTP client sees, then drive the HTTP layer to prove the
converted scenario is genuinely excluded from run creation — the P1a
DRAFT-exclusion gate (epic #34 P1a, spec §4) composed end-to-end with the
P1b converter output, not just a unit-level status flag check.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.enums import EntityStatus, ThreatCategory
from idraa.models.qualitative_mapping import QualitativeMappingBand
from idraa.models.user import User
from idraa.services.qualitative_converter import BoundRow, QualitativeConverterService
from tests.conftest import csrf_post


async def _seed_band(
    db: AsyncSession, *, kind: str, label: str, low: float, mode: float, high: float
) -> None:
    db.add(
        QualitativeMappingBand(
            kind=kind,
            label=label,
            low=low,
            mode=mode,
            high=high,
            sort_order=1,
            derivation="integration-test canonical band, not a real citation",
            version=1,
        )
    )
    await db.flush()


async def _org_user(db: AsyncSession, org_id: object) -> User:
    row = (await db.execute(select(User).where(User.organization_id == org_id))).scalars().first()
    assert row is not None
    return row


@pytest.mark.asyncio
async def test_converted_scenario_excluded_from_run_creation(
    authed_analyst: tuple[object, object], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    user = await _org_user(db_session, org_id)
    await _seed_band(db_session, kind="frequency", label="moderate", low=1.0, mode=3.2, high=10.0)
    await _seed_band(
        db_session,
        kind="magnitude",
        label="high",
        low=1_000_000.0,
        mode=3_200_000.0,
        high=10_000_000.0,
    )

    row = BoundRow(
        source_row=1,
        title="Converted from register",
        description="Register-sourced row.",
        owner="Register Owner",
        likelihood_label="moderate",
        magnitude_label="high",
        category=ThreatCategory.SOCIAL_ENGINEERING,
        raw={"likelihood": "Likely", "impact": "High", "category": "Phishing"},
    )
    report = await QualitativeConverterService(db_session).convert(
        organization_id=org_id, user=user, source_file="register.xlsx", rows=[row]
    )
    await db_session.commit()
    assert len(report.created) == 1
    scenario_id = report.created[0].scenario_id

    from idraa.models.scenario import Scenario

    persisted = (
        await db_session.execute(select(Scenario).where(Scenario.id == scenario_id))
    ).scalar_one()
    assert persisted.status == EntityStatus.DRAFT
    assert persisted.vuln_framing == "legacy_residual"

    r = await csrf_post(
        client,
        "/analyses",
        {"scenario_ids": [str(scenario_id)], "mc_iterations": "1000"},
        follow_redirects=False,
    )
    assert r.status_code == 422
    assert "draft" in r.text.lower()
