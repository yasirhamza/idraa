"""Migration roundtrip tests for PR xi (scenario_id nullable).

Uses the shared alembic_config + alembic_engine fixtures from
tests/migrations/conftest.py (same pattern as test_pr_iota_control_reshape.py).
alembic.command drives the migrations; alembic_engine (sync) seeds raw SQL rows
for the downgrade-blocked scenario.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine

# Revision IDs
_DOWN_REV = "a1b2c3d4e5f6"  # PR iota CFA migration (pre-PR-xi head)
_OUR_REV = "8db26570b7a2"  # PR xi aggregate_runs migration

# After PR pi (`ae67f3cda318`) the downgrade chain raises
# NotImplementedError, so these tests can no longer traverse `head` through PR
# pi's downgrade. Upgrade only as far as PR xi's own revision (_OUR_REV) and
# round-trip from there -- the PR xi migration is what we're exercising.


def test_pr_xi_migration_roundtrip_on_empty_db(
    alembic_config: Config,
) -> None:
    """upgrade _OUR_REV -> downgrade past PR xi -> upgrade _OUR_REV succeeds on
    empty DB.

    Targets _DOWN_REV (pre-PR-xi head) explicitly so the test stays correct as
    later migrations land on top of PR xi. Stops at _OUR_REV rather than
    ``head`` because PR pi (downstream of PR xi) blocks downgrade entirely.
    """
    command.upgrade(alembic_config, _OUR_REV)
    command.downgrade(alembic_config, _DOWN_REV)
    command.upgrade(alembic_config, _OUR_REV)


def test_pr_xi_downgrade_blocked_with_aggregate_rows(
    alembic_config: Config,
    alembic_engine: Engine,
) -> None:
    """downgrade aborts with RuntimeError when AGGREGATE rows have NULL scenario_id.

    Downgrades to _DOWN_REV (pre-PR-xi head) so the PR xi migration's downgrade
    runs and trips its guard. Upgrades only to _OUR_REV (PR xi) rather than
    ``head`` -- PR pi downstream blocks downgrade unconditionally.
    """
    command.upgrade(alembic_config, _OUR_REV)

    org_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())

    with alembic_engine.begin() as conn:
        # FK enforcement off: we only need an AGGREGATE row in risk_analysis_runs
        # to trigger the downgrade guard; parent rows are not required.
        conn.execute(sa.text("PRAGMA foreign_keys = OFF"))
        conn.execute(
            sa.text(
                "INSERT INTO risk_analysis_runs "
                "(id, organization_id, run_type, scenario_id, aggregate_scenario_ids, "
                "control_ids_used, mc_iterations, inputs_hash, status, created_by, "
                "created_at, updated_at, controls_snapshot) "
                "VALUES (:id, :org, :rt, NULL, :agg, :ctrl, :mc, :hash, :status, "
                ":created_by, datetime('now'), datetime('now'), :snap)"
            ),
            {
                "id": run_id,
                "org": org_id,
                "rt": "aggregate",
                "agg": '["sid_1", "sid_2"]',
                "ctrl": "[]",
                "mc": 10000,
                "hash": "abc",
                "status": "queued",
                "created_by": user_id,
                "snap": "[]",
            },
        )

    with pytest.raises(RuntimeError, match="AGGREGATE rows have scenario_id=NULL"):
        command.downgrade(alembic_config, _DOWN_REV)
