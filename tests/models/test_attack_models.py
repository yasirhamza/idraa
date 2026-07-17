"""Model tests for the ATT&CK catalog + mapping tables (issue #475 PR 1).

The pytest harness creates schema via Base.metadata.create_all, so these
tests exercise the ORM-declared constraints directly on the test DB.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.attack import (
    AttackTactic,
    AttackTechnique,
    ScenarioAttackMapping,
)


def _tactic(**kw):
    base = {
        "domain": "enterprise",
        "tactic_id": "TA0001",
        "shortname": "initial-access",
        "name": "Initial Access",
        "description": "Getting in.",
        "display_order": 0,
        "url": "https://attack.mitre.org/tactics/TA0001/",
    }
    base.update(kw)
    return AttackTactic(**base)


def _technique(**kw):
    base = {
        "domain": "enterprise",
        "technique_id": "T1566",
        "name": "Phishing",
        "description": "Adversaries may send phishing messages.",
        "tactics": ["initial-access"],
        "parent_technique_id": None,
        "deprecated": False,
        "catalog_version": "18.0",
        "url": "https://attack.mitre.org/techniques/T1566/",
        "citation": {
            "source": "MITRE ATT&CK",
            "copyright": "© 2026 The MITRE Corporation.",
            "license": "MITRE ATT&CK Terms of Use",
            "document": "ATT&CK Enterprise Matrix",
            "attack_version": "18.0",
            "accessed": "2026-07-04",
        },
    }
    base.update(kw)
    return AttackTechnique(**base)


@pytest.mark.asyncio
async def test_technique_unique_per_domain(db_session: AsyncSession):
    db_session.add(_technique())
    await db_session.flush()
    db_session.add(_technique(name="Phishing dupe"))
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()
    # Same technique_id in a DIFFERENT domain is allowed.
    db_session.add(_technique())
    db_session.add(_technique(domain="ics"))
    await db_session.flush()


@pytest.mark.asyncio
async def test_tactic_unique_shortname_per_domain(db_session: AsyncSession):
    db_session.add(_tactic())
    await db_session.flush()
    db_session.add(_tactic(tactic_id="TA9999"))  # same (domain, shortname)
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_scenario_mapping_unique_and_cascade(db_session: AsyncSession, scenario_factory):
    """One mapping per (scenario, technique); deleting the scenario deletes mappings."""
    scenario = await scenario_factory()
    tech = _technique()
    db_session.add(tech)
    await db_session.flush()
    db_session.add(
        ScenarioAttackMapping(
            organization_id=scenario.organization_id,
            scenario_id=scenario.id,
            technique_id=tech.id,
            source="user",
        )
    )
    await db_session.flush()
    db_session.add(
        ScenarioAttackMapping(
            organization_id=scenario.organization_id,
            scenario_id=scenario.id,
            technique_id=tech.id,
            source="library",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()

    # SC-I2: the rollback discarded the flushed-but-uncommitted scenario/tech
    # rows too (plain per-test session, no savepoints) — re-create everything
    # before exercising the delete-cascade, else delete() targets a
    # no-longer-persistent instance and the assertion is vacuous.
    scenario = await scenario_factory()
    tech = _technique()
    db_session.add(tech)
    await db_session.flush()
    db_session.add(
        ScenarioAttackMapping(
            organization_id=scenario.organization_id,
            scenario_id=scenario.id,
            technique_id=tech.id,
            source="user",
        )
    )
    await db_session.flush()

    # ORM cascade: removing the scenario removes its mappings.
    await db_session.delete(scenario)
    await db_session.flush()
    left = (
        (
            await db_session.execute(
                select(ScenarioAttackMapping).where(
                    ScenarioAttackMapping.scenario_id == scenario.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert left == []


@pytest.mark.asyncio
async def test_scenario_relationship_loads_mappings_with_techniques(
    db_session: AsyncSession, scenario_factory
):
    scenario = await scenario_factory()
    t1 = _technique()
    t2 = _technique(technique_id="T1486", name="Data Encrypted for Impact", tactics=["impact"])
    db_session.add_all([t1, t2])
    await db_session.flush()
    db_session.add_all(
        [
            ScenarioAttackMapping(
                organization_id=scenario.organization_id,
                scenario_id=scenario.id,
                technique_id=t1.id,
                source="user",
            ),
            ScenarioAttackMapping(
                organization_id=scenario.organization_id,
                scenario_id=scenario.id,
                technique_id=t2.id,
                source="library",
                rationale="curated",
            ),
        ]
    )
    await db_session.flush()
    # Capture ID before expire_all so we don't trigger async lazy-load on the
    # expired object (established repo pattern — see
    # tests/integration/test_reports_routes.py:570).
    scenario_id = scenario.id
    db_session.expire_all()

    from idraa.models.scenario import Scenario

    loaded = (
        await db_session.execute(select(Scenario).where(Scenario.id == scenario_id))
    ).scalar_one()
    assert {m.technique.technique_id for m in loaded.attack_mappings} == {"T1566", "T1486"}
