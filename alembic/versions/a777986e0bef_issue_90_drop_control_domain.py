"""issue_90_drop_control_domain

Drops Control.domain ENUM column. Domain is now derived at query time
from ControlFunctionAssignment.sub_function via Control.domains
property. See docs/plans/2026-05-14-issue-90-control-domain-derived-design.md.

Revision ID: a777986e0bef
Revises: 4af391c766a9
Create Date: 2026-05-14 18:07:51.821226

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "a777986e0bef"
down_revision: str = "4af391c766a9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("controls") as batch:
        batch.drop_column("domain")


def downgrade() -> None:
    # The original column denormalization was lossy — pre-issue-90 it stored
    # only one of the (potentially multi-domain) assignment-derived domains.
    # Downgrade restores the column shape with a safe default; data is
    # unrecoverable from the post-upgrade schema.
    with op.batch_alter_table("controls") as batch:
        batch.add_column(
            sa.Column(
                "domain",
                sa.Enum(
                    "loss_event",
                    "variance_management",
                    "decision_support",
                    name="controldomain",
                    native_enum=False,
                ),
                nullable=False,
                server_default="loss_event",
            )
        )
