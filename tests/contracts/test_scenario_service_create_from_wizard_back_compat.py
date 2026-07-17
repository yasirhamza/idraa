"""T5 / Arch-21 R2: default-None ``per_fieldset_pooling_summary`` kwarg
preserves legacy callers of ``ScenarioService.create_from_wizard``.

Also verifies Arch-17 PR2: the ``actor=`` alias accepts a User in the
same slot as ``current_user=``.

Iteration-contract style (per CLAUDE.md > Data contract enforcement):
the back-compat surface is exactly the kwarg set legacy callers rely on,
plus the new opt-in ``per_fieldset_pooling_summary`` kwarg, plus the
new ``actor=`` alias. Every legacy kwarg shape MUST still work; the new
kwarg MUST land in the audit ``changes`` dict when supplied.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from idraa.db import Base
from idraa.models.audit_log import AuditLog
from idraa.models.enums import ScenarioSource, ThreatCategory, UserRole
from idraa.models.organization import Organization
from idraa.models.user import User
from idraa.schemas.scenario import ScenarioForm
from idraa.services.scenarios import ScenarioService
from tests.factories import create_org, create_user


@pytest_asyncio.fixture
async def db() -> AsyncGenerator[AsyncSession, None]:
    """In-memory SQLite session for back-compat assertions.

    Local to this file because tests/contracts/conftest.py is reserved
    for syrupy snapshot extensions; we don't want to entangle the two.
    """
    from idraa.db import strict_json_dumps

    # json_serializer mirrors get_engine() (#327): this fixture writes
    # Scenario distribution + AuditLog JSON columns — non-finite floats must
    # fail at flush exactly as they do in prod.
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", echo=False, json_serializer=strict_json_dumps
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def org_id(db: AsyncSession) -> UUID:
    org = await create_org(db, name="T5 Back-Compat Test Org")
    return org.id


@pytest_asyncio.fixture
async def actor(db: AsyncSession, org_id: UUID) -> User:
    org = await db.get(Organization, org_id)
    assert org is not None
    return await create_user(
        db,
        org,
        email="t5-actor@test.local",
        role=UserRole.ADMIN,
    )


@pytest.fixture
def scenario_form() -> ScenarioForm:
    """Minimal valid ScenarioForm for back-compat assertions.

    Mirrors tests/unit/test_create_from_wizard.py:_wizard_form so the
    back-compat surface here matches what existing wizard tests rely on.
    """
    return ScenarioForm(
        name="T5 BackCompat Scenario",
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


async def test_legacy_call_without_summary_works(
    db: AsyncSession, actor: User, org_id: UUID, scenario_form: ScenarioForm
) -> None:
    """Arch-21 R2: pre-T5 callers (no ``per_fieldset_pooling_summary``)
    continue to work unchanged. Uses ``current_user=`` (the existing
    kwarg name) for byte-for-byte parity with tests/unit/test_create_from_wizard.py."""
    svc = ScenarioService(db)
    scenario = await svc.create_from_wizard(
        organization_id=org_id,
        form=scenario_form,
        library_pin=None,
        current_user=actor,
    )
    assert scenario is not None
    assert scenario.source == ScenarioSource.EXPERT_JUDGMENT
    assert scenario.organization_id == org_id


async def test_actor_alias_accepted(
    db: AsyncSession, actor: User, org_id: UUID, scenario_form: ScenarioForm
) -> None:
    """Arch-17 PR2: T11's finalize handler idiomatically uses
    ``actor=user``; this test pins that the alias is honored."""
    svc = ScenarioService(db)
    scenario = await svc.create_from_wizard(
        organization_id=org_id,
        form=scenario_form,
        library_pin=None,
        actor=actor,  # alias instead of current_user
    )
    assert scenario is not None
    assert scenario.organization_id == org_id


async def test_missing_user_raises(
    db: AsyncSession, org_id: UUID, scenario_form: ScenarioForm
) -> None:
    """Defensive: neither current_user nor actor supplied -> ValueError."""
    svc = ScenarioService(db)
    with pytest.raises(ValueError, match="current_user"):
        await svc.create_from_wizard(
            organization_id=org_id,
            form=scenario_form,
            library_pin=None,
        )


async def test_call_with_summary_includes_in_audit(
    db: AsyncSession, actor: User, org_id: UUID, scenario_form: ScenarioForm
) -> None:
    """The new ``per_fieldset_pooling_summary`` kwarg lands in the
    ``scenario.create`` audit row's ``changes`` dict for forensic
    reproducibility of the evaluator-style finalize path."""
    svc = ScenarioService(db)
    summary = {
        "tef": {
            "n_smes": 3,
            "pooled_meanlog": 5.2,
            "pooled_sdlog": 1.8,
        }
    }
    scenario = await svc.create_from_wizard(
        organization_id=org_id,
        form=scenario_form,
        library_pin=None,
        actor=actor,
        per_fieldset_pooling_summary=summary,
    )
    audit = (
        await db.execute(
            select(AuditLog).where(
                AuditLog.entity_type == "scenario",
                AuditLog.entity_id == scenario.id,
                AuditLog.action == "scenario.create",
            )
        )
    ).scalar_one()
    assert audit.changes["per_fieldset_pooling_summary"] == [None, summary]


async def test_legacy_call_omits_summary_from_audit(
    db: AsyncSession, actor: User, org_id: UUID, scenario_form: ScenarioForm
) -> None:
    """Negative-space: legacy callers (no kwarg) MUST NOT see
    per_fieldset_pooling_summary in the audit changes dict -- the key
    only appears when the wizard finalize path explicitly supplies it."""
    svc = ScenarioService(db)
    scenario = await svc.create_from_wizard(
        organization_id=org_id,
        form=scenario_form,
        library_pin=None,
        current_user=actor,
    )
    audit = (
        await db.execute(
            select(AuditLog).where(
                AuditLog.entity_type == "scenario",
                AuditLog.entity_id == scenario.id,
                AuditLog.action == "scenario.create",
            )
        )
    ).scalar_one()
    assert "per_fieldset_pooling_summary" not in audit.changes
