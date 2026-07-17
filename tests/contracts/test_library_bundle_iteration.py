"""Adapter iteration contract — N≥3 bundle entries all survive apply.

Per the project Data-contract-enforcement rule: any list-input → list-output
path needs a regression test that builds N≥3 items and asserts all N are
preserved. Catches a future ``[0]`` / ``[-1]`` optimization silently dropping
entries from the bundle-apply loop.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select

from idraa.models.scenario_library import ScenarioLibraryEntry
from idraa.services.library_bundle_import import apply_validated_preview, validate_upload


@pytest.mark.asyncio
async def test_three_entry_bundle_all_imported(db_session, organization, admin_user) -> None:
    data = json.dumps(
        [
            {
                "slug": f"iter-{i}",
                "name": f"N{i}",
                "status": "published",
                "threat_event_type": "malware",
                "threat_actor_type": "cybercriminals",
                "asset_class": "systems",
                "description": "d" * 25,
                "canonical_fair_gap": "g" * 25,
                "threat_event_frequency": {"distribution": "PERT", "low": 1, "mode": 2, "high": 3},
                "vulnerability": {"distribution": "PERT", "low": 0.1, "mode": 0.2, "high": 0.3},
                "primary_loss": {"distribution": "PERT", "low": 1, "mode": 2, "high": 3},
                "calibration_anchor": {"industry": "other", "revenue_tier": "100m_to_1b"},
            }
            for i in range(3)
        ]
    ).encode()
    token, _p, _e = await validate_upload(
        db_session, org_id=organization.id, user_id=admin_user.id, data=data
    )
    imported, skipped, errors = await apply_validated_preview(
        db_session, token=token, org_id=organization.id, user=admin_user
    )
    assert (imported, skipped, errors) == (3, 0, [])
    n = (
        await db_session.execute(
            select(func.count())
            .select_from(ScenarioLibraryEntry)
            .where(ScenarioLibraryEntry.source == "imported")
        )
    ).scalar_one()
    assert n == 3
