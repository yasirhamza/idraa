"""widen threat_category CHECK for ot_integrity

Revision ID: 7e29245a1930
Revises: de2080181a9c
Create Date: 2026-06-03 00:00:00.000000

The production schema (from ``b8e0334b7f43``) installs a 12-value CHECK
constraint named ``threatcategory`` on BOTH
``scenario_library_entries.threat_event_type`` and
``scenarios.threat_category`` (via ``sa.Enum(...12 values..., native_enum=False,
create_constraint=True)``). Inserting an ``ot_integrity`` row would violate that
CHECK on any migrated / production DB.

This migration widens both CHECKs to 13 values (adding ``ot_integrity``) via
``batch_alter_table`` — on SQLite this rebuilds the table and regenerates the
CHECK with the new value list. The ``ThreatCategory.OT_INTEGRITY`` enum value
itself landed in a prior commit; this is the DB-constraint half of that change.
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "7e29245a1930"
down_revision = "de2080181a9c"
branch_labels = None
depends_on = None

# The 12 values match ``b8e0334b7f43``'s ``_THREAT_CATEGORY_ENUM`` exactly,
# value-for-value and in order. Do not reorder — the CHECK rebuild must match
# the constraint the production DB already has.
_VALUES_12 = (
    "malware",
    "ransomware",
    "data_disclosure",
    "data_tampering",
    "denial_of_service",
    "social_engineering",
    "physical_tampering",
    "supply_chain",
    "insider_misuse",
    "ot_safety_tampering",
    "ot_availability",
    "miscellaneous",
)
_VALUES_13 = (*_VALUES_12, "ot_integrity")


def _enum(values: tuple[str, ...]) -> sa.Enum:
    return sa.Enum(
        *values,
        name="threatcategory",
        native_enum=False,
        create_constraint=True,
    )


# Both columns are NOT NULL in the live schema:
#   - scenario_library_entries.threat_event_type  (b8e0334b7f43, model line 78)
#   - scenarios.threat_category                   (NOT NULL after the b8e0334b7f43
#                                                  Step-7 rename; model line 69)
# so existing_nullable=False for both targets.
_TARGETS = (
    ("scenario_library_entries", "threat_event_type"),
    ("scenarios", "threat_category"),
)


def upgrade() -> None:
    for table, col in _TARGETS:
        with op.batch_alter_table(table) as batch:
            batch.alter_column(
                col,
                existing_type=_enum(_VALUES_12),
                type_=_enum(_VALUES_13),
                existing_nullable=False,
            )


def downgrade() -> None:
    # Narrow back to 12. Safe only if no ot_integrity rows remain — the additive
    # seed migration's downgrade (which runs first, in reverse order) deletes the
    # ot_integrity library entries; any user scenario with
    # threat_category='ot_integrity' must be cleared before this runs.
    for table, col in _TARGETS:
        with op.batch_alter_table(table) as batch:
            batch.alter_column(
                col,
                existing_type=_enum(_VALUES_13),
                type_=_enum(_VALUES_12),
                existing_nullable=False,
            )
