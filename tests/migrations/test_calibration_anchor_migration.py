"""alembic round-trip + ORM round-trip for ScenarioLibraryEntry.calibration_anchor.

F1 (PR γ-2) introduced this column nullable.
PR γ-3 curated all 31 seed entries.
F4 (PR γ-4, #115) flipped the column to NOT NULL — the test below now asserts
``nullable is False`` and inserts without an anchor are expected to fail.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.enums import AssetClass, ThreatActorType, ThreatCategory
from idraa.models.scenario_library import ScenarioLibraryEntry


def _entry_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "version": 1,
        "slug": "x",
        "name": "x",
        "status": "published",
        "threat_event_type": ThreatCategory.RANSOMWARE,
        "threat_actor_type": ThreatActorType.CYBERCRIMINALS,
        "asset_class": AssetClass.SYSTEMS,
        "tags": [],
        "description": "d",
        "canonical_fair_gap": "g",
        "source_citations": [],
        "threat_event_frequency": {"distribution": "PERT", "low": 1.0, "mode": 4.0, "high": 12.0},
        "vulnerability": {"distribution": "PERT", "low": 0.05, "mode": 0.20, "high": 0.50},
        "primary_loss": {"distribution": "PERT", "low": 1.0, "mode": 2.0, "high": 3.0},
        "secondary_loss": None,
        "suggested_control_ids": [],
        "calibration_anchor": {"industry": "healthcare", "revenue_tier": "10b_to_100b"},
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_calibration_anchor_column_is_not_null(db_session: AsyncSession) -> None:
    """Post-γ-4 (#115): column is NOT NULL. Regression guard against future
    migrations accidentally re-introducing nullable.
    """
    columns = await db_session.run_sync(
        lambda sync_session: {
            c["name"]: c for c in inspect(sync_session.bind).get_columns("scenario_library_entries")
        }
    )
    assert "calibration_anchor" in columns
    assert columns["calibration_anchor"]["nullable"] is False, (
        "calibration_anchor MUST stay NOT NULL post-γ-4. If you need to "
        "re-introduce nullable, re-litigate against the FAIR-CAM-native "
        "design (spec §5.1) — controls express maturity, anchors are required."
    )


@pytest.mark.asyncio
async def test_calibration_anchor_round_trips_json_dict(db_session: AsyncSession) -> None:
    entry = ScenarioLibraryEntry(
        **_entry_kwargs(
            slug="calib-rt",
            calibration_anchor={"industry": "healthcare", "revenue_tier": "10b_to_100b"},
        )
    )
    db_session.add(entry)
    await db_session.flush()
    await db_session.commit()

    loaded = (
        await db_session.execute(
            select(ScenarioLibraryEntry).where(ScenarioLibraryEntry.slug == "calib-rt")
        )
    ).scalar_one()
    assert loaded.calibration_anchor == {
        "industry": "healthcare",
        "revenue_tier": "10b_to_100b",
    }


# test_calibration_anchor_defaults_to_none — DELETED in PR γ-4 (#115)
# Post NOT NULL flip the column cannot default to None at the structural
# level. SQLite-specific quirk: SQLAlchemy serializes Python None to JSON
# 'null' string for JSON columns (per scenario_library_repo.py:_json_array_overlaps
# documentation), so a DB-layer IntegrityError test isn't meaningful here.
# The contract is enforced via:
#   - test_calibration_anchor_column_is_not_null above (structural ORM check).
#   - test_seed_loader_rejects_entry_without_calibration_anchor in
#     tests/services/test_library_calibration.py (Pydantic seed validator).
