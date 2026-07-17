"""issue_66_controls_annual_cost

Migrates Control.cost_model: dict (JSON) → Control.annual_cost: Decimal
(Numeric(18, 2), NOT NULL, default 0). Single-PR big-bang per
docs/plans/2026-05-14-issue-66-cost-model-decimal-design.md.

Revision ID: 4af391c766a9
Revises: a7c19e84f3b2
Create Date: 2026-05-14 14:26:34.855393
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "4af391c766a9"
down_revision: str = "a7c19e84f3b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # server_default="0" is permanent (not just a backfill scaffold) so the
    # schema matches what the ORM produces (server_default="0" on the
    # Control model).
    with op.batch_alter_table("controls") as batch:
        batch.add_column(
            sa.Column(
                "annual_cost",
                sa.Numeric(18, 2),
                nullable=False,
                server_default="0",
            )
        )

    # Backfill from the JSON column. COALESCE guards against the legacy
    # cost_model={} shape (no observed rows in dev DB; defense-in-depth
    # for any older copies in the wild).
    op.execute(
        "UPDATE controls SET annual_cost = "
        "COALESCE(CAST(json_extract(cost_model, '$.annual_cost') AS NUMERIC), 0)"
    )

    with op.batch_alter_table("controls") as batch:
        batch.drop_column("cost_model")


def downgrade() -> None:
    with op.batch_alter_table("controls") as batch:
        batch.add_column(
            sa.Column(
                "cost_model",
                sa.JSON(),
                nullable=False,
                server_default="{}",
            )
        )
    op.execute(
        "UPDATE controls SET cost_model = "
        "json_object('annual_cost', CAST(annual_cost AS REAL))"
    )
    with op.batch_alter_table("controls") as batch:
        batch.drop_column("annual_cost")
