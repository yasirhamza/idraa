"""Service-layer test fixtures.

Self-contained: an in-memory SQLite AsyncEngine session plus pre-seeded
organization + actor user. Tests that exercise the service layer end-to-end
(no HTTP client, no auth dance) consume `db`, `org_id`, `actor_id`.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from uuid import UUID

import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from idraa.db import Base
from idraa.models.enums import UserRole
from tests.factories import create_org, create_user


@pytest_asyncio.fixture
async def db() -> AsyncGenerator[AsyncSession, None]:
    """In-memory SQLite session with all tables created."""
    from idraa.db import strict_json_dumps

    # json_serializer mirrors get_engine() (#327): non-finite floats must
    # fail at flush in tests exactly as they do in prod.
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
    """Seed a fresh Organization row and return its id."""
    org = await create_org(db, name="SME Directory Test Org")
    return org.id


@pytest_asyncio.fixture
async def actor_id(db: AsyncSession, org_id: UUID) -> UUID:
    """Seed an ADMIN user inside the test org and return its id."""
    # `actor_id` mirrors the user_id threaded through service kwargs; the
    # SME directory's `created_by` / `archived_by` FK both point at users.id
    # so we materialise a real User row to satisfy referential integrity.
    from idraa.models.organization import Organization

    org = await db.get(Organization, org_id)
    assert org is not None
    user = await create_user(
        db,
        org,
        email="sme-actor@test.local",
        role=UserRole.ADMIN,
    )
    return user.id
