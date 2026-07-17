"""Contract: scenario_sme_estimates enforces (sme_id IS NULL) != (sme_name IS NULL).

Exactly one identity path per row. Inserting both-null or both-set must fail
at the DB layer regardless of what the Python schema allows — defense in
depth for the XOR invariant the free-text + FK hybrid design depends on.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from idraa.models.enums import ScenarioFieldset
from idraa.models.scenario_sme_estimate import ScenarioSMEEstimate
from idraa.services import sme_directory as svc


@pytest.mark.asyncio
async def test_both_null_violates_xor(
    db_session, seed_organization, seed_user, seed_scenario_factory
) -> None:
    scenario = await seed_scenario_factory(name="XOR both-null scenario")
    db_session.add(
        ScenarioSMEEstimate(
            organization_id=seed_organization.id,
            scenario_id=scenario.id,
            fieldset=ScenarioFieldset.TEF,
            sme_id=None,
            sme_name=None,
            low=0.1,
            high=0.5,
            recorded_at=datetime.now(UTC),
            recorded_by=seed_user.id,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_both_set_violates_xor(
    db_session, seed_organization, seed_user, seed_scenario_factory
) -> None:
    scenario = await seed_scenario_factory(name="XOR both-set scenario")
    iris, _ = await svc.get_or_create_iris_sme(db_session, seed_organization.id)
    db_session.add(
        ScenarioSMEEstimate(
            organization_id=seed_organization.id,
            scenario_id=scenario.id,
            fieldset=ScenarioFieldset.TEF,
            sme_id=iris.id,
            sme_name="Alice Chen",
            low=0.1,
            high=0.5,
            recorded_at=datetime.now(UTC),
            recorded_by=seed_user.id,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_only_sme_id_succeeds(
    db_session, seed_organization, seed_user, seed_scenario_factory
) -> None:
    scenario = await seed_scenario_factory(name="XOR sme_id-only scenario")
    iris, _ = await svc.get_or_create_iris_sme(db_session, seed_organization.id)
    db_session.add(
        ScenarioSMEEstimate(
            organization_id=seed_organization.id,
            scenario_id=scenario.id,
            fieldset=ScenarioFieldset.TEF,
            sme_id=iris.id,
            sme_name=None,
            low=0.1,
            high=0.5,
            recorded_at=datetime.now(UTC),
            recorded_by=seed_user.id,
        )
    )
    await db_session.flush()


@pytest.mark.asyncio
async def test_only_sme_name_succeeds(
    db_session, seed_organization, seed_user, seed_scenario_factory
) -> None:
    scenario = await seed_scenario_factory(name="XOR sme_name-only scenario")
    db_session.add(
        ScenarioSMEEstimate(
            organization_id=seed_organization.id,
            scenario_id=scenario.id,
            fieldset=ScenarioFieldset.TEF,
            sme_id=None,
            sme_name="Alice Chen",
            low=0.1,
            high=0.5,
            recorded_at=datetime.now(UTC),
            recorded_by=seed_user.id,
        )
    )
    await db_session.flush()
