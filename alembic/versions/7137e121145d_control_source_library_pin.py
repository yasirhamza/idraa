"""control source + library_pin

Revision ID: 7137e121145d
Revises: 3fc33f8e7ddc
Create Date: 2026-06-01 21:37:37.437679

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7137e121145d'
down_revision: Union[str, Sequence[str], None] = '3fc33f8e7ddc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("controls") as batch:
        batch.add_column(
            sa.Column(
                "source",
                sa.Enum("custom", "library_derived", name="controlsource", native_enum=False),
                nullable=False,
                server_default="custom",
            )
        )
        batch.add_column(sa.Column("library_pin", sa.JSON(), nullable=True))
    op.execute("UPDATE controls SET source = 'custom' WHERE source IS NULL")


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("controls") as batch:
        batch.drop_column("library_pin")
        batch.drop_column("source")
