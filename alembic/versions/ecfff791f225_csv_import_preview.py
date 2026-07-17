"""csv_import_preview — shared two-step CSV upload staging table

Revision ID: ecfff791f225
Revises: d8e4e0b19bac
Create Date: 2026-04-25 18:00:00.000000

Per plan §C6 + B13: bulk CSV imports are split into ``validate_csv``
(persists raw bytes here under a token + 10 min TTL) and
``apply_validated_preview`` (reads, re-parses, upserts, deletes the row).
The table is **shared** across import flows — ``entity_type`` discriminates
overlay imports (C6) from calibration-override imports (PR δ) without a
schema migration.

CHECK constraints use ``length(...)`` rather than ``char_length(...)`` so
they're cross-DB (SQLite + Postgres). The ``expires_at > created_at`` CHECK
catches non-positive TTLs that would otherwise let a stale or
misconfigured row sit in the table forever.

Round-trippable: ``downgrade()`` drops the table + index in one shot —
SQLite drops the index implicitly with the table but we drop explicitly
so Postgres deploys behave the same way.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ecfff791f225"
down_revision: str | None = "d8e4e0b19bac"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "csv_import_preview",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "organization_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("csv_bytes", sa.LargeBinary, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(entity_type) > 0",
            name="ck_csv_import_preview_entity_type_required",
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="ck_csv_import_preview_expiry_after_creation",
        ),
    )
    op.create_index(
        "ix_csv_import_preview_organization_id",
        "csv_import_preview",
        ["organization_id"],
    )
    op.create_index(
        "ix_csv_import_preview_expires_at",
        "csv_import_preview",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_csv_import_preview_expires_at",
        table_name="csv_import_preview",
    )
    op.drop_index(
        "ix_csv_import_preview_organization_id",
        table_name="csv_import_preview",
    )
    op.drop_table("csv_import_preview")
