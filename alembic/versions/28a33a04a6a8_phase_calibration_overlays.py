"""phase_calibration_overlays

Revision ID: 28a33a04a6a8
Revises: 355450b21719
Create Date: 2026-04-25 16:38:10.283920

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "28a33a04a6a8"
down_revision: str | None = "355450b21719"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "overlay_definitions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("organization_id", sa.Uuid(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("tag", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("frequency_multiplier", sa.Float, nullable=False),
        sa.Column("magnitude_multiplier", sa.Float, nullable=False),
        sa.Column("sources", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("methodology", sa.Text, nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("organization_id", "tag", name="uq_overlay_per_org_tag"),
        sa.CheckConstraint("length(trim(methodology)) >= 20",
                           name="ck_overlay_methodology_required"),
        sa.CheckConstraint("length(tag) > 0", name="ck_overlay_tag_required"),
        sa.CheckConstraint("frequency_multiplier > 0",
                           name="ck_overlay_frequency_positive"),
        sa.CheckConstraint("magnitude_multiplier > 0",
                           name="ck_overlay_magnitude_positive"),
    )
    op.create_index(
        "ix_overlay_definitions_organization_id",
        "overlay_definitions", ["organization_id"],
    )

    op.create_table(
        "overlay_definition_revisions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("overlay_definition_id", sa.Uuid(as_uuid=True),
                  sa.ForeignKey("overlay_definitions.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("tag", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("frequency_multiplier", sa.Float, nullable=False),
        sa.Column("magnitude_multiplier", sa.Float, nullable=False),
        sa.Column("sources", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("methodology", sa.Text, nullable=False),
        sa.Column("methodology_change_reason", sa.Text, nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.UniqueConstraint("overlay_definition_id", "version",
                            name="uq_overlay_revision"),
    )
    op.create_index(
        "ix_overlay_definition_revisions_overlay_definition_id",
        "overlay_definition_revisions", ["overlay_definition_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_overlay_definition_revisions_overlay_definition_id",
                  table_name="overlay_definition_revisions")
    op.drop_table("overlay_definition_revisions")
    op.drop_index("ix_overlay_definitions_organization_id",
                  table_name="overlay_definitions")
    op.drop_table("overlay_definitions")
