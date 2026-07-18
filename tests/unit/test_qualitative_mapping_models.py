"""Model-shape tests for the qualitative mapping band layer (epic #34 P1b)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from idraa.models.enums import ScenarioSource
from idraa.models.qualitative_mapping import QualitativeMappingBand, QualitativeMappingOrgBand


def test_scenario_source_has_qualitative_member():
    assert ScenarioSource.QUALITATIVE_REGISTER_IMPORT.value == "qualitative_register_import"


@pytest.mark.asyncio
async def test_band_tables_roundtrip(db_session):
    band = QualitativeMappingBand(
        kind="frequency",
        label="moderate",
        low=1.0,
        mode=3.0,
        high=10.0,
        sort_order=2,
        derivation="test",
        version=1,
    )
    db_session.add(band)
    await db_session.flush()
    got = (await db_session.execute(select(QualitativeMappingBand))).scalars().one()
    assert (got.kind, got.label, got.mode) == ("frequency", "moderate", 3.0)


@pytest.mark.asyncio
async def test_org_band_roundtrip(db_session, organization):
    org_band = QualitativeMappingOrgBand(
        organization_id=organization.id,
        kind="magnitude",
        label="custom_tier",
        low=5_000.0,
        mode=8_000.0,
        high=12_000.0,
        reason="org-specific loss-capacity calibration",
    )
    db_session.add(org_band)
    await db_session.flush()
    got = (await db_session.execute(select(QualitativeMappingOrgBand))).scalars().one()
    assert got.organization_id == organization.id
    assert got.reason == "org-specific loss-capacity calibration"
    assert got.deleted_at is None
    assert (got.kind, got.label, got.low, got.mode, got.high) == (
        "magnitude",
        "custom_tier",
        5_000.0,
        8_000.0,
        12_000.0,
    )
