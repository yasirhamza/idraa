"""pytest-alembic fixtures for PR iota migration tests.

Provides synchronous SQLite engine and alembic config fixtures required by
pytest-alembic's `alembic_runner` fixture. Uses a tmp_path-scoped SQLite file
so each test run gets an isolated DB.

The alembic config URL is sqlite+aiosqlite:// because the project's
alembic/env.py uses async_engine_from_config and requires an async driver.
The alembic_engine fixture, on the other hand, returns a SYNC engine for
test queries — pytest-alembic's runner uses env.py's async path; tests
use the sync engine to query the resulting DB. Both URLs point at the
same physical SQLite file.

pytest-alembic docs: https://pytest-alembic.readthedocs.io/en/latest/
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config


@pytest.fixture
def alembic_config(tmp_path: Path) -> Config:
    """Return an Alembic Config pointed at a per-test SQLite file.

    Uses sqlite+aiosqlite:// because env.py requires an async driver.
    pytest-alembic consumes this via the alembic_config fixture name.
    """
    db_file = tmp_path / f"migration_test_{id(tmp_path)}.db"
    project_root = Path(__file__).parent.parent.parent
    cfg = Config(str(project_root / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_file.as_posix()}")
    cfg.set_main_option(
        "script_location",
        str(project_root / "alembic"),
    )
    return cfg


@pytest.fixture
def alembic_engine(alembic_config: Config) -> sa.Engine:
    """Return a SYNCHRONOUS SQLAlchemy engine for pytest-alembic test queries.

    pytest-alembic's runner drives env.py (async); this fixture is for tests
    that need to query the resulting DB synchronously. The URL is the sync
    counterpart of the alembic_config URL — both point at the same SQLite
    file.
    """
    config_url = alembic_config.get_main_option("sqlalchemy.url")
    assert config_url is not None
    # Strip +aiosqlite for the sync engine; both drivers can read the same file.
    sync_url = config_url.replace("sqlite+aiosqlite://", "sqlite://")
    engine = sa.create_engine(sync_url)
    # Enable foreign keys for SQLite so CASCADE / RESTRICT constraints fire.
    with engine.connect() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = ON"))
    return engine


# Columns the CURRENT ORM writes on INSERT that a database frozen at an OLD revision
# does not have yet. A migration test that seeds rows through the real ORM after
# ``migrate_up_to(PRE)`` would otherwise die with "table X has no column named Y".
# One static DDL statement per column (no f-string SQL). ADD-only: append an entry
# whenever a new migration adds an ORM-written column.
_ORM_COLUMNS_ADDED_LATER: tuple[tuple[str, str, str], ...] = (
    # (table, column, ALTER statement)
    (
        "controls",
        "org_wide_meta",  # a7c2e9f14b63 (#299)
        "ALTER TABLE controls ADD COLUMN org_wide_meta BOOLEAN NOT NULL DEFAULT 0",
    ),
    (
        "risk_analysis_runs",
        "inherited_control_ids_per_scenario",  # a7c2e9f14b63 (#299)
        "ALTER TABLE risk_analysis_runs ADD COLUMN inherited_control_ids_per_scenario JSON",
    ),
)


def add_orm_columns_missing_at_old_revision(engine: sa.Engine) -> None:
    """Forward-compat shim for tests that seed through the REAL ORM at an old revision.

    Call it right after ``alembic_runner.migrate_up_to(PRE)`` and before the ORM seed.
    Idempotent. ONLY valid for a test that never migrates past the revision that adds
    the column (the migration itself would then collide on a duplicate column) -- every
    caller migrates up to its own REV, which precedes the columns listed above.
    """
    inspector = sa.inspect(engine)
    with engine.begin() as conn:
        for table, column, ddl in _ORM_COLUMNS_ADDED_LATER:
            if column not in {c["name"] for c in inspector.get_columns(table)}:
                conn.exec_driver_sql(ddl)
