"""widen organizations.industry_sub_sector CHECK to the 39-member IndustrySubSector

Revision ID: 3e0c10ecedb3
Revises: e3a7c5b1d9f2
Create Date: 2026-10-23

Issue #241. ``IndustrySubSector`` grew from the original 8 OT/energy members to
39 so the library's sub-sector filter spans every ``applicable_sub_sectors``
value the seed files use. ``922b63358719`` installed
``organizations.industry_sub_sector`` as a ``sa.Enum(..., native_enum=False,
create_constraint=True)`` carrying a named CHECK, ``industry_sub_sector_enum``,
over those 8 values; an organisation saved with one of the 31 new members would
violate it on any migrated / production DB. This migration is the DB-constraint
half of that enum change.

``batch_alter_table`` rebuilds the table on SQLite and regenerates the CHECK
with the full 39-value list under the same constraint name (the precedent is
``7e29245a1930``, which widened ``threatcategory`` the same way). The column
type stays VARCHAR(22): the longest old value is ``chemical_manufacturing`` and
the longest new one is ``intelligence_community``, both 22 characters.

Upgrade writes no data. Downgrade narrows back to the 8-value CHECK, which is
only safe when no organisation holds one of the 31 added members; it validates
first and FAILS LOUDLY, naming the offending rows (mirroring ``922b63358719``'s
own data-validation style) rather than letting the table rebuild fail midway
or silently lose data.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# --- Revision identifiers ---
revision = "3e0c10ecedb3"
down_revision = "e3a7c5b1d9f2"
branch_labels = None
depends_on = None

# The constraint name ``922b63358719`` gave the CHECK. Do not rename: the batch
# rebuild drops the existing CHECK by this name and recreates it under it.
_CONSTRAINT_NAME = "industry_sub_sector_enum"

# The 8 values match ``922b63358719``'s ``_VALID_SUB_SECTOR_VALUES``
# value-for-value and in order. Do not reorder -- the CHECK rebuild must match
# the constraint the production DB already has.
_VALUES_8 = (
    "oil_and_gas",
    "electric_utility",
    "water_utility",
    "pipeline",
    "chemical_manufacturing",
    "nuclear",
    "process_manufacturing",
    "other",
)

# The 31 members added by #241, in IndustrySubSector declaration order.
_ADDED_31 = (
    # Healthcare
    "health_plan",
    "health_system",
    "hospital",
    "physician_practice",
    # Education
    "community_college",
    "higher_education",
    "k12",
    # Public
    "federal_government",
    "state_government",
    "local_government",
    "judiciary",
    "law_enforcement",
    "intelligence_community",
    # Information
    "broadband_provider",
    "wireless_carrier",
    "mvno",
    # Professional
    "accounting",
    "consulting",
    "engineering_services",
    "law_firm",
    # Manufacturing
    "aerospace",
    "automotive",
    "food_manufacturing",
    # Utilities
    "gas_utility",
    # Transportation
    "airline",
    "car_rental",
    # Hospitality
    "casino",
    "hotel_lodging",
    "resort",
    "restaurant",
    "entertainment_venue",
)
_VALUES_39 = (*_VALUES_8, *_ADDED_31)


def _enum(values: tuple[str, ...]) -> sa.Enum:
    return sa.Enum(
        *values,
        name=_CONSTRAINT_NAME,
        native_enum=False,
        create_constraint=True,
    )


def upgrade() -> None:
    # Widening only: every row valid under the 8-value CHECK is valid under the
    # 39-value one, so there is nothing to validate and no data is written.
    with op.batch_alter_table("organizations", schema=None) as batch_op:
        batch_op.alter_column(
            "industry_sub_sector",
            existing_type=_enum(_VALUES_8),
            type_=_enum(_VALUES_39),
            existing_nullable=True,
        )


def downgrade() -> None:
    # Validate BEFORE touching the table. ``._mapping["..."]`` for cross-dialect
    # row access, as in 922b63358719 (direct attribute access raises on SQLite's
    # LegacyCursorResult rows).
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, industry_sub_sector FROM organizations "
            "WHERE industry_sub_sector IS NOT NULL "
            "  AND industry_sub_sector NOT IN :valid"
        ).bindparams(
            sa.bindparam(
                "valid",
                value=list(_VALUES_8),
                expanding=True,
            )
        )
    ).fetchall()

    if rows:
        offenders = ", ".join(
            f"{r._mapping['id']} ({r._mapping['industry_sub_sector']!r})" for r in rows
        )
        raise RuntimeError(
            "Downgrade BLOCKED: organizations have industry_sub_sector "
            "values that the 8-member CHECK (pre-#241) does not allow. "
            "Update or NULL them before re-running this downgrade. "
            f"Offending rows: {offenders}. "
            f"Values allowed after downgrade: {sorted(_VALUES_8)}."
        )

    with op.batch_alter_table("organizations", schema=None) as batch_op:
        batch_op.alter_column(
            "industry_sub_sector",
            existing_type=_enum(_VALUES_39),
            type_=_enum(_VALUES_8),
            existing_nullable=True,
        )
