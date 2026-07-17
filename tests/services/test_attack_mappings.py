"""Service tests for scenario↔ATT&CK mapping authoring (issue #475 T7)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.errors import ValidationError
from idraa.models.attack import (
    ScenarioAttackMapping,
    ScenarioLibraryEntryAttackMapping,
)
from idraa.routes.scenario_form_helpers import extract_attack_mapping_ids
from idraa.services.attack_mappings import (
    copy_library_attack_mappings,
    set_scenario_attack_mappings,
)

# Reuse the _technique helper shape from tests/models/test_attack_models.py —
# import it or duplicate the factory locally (duplication is fine in tests).
from tests.models.test_attack_models import _technique


def test_extract_pops_keys_dedupes_and_orders():
    a, b = uuid.uuid4(), uuid.uuid4()
    raw = {
        "name": "x",
        "attack_mappings[2][technique_id]": str(b),
        "attack_mappings[0][technique_id]": str(a),
        "attack_mappings[1][technique_id]": str(a),  # duplicate — collapses
        "attack_mappings[3][technique_id]": "",  # blank row — ignored
    }
    ids = extract_attack_mapping_ids(raw)
    assert ids == [a, b]
    assert [k for k in raw if k.startswith("attack_mappings[")] == []
    assert raw["name"] == "x"  # untouched


def test_extract_raises_on_garbage_uuid():
    with pytest.raises(ValueError):
        extract_attack_mapping_ids({"attack_mappings[0][technique_id]": "not-a-uuid"})


def test_extract_caps_row_count():
    """Sec-I2: unbounded submitted rows would blow the SQL variable limit → 500."""
    raw = {f"attack_mappings[{i}][technique_id]": str(uuid.uuid4()) for i in range(201)}
    with pytest.raises(ValueError):
        extract_attack_mapping_ids(raw)


@pytest.fixture
async def library_entry_factory(db_session: AsyncSession):
    """Local factory: canonical ScenarioLibraryEntry, version=1.

    No shared ``library_entry_factory`` fixture exists yet — mirrors the
    ``ScenarioLibraryEntry`` construction in
    ``tests/routes/test_scenario_detail_recommendations.py``.
    """
    from idraa.models.enums import AssetClass, ThreatActorType, ThreatCategory
    from idraa.models.scenario_library import ScenarioLibraryEntry

    async def _factory(**overrides) -> ScenarioLibraryEntry:
        base = {
            "id": uuid.uuid4(),
            "version": 1,
            "slug": f"test-entry-{uuid.uuid4().hex[:8]}",
            "name": "Ransomware on EHR",
            "status": "published",
            "threat_event_type": ThreatCategory.RANSOMWARE,
            "threat_actor_type": ThreatActorType.CYBERCRIMINALS,
            "asset_class": AssetClass.SYSTEMS,
            "tags": [],
            "description": "d" * 25,
            "canonical_fair_gap": "g" * 25,
            "source_citations": [],
            "threat_event_frequency": {
                "distribution": "PERT",
                "low": 1.0,
                "mode": 4.0,
                "high": 12.0,
            },
            "vulnerability": {"distribution": "PERT", "low": 0.05, "mode": 0.20, "high": 0.50},
            "primary_loss": {
                "distribution": "PERT",
                "low": 100_000.0,
                "mode": 750_000.0,
                "high": 5_000_000.0,
            },
        }
        base.update(overrides)
        entry = ScenarioLibraryEntry(**base)
        db_session.add(entry)
        await db_session.flush()
        return entry

    return _factory


@pytest.mark.asyncio
async def test_set_emits_audit_row_on_diff_only(db_session: AsyncSession, scenario_factory):
    """Sec-I1: a mapping-only edit must leave an audit trail; a no-op must not.

    Adapted to the real AuditLog columns (src/idraa/models/audit_log.py):
    ``changes`` is the JSON column holding the diff dict.
    """
    scenario = await scenario_factory()
    tech = _technique()
    db_session.add(tech)
    await db_session.flush()

    await set_scenario_attack_mappings(
        db_session,
        scenario_id=scenario.id,
        organization_id=scenario.organization_id,
        technique_ids=[tech.id],
        actor_id=None,
    )
    from idraa.models.audit_log import AuditLog

    rows = (await db_session.execute(select(AuditLog))).scalars().all()
    mapping_audits = [r for r in rows if "attack_techniques" in str(r.changes)]
    assert len(mapping_audits) == 1  # diff → one audit row
    assert mapping_audits[0].changes["attack_techniques"] == [[], ["enterprise/T1566"]]

    # No-op resubmit of the same set → no new audit row.
    await set_scenario_attack_mappings(
        db_session,
        scenario_id=scenario.id,
        organization_id=scenario.organization_id,
        technique_ids=[tech.id],
        actor_id=None,
    )
    rows = (await db_session.execute(select(AuditLog))).scalars().all()
    mapping_audits = [r for r in rows if "attack_techniques" in str(r.changes)]
    assert len(mapping_audits) == 1


@pytest.mark.asyncio
async def test_set_diff_preserves_library_source(db_session: AsyncSession, scenario_factory):
    """Diff-apply: survivors keep source/rationale; removed rows deleted; new rows source='user'."""
    scenario = await scenario_factory()
    t1, t2, t3 = _technique(), _technique(technique_id="T1486"), _technique(technique_id="T1078")
    db_session.add_all([t1, t2, t3])
    await db_session.flush()
    db_session.add(
        ScenarioAttackMapping(
            organization_id=scenario.organization_id,
            scenario_id=scenario.id,
            technique_id=t1.id,
            source="library",
            rationale="curated rationale",
        )
    )
    await db_session.flush()

    # Keep t1, add t2 + t3, i.e. submitted set = {t1, t2, t3}.
    await set_scenario_attack_mappings(
        db_session,
        scenario_id=scenario.id,
        organization_id=scenario.organization_id,
        technique_ids=[t1.id, t2.id, t3.id],
    )
    rows = (
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
    by_tech = {r.technique_id: r for r in rows}
    assert set(by_tech) == {t1.id, t2.id, t3.id}  # all 3 present (N≥3 iteration guard)
    assert by_tech[t1.id].source == "library"  # survivor untouched
    assert by_tech[t1.id].rationale == "curated rationale"
    assert by_tech[t2.id].source == "user" and by_tech[t3.id].source == "user"

    # Now remove t1 and t3.
    await set_scenario_attack_mappings(
        db_session,
        scenario_id=scenario.id,
        organization_id=scenario.organization_id,
        technique_ids=[t2.id],
    )
    rows = (
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
    assert [r.technique_id for r in rows] == [t2.id]


@pytest.mark.asyncio
async def test_set_rejects_unknown_and_newly_deprecated(db_session: AsyncSession, scenario_factory):
    scenario = await scenario_factory()
    dead = _technique(technique_id="T9999", deprecated=True)
    db_session.add(dead)
    await db_session.flush()
    with pytest.raises(ValidationError):
        await set_scenario_attack_mappings(
            db_session,
            scenario_id=scenario.id,
            organization_id=scenario.organization_id,
            technique_ids=[uuid.uuid4()],  # unknown
        )
    with pytest.raises(ValidationError):
        await set_scenario_attack_mappings(
            db_session,
            scenario_id=scenario.id,
            organization_id=scenario.organization_id,
            technique_ids=[dead.id],  # deprecated — blocked for NEW adds
        )


@pytest.mark.asyncio
async def test_set_keeps_existing_deprecated_mapping(db_session: AsyncSession, scenario_factory):
    """A pre-existing mapping to a (later-)deprecated technique survives a resubmit."""
    scenario = await scenario_factory()
    dead = _technique(technique_id="T9999", deprecated=True)
    live = _technique()
    db_session.add_all([dead, live])
    await db_session.flush()
    db_session.add(
        ScenarioAttackMapping(
            organization_id=scenario.organization_id,
            scenario_id=scenario.id,
            technique_id=dead.id,
            source="user",
        )
    )
    await db_session.flush()
    await set_scenario_attack_mappings(
        db_session,
        scenario_id=scenario.id,
        organization_id=scenario.organization_id,
        technique_ids=[dead.id, live.id],  # dead is a SURVIVOR, not a new add
    )
    rows = (
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
    assert {r.technique_id for r in rows} == {dead.id, live.id}


@pytest.mark.asyncio
async def test_copy_library_mappings_preserves_all_n(
    db_session: AsyncSession, scenario_factory, library_entry_factory
):
    """Adapter-iteration contract: clone copies ALL N≥3 curated mappings."""
    scenario = await scenario_factory()
    entry = await library_entry_factory()
    techs = [_technique(), _technique(technique_id="T1486"), _technique(technique_id="T1078")]
    db_session.add_all(techs)
    await db_session.flush()
    for t in techs:
        db_session.add(
            ScenarioLibraryEntryAttackMapping(
                library_entry_id=entry.id,
                library_entry_version=entry.version,
                technique_id=t.id,
                rationale=f"curated {t.technique_id}",
                provenance="expert-estimate",
                citations=[],
            )
        )
    await db_session.flush()

    n = await copy_library_attack_mappings(
        db_session,
        scenario_id=scenario.id,
        organization_id=scenario.organization_id,
        entry_id=entry.id,
        entry_version=entry.version,
    )
    assert n == 3
    rows = (
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
    assert len(rows) == 3
    assert all(r.source == "library" for r in rows)
    assert {r.rationale for r in rows} == {f"curated {t.technique_id}" for t in techs}
