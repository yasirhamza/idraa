"""Overlay master-data ORM — admin-editable cross-cutting risk multipliers."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from idraa.db import Base
from idraa.models._methodology import METHODOLOGY_MIN_LENGTH
from idraa.models.mixins import IdMixin, OrgMixin, TimestampMixin


class OverlayDefinition(IdMixin, TimestampMixin, OrgMixin, Base):
    """Live overlay row. Admin edits create new revisions and bump version."""

    __tablename__ = "overlay_definitions"

    tag: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    frequency_multiplier: Mapped[float] = mapped_column(Float, nullable=False)
    magnitude_multiplier: Mapped[float] = mapped_column(Float, nullable=False)

    sources: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    methodology: Mapped[str] = mapped_column(Text, nullable=False)

    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    def __init__(self, **kwargs: Any) -> None:
        # SQLAlchemy Python-side ``default=`` only fires at flush. Mirror the
        # ``version``/``is_active``/``sources`` defaults into ``__init__`` kwargs
        # so freshly constructed in-memory instances carry the expected state
        # before they hit the DB — same pattern as IdMixin/TimestampMixin.
        kwargs.setdefault("version", 1)
        kwargs.setdefault("is_active", True)
        kwargs.setdefault("sources", [])
        super().__init__(**kwargs)

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "tag",
            name="uq_overlay_per_org_tag",
        ),
        CheckConstraint(
            # length() is cross-DB (SQLite + Postgres); char_length is Postgres-only.
            # Tightened to >= 20 to match the OverlayForm Pydantic validator —
            # eliminates form-bypass via raw SQL.
            f"length(trim(methodology)) >= {METHODOLOGY_MIN_LENGTH}",
            name="ck_overlay_methodology_required",
        ),
        CheckConstraint(
            "length(tag) > 0",
            name="ck_overlay_tag_required",
        ),
        CheckConstraint(
            "frequency_multiplier > 0",
            name="ck_overlay_frequency_positive",
        ),
        CheckConstraint(
            "magnitude_multiplier > 0",
            name="ck_overlay_magnitude_positive",
        ),
    )


class OverlayDefinitionRevision(IdMixin, TimestampMixin, Base):
    """Append-only revision history. Scenarios pin to specific revisions."""

    __tablename__ = "overlay_definition_revisions"

    overlay_definition_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("overlay_definitions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    # snapshot of overlay state at this version
    tag: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    frequency_multiplier: Mapped[float] = mapped_column(Float, nullable=False)
    magnitude_multiplier: Mapped[float] = mapped_column(Float, nullable=False)
    sources: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    methodology: Mapped[str] = mapped_column(Text, nullable=False)

    # required on every edit — the "why are you changing this?" rationale
    methodology_change_reason: Mapped[str] = mapped_column(Text, nullable=False)

    # who made the change (FK to users; nullable for system seeds)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "overlay_definition_id",
            "version",
            name="uq_overlay_revision",
        ),
    )

    def __init__(self, **kwargs: Any) -> None:
        # Mirror ``sources`` default into __init__ kwargs (SA flush-time defaults
        # don't materialise at construction) — same pattern as OverlayDefinition.
        kwargs.setdefault("sources", [])
        super().__init__(**kwargs)
