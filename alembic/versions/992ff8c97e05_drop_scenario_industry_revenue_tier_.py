"""drop scenario industry revenue_tier issue 88

Revision ID: 992ff8c97e05
Revises: 2b8317b19290
Create Date: 2026-05-13 02:57:42.821156

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '992ff8c97e05'
down_revision: Union[str, Sequence[str], None] = '2b8317b19290'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Rename stale cfa indexes to match current SA naming convention.
    op.drop_index(op.f('ix_cfa_control_id'), table_name='control_function_assignments')
    op.drop_index(op.f('ix_cfa_organization_id'), table_name='control_function_assignments')
    op.create_index(op.f('ix_control_function_assignments_control_id'), 'control_function_assignments', ['control_id'], unique=False)
    op.create_index(op.f('ix_control_function_assignments_organization_id'), 'control_function_assignments', ['organization_id'], unique=False)
    # Drop scenario columns now sourced from org — SQLite requires batch mode for DROP COLUMN.
    with op.batch_alter_table("scenarios") as batch_op:
        batch_op.drop_column("industry")
        batch_op.drop_column("revenue_tier")


def downgrade() -> None:
    """WARNING: rollback back-fills industry='other' / revenue_tier='100m_to_1b'
    on EVERY existing row. Task 9 rerouted these values to Organization;
    rollback does NOT restore the original per-scenario values.
    """
    # Re-add columns with server_default so re-creation on a populated table doesn't fail.
    with op.batch_alter_table("scenarios") as batch_op:
        batch_op.add_column(sa.Column(
            "industry", sa.String(length=64), nullable=False, server_default="other"
        ))
        batch_op.add_column(sa.Column(
            "revenue_tier", sa.String(length=64), nullable=False, server_default="100m_to_1b"
        ))
    # Restore old cfa index names.
    op.drop_index(op.f('ix_control_function_assignments_organization_id'), table_name='control_function_assignments')
    op.drop_index(op.f('ix_control_function_assignments_control_id'), table_name='control_function_assignments')
    op.create_index(op.f('ix_cfa_organization_id'), 'control_function_assignments', ['organization_id'], unique=False)
    op.create_index(op.f('ix_cfa_control_id'), 'control_function_assignments', ['control_id'], unique=False)
