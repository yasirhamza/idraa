"""AuthSession ORM — DB-backed session, id is the (signed) cookie value.

Called ``AuthSession`` not ``Session`` because ``Session`` collides with
SQLAlchemy's own session class at every import site. The table name
``auth_sessions`` is likewise distinct from SQLAlchemy's internals.

FK to ``users`` is ``ondelete="CASCADE"``: when a user is hard-deleted
the session rows should go with them, since without a valid user a
session cannot be authenticated anyway. ``token_hash`` isn't stored as a
separate column — the primary key IS the (signed) cookie value, which
keeps the lookup path to a single indexed PK read.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from idraa.db import Base
from idraa.models._types import now_utc


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, nullable=False
    )
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
