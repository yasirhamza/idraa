"""JSON export/import round-trip for scenario ATT&CK mappings (issue #475 T12)."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.attack import ScenarioAttackMapping
from idraa.routes.scenario_form_helpers import MAX_ATTACK_MAPPINGS
from idraa.services.scenario_export import scenario_to_json_obj
from idraa.services.scenario_import import apply_validated_preview, validate_upload
from tests.models.test_attack_models import _technique

# Reuse the seeded_catalog fixture from Task 8's test module (mirrors how
# tests/routes/test_scenario_form_attack_mappings.py imports it).
from tests.routes.test_attack_mapping_partial import seeded_catalog  # noqa: F401


@pytest.mark.asyncio
async def test_export_includes_natural_keys(db_session: AsyncSession, scenario_factory):
    """N >= 3 adapter-iteration contract test (CLAUDE.md data-contract rule):
    3 mappings in -> 3 natural-keyed entries out, none dropped."""
    scenario = await scenario_factory()
    t1 = _technique()
    t2 = _technique(
        domain="ics",
        technique_id="T0836",
        name="Modify Parameter",
        tactics=["impair-process-control"],
    )
    t3 = _technique(
        domain="enterprise",
        technique_id="T1486",
        name="Data Encrypted for Impact",
        tactics=["impact"],
    )
    db_session.add_all([t1, t2, t3])
    await db_session.flush()
    db_session.add_all(
        [
            ScenarioAttackMapping(
                organization_id=scenario.organization_id,
                scenario_id=scenario.id,
                technique_id=t1.id,
                source="library",
                rationale="curated",
            ),
            ScenarioAttackMapping(
                organization_id=scenario.organization_id,
                scenario_id=scenario.id,
                technique_id=t2.id,
                source="user",
            ),
            ScenarioAttackMapping(
                organization_id=scenario.organization_id,
                scenario_id=scenario.id,
                technique_id=t3.id,
                source="user",
                rationale="ransomware impact",
            ),
        ]
    )
    await db_session.flush()
    scenario_id = scenario.id  # capture BEFORE expire_all() (expired-attribute
    # access outside an await is a sync DB round-trip -> MissingGreenlet)
    db_session.expire_all()
    from idraa.models.scenario import Scenario

    loaded = (
        await db_session.execute(select(Scenario).where(Scenario.id == scenario_id))
    ).scalar_one()

    obj = scenario_to_json_obj(loaded)
    by_tid = {m["technique_id"]: m for m in obj["attack_techniques"]}
    assert set(by_tid) == {"T1566", "T0836", "T1486"}
    assert by_tid["T1566"] == {
        "domain": "enterprise",
        "technique_id": "T1566",
        "rationale": "curated",
    }
    assert by_tid["T0836"] == {"domain": "ics", "technique_id": "T0836", "rationale": None}
    assert by_tid["T1486"] == {
        "domain": "enterprise",
        "technique_id": "T1486",
        "rationale": "ransomware impact",
    }


def _scenario_obj(name: str, attack_techniques: list[dict]) -> dict:
    """Minimal JSON scenario object carrying an attack_techniques list."""
    return {
        "name": name,
        "threat_category": "ransomware",
        "threat_event_frequency": {"distribution": "PERT", "low": 0.1, "mode": 0.5, "high": 2},
        "vulnerability": {"distribution": "PERT", "low": 0.2, "mode": 0.35, "high": 0.6},
        "primary_loss": {
            "distribution": "PERT",
            "low": 100000,
            "mode": 1000000,
            "high": 15000000,
        },
        "attack_techniques": attack_techniques,
    }


@pytest.mark.asyncio
async def test_import_roundtrip_and_unknown_skip(
    db_session: AsyncSession,
    organization,
    admin_user,
    seeded_catalog,  # noqa: F811
):
    """Import a JSON scenario carrying 2 valid + 1 unknown + 1 deprecated technique:
    scenario imports, 2 mappings created (source='user'), 2 apply_errors recorded.

    SC2-I4: fixtures are the REAL ones — `organization` + `admin_user` (what
    tests/integration/test_scenario_import_apply.py uses), plus Task 8's
    `seeded_catalog` imported the way Task 9's test module imports it.
    Drive validate_upload -> apply_validated_preview exactly the way
    tests/integration/test_scenario_import_apply.py does.

    Also asserts: a duplicate (domain, technique_id) pair in the file yields
    ONE mapping (dedupe), and an attack_techniques list exceeding
    MAX_ATTACK_MAPPINGS after dedupe is a clean row error, not a 500.
    """
    # seeded_catalog order (tests/routes/test_attack_mapping_partial.py):
    # [T1566 enterprise, T1486 enterprise, T0836 ics, T9999 deprecated, T9998 deprecated]
    over_cap = [
        {"domain": "enterprise", "technique_id": f"T{2000 + i:04d}"}
        for i in range(MAX_ATTACK_MAPPINGS + 1)
    ]
    rows = [
        _scenario_obj(
            "AttackImp-Valid",
            [
                {"domain": "enterprise", "technique_id": "T1566", "rationale": "phish"},
                {"domain": "enterprise", "technique_id": "T1566"},  # duplicate pair -> dedupe
                {"domain": "enterprise", "technique_id": "T1486"},
                {"domain": "enterprise", "technique_id": "T9000"},  # unknown
                {"domain": "enterprise", "technique_id": "T9999"},  # deprecated
            ],
        ),
        _scenario_obj("AttackImp-OverCap", over_cap),
    ]
    data = json.dumps(rows).encode()

    token, preview, errors = await validate_upload(
        db_session,
        org_id=organization.id,
        user_id=admin_user.id,
        data=data,
        filename="s.json",
        content_type="application/json",
    )
    assert [p["action"] for p in preview] == ["create", "error"]
    assert any(
        e["column"] == "attack_techniques" and "exceeding the maximum" in e["reason"]
        for e in errors
    )

    imported, skipped, apply_errors = await apply_validated_preview(
        db_session,
        token=token,
        org_id=organization.id,
        user=admin_user,
    )
    assert imported == 1
    # Mapping-skip errors do NOT bump the `skipped` SCENARIO counter; the
    # over-cap row was already an "error" action (not "skip") at validation.
    assert skipped == 0

    from idraa.models.scenario import Scenario

    scenario = (
        await db_session.execute(select(Scenario).where(Scenario.name == "AttackImp-Valid"))
    ).scalar_one()
    assert scenario.source.value == "file_import"

    mappings = (
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
    # Duplicate (enterprise, T1566) pair collapsed to one mapping; T9000/T9999
    # never created a row at all.
    assert len(mappings) == 2
    assert {m.source for m in mappings} == {"user"}
    by_tid = {m.technique.technique_id: m for m in mappings}
    assert set(by_tid) == {"T1566", "T1486"}
    assert by_tid["T1566"].rationale == "phish"  # kept from the FIRST occurrence

    mapping_errors = [e for e in apply_errors if e["column"] == "attack_techniques"]
    # 1 unknown (T9000) + 1 deprecated (T9999) from the valid row, PLUS the
    # structural over-cap error re-surfaced from the second row (apply_errors
    # seeds from the same `errors` list validate_upload already saw).
    assert len(mapping_errors) == 3
    assert any("not found in catalog" in e["reason"] for e in mapping_errors)
    assert any("is deprecated" in e["reason"] for e in mapping_errors)
    assert any("exceeding the maximum" in e["reason"] for e in mapping_errors)
