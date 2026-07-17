"""add_org_loss_tolerance

Adds two nullable columns to ``organizations`` for the LEC tolerance
overlay (vertical+horizontal lines on the Loss Exceedance Curve so the
chart is decision-useful, not just descriptive):

  loss_tolerance_amount      Numeric(18, 2)  — annual $ loss above which
                                                 the org considers the
                                                 risk unacceptable.
  loss_tolerance_probability Float           — exceedance probability
                                                 (0-1) above which the org
                                                 considers the risk
                                                 unacceptable.

Both nullable: existing orgs render LECs unchanged until tolerance is set
on the profile. The chart macro elides the overlay when either is null.

Revision ID: f8b3c19d4a02
Revises: e7d0c3a91f2b
Create Date: 2026-05-09 13:30:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "f8b3c19d4a02"
down_revision = "e7d0c3a91f2b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("organizations") as batch:
        batch.add_column(
            sa.Column("loss_tolerance_amount", sa.Numeric(18, 2), nullable=True)
        )
        batch.add_column(sa.Column("loss_tolerance_probability", sa.Float, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("organizations") as batch:
        batch.drop_column("loss_tolerance_probability")
        batch.drop_column("loss_tolerance_amount")
