# tests/contracts/test_sme_estimate_persist_iteration.py
"""Iteration contract for wizard_finalize.persist_estimates (spec §9.2).

CLAUDE.md data-contract enforcement: any `list[X] -> list[Y]` mapping needs
a regression guard against `[0]` / `[-1]` / `[first]` truncations. Here:
N=5 SME-estimate rows in -> N=5 ScenarioSMEEstimate ORM rows out.

We exercise persist_estimates directly (not through HTTP) so the test
stays focused on the iteration contract, not the route plumbing.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fair_cam.quantile_pooling import LogNormalTruncFit, PertTriple
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.enums import (
    AssetClass,
    EntityStatus,
    ScenarioFieldset,
    ScenarioSource,
    ScenarioType,
    ThreatActorType,
    ThreatCategory,
)
from idraa.models.scenario import Scenario
from idraa.models.scenario_sme_estimate import ScenarioSMEEstimate
from idraa.models.sme import SubjectMatterExpert
from idraa.services.wizard_finalize import PerFieldsetResult, persist_estimates


@pytest.mark.asyncio
async def test_persist_estimates_round_trips_n5_rows(
    db_session: AsyncSession,
    seed_organization: Any,
    seed_user: Any,
) -> None:
    """N=5 SME-estimate rows -> 5 ScenarioSMEEstimate ORM rows persisted.

    Catches future `[0]` / `[first]` regressions in persist_estimates'
    inner loop. The test seeds 5 user SMEs, builds a PerFieldsetResult
    with 5 rows on the tef fieldset, calls persist_estimates, and asserts
    exactly 5 rows landed in scenario_sme_estimates.
    """
    org_id = seed_organization.id
    # Need 5 SMEs to satisfy the FK on scenario_sme_estimates.sme_id.
    sme_ids: list[uuid.UUID] = []
    for i in range(5):
        sme = SubjectMatterExpert(
            organization_id=org_id,
            name=f"SME {i}",
            email=f"sme{i}@example.com",
            created_by=seed_user.id,
            created_via="admin",
        )
        db_session.add(sme)
        await db_session.flush()
        sme_ids.append(sme.id)

    # Seed a real scenario row to satisfy the FK on scenario_sme_estimates.scenario_id.
    scenario = Scenario(
        organization_id=org_id,
        name="iteration-test-scenario",
        scenario_type=ScenarioType.CUSTOM,
        threat_category=ThreatCategory.RANSOMWARE,
        threat_actor_type=ThreatActorType.CYBERCRIMINALS,
        asset_class=AssetClass.SYSTEMS,
        attack_vector="email",
        threat_event_frequency={"low": 1.0, "mode": 2.0, "high": 3.0},
        vulnerability={"low": 0.1, "mode": 0.2, "high": 0.3},
        primary_loss={"low": 100.0, "mode": 200.0, "high": 300.0},
        source=ScenarioSource.EXPERT_JUDGMENT,
        status=EntityStatus.ACTIVE,
        version="1.0",
        created_by=seed_user.id,
    )
    db_session.add(scenario)
    await db_session.flush()

    rows = [{"sme_id": sme_id, "low": 1.0 + i, "high": 2.0 + i} for i, sme_id in enumerate(sme_ids)]
    results: dict[str, PerFieldsetResult] = {
        "tef": PerFieldsetResult(
            pooled=LogNormalTruncFit(
                meanlog=0.5,
                sdlog=0.5,
                min_support=0.0,
                max_support=float("inf"),
            ),
            pert=PertTriple(low=1.0, mode=2.0, high=3.0),
            mode_clamp_reason=None,
            rows=rows,
            clamp_events=[],
        )
    }
    await persist_estimates(
        db_session,
        scenario.id,
        results=results,
        actor_id=seed_user.id,
        organization_id=org_id,
    )

    persisted = (
        (
            await db_session.execute(
                select(ScenarioSMEEstimate).where(
                    ScenarioSMEEstimate.scenario_id == scenario.id,
                    ScenarioSMEEstimate.fieldset == ScenarioFieldset.TEF,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(persisted) == 5, (
        f"persist_estimates: expected 5 rows persisted for tef, got {len(persisted)}. "
        f"kappa-class silent-data-loss if < 5."
    )
    # Belt-and-suspenders: SME ids round-trip in full.
    assert {p.sme_id for p in persisted} == set(sme_ids)
