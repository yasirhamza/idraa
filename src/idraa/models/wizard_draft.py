"""WizardDraft — DB-backed wizard state per (user_id, tx_id).

Spec: Phase 1.5a paranoid-review Decision A — DB-backed storage replaces
the dict-on-session approach (which was incompatible with v3's middleware
shape and CLAUDE.md's "single source of truth: the DB" principle).

Lifecycle:
- ``get_or_create``: fetch by (user_id, tx_id) or create with empty state_json.
- ``advance_step``: upsert state_json + bump updated_at.
- ``clear``: delete the row.
- ``cleanup_expired``: ``DELETE WHERE updated_at < now - max_age_minutes``
  (caller-supplied; ``Settings.wizard_draft_ttl_days`` in production, swept
  by ``services.run_reaper.sweep_wizard_drafts``).

PK is composite ``(user_id, tx_id)``. ``state_json`` is the WizardState
dataclass serialised via ``asdict``; F17 owns the dataclass + service.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy import JSON, ForeignKey, PrimaryKeyConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from idraa.db import Base
from idraa.models._types import UtcDateTime, now_utc


class WizardDraft(Base):
    __tablename__ = "wizard_drafts"
    __table_args__ = (PrimaryKeyConstraint("user_id", "tx_id", name="pk_wizard_drafts"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    tx_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    state_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    version_token: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    # Use UtcDateTime (the project's strict TypeDecorator) over the
    # DateTime(timezone=True) pattern used by TimestampMixin: WizardDraft is a
    # new table whose updated_at drives cleanup_expired's TTL query — naive-
    # datetime drift would silently misalign the cutoff. TimestampMixin's
    # pattern can be migrated to UtcDateTime in a future cleanup PR.
    updated_at: Mapped[datetime.datetime] = mapped_column(
        UtcDateTime,
        default=now_utc,
        onupdate=now_utc,
        server_default=func.now(),
        nullable=False,
    )
