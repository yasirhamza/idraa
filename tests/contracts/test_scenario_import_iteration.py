"""Data-contract iteration test for the scenario importer (PR ρ rule).

For any ``list[ORM] → list[DTO]`` / multi-row apply path, build input with N ≥ 3
rows and assert the output preserves ALL N — catches a future ``[0]`` / ``[-1]``
optimization that silently drops rows from the import apply loop.

Fixture names adapted to the repo (``db_session`` / ``organization`` /
``admin_user`` from tests/conftest.py); the plan's skeleton uses ``seeded_org``.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select

from idraa.models.scenario import Scenario
from idraa.services.scenario_import import apply_validated_preview, validate_upload


@pytest.mark.asyncio
async def test_three_distinct_rows_all_imported(db_session, organization, admin_user) -> None:
    data = json.dumps(
        [
            {
                "name": f"Row{i}",
                "threat_category": "malware",
                "threat_event_frequency": {
                    "distribution": "PERT",
                    "low": 1,
                    "mode": 2,
                    "high": 3,
                },
                "vulnerability": {"distribution": "PERT", "low": 0.1, "mode": 0.2, "high": 0.3},
                "primary_loss": {"distribution": "PERT", "low": 10, "mode": 20, "high": 30},
            }
            for i in range(3)
        ]
    ).encode()
    token, _p, _e = await validate_upload(
        db_session,
        org_id=organization.id,
        user_id=admin_user.id,
        data=data,
        filename="s.json",
        content_type="application/json",
    )
    imported, skipped, errors = await apply_validated_preview(
        db_session,
        token=token,
        org_id=organization.id,
        user=admin_user,
    )
    assert (imported, skipped, errors) == (3, 0, [])
    count = (
        await db_session.execute(
            select(func.count())
            .select_from(Scenario)
            .where(Scenario.organization_id == organization.id)
        )
    ).scalar_one()
    assert count == 3
