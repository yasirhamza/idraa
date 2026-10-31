"""org-wide meta controls (#299)

Adds ``controls.org_wide_meta`` (NOT NULL DEFAULT 0 — every existing control
stays scenario-scoped, so no result changes) and
``risk_analysis_runs.inherited_control_ids_per_scenario`` (JSON NULL — NULL
marks runs that predate the feature). Upgrade writes no data.

Downgrade flags every COMPLETED run whose column is NOT NULL as stale, before
dropping the column (spec R1). The criterion is DELIBERATELY A SUPERSET of the
runs that actually change: a re-run under the reverted code would not apply
inheritance, so any run that inherited at least one control (SINGLE or AGGREGATE)
would compute different numbers; but a run that inherited nothing (``{}`` or
``{scenario_id: []}``) reproduces its numbers exactly on a re-run, and is flagged
anyway. The set cannot be narrowed once the column is dropped, and staleness is
advisory (the run stays COMPLETED and visible; the UI prompts a re-run), so the
over-inclusion is kept (same stance as ``c4d2e8f1a6b9_issue_199_c8_stale_runs``).

Audit: like that migration, the downgrade writes one ``audit_log`` row per run it flips
(``user_id`` NULL = system actor, ``action = 'run.flagged_stale'``, ``entity_type =
'risk_analysis_run'``, ids bound as the NO-HYPHEN hex read from the row, ``changes =
{"is_stale": [false, true], "reason": ["", "issue-299 org-wide meta controls rollback"]}``),
so the stale flag is never set without a trail. Idempotent: an already-stale run is neither
rewritten nor given a row.

Precondition for the downgrade: no QUEUED or RUNNING runs (stop the machine, or let
runs drain, first). A run still RUNNING when this executes lands COMPLETED through
the executor's guarded UPDATE, which never touches ``is_stale``: it would finish
unflagged, with results computed under inheritance, after the evidence column is gone.

Revision ID: a7c2e9f14b63
Revises: 3e0c10ecedb3
Create Date: 2026-10-29
"""

import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "a7c2e9f14b63"
down_revision = "3e0c10ecedb3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACTION = "run.flagged_stale"
_REASON = "issue-299 org-wide meta controls rollback"


def upgrade() -> None:
    with op.batch_alter_table("controls") as batch_op:
        batch_op.add_column(
            sa.Column("org_wide_meta", sa.Boolean(), nullable=False, server_default="0")
        )
    with op.batch_alter_table("risk_analysis_runs") as batch_op:
        batch_op.add_column(
            sa.Column("inherited_control_ids_per_scenario", sa.JSON(), nullable=True)
        )


def downgrade() -> None:
    # Spec R1: feature-era runs must be re-run after rollback; say so via is_stale.
    # Bound parameters, not literals: portable across SQLite and Postgres (precedent:
    # alembic/versions/c4d2e8f1a6b9_issue_199_c8_stale_runs.py:166, SC-N9). Only not-yet-stale
    # COMPLETED runs are candidates (idempotent, and a row is written only for a real flip).
    bind = op.get_bind()
    candidates = bind.execute(
        sa.text(
            "SELECT id, organization_id FROM risk_analysis_runs "
            "WHERE inherited_control_ids_per_scenario IS NOT NULL "
            "AND status = 'completed' AND is_stale = :f"
        ),
        {"f": False},
    ).fetchall()
    now = datetime.now(UTC).isoformat(sep=" ")
    for run_id, org_id in candidates:
        bind.execute(
            sa.text("UPDATE risk_analysis_runs SET is_stale = :t WHERE id = :id"),
            {"t": True, "id": run_id},
        )
        # Same row shape as c4d2e8f1a6b9:170-182: system actor, org-scoped, no-hyphen hex ids.
        bind.execute(
            sa.text(
                "INSERT INTO audit_log "
                "(id, organization_id, entity_type, entity_id, user_id, action, changes, timestamp) "
                "VALUES (:id, :org, 'risk_analysis_run', :eid, NULL, :action, :ch, :ts)"
            ),
            {
                "id": uuid.uuid4().hex,
                "org": org_id,
                "eid": run_id,
                "action": _ACTION,
                "ch": json.dumps({"is_stale": [False, True], "reason": ["", _REASON]}),
                "ts": now,
            },
        )
    with op.batch_alter_table("risk_analysis_runs") as batch_op:
        batch_op.drop_column("inherited_control_ids_per_scenario")
    with op.batch_alter_table("controls") as batch_op:
        batch_op.drop_column("org_wide_meta")
