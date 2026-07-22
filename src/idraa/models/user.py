"""User ORM — email unique within org.

Email is stored as a plain ``String`` column with an ``(organization_id,
email)`` composite unique constraint. Case-insensitive uniqueness is
enforced at the service layer (normalize to lowercase before insert /
lookup); a functional unique index on ``LOWER(email)`` would require a
SQLite extension and lock us out of the cross-dialect story the whole
schema is built around.

The organization FK comes from ``OrgMixin`` and uses
``ondelete="RESTRICT"``: deleting an org with active users is an
administrative action that should fail loudly rather than silently
cascade through auth data.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Enum, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from idraa.db import Base
from idraa.models.enums import UserRole
from idraa.models.mixins import IdMixin, OrgMixin, TimestampMixin


class User(IdMixin, TimestampMixin, OrgMixin, Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("organization_id", "email", name="uq_users_org_email"),)

    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, native_enum=False, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    mfa_enrolled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Minimal login throttle (idraa#81 slice, plan-gate B1).
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
