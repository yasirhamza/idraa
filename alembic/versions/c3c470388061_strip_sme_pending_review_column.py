"""strip sme pending_review column

The pending_review approval workflow was never wired into the UI (no admin
page existed to approve/reject pending SMEs). Per the SME free-text design
(2026-05-25 spec), we strip the entire workflow: column, approve/reject
routes, and PendingReviewQuotaExceededError. Analyst-requested SMEs now
land live immediately.

Revision ID: c3c470388061
Revises: ed70de5aa6b9
Create Date: 2026-05-25 17:00:53.651824

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, auto-populated by alembic
revision: str = "c3c470388061"
down_revision: str | Sequence[str] | None = "ed70de5aa6b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("subject_matter_experts", "pending_review")


def downgrade() -> None:
    import sqlalchemy as sa

    op.add_column(
        "subject_matter_experts",
        sa.Column(
            "pending_review",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
