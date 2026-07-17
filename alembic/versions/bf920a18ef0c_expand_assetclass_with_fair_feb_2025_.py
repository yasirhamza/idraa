"""expand assetclass with fair feb 2025 canonical values

Adds 4 FAIR Feb 2025 canonical asset types to the `assetclass` enum CHECK
constraint that gates the `scenarios.asset_class` and
`scenario_library_entries.asset_class` columns:
 - cash_or_equivalent
 - business_process_revenue
 - business_process_third_party_revenue
 - business_process_cost

These are purely additive — no existing row's value changes, no backfill
needed. UAT surfaced "no way to express cash/cash-equivalent scenarios"
(FAIR Feb 2025 taxonomy Figure 2 p7 lists "Cash or Cash Equivalent" as a
canonical asset type).

For SQLite, `op.batch_alter_table` rebuilds each affected table with the
new column definition (and therefore the new CHECK constraint). Postgres
ignores the batch wrapper.

Revision ID: bf920a18ef0c
Revises: fbd863cb2dc4
Create Date: 2026-05-25 22:55:57.360249
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "bf920a18ef0c"
down_revision: str | Sequence[str] | None = "fbd863cb2dc4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_ASSET_CLASS_VALUES_NEW = (
    "data",
    "systems",
    "people",
    "facilities",
    "ot_systems",
    "safety_systems",
    "cash_or_equivalent",
    "business_process_revenue",
    "business_process_third_party_revenue",
    "business_process_cost",
    "other",
)

_ASSET_CLASS_VALUES_OLD = (
    "data",
    "systems",
    "people",
    "facilities",
    "ot_systems",
    "safety_systems",
    "other",
)


def _enum(values: tuple[str, ...]) -> sa.Enum:
    return sa.Enum(
        *values,
        name="assetclass",
        native_enum=False,
        create_constraint=True,
    )


def upgrade() -> None:
    new_enum = _enum(_ASSET_CLASS_VALUES_NEW)

    # scenario_library_entries.asset_class (NOT NULL, Enum CHECK)
    with op.batch_alter_table("scenario_library_entries") as batch:
        batch.alter_column(
            "asset_class",
            existing_type=_enum(_ASSET_CLASS_VALUES_OLD),
            type_=new_enum,
            existing_nullable=False,
        )

    # scenarios.asset_class (nullable, Enum CHECK)
    with op.batch_alter_table("scenarios") as batch:
        batch.alter_column(
            "asset_class",
            existing_type=_enum(_ASSET_CLASS_VALUES_OLD),
            type_=new_enum,
            existing_nullable=True,
        )


def downgrade() -> None:
    old_enum = _enum(_ASSET_CLASS_VALUES_OLD)

    # Downgrade-safety guard: if any row already uses one of the new values,
    # downgrade would silently violate the narrowed CHECK on next write. The
    # batch rebuild copies rows as-is and the new CHECK would reject them on
    # subsequent inserts/updates. Block downgrade with a clear error in that
    # case so an operator must consciously migrate or drop the offending rows
    # before reversing.
    bind = op.get_bind()
    for table in ("scenarios", "scenario_library_entries"):
        offending = bind.execute(
            sa.text(
                f"SELECT COUNT(*) FROM {table} WHERE asset_class IN "
                "('cash_or_equivalent', 'business_process_revenue', "
                "'business_process_third_party_revenue', 'business_process_cost')"
            )
        ).scalar()
        if offending:
            raise RuntimeError(
                f"Cannot downgrade: {table} has {offending} row(s) using new "
                "AssetClass values. Migrate or drop them before reversing."
            )

    with op.batch_alter_table("scenario_library_entries") as batch:
        batch.alter_column(
            "asset_class",
            existing_type=_enum(_ASSET_CLASS_VALUES_NEW),
            type_=old_enum,
            existing_nullable=False,
        )

    with op.batch_alter_table("scenarios") as batch:
        batch.alter_column(
            "asset_class",
            existing_type=_enum(_ASSET_CLASS_VALUES_NEW),
            type_=old_enum,
            existing_nullable=True,
        )
