"""Migration roundtrip tests for issue #89 (aggregate_control_ids_per_scenario column).

Uses the shared alembic_config fixture from tests/migrations/conftest.py. Pattern
mirrors test_pr_xi_migration.py.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine

_DOWN_REV = "f8b3c19d4a02"  # immediate predecessor (org loss_tolerance)
_OUR_REV = "2b8317b19290"  # issue #89: aggregate_control_ids_per_scenario column


def test_issue_89_migration_adds_column(
    alembic_config: Config,
    alembic_engine: Engine,
) -> None:
    """upgrade _OUR_REV adds aggregate_control_ids_per_scenario as nullable JSON."""
    command.upgrade(alembic_config, _OUR_REV)

    inspector = sa.inspect(alembic_engine)
    cols = {c["name"]: c for c in inspector.get_columns("risk_analysis_runs")}
    assert "aggregate_control_ids_per_scenario" in cols
    assert cols["aggregate_control_ids_per_scenario"]["nullable"] is True


def test_issue_89_migration_roundtrip(
    alembic_config: Config,
    alembic_engine: Engine,
) -> None:
    """downgrade then upgrade succeeds; column is dropped on downgrade."""
    command.upgrade(alembic_config, _OUR_REV)
    command.downgrade(alembic_config, _DOWN_REV)

    inspector = sa.inspect(alembic_engine)
    cols = {c["name"] for c in inspector.get_columns("risk_analysis_runs")}
    assert "aggregate_control_ids_per_scenario" not in cols

    command.upgrade(alembic_config, _OUR_REV)
    inspector = sa.inspect(alembic_engine)
    cols2 = {c["name"] for c in inspector.get_columns("risk_analysis_runs")}
    assert "aggregate_control_ids_per_scenario" in cols2
