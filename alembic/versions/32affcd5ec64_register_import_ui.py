"""register import ui — staging state column + binding profiles

Revision ID: 32affcd5ec64
Revises: e6882513a026
Create Date: 2026-07-18 21:29:41.846074

Epic #34 P1c Task 2: schema half of the register-import staging flow.

- ``csv_import_preview.state_json`` (nullable JSON): accumulating step-choice
  storage for the staged register-import wizard (sheet selection, column
  map, value bindings). Register-import-only today — existing overlay /
  calibration-override importer rows keep it NULL. See the ORM model
  docstring (``models/csv_import_preview.py``) for the whole-dict-
  reassignment write rule (Arch-I1 plan-gate amendment).
- ``register_binding_profiles`` (new table): per-org saved (column_map,
  value_bindings) profile for re-applying a known register shape on a
  future upload, plus a ``mapping_versions_snapshot`` used by Task 3's
  ``apply_profile`` drift detection. No canonical/seed counterpart — this
  is a pure per-org CRUD table (see the ORM model docstring for the full
  field-level rationale).

Data-contract note (Arch-N3 plan-gate amendment): ``register_binding_profiles``
has no Pydantic DTO pair in P1c — column_map/value_bindings/
mapping_versions_snapshot are JSON blobs consumed at the route layer, not
mapped through a schema class. The ORM<->DTO field-sync contract test
(``tests/contracts/test_field_sync.py``) therefore does not apply to this
table; the schema-snapshot test is the sole structural guard.

Round-trippable: ``downgrade()`` drops the new column and the new table
(with its index) in reverse creation order.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "32affcd5ec64"
down_revision: str | None = "e6882513a026"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "register_binding_profiles",
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
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("column_map", sa.JSON(), nullable=False),
        sa.Column("value_bindings", sa.JSON(), nullable=False),
        sa.Column("mapping_versions_snapshot", sa.JSON(), nullable=False),
        sa.Column(
            "created_by",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.UniqueConstraint("organization_id", "name", name="uq_register_profile_org_name"),
    )
    op.create_index(
        "ix_register_binding_profiles_organization_id",
        "register_binding_profiles",
        ["organization_id"],
    )
    op.add_column("csv_import_preview", sa.Column("state_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("csv_import_preview", "state_json")
    op.drop_index(
        "ix_register_binding_profiles_organization_id",
        table_name="register_binding_profiles",
    )
    op.drop_table("register_binding_profiles")
