"""add per-value assignment provenance

Revision ID: e14c75d22129
Revises: 9ae19de17172
Create Date: 2026-06-30 17:53:10.502169

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e14c75d22129'
down_revision: Union[str, Sequence[str], None] = '9ae19de17172'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("control_library_entry_assignments") as b:
        b.add_column(sa.Column("capability_provenance", sa.String(32), nullable=True))
        b.add_column(sa.Column("capability_citations", sa.JSON(), nullable=False, server_default="[]"))
        b.add_column(sa.Column("coverage_provenance", sa.String(32), nullable=False, server_default="expert-estimate"))
        b.add_column(sa.Column("coverage_citations", sa.JSON(), nullable=False, server_default="[]"))
        b.add_column(sa.Column("reliability_provenance", sa.String(32), nullable=False, server_default="expert-estimate"))
        b.add_column(sa.Column("reliability_citations", sa.JSON(), nullable=False, server_default="[]"))
    # NEW-B1: existing capability values are estimates -> backfill provenance (mirrors the DTO auto-fill)
    op.execute(
        "UPDATE control_library_entry_assignments SET capability_provenance='expert-estimate' "
        "WHERE capability_default IS NOT NULL"
    )


def downgrade() -> None:
    with op.batch_alter_table("control_library_entry_assignments") as b:
        for c in ("capability_provenance", "capability_citations", "coverage_provenance",
                  "coverage_citations", "reliability_provenance", "reliability_citations"):
            b.drop_column(c)
