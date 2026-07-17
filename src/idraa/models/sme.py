"""Per-org SME directory. See spec §6.1."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import CheckConstraint, ForeignKey, Index, text
from sqlalchemy.orm import Mapped, mapped_column

from idraa.db import Base
from idraa.models.mixins import IdMixin, OrgMixin, TimestampMixin


class SubjectMatterExpert(Base, IdMixin, TimestampMixin, OrgMixin):
    __tablename__ = "subject_matter_experts"

    name: Mapped[str] = mapped_column(sa.String(200))
    email: Mapped[str | None] = mapped_column(sa.String(320))
    # Arch-14 R2 + Arch-24 R3: DB-COMPUTED lowercase via sa.text();
    # avoids the sa.column() unbound-reference foot-gun.
    email_lower: Mapped[str | None] = mapped_column(
        sa.String(320),
        sa.Computed(sa.text("lower(email)"), persisted=True),
    )
    role_title: Mapped[str | None] = mapped_column(sa.String(200))
    notes: Mapped[str | None] = mapped_column(sa.Text)
    archived_at: Mapped[datetime | None]
    archived_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    is_system_owned: Mapped[bool] = mapped_column(default=False)
    # 4 valid values: admin | analyst_request | analyst_request_rejected | system
    created_via: Mapped[str] = mapped_column(sa.String(40), default="admin")
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))

    __table_args__ = (
        Index(
            "ux_sme_org_email_live",
            "organization_id",
            "email_lower",
            unique=True,
            sqlite_where=text(
                "email_lower IS NOT NULL AND archived_at IS NULL AND is_system_owned = 0"
            ),
            postgresql_where=text(
                "email_lower IS NOT NULL AND archived_at IS NULL AND is_system_owned = FALSE"
            ),
        ),
        # #wizard-library-prefill (2026-07-07): widened from (organization_id)
        # to (organization_id, name) so an org can hold both the "Industry
        # baseline" (IRIS) and "Library reference" system SMEs. Still one system
        # SME per (org, name) — no unbounded duplication.
        Index(
            "ux_sme_org_system_owned",
            "organization_id",
            "name",
            unique=True,
            sqlite_where=text("is_system_owned = 1"),
            postgresql_where=text("is_system_owned = TRUE"),
        ),
        Index(
            "ix_sme_org_name",
            "organization_id",
            "name",
            sqlite_where=text("archived_at IS NULL"),
            postgresql_where=text("archived_at IS NULL"),
        ),
        CheckConstraint(
            "(is_system_owned = TRUE AND created_by IS NULL) OR "
            "(is_system_owned = FALSE AND created_by IS NOT NULL)",
            name="ck_sme_system_or_user_owned",
        ),
        CheckConstraint(
            "created_via IN ('admin', 'analyst_request', 'analyst_request_rejected', 'system')",
            name="ck_sme_created_via_enum",
        ),
    )
