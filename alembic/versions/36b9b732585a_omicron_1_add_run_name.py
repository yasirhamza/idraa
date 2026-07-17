"""omicron_1_add_run_name

Revision ID: 36b9b732585a
Revises: 8db26570b7a2
Create Date: 2026-05-06 09:58:25.927700

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '36b9b732585a'
down_revision: Union[str, Sequence[str], None] = '8db26570b7a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "risk_analysis_runs",
        sa.Column("name", sa.String(length=200), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("risk_analysis_runs", "name")
