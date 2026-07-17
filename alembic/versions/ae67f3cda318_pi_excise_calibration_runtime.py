"""pi excise calibration runtime

Revision ID: ae67f3cda318
Revises: 36b9b732585a
Create Date: 2026-05-06 22:09:19.132833

PR pi: excise the calibration runtime framework.

- Drops 7 Scenario columns (overlay_pins, sub_sector_pin,
  calibration_override_pin, iris_calibration_year, mc_iterations,
  last_simulated_at, last_simulation_inputs_hash).
- Drops calibration_override_revisions + calibration_overrides tables.
- Deletes pre-PR risk_analysis_runs rows (dev/testing artifacts under
  the calibration framework -- user authorized in brainstorm Q4).

Downgrade is a no-op since v3 has not shipped to production and the
calibration framework is gone.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ae67f3cda318'
down_revision: Union[str, Sequence[str], None] = '36b9b732585a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Pre-PR runs were under the calibration framework; results are not
    # comparable to the post-PR pipeline. User authorized destructive
    # cleanup in brainstorm Q4 (dev/testing artifacts only).
    #
    # Audit log cleanup: audit_log entries reference run.id by string in
    # entity_id (no FK), so the run-DELETE leaves orphan audit rows. Per
    # security review, also DELETE the audit rows that point at deceased
    # run IDs so the audit table doesn't accumulate dangling references.
    # Confirmed exact entity_type strings via grep against
    # services/runs.py + services/run_executor.py: only
    # 'risk_analysis_run' (snake_case) is written today; the
    # 'RiskAnalysisRun' PascalCase string is included defensively.
    # NOTE: actual table name is `audit_log` (singular). Verified at
    # src/idraa/models/audit_log.py:37 __tablename__ = "audit_log".
    op.execute(
        "DELETE FROM audit_log WHERE entity_type IN "
        "('risk_analysis_run', 'RiskAnalysisRun');"
    )
    op.execute("DELETE FROM risk_analysis_runs;")

    # Drop 7 Scenario columns.
    with op.batch_alter_table("scenarios") as batch:
        batch.drop_column("overlay_pins")
        batch.drop_column("sub_sector_pin")
        batch.drop_column("calibration_override_pin")
        batch.drop_column("iris_calibration_year")
        batch.drop_column("mc_iterations")
        batch.drop_column("last_simulated_at")
        batch.drop_column("last_simulation_inputs_hash")

    # Drop CalibrationOverride tables (FK-respected order: revision -> parent).
    op.drop_table("calibration_override_revisions")
    op.drop_table("calibration_overrides")


def downgrade() -> None:
    raise NotImplementedError(
        "PR pi excised the calibration framework; downgrading is not supported."
    )
