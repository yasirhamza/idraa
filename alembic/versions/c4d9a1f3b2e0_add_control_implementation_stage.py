"""add control implementation_stage

Revision ID: c4d9a1f3b2e0
Revises: 1a5eb1814c32
Create Date: 2026-06-16

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4d9a1f3b2e0"
down_revision: Union[str, Sequence[str], None] = "1a5eb1814c32"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("controls") as batch:
        batch.add_column(
            sa.Column(
                "implementation_stage",
                sa.Enum(
                    "non_existent",
                    "planned",
                    "in_project",
                    "active",
                    name="controlimplementationstage",
                    native_enum=False,
                ),
                nullable=False,
                server_default="active",
            )
        )
    # Explicit backfill so every pre-existing control composes exactly as
    # before this feature (issue #395 design §1: results unchanged until a
    # stage is deliberately demoted).
    op.execute("UPDATE controls SET implementation_stage = 'active'")


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("controls") as batch:
        batch.drop_column("implementation_stage")
