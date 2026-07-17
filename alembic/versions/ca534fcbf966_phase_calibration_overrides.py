"""phase_calibration_overrides

Revision ID: ca534fcbf966
Revises: ecfff791f225
Create Date: 2026-04-26 07:16:09.848430

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ca534fcbf966"
down_revision: str | None = "ecfff791f225"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "calibration_overrides",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("organization_id", sa.Uuid(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("industry", sa.String(64), nullable=False),
        sa.Column("revenue_tier", sa.String(32), nullable=False),
        sa.Column("frequency_multiplier", sa.Float, nullable=False,
                  server_default="1.0"),
        sa.Column("magnitude_multiplier", sa.Float, nullable=False,
                  server_default="1.0"),
        sa.Column("sources", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("methodology", sa.Text, nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("organization_id", "industry", "revenue_tier",
                            name="uq_override_per_org_industry_tier"),
        # Tightened to >= 20 to match D1 model + CalibrationOverrideForm Pydantic
        # validator — see plan preamble fold-in B5 at
        # docs/superpowers/plans/2026-04-25-calibration-data-framework.md.
        # length() is cross-DB (SQLite + Postgres); char_length is Postgres-only.
        sa.CheckConstraint("length(trim(methodology)) >= 20",
                           name="ck_override_methodology_required"),
        sa.CheckConstraint("frequency_multiplier > 0",
                           name="ck_override_frequency_positive"),
        sa.CheckConstraint("magnitude_multiplier > 0",
                           name="ck_override_magnitude_positive"),
    )
    op.create_index(
        "ix_calibration_overrides_organization_id",
        "calibration_overrides", ["organization_id"],
    )

    op.create_table(
        "calibration_override_revisions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("calibration_override_id", sa.Uuid(as_uuid=True),
                  sa.ForeignKey("calibration_overrides.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("industry", sa.String(64), nullable=False),
        sa.Column("revenue_tier", sa.String(32), nullable=False),
        sa.Column("frequency_multiplier", sa.Float, nullable=False),
        sa.Column("magnitude_multiplier", sa.Float, nullable=False),
        sa.Column("sources", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("methodology", sa.Text, nullable=False),
        sa.Column("methodology_change_reason", sa.Text, nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.UniqueConstraint("calibration_override_id", "version",
                            name="uq_override_revision"),
    )
    op.create_index(
        "ix_calibration_override_revisions_calibration_override_id",
        "calibration_override_revisions", ["calibration_override_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_calibration_override_revisions_calibration_override_id",
                  table_name="calibration_override_revisions")
    op.drop_table("calibration_override_revisions")
    op.drop_index("ix_calibration_overrides_organization_id",
                  table_name="calibration_overrides")
    op.drop_table("calibration_overrides")
