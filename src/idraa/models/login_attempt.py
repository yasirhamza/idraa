"""LoginAttempt — per-source login throttle store (idraa#81).

Keyed on "<surface>:<normalized-ip>" (surface = login | stepup; IPv6 -> /64),
pre-auth. Deliberately NO organization_id: like AuthSession + the MFA tables it
is an auth-layer table scoped by connection identity, not an org-scoped business
entity. One row per source_key; the reaper purges inactive rows.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from idraa.db import Base
from idraa.models.mixins import IdMixin, TimestampMixin


class LoginAttempt(IdMixin, TimestampMixin, Base):
    __tablename__ = "login_attempt"

    # unique implies an index — do NOT also pass index=True (redundant 2nd index).
    source_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
