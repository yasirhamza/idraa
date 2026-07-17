"""Shared fixtures for tests/routes/ (issue #475 fixture consolidation).

``scenario_factory`` below was triplicated byte-identically (module-level
overrides) across test_attack_mapping_partial.py, test_scenario_view_attack_badges.py,
and test_attack_coverage_page.py (Tasks 8/11/14) — consolidated here per the
DRY-fixtures convention. Package-scoped conftest.py fixtures resolve to every
module under tests/routes/ without an import, exactly like the module-level
overrides they replace.
"""

from __future__ import annotations

import uuid

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def scenario_factory(
    db_session: AsyncSession,
    authed_analyst: tuple[AsyncClient, uuid.UUID],
):
    """Module OVERRIDE (Arch4-N2): the root ``tests/conftest.py``
    ``scenario_factory`` defaults to ``seed_organization``, but the
    authenticated client (``analyst_client`` / ``authed_analyst``) used by
    the ATT&CK route tests lives in a DIFFERENT org — mirrors
    ``tests/routes/test_scenario_detail_recommendations.py``'s seed fixtures.
    Same override name/shape reused verbatim across Tasks 8/11/14; promoted
    to this shared conftest so the three modules stop carrying byte-identical
    copies.
    """
    from sqlalchemy import select

    from idraa.models.enums import EntityStatus, ScenarioType, ThreatCategory
    from idraa.models.scenario import Scenario
    from idraa.models.user import User

    _client, org_id = authed_analyst

    async def _factory(**kwargs):
        created_by = kwargs.pop("created_by", None)
        if created_by is None:
            created_by = (
                await db_session.execute(
                    select(User.id).where(User.organization_id == org_id).limit(1)
                )
            ).scalar_one()
        defaults = {
            "organization_id": kwargs.pop("organization_id", org_id),
            "name": kwargs.pop("name", "Scenario"),
            "scenario_type": kwargs.pop("scenario_type", ScenarioType.CUSTOM),
            "threat_category": kwargs.pop("threat_category", ThreatCategory.RANSOMWARE),
            "threat_event_frequency": kwargs.pop(
                "threat_event_frequency",
                {"distribution": "PERT", "low": 0.1, "mode": 0.5, "high": 2.0},
            ),
            "vulnerability": kwargs.pop(
                "vulnerability", {"distribution": "PERT", "low": 0.2, "mode": 0.4, "high": 0.6}
            ),
            "primary_loss": kwargs.pop(
                "primary_loss",
                {"distribution": "PERT", "low": 50_000.0, "mode": 250_000.0, "high": 2_000_000.0},
            ),
            "status": kwargs.pop("status", EntityStatus.ACTIVE),
            "created_by": created_by,
        }
        defaults.update(kwargs)
        scenario = Scenario(**defaults)
        db_session.add(scenario)
        await db_session.flush()
        return scenario

    return _factory
