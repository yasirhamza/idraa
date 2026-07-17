"""PR η — Organization.industry_sub_sector enum + Scenario.sub_sector_pin

Revision ID: 922b63358719
Revises: 3e5d550fcade
Create Date: 2026-04-28

Spec: docs/superpowers/specs/2026-04-28-pr-eta-iris-sub-sector-overlays-design.md §8.

Schema changes:
1. Tighten Organization.industry_sub_sector from String(120) to
   SAEnum(IndustrySubSector, native_enum=False).
   - Data validation: existing non-null values must be one of the 8
     enum members. Migration FAILS LOUDLY on unknowns, naming offending rows.
   - At PR η ship time the column is unused in production; this is a
     guard against future drift in dev.
2. Add Scenario.sub_sector_pin JSON column, nullable.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


# --- Revision identifiers ---
revision = "922b63358719"
down_revision = "3e5d550fcade"
branch_labels = None
depends_on = None


_VALID_SUB_SECTOR_VALUES = (
    "oil_and_gas",
    "electric_utility",
    "water_utility",
    "pipeline",
    "chemical_manufacturing",
    "nuclear",
    "process_manufacturing",
    "other",
)


def upgrade() -> None:
    # === 1. Validate existing data in organizations.industry_sub_sector ===
    # Use ._mapping["..."] for cross-dialect row attribute access — direct
    # attribute access (r.id, r.industry_sub_sector) raises AttributeError
    # on SQLite's LegacyCursorResult rows. Verified vs alembic precedent.
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, industry_sub_sector FROM organizations "
            "WHERE industry_sub_sector IS NOT NULL "
            "  AND industry_sub_sector NOT IN :valid"
        ).bindparams(
            sa.bindparam(
                "valid",
                value=list(_VALID_SUB_SECTOR_VALUES),
                expanding=True,
            )
        )
    ).fetchall()

    if rows:
        offenders = ", ".join(
            f"{r._mapping['id']} ({r._mapping['industry_sub_sector']!r})"
            for r in rows
        )
        raise RuntimeError(
            "Migration BLOCKED: organizations have industry_sub_sector "
            "values that are not members of the IndustrySubSector enum. "
            "Update or NULL them before re-running this migration. "
            f"Offending rows: {offenders}. "
            f"Valid values: {sorted(_VALID_SUB_SECTOR_VALUES)}."
        )

    # === 2. Alter Organization.industry_sub_sector to enum-backed VARCHAR ===
    # native_enum=False emits VARCHAR + named CHECK constraint, matching the
    # repo convention (Organization.industry_type, etc.). Cross-dialect safe.
    # The constraint NAME is explicit so downgrade() can reference it; without
    # a name, SQLite's batch mode generates an auto-named constraint that
    # downgrade cannot reliably drop.
    with op.batch_alter_table("organizations", schema=None) as batch_op:
        batch_op.alter_column(
            "industry_sub_sector",
            existing_type=sa.String(120),
            type_=sa.Enum(
                *_VALID_SUB_SECTOR_VALUES,
                name="industry_sub_sector_enum",
                native_enum=False,
                create_constraint=True,  # emit explicit CHECK
            ),
            existing_nullable=True,
        )

    # === 3. Add Scenario.sub_sector_pin JSON column ===
    with op.batch_alter_table("scenarios", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("sub_sector_pin", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    with op.batch_alter_table("scenarios", schema=None) as batch_op:
        batch_op.drop_column("sub_sector_pin")

    with op.batch_alter_table("organizations", schema=None) as batch_op:
        batch_op.alter_column(
            "industry_sub_sector",
            existing_type=sa.Enum(
                *_VALID_SUB_SECTOR_VALUES,
                name="industry_sub_sector_enum",
                native_enum=False,
                create_constraint=True,
            ),
            type_=sa.String(120),
            existing_nullable=True,
        )
