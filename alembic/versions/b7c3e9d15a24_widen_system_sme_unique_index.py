"""#wizard-library-prefill: widen the per-org system-SME partial unique index
from (organization_id) to (organization_id, name) so an org can hold BOTH the
"Industry baseline" (IRIS) and "Library reference" system SMEs. Widening a
unique key is strictly more permissive — no existing row can newly conflict.

Revision ID: b7c3e9d15a24
Down revision: d4918202a23a
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b7c3e9d15a24"
down_revision = "d4918202a23a"
branch_labels = None
depends_on = None

_NAME = "ux_sme_org_system_owned"
_TABLE = "subject_matter_experts"


def _dialect_where() -> str:
    # Predicate text must match the index DDL byte-for-byte per dialect
    # (SQLite: `= 1`; PG: `= TRUE`) so ON CONFLICT ... index_where plans.
    return (
        "is_system_owned = TRUE"
        if op.get_bind().dialect.name == "postgresql"
        else ("is_system_owned = 1")
    )


def upgrade() -> None:
    where = _dialect_where()
    op.drop_index(_NAME, table_name=_TABLE)
    op.create_index(
        _NAME,
        _TABLE,
        ["organization_id", "name"],
        unique=True,
        sqlite_where=sa.text(where),
        postgresql_where=sa.text(where),
    )


def downgrade() -> None:
    # Recreate the (organization_id) form. Safe unless an org already holds 2+
    # system SMEs (e.g. both Industry-baseline + Library-reference materialized)
    # at downgrade time — matches the forward-only content-migration posture;
    # a real downgrade would first need to prune extra system SMEs.
    where = _dialect_where()
    op.drop_index(_NAME, table_name=_TABLE)
    op.create_index(
        _NAME,
        _TABLE,
        ["organization_id"],
        unique=True,
        sqlite_where=sa.text(where),
        postgresql_where=sa.text(where),
    )
