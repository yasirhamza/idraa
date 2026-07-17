"""Test-data factories."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.enums import IndustryType, OrganizationSize, UserRole
from idraa.models.organization import Organization
from idraa.models.user import User
from idraa.services.auth import create_session, hash_password


async def create_org(
    db: AsyncSession,
    name: str = "Test Org",
    industry: IndustryType = IndustryType.MANUFACTURING,
    size: OrganizationSize = OrganizationSize.MEDIUM,
) -> Organization:
    org = Organization(name=name, industry_type=industry, organization_size=size)
    db.add(org)
    await db.flush()
    return org


async def create_user(
    db: AsyncSession,
    org: Organization,
    *,
    email: str = "user@test.local",
    role: UserRole = UserRole.ADMIN,
    password: str = "pw-12345678",
) -> User:
    # Normalize with .lower().strip() to match the 1.1.2/1.1.3/1.1.5 invariant
    # that every email write normalizes on both sides. Without .strip(), a call
    # like create_user(..., email="a@b.c ") would seed a row that can never log
    # in, because load_user_by_email strips on lookup.
    user = User(
        organization_id=org.id,
        email=email.lower().strip(),
        password_hash=hash_password(password),
        full_name=email.split("@")[0],
        role=role,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    return user


async def login_client_as(db: AsyncSession, user: User) -> str:
    """Create a session for user and return the signed cookie value to set on a client."""
    from idraa.services.auth import sign_session_id

    sess = await create_session(db, user.id, ip="127.0.0.1")
    await db.commit()
    return sign_session_id(sess.id)
