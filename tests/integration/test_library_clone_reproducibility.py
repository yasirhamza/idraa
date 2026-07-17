"""§6.9.4 reproducibility — library-pinned scenario hash matches re-clone after
library entry/override version bumps.

Spec §4.3 + §9.4.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.enums import AssetClass, ScenarioSource, ThreatActorType, ThreatCategory
from idraa.models.organization import Organization
from idraa.models.scenario_library import ScenarioLibraryEntry, ScenarioLibraryOverride
from idraa.models.user import User
from idraa.repositories.scenario_library_repo import ScenarioLibraryRepo
from idraa.schemas.scenario import ScenarioForm
from idraa.services.run_inputs_hash import build_inputs_hash
from idraa.services.scenario_library import ScenarioLibraryService
from idraa.services.scenarios import ScenarioService

# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------


def _form_from_entry(
    entry: Any,
    *,
    name: str,
    override: Any = None,
    **field_overrides: Any,
) -> ScenarioForm:
    """Build a ScenarioForm matching a library entry's distributions
    (optionally overlaid by an override row). Mirrors the wizard's
    capture-at-clone-time snapshot semantics.

    Gap 4 fix: ``scenario_type`` is omitted; ScenarioForm defaults to
    ScenarioType.CUSTOM (the only value in the enum). Passing
    ``"single_event"`` raises a Pydantic validation error because
    ScenarioType has only ``CUSTOM = "custom"``.
    """
    tef = (
        override.threat_event_frequency
        if override and override.threat_event_frequency
        else entry.threat_event_frequency
    )
    vuln = override.vulnerability if override and override.vulnerability else entry.vulnerability
    pl = override.primary_loss if override and override.primary_loss else entry.primary_loss
    sl = (
        override.secondary_loss
        if override and override.secondary_loss
        else getattr(entry, "secondary_loss", None)
    )
    # Gap 5: defensive .value access for StrEnum instances that might
    # be either an enum member or already a plain str.
    # industry/revenue_tier are no longer ScenarioForm fields (issue #88
    # Task 9); the service derives them from the live org row.
    base: dict[str, Any] = {
        "name": name,
        "description": None,
        "threat_category": (
            entry.threat_event_type.value
            if hasattr(entry.threat_event_type, "value")
            else entry.threat_event_type
        ),
        "threat_actor_type": (
            entry.threat_actor_type.value
            if hasattr(entry.threat_actor_type, "value")
            else entry.threat_actor_type
        ),
        "asset_class": (
            entry.asset_class.value if hasattr(entry.asset_class, "value") else entry.asset_class
        ),
        "attack_vector": None,
        "threat_event_frequency": tef,
        "vulnerability": vuln,
        "primary_loss": pl,
        "secondary_loss": sl,
        "source": ScenarioSource.LIBRARY_DERIVED.value,
        "status": "draft",
        "version": "v1",
        "library_entry_id": entry.id,
    }
    base.update(field_overrides)
    return ScenarioForm(**base)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_library_clone_hash_match_after_entry_version_bump(
    analyst_client: AsyncClient,
    db_session: AsyncSession,
    seed_organization: Organization,
    seed_user: User,
) -> None:
    """Steps:
    1. Create library entry v1
    2. Clone scenario A from v1 (no override)
    3. Compute hash A
    4. Publish v2 of same logical entry with DIFFERENT params
    5. Re-clone scenario B via resolve_for_clone(entry_id, org_id, version=1)
       AFTER v2 has been published — proves entry-version pinning works
    6. Compute hash B
    7. Assert hash_a == hash_b; both pinned to v1
    """
    # Step 1: create v1
    eid = uuid.uuid4()
    v1 = ScenarioLibraryEntry(
        id=eid,
        version=1,
        slug="repro-test",
        name="Repro test",
        status="published",
        threat_event_type=ThreatCategory.RANSOMWARE,
        threat_actor_type=ThreatActorType.CYBERCRIMINALS,
        asset_class=AssetClass.SYSTEMS,
        tags=[],
        description="d",
        canonical_fair_gap="g",
        source_citations=[],
        threat_event_frequency={"distribution": "PERT", "low": 1.0, "mode": 4.0, "high": 12.0},
        vulnerability={"distribution": "PERT", "low": 0.05, "mode": 0.20, "high": 0.50},
        primary_loss={
            "distribution": "PERT",
            "low": 100_000,
            "mode": 750_000,
            "high": 5_000_000,
        },
        suggested_control_ids=[],
    )
    db_session.add(v1)
    await db_session.commit()

    # Step 2: clone scenario A from v1
    pin_v1: dict[str, Any] = {
        "entry_id": str(eid),
        "version": 1,
        "override_id": None,
        "override_version": None,
    }
    form_a = _form_from_entry(v1, name="Scenario A (v1)")
    scenario_a = await ScenarioService(db_session).create_from_wizard(
        organization_id=seed_organization.id,
        form=form_a,
        library_pin=pin_v1,
        current_user=seed_user,
        ip_address=None,
    )
    await db_session.commit()

    hash_a = build_inputs_hash(scenario_a, [], 1000)

    # Step 4: publish v2 with DIFFERENT params
    v2 = ScenarioLibraryEntry(
        id=eid,
        version=2,
        slug="repro-test",
        name="Repro test",
        status="published",
        threat_event_type=ThreatCategory.RANSOMWARE,
        threat_actor_type=ThreatActorType.CYBERCRIMINALS,
        asset_class=AssetClass.SYSTEMS,
        tags=[],
        description="d v2",
        canonical_fair_gap="g v2",
        source_citations=[],
        threat_event_frequency={"distribution": "PERT", "low": 2.0, "mode": 8.0, "high": 24.0},
        vulnerability={"distribution": "PERT", "low": 0.10, "mode": 0.30, "high": 0.60},
        primary_loss={
            "distribution": "PERT",
            "low": 200_000,
            "mode": 1_500_000,
            "high": 10_000_000,
        },
        suggested_control_ids=[],
    )
    db_session.add(v2)
    await db_session.commit()

    # Step 5: re-clone via resolve_for_clone(entry_id, org_id, version=1)
    # AFTER v2 was published. This is the substantive test of entry-version
    # pinning — it proves the resolver returns v1 (not the newer v2) when
    # given an explicit version argument.
    svc = ScenarioLibraryService(db_session)
    resolved_v1 = await svc.resolve_for_clone(
        entry_id=eid,
        organization_id=seed_organization.id,
        version=1,
    )
    assert resolved_v1.entry.version == 1
    # The merged distributions equal v1's (no override on this entry).
    form_b = _form_from_entry(resolved_v1.entry, name="Scenario B (v1-pinned re-clone)")
    scenario_b = await ScenarioService(db_session).create_from_wizard(
        organization_id=seed_organization.id,
        form=form_b,
        library_pin=pin_v1,
        current_user=seed_user,
        ip_address=None,
    )
    await db_session.commit()

    hash_b = build_inputs_hash(scenario_b, [], 1000)

    # Step 7: assert hashes match (both pinned to v1, same params)
    assert hash_a == hash_b
    # library_pin is stamped at create time and is never None after
    # create_from_wizard with a non-null library_pin argument.
    assert scenario_a.library_pin is not None
    assert scenario_b.library_pin is not None
    assert scenario_a.library_pin["version"] == 1
    assert scenario_b.library_pin["version"] == 1


@pytest.mark.asyncio
async def test_library_pin_resolves_after_override_tombstone(
    db_session: AsyncSession,
    seed_organization: Organization,
    seed_user: User,
    seed_library_entry: Any,
) -> None:
    """Tombstone-then-resimulate reproducibility.

    1. Create override v1.
    2. Clone scenario A pinned to (entry, override v1).
    3. Compute hash_a via build_inputs_hash.
    4. Tombstone the override (set deleted_at directly — testing the pin
       resolution path, not the delete-service path).
    5. Re-resolve scenario A's calibration via library_pin.override_id
       (uses get_override_by_version, which does NOT filter deleted_at).
    6. Re-compute hash; assert hash_a == hash_b — tombstone did not break
       pin lookup, and the audit-grade reproducibility path holds.
    """
    o1 = ScenarioLibraryOverride(
        organization_id=seed_organization.id,
        library_entry_id=seed_library_entry.id,
        library_entry_version=seed_library_entry.version,
        threat_event_frequency={"distribution": "PERT", "low": 2.0, "mode": 6.0, "high": 18.0},
        reason="active v1",
        version=1,
        row_version=1,
        created_by=seed_user.id,
    )
    db_session.add(o1)
    await db_session.commit()

    pin: dict[str, Any] = {
        "entry_id": str(seed_library_entry.id),
        "version": seed_library_entry.version,
        "override_id": str(o1.id),
        "override_version": 1,
    }
    form_a = _form_from_entry(seed_library_entry, name="A (override v1 active)", override=o1)
    scenario_a = await ScenarioService(db_session).create_from_wizard(
        organization_id=seed_organization.id,
        form=form_a,
        library_pin=pin,
        current_user=seed_user,
        ip_address=None,
    )
    await db_session.commit()
    hash_a = build_inputs_hash(scenario_a, [], 1000)

    # Tombstone the override via direct mutation (intentional — this test
    # exercises pin-resolution-after-tombstone, NOT the delete-service path).
    o1.deleted_at = dt.datetime.now(dt.UTC)
    await db_session.commit()

    # Re-resolve via the pin lookup; get_override_by_version MUST return the
    # tombstoned row so audit-grade reproducibility survives.
    repo = ScenarioLibraryRepo(db_session)
    resolved_override = await repo.get_override_by_version(o1.id, 1)
    assert resolved_override is not None
    assert resolved_override.id == o1.id

    # Stamp B with the same captured snapshot (mirroring the analyst replay
    # path: form_b is a re-clone using the resolved tombstoned override).
    form_b = _form_from_entry(
        seed_library_entry,
        name="B (tombstoned override re-clone)",
        override=resolved_override,
    )
    scenario_b = await ScenarioService(db_session).create_from_wizard(
        organization_id=seed_organization.id,
        form=form_b,
        library_pin=pin,
        current_user=seed_user,
        ip_address=None,
    )
    await db_session.commit()
    hash_b = build_inputs_hash(scenario_b, [], 1000)

    assert hash_a == hash_b
