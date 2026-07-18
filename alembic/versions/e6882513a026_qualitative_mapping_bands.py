"""qualitative mapping bands

Revision ID: e6882513a026
Revises: b7d3f1a9c4e2
Create Date: 2026-07-18

Epic #34 P1b: the layered qualitative-mapping band schema for the register
converter — ``qualitative_mapping_bands`` (canonical, org-less, seeded below)
and ``qualitative_mapping_org_bands`` (per-org override layer, admin CRUD via
the service layer, empty at migration time). Also adds
``scenarios.conversion_metadata`` (nullable JSON) — the converter's per-row
provenance record, NULL for every scenario not created via the converter.

Seed phase: loads ``data/seed_qualitative_bands.json`` (10 rows: 5 frequency +
5 magnitude bands, values pinned to spec §2.2 / O-RA 2.0.1 Table 1 §6.6 p.33)
and validates every row through ``BandSeed`` before insert — mirrors
``c1d2e3f4a5b6_seed_library_entries.py``. Only the ``BandSeed`` Pydantic class
is imported from ``services/seed_qualitative_bands_loader.py``; the JSON load,
path anchor, and INSERT loop are inline here (not delegated to a loader
function) so a later refactor of that module cannot silently break
``alembic upgrade`` on a fresh database. UUIDs are ``uuid.uuid4().hex``
(32-char, no hyphens) bound via ``sa.text`` params — matches the ORM's
``Uuid(as_uuid=True)`` on-disk format (the #303 raw-UUID foot-gun).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e6882513a026"
down_revision: str | Sequence[str] | None = "b7d3f1a9c4e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "qualitative_mapping_bands",
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=False),
        sa.Column("low", sa.Float(), nullable=False),
        sa.Column("mode", sa.Float(), nullable=False),
        sa.Column("high", sa.Float(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("derivation", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind", "label", "version", name="uq_qual_band_kind_label_version"),
    )
    op.create_table(
        "qualitative_mapping_org_bands",
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=False),
        sa.Column("low", sa.Float(), nullable=False),
        sa.Column("mode", sa.Float(), nullable=False),
        sa.Column("high", sa.Float(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("row_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_qualitative_mapping_org_bands_organization_id"),
        "qualitative_mapping_org_bands",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ux_qual_org_band_org_kind_label",
        "qualitative_mapping_org_bands",
        ["organization_id", "kind", "label"],
        unique=True,
        sqlite_where=sa.text("deleted_at IS NULL"),
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.add_column("scenarios", sa.Column("conversion_metadata", sa.JSON(), nullable=True))

    # --- Seed phase: canonical bands ---------------------------------------
    # Paranoid-review precedent (c1d2e3f4a5b6): resolve the seed JSON via an
    # explicit project-root anchor, not a fragile Path(__file__).parent chain.
    import idraa

    project_root = Path(idraa.__file__).resolve().parent.parent.parent
    seed_path = project_root / "data" / "seed_qualitative_bands.json"
    if not seed_path.exists():
        # Fallback for non-standard layouts (CI artefacts, packaged distros).
        seed_path = Path(__file__).resolve().parent.parent.parent / "data" / "seed_qualitative_bands.json"
    rows = json.loads(seed_path.read_text(encoding="utf-8"))

    from idraa.services.seed_qualitative_bands_loader import BandSeed

    validated = [BandSeed.model_validate(r).model_dump() for r in rows]

    bind = op.get_bind()
    now = datetime.now(UTC).isoformat()
    for row in validated:
        bind.execute(
            sa.text(
                """
                INSERT INTO qualitative_mapping_bands
                  (id, kind, label, low, mode, high, sort_order, derivation,
                   version, created_at, updated_at)
                VALUES
                  (:id, :kind, :label, :low, :mode, :high, :sort_order,
                   :derivation, 1, :now, :now)
                """
            ),
            {
                "id": uuid.uuid4().hex,
                "kind": row["kind"],
                "label": row["label"],
                "low": row["low"],
                "mode": row["mode"],
                "high": row["high"],
                "sort_order": row["sort_order"],
                "derivation": row["derivation"],
                "now": now,
            },
        )


def downgrade() -> None:
    op.drop_column("scenarios", "conversion_metadata")
    op.drop_index(
        "ux_qual_org_band_org_kind_label",
        table_name="qualitative_mapping_org_bands",
        sqlite_where=sa.text("deleted_at IS NULL"),
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_index(
        op.f("ix_qualitative_mapping_org_bands_organization_id"),
        table_name="qualitative_mapping_org_bands",
    )
    op.drop_table("qualitative_mapping_org_bands")
    op.drop_table("qualitative_mapping_bands")
