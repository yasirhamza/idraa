"""Migration test for the scenario_inputs_snapshot column (#351 T2).

Guards:
1. Round-trip: after upgrade to 3011adc6a115, the column exists and a NULL
   value is tolerated (legacy-null contract).
2. A non-NULL JSON value round-trips correctly (insert → select → equal).
3. Downgrade removes the column cleanly.
"""

from __future__ import annotations

import json
import uuid

import sqlalchemy as sa
from pytest_alembic import MigrationContext


def test_scenario_inputs_snapshot_column_exists_after_upgrade(
    alembic_runner: MigrationContext,
    alembic_engine: sa.Engine,
) -> None:
    """After upgrading to 3011adc6a115, the column is present in the table."""
    alembic_runner.migrate_up_to("3011adc6a115")
    with alembic_engine.connect() as conn:
        result = conn.execute(sa.text("PRAGMA table_info(risk_analysis_runs)"))
        col_names = [row[1] for row in result.fetchall()]
    assert "scenario_inputs_snapshot" in col_names


def _insert_run_no_fk(
    conn: sa.Connection,
    *,
    run_id: str,
    org_id: str,
    snapshot_value: str | None,
) -> None:
    """Insert a minimal run row with FK constraints disabled.

    Only the column-level NULL/JSON constraint on scenario_inputs_snapshot
    is under test; FK chains (organizations, users) are not the test subject.
    Using PRAGMA foreign_keys = OFF is standard practice in migration tests
    that verify column additions rather than relational integrity.
    """
    conn.execute(sa.text("PRAGMA foreign_keys = OFF"))
    conn.execute(
        sa.text(
            """
            INSERT INTO risk_analysis_runs
              (id, organization_id, run_type, status, mc_iterations,
               inputs_hash, controls_snapshot, control_ids_used,
               created_at, updated_at, scenario_inputs_snapshot)
            VALUES
              (:id, :org_id, 'single', 'completed', 10000,
               'abc123', '[]', '[]',
               '2026-06-11T00:00:00', '2026-06-11T00:00:00', :snap)
            """
        ),
        {"id": run_id, "org_id": org_id, "snap": snapshot_value},
    )
    conn.commit()
    conn.execute(sa.text("PRAGMA foreign_keys = ON"))


def test_scenario_inputs_snapshot_legacy_null_tolerated(
    alembic_runner: MigrationContext,
    alembic_engine: sa.Engine,
) -> None:
    """A row with NULL scenario_inputs_snapshot is accepted (backward compat)."""
    alembic_runner.migrate_up_to("3011adc6a115")
    run_id = uuid.uuid4().hex  # no-hyphen format per project UUID convention
    org_id = uuid.uuid4().hex
    with alembic_engine.connect() as conn:
        _insert_run_no_fk(conn, run_id=run_id, org_id=org_id, snapshot_value=None)
        result = conn.execute(
            sa.text("SELECT scenario_inputs_snapshot FROM risk_analysis_runs WHERE id = :id"),
            {"id": run_id},
        )
        row = result.fetchone()
    assert row is not None
    assert row[0] is None  # NULL tolerated


def test_scenario_inputs_snapshot_json_round_trip(
    alembic_runner: MigrationContext,
    alembic_engine: sa.Engine,
) -> None:
    """A JSON value in scenario_inputs_snapshot round-trips correctly."""
    alembic_runner.migrate_up_to("3011adc6a115")
    run_id = uuid.uuid4().hex
    org_id = uuid.uuid4().hex
    snapshot = {
        "scenarios": [
            {
                "scenario_id": "s1",
                "scenario_name": "Test",
                "threat_event_frequency": {
                    "distribution": "PERT",
                    "low": 0.1,
                    "mode": 0.5,
                    "high": 1.0,
                },
                "vulnerability": {"distribution": "PERT", "low": 0.2, "mode": 0.4, "high": 0.6},
                "primary_loss": {
                    "distribution": "PERT",
                    "low": 10000.0,
                    "mode": 100000.0,
                    "high": 1000000.0,
                },
                "secondary_loss": None,
            }
        ]
    }
    snapshot_json = json.dumps(snapshot)
    with alembic_engine.connect() as conn:
        _insert_run_no_fk(conn, run_id=run_id, org_id=org_id, snapshot_value=snapshot_json)
        result = conn.execute(
            sa.text("SELECT scenario_inputs_snapshot FROM risk_analysis_runs WHERE id = :id"),
            {"id": run_id},
        )
        row = result.fetchone()
    assert row is not None
    # SQLite may return the JSON as a string or dict depending on dialect
    stored = row[0]
    if isinstance(stored, str):
        stored = json.loads(stored)
    assert stored["scenarios"][0]["scenario_name"] == "Test"
    assert stored["scenarios"][0]["threat_event_frequency"]["distribution"] == "PERT"


def test_scenario_inputs_snapshot_downgrade_removes_column(
    alembic_runner: MigrationContext,
    alembic_engine: sa.Engine,
) -> None:
    """After downgrading from 3011adc6a115 to 60ff242180f6, the column is gone."""
    alembic_runner.migrate_up_to("3011adc6a115")
    alembic_runner.migrate_down_to("60ff242180f6")
    with alembic_engine.connect() as conn:
        result = conn.execute(sa.text("PRAGMA table_info(risk_analysis_runs)"))
        col_names = [row[1] for row in result.fetchall()]
    assert "scenario_inputs_snapshot" not in col_names
