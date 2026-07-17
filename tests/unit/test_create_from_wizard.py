"""F10 — ScenarioService._stamp_new_scenario + create_from_wizard.

Five tests verifying:
1. create_from_wizard with library_pin sets library_pin on the row and
   source=LIBRARY_DERIVED.
2. create_from_wizard with library_pin=None (blank-slate) sets library_pin
   to None and source=EXPERT_JUDGMENT.
3. IDOR guard blocks cross-org create in _stamp_new_scenario.
4. Audit row is written with the real AuditWriter API + [None, value] diff shape.
5. Existing create() path still works after the _stamp_new_scenario refactor
   (regression guard).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.errors import IDORError
from idraa.models.audit_log import AuditLog
from idraa.models.enums import ScenarioSource, ThreatCategory
from idraa.schemas.scenario import ScenarioForm
from idraa.services.scenarios import ScenarioService

# ---------------------------------------------------------------------------
# Helper: minimal valid ScenarioForm
# ---------------------------------------------------------------------------

SeedOrgUser = Callable[..., Awaitable[Any]]


def _wizard_form(
    *,
    name: str = "Wizard Ransomware",
) -> ScenarioForm:
    """Minimal valid ScenarioForm for wizard tests.

    industry/revenue_tier are no longer ScenarioForm fields (issue #88
    Task 9); the service derives them from the live org row.
    """
    return ScenarioForm(
        name=name,
        threat_category=ThreatCategory.RANSOMWARE,
        threat_event_frequency={
            "distribution": "PERT",
            "low": 0.1,
            "mode": 0.5,
            "high": 2.0,
        },
        vulnerability={
            "distribution": "PERT",
            "low": 0.2,
            "mode": 0.4,
            "high": 0.6,
        },
        primary_loss={
            "distribution": "PERT",
            "low": 50_000,
            "mode": 250_000,
            "high": 2_000_000,
        },
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_create_from_wizard_with_library_sets_pin_and_source(
    db_session: AsyncSession,
    seed_org_user: SeedOrgUser,
) -> None:
    """create_from_wizard with a library_pin dict stamps the pin on the row
    and sets source=LIBRARY_DERIVED."""
    org, user = await seed_org_user(db_session)
    form = _wizard_form()

    # Insert a published library entry so the TOCTOU re-validation in
    # _stamp_new_scenario can lock and verify status="published".
    from idraa.models.enums import AssetClass, ThreatActorType
    from idraa.models.scenario_library import ScenarioLibraryEntry

    entry_id = uuid.uuid4()
    entry = ScenarioLibraryEntry(
        id=entry_id,
        version=1,
        slug="wizard-test-entry",
        name="Wizard Test Entry",
        status="published",
        threat_event_type=ThreatCategory.RANSOMWARE,
        threat_actor_type=ThreatActorType.CYBERCRIMINALS,
        asset_class=AssetClass.SYSTEMS,
        tags=[],
        description="For wizard F10 test.",
        canonical_fair_gap="Test gap.",
        source_citations=[],
        threat_event_frequency={"distribution": "PERT", "low": 0.1, "mode": 0.5, "high": 2.0},
        vulnerability={"distribution": "PERT", "low": 0.2, "mode": 0.4, "high": 0.6},
        primary_loss={"distribution": "PERT", "low": 50_000, "mode": 250_000, "high": 2_000_000},
        suggested_control_ids=[],
    )
    db_session.add(entry)
    await db_session.flush()

    library_pin: dict[str, Any] = {
        "entry_id": str(entry_id),
        "version": 1,
        "override_id": None,
        "override_version": None,
    }

    svc = ScenarioService(db_session)
    scenario = await svc.create_from_wizard(
        organization_id=org.id,
        form=form,
        library_pin=library_pin,
        current_user=user,
        ip_address="10.0.0.1",
    )

    assert scenario.library_pin == library_pin
    assert scenario.source == ScenarioSource.LIBRARY_DERIVED


async def test_create_from_wizard_blank_path_no_pin(
    db_session: AsyncSession,
    seed_org_user: SeedOrgUser,
) -> None:
    """create_from_wizard with library_pin=None leaves library_pin NULL
    and sets source=EXPERT_JUDGMENT."""
    org, user = await seed_org_user(db_session)
    form = _wizard_form()

    svc = ScenarioService(db_session)
    scenario = await svc.create_from_wizard(
        organization_id=org.id,
        form=form,
        library_pin=None,
        current_user=user,
    )

    assert scenario.library_pin is None
    assert scenario.source == ScenarioSource.EXPERT_JUDGMENT


async def test_create_from_wizard_idor_blocks_cross_org(
    db_session: AsyncSession,
    seed_org_user: SeedOrgUser,
) -> None:
    """_stamp_new_scenario raises IDORError when user.organization_id
    does not match the target organization_id (cross-org create blocked)."""
    _org_a, user_a = await seed_org_user(db_session, org_name="OrgA", email="a@example.com")
    org_b, _user_b = await seed_org_user(db_session, org_name="OrgB", email="b@example.com")
    form = _wizard_form()

    svc = ScenarioService(db_session)
    with pytest.raises(IDORError):
        # user_a belongs to org_a but we pass org_b.id as the target.
        await svc.create_from_wizard(
            organization_id=org_b.id,
            form=form,
            library_pin=None,
            current_user=user_a,
        )


async def test_create_from_wizard_writes_audit(
    db_session: AsyncSession,
    seed_org_user: SeedOrgUser,
) -> None:
    """_stamp_new_scenario writes an audit row with action='scenario.create'
    using the real AuditWriter API and [None, value] diff shape."""
    org, user = await seed_org_user(db_session)
    form = _wizard_form()

    svc = ScenarioService(db_session)
    scenario = await svc.create_from_wizard(
        organization_id=org.id,
        form=form,
        library_pin=None,
        current_user=user,
        ip_address="192.168.1.1",
    )

    audit = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.entity_type == "scenario",
                AuditLog.entity_id == scenario.id,
                AuditLog.action == "scenario.create",
            )
        )
    ).scalar_one()

    assert audit.organization_id == org.id
    assert audit.user_id == user.id
    assert audit.ip_address == "192.168.1.1"
    # [None, value] diff shape on every tracked field.
    assert audit.changes["name"] == [None, form.name]
    assert audit.changes["source"] == [None, ScenarioSource.EXPERT_JUDGMENT.value]
    assert audit.changes["row_version"] == [None, 1]
    assert audit.changes["library_pin"] == [None, None]


async def test_stamp_new_scenario_called_by_existing_create_path_too(
    db_session: AsyncSession,
    seed_org_user: SeedOrgUser,
) -> None:
    """Regression guard: ScenarioService.create() still works after the
    _stamp_new_scenario refactor; both paths produce equivalent rows."""
    org, user = await seed_org_user(db_session)
    form = _wizard_form(name="Expert Form Scenario")

    svc = ScenarioService(db_session)
    scenario = await svc.create(
        organization_id=org.id,
        form=form,
        current_user=user,
        ip_address="10.0.0.99",
    )

    assert scenario.name == "Expert Form Scenario"
    assert scenario.organization_id == org.id
    # library_pin is None on expert-form path when no library_entry_id supplied.
    assert scenario.library_pin is None
    # source defaults to EXPERT_JUDGMENT via form field.
    assert scenario.source == ScenarioSource.EXPERT_JUDGMENT
    # row_version starts at 1.
    assert scenario.row_version == 1

    # Verify audit was emitted.
    audit = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.entity_type == "scenario",
                AuditLog.entity_id == scenario.id,
            )
        )
    ).scalar_one()
    assert audit.action == "scenario.create"
    assert audit.ip_address == "10.0.0.99"
