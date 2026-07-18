"""Migration test for register import staging state + binding profiles
(epic #34 P1c Task 2, revision 32affcd5ec64).

Guards:
1. Upgrade adds ``csv_import_preview.state_json`` (nullable JSON).
2. Upgrade creates ``register_binding_profiles`` with all expected columns.
3. A row round-trips through ``register_binding_profiles`` (JSON columns
   included) and honors the (organization_id, name) unique constraint.
4. Downgrade drops BOTH the new column and the new table.
"""

from __future__ import annotations

import json
import uuid

import sqlalchemy as sa
from pytest_alembic import MigrationContext


def _table_columns(engine: sa.Engine, table: str) -> list[str]:
    with engine.connect() as conn:
        result = conn.execute(sa.text(f"PRAGMA table_info({table})"))
        return [row[1] for row in result.fetchall()]


def _table_exists(engine: sa.Engine, table: str) -> bool:
    with engine.connect() as conn:
        result = conn.execute(
            sa.text("SELECT name FROM sqlite_master WHERE type='table' AND name=:name"),
            {"name": table},
        )
        return result.fetchone() is not None


def test_state_json_column_exists_after_upgrade(
    alembic_runner: MigrationContext,
    alembic_engine: sa.Engine,
) -> None:
    """After upgrading to 32affcd5ec64, csv_import_preview.state_json exists."""
    alembic_runner.migrate_up_to("32affcd5ec64")
    assert "state_json" in _table_columns(alembic_engine, "csv_import_preview")


def test_register_binding_profiles_table_exists_after_upgrade(
    alembic_runner: MigrationContext,
    alembic_engine: sa.Engine,
) -> None:
    """After upgrading to 32affcd5ec64, register_binding_profiles exists with
    every expected column."""
    alembic_runner.migrate_up_to("32affcd5ec64")
    assert _table_exists(alembic_engine, "register_binding_profiles")
    cols = set(_table_columns(alembic_engine, "register_binding_profiles"))
    assert cols == {
        "id",
        "created_at",
        "updated_at",
        "organization_id",
        "name",
        "column_map",
        "value_bindings",
        "mapping_versions_snapshot",
        "created_by",
    }


def _insert_org_no_fk(conn: sa.Connection, *, org_id: str) -> None:
    conn.execute(sa.text("PRAGMA foreign_keys = OFF"))
    conn.execute(
        sa.text(
            """
            INSERT INTO organizations
              (id, name, industry_type, organization_size, geographic_regions,
               security_maturity, has_cyber_insurance, risk_appetite,
               compliance_requirements, regulatory_environment, technology_stack,
               preferred_currency, preferred_language, created_at, updated_at)
            VALUES
              (:id, 'Test Org', 'manufacturing', 'medium', '[]',
               'basic', 0, 'moderate',
               '[]', '[]', '[]',
               'USD', 'en', '2026-07-18T00:00:00', '2026-07-18T00:00:00')
            """
        ),
        {"id": org_id},
    )
    conn.commit()
    conn.execute(sa.text("PRAGMA foreign_keys = ON"))


def test_register_binding_profile_json_round_trip_and_unique_constraint(
    alembic_runner: MigrationContext,
    alembic_engine: sa.Engine,
) -> None:
    """A row round-trips its JSON columns; the (organization_id, name)
    UNIQUE constraint rejects a duplicate name within the same org."""
    alembic_runner.migrate_up_to("32affcd5ec64")
    org_id = uuid.uuid4().hex
    profile_id = uuid.uuid4().hex
    column_map = {"Threat": "title", "Impact": "impact"}

    with alembic_engine.connect() as conn:
        _insert_org_no_fk(conn, org_id=org_id)
        conn.execute(sa.text("PRAGMA foreign_keys = OFF"))
        conn.execute(
            sa.text(
                """
                INSERT INTO register_binding_profiles
                  (id, organization_id, name, column_map, value_bindings,
                   mapping_versions_snapshot, created_by, created_at, updated_at)
                VALUES
                  (:id, :org_id, 'Quarterly export', :column_map, '{}', '{}',
                   NULL, '2026-07-18T00:00:00', '2026-07-18T00:00:00')
                """
            ),
            {"id": profile_id, "org_id": org_id, "column_map": json.dumps(column_map)},
        )
        conn.commit()

        row = conn.execute(
            sa.text("SELECT column_map FROM register_binding_profiles WHERE id = :id"),
            {"id": profile_id},
        ).fetchone()
        assert row is not None
        stored = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        assert stored == column_map

        # Duplicate (organization_id, name) must be rejected.
        try:
            conn.execute(
                sa.text(
                    """
                    INSERT INTO register_binding_profiles
                      (id, organization_id, name, column_map, value_bindings,
                       mapping_versions_snapshot, created_by, created_at, updated_at)
                    VALUES
                      (:id, :org_id, 'Quarterly export', '{}', '{}', '{}',
                       NULL, '2026-07-18T00:00:00', '2026-07-18T00:00:00')
                    """
                ),
                {"id": uuid.uuid4().hex, "org_id": org_id},
            )
            conn.commit()
            raised = False
        except sa.exc.IntegrityError:
            conn.rollback()
            raised = True
        assert raised, "duplicate (organization_id, name) should violate the UNIQUE constraint"


def test_downgrade_drops_state_json_and_register_binding_profiles(
    alembic_runner: MigrationContext,
    alembic_engine: sa.Engine,
) -> None:
    """Downgrading past 32affcd5ec64 removes both the column and the table."""
    alembic_runner.migrate_up_to("32affcd5ec64")
    alembic_runner.migrate_down_to("e6882513a026")
    assert "state_json" not in _table_columns(alembic_engine, "csv_import_preview")
    assert not _table_exists(alembic_engine, "register_binding_profiles")
