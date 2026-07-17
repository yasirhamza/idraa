"""Model smoke tests — round-trip + column presence for Org / User / Session / AuditLog.

Covers Task 1.1.2: the four domain tables that underpin auth + audit. Each
test exercises the full ORM → SQLite round-trip so the mixin-derived columns
(id, timestamps, organization_id) are known to persist on the cross-dialect
``Uuid(as_uuid=True)`` column type in addition to the rows these tests write.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.audit_log import AuditLog
from idraa.models.enums import IndustryType, OrganizationSize, UserRole
from idraa.models.organization import Organization
from idraa.models.session import AuthSession
from idraa.models.user import User


async def test_organization_insert(db_session: AsyncSession) -> None:
    org = Organization(
        name="Acme",
        industry_type=IndustryType.MANUFACTURING,
        organization_size=OrganizationSize.MEDIUM,
    )
    db_session.add(org)
    await db_session.commit()

    fetched = (await db_session.execute(select(Organization))).scalar_one()
    assert fetched.name == "Acme"
    assert fetched.industry_type is IndustryType.MANUFACTURING
    assert fetched.organization_size is OrganizationSize.MEDIUM
    # Mixin-populated defaults must be present in-memory AND after round-trip.
    assert isinstance(fetched.id, uuid.UUID)
    assert isinstance(fetched.created_at, datetime)
    # JSON-backed list columns default to [] server-side.
    assert fetched.geographic_regions == []
    assert fetched.compliance_requirements == []


async def test_user_requires_org(db_session: AsyncSession) -> None:
    org = Organization(
        name="Acme",
        industry_type=IndustryType.INFORMATION,
        organization_size=OrganizationSize.SMALL,
    )
    db_session.add(org)
    await db_session.flush()

    user = User(
        organization_id=org.id,
        email="a@b.c",
        password_hash="x",
        full_name="A",
        role=UserRole.ADMIN,
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()

    fetched = (await db_session.execute(select(User))).scalar_one()
    assert fetched.email == "a@b.c"
    assert fetched.role is UserRole.ADMIN
    assert fetched.organization_id == org.id


async def test_session_linked_to_user(db_session: AsyncSession) -> None:
    org = Organization(
        name="Acme",
        industry_type=IndustryType.INFORMATION,
        organization_size=OrganizationSize.SMALL,
    )
    db_session.add(org)
    await db_session.flush()

    user = User(
        organization_id=org.id,
        email="a@b.c",
        password_hash="x",
        full_name="A",
        role=UserRole.ADMIN,
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()

    sess = AuthSession(
        id=uuid.uuid4(),
        user_id=user.id,
        expires_at=datetime(2099, 1, 1, tzinfo=UTC),
    )
    db_session.add(sess)
    await db_session.commit()

    fetched = (await db_session.execute(select(AuthSession))).scalar_one()
    assert fetched.user_id == user.id
    assert fetched.expires_at.year == 2099


async def test_audit_log_insert(db_session: AsyncSession) -> None:
    org = Organization(
        name="Acme",
        industry_type=IndustryType.INFORMATION,
        organization_size=OrganizationSize.SMALL,
    )
    db_session.add(org)
    await db_session.flush()

    log = AuditLog(
        organization_id=org.id,
        entity_type="organization",
        entity_id=org.id,
        action="create",
        changes={"name": ["", "Acme"]},
    )
    db_session.add(log)
    await db_session.commit()

    fetched = (await db_session.execute(select(AuditLog))).scalar_one()
    assert fetched.action == "create"
    assert fetched.changes == {"name": ["", "Acme"]}
    assert fetched.organization_id == org.id
