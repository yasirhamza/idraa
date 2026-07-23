"""Per-org admin-settable security policy overrides (NULL = follow env default).

One row per org, upserted. Consumed via the cache-backed
services/security_settings.py resolver (which caches a primitive SNAPSHOT, not
this ORM instance) — do NOT read these columns directly elsewhere.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from idraa.db import Base
from idraa.models.mixins import IdMixin, OrgMixin, TimestampMixin


class SecuritySettings(IdMixin, OrgMixin, TimestampMixin, Base):
    __tablename__ = "security_settings"
    __table_args__ = (UniqueConstraint("organization_id", name="uq_security_settings_org"),)

    mfa_policy: Mapped[str | None] = mapped_column(String(16), nullable=True)
    step_up_window_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    step_up_exports: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    step_up_destructive: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    step_up_admin: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    step_up_credentials: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
