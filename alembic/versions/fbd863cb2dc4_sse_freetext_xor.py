"""sse freetext xor

Adds sme_name to scenario_sme_estimates, relaxes sme_id to nullable, and
enforces XOR identity via ck_sse_sme_id_xor_name. Existing rows all have
sme_id set + sme_name NULL, so the XOR CHECK is satisfied with zero
backfill.

Revision ID: fbd863cb2dc4
Revises: c3c470388061
Create Date: 2026-05-25 17:17:09.663586

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "fbd863cb2dc4"
down_revision: str | Sequence[str] | None = "c3c470388061"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("scenario_sme_estimates") as batch_op:
        batch_op.add_column(sa.Column("sme_name", sa.String(200), nullable=True))
        batch_op.alter_column("sme_id", existing_type=sa.Uuid(), nullable=True)
        batch_op.create_check_constraint(
            "ck_sse_sme_id_xor_name",
            "(sme_id IS NULL) != (sme_name IS NULL)",
        )


def downgrade() -> None:
    with op.batch_alter_table("scenario_sme_estimates") as batch_op:
        batch_op.drop_constraint("ck_sse_sme_id_xor_name", type_="check")
        batch_op.alter_column("sme_id", existing_type=sa.Uuid(), nullable=False)
        batch_op.drop_column("sme_name")
