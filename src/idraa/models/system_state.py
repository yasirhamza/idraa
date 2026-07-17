"""Single-row-per-org operational scheduler state (#297). Phase-1 cursor:
last_retention_sweep_at, target of the atomic-throttle conditional UPDATE.
UNIQUE org_id so the self-seeding upsert in the retention trigger is atomic.
"""

from __future__ import annotations

import datetime

from sqlalchemy import DateTime, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from idraa.db import Base
from idraa.models.mixins import IdMixin, OrgMixin


class SystemState(IdMixin, OrgMixin, Base):
    __tablename__ = "system_state"
    __table_args__ = (UniqueConstraint("organization_id", name="uq_system_state_org"),)

    last_retention_sweep_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
