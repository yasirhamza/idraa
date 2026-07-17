"""pr_mu_1_capability_value_upper_bound

Revision ID: 1297897c44f5
Revises: a777986e0bef
Create Date: 2026-05-14 22:51:16.560882

PR μ.1 input-safety hardening (Sec-I3): add CHECK constraint that
`capability_value` is NULL or <= 1e10. ~30 years in days, > $10B in
dollars — well beyond any realistic data. Defense-in-depth against
direct ORM writes that bypass Pydantic isfinite + range checks.

No data backfill in this migration. Production NULL-backfill of
TIME_UNIT_EXCLUDED placeholder rows is deferred to PR μ.1b where the
audit_log schema mismatches (Sec-B1) can be properly fixed.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1297897c44f5"
down_revision: str = "a777986e0bef"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add CHECK: capability_value IS NULL OR capability_value <= 1e10."""
    # Mirror: src/idraa/models/control_function_assignment.py __table_args__
    # `ck_cfa_capability_upper_bound`. Two declarations must stay in sync until
    # snapshot_orm_shape captures CHECK constraint sqltext.
    with op.batch_alter_table("control_function_assignments") as batch_op:
        batch_op.create_check_constraint(
            "ck_cfa_capability_upper_bound",
            "capability_value IS NULL OR capability_value <= 1e10",
        )


def downgrade() -> None:
    """Drop the upper-bound CHECK."""
    with op.batch_alter_table("control_function_assignments") as batch_op:
        batch_op.drop_constraint("ck_cfa_capability_upper_bound", type_="check")
