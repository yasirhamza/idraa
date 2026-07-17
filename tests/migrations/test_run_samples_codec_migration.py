"""Migration test for the arrays_codec + nullable-arrays run_samples change.

The critical property this migration must preserve: SQLite forces a batch
(copy-and-swap) table recreate to make ``arrays`` nullable, and that recreate
is a known foot-gun for silently dropping FK ``ondelete`` clauses. This test
proves, at the DATABASE level (raw SQL DELETE on the parent, with
``PRAGMA foreign_keys = ON``), that the ``run_id -> risk_analysis_runs.id``
ON DELETE CASCADE FK still fires post-migration — the load-bearing behaviour
retention (#297) depends on.

Uses pytest-alembic's ``alembic_runner``/``alembic_engine`` fixtures (see
tests/migrations/conftest.py), matching the established raw-SQL migration
test convention (tests/migrations/test_pr_iota_control_reshape.py). UUIDs are
inserted as ``.hex`` (no-hyphen), matching SQLAlchemy's ``Uuid(as_uuid=True)``
on-disk representation on SQLite -- see the "Raw-text seed UUID foot-gun"
project convention.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine

# Revision IDs
_DOWN_REV = "e1f2a3b4c5d6"  # pre-Task-2 head (library_entry loss_form_profile)
_OUR_REV = "596309a1dc46"  # this migration: run_samples arrays_codec + nullable arrays


def _insert_org(conn: sa.Connection) -> str:
    org_id = uuid.uuid4().hex
    conn.execute(
        sa.text(
            "INSERT INTO organizations "
            "(id, name, organization_size, industry_type, security_maturity, "
            "has_cyber_insurance, risk_appetite, compliance_requirements, "
            "regulatory_environment, technology_stack, geographic_regions, "
            "preferred_currency, preferred_language, "
            "created_at, updated_at) "
            "VALUES (:id, 'TestOrg', 'MEDIUM', 'manufacturing', 'BASIC', "
            "0, 'MODERATE', '[]', '[]', '[]', '[]', "
            "'USD', 'en', "
            "(CURRENT_TIMESTAMP), (CURRENT_TIMESTAMP))"
        ),
        {"id": org_id},
    )
    return org_id


def _insert_run(conn: sa.Connection, *, org_id: str) -> str:
    run_id = uuid.uuid4().hex
    conn.execute(
        sa.text(
            "INSERT INTO risk_analysis_runs "
            "(id, organization_id, run_type, status, mc_iterations, inputs_hash, "
            "controls_snapshot, control_ids_used, created_at, updated_at) "
            "VALUES (:id, :org_id, 'single', 'completed', 1000, :hash, "
            "'[]', '[]', (CURRENT_TIMESTAMP), (CURRENT_TIMESTAMP))"
        ),
        {"id": run_id, "org_id": org_id, "hash": "0" * 64},
    )
    return run_id


def test_fk_ondelete_actions_preserved_post_migration(
    alembic_config: Config,
    alembic_engine: Engine,
) -> None:
    """PRAGMA foreign_key_list reports the same ondelete actions after the
    batch recreate as before it: run_id->CASCADE, organization_id->RESTRICT.
    """
    command.upgrade(alembic_config, _OUR_REV)

    with alembic_engine.begin() as conn:
        fks = conn.execute(sa.text("PRAGMA foreign_key_list(run_samples)")).fetchall()

    by_table = {row[2]: row for row in fks}  # row[2] == referenced table
    assert by_table["risk_analysis_runs"][6] == "CASCADE"  # row[6] == on_delete
    assert by_table["organizations"][6] == "RESTRICT"


def test_org_index_preserved_post_migration(
    alembic_config: Config,
    alembic_engine: Engine,
) -> None:
    command.upgrade(alembic_config, _OUR_REV)

    with alembic_engine.begin() as conn:
        idx_names = {row[1] for row in conn.execute(sa.text("PRAGMA index_list(run_samples)"))}
    assert "ix_run_samples_organization_id" in idx_names


def test_arrays_codec_column_added_and_arrays_nullable(
    alembic_config: Config,
    alembic_engine: Engine,
) -> None:
    command.upgrade(alembic_config, _OUR_REV)

    with alembic_engine.begin() as conn:
        cols = {row[1]: row for row in conn.execute(sa.text("PRAGMA table_info(run_samples)"))}
    assert "arrays_codec" in cols
    assert cols["arrays_codec"][2].upper() == "BLOB"
    assert cols["arrays_codec"][3] == 0  # notnull == 0 (nullable)
    assert cols["arrays"][3] == 0  # notnull == 0 (now nullable)


def test_parent_delete_cascades_samples_after_migration(
    alembic_config: Config,
    alembic_engine: Engine,
) -> None:
    """DB-level proof: deleting the parent risk_analysis_runs row still
    cascades the run_samples row post-migration (retention #297).

    Requires PRAGMA foreign_keys = ON per-connection -- SQLite silently
    ignores ON DELETE CASCADE without it.
    """
    command.upgrade(alembic_config, _OUR_REV)

    with alembic_engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = ON"))
        org_id = _insert_org(conn)
        run_id = _insert_run(conn, org_id=org_id)
        conn.execute(
            sa.text(
                "INSERT INTO run_samples "
                "(run_id, organization_id, arrays, created_at) "
                "VALUES (:run_id, :org_id, :arrays, (CURRENT_TIMESTAMP))"
            ),
            {"run_id": run_id, "org_id": org_id, "arrays": '{"base_risk": [1.0, 2.0]}'},
        )

    with alembic_engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = ON"))
        before = conn.execute(
            sa.text("SELECT COUNT(*) FROM run_samples WHERE run_id = :id"), {"id": run_id}
        ).scalar()
        assert before == 1

        conn.execute(sa.text("DELETE FROM risk_analysis_runs WHERE id = :id"), {"id": run_id})

        after = conn.execute(
            sa.text("SELECT COUNT(*) FROM run_samples WHERE run_id = :id"), {"id": run_id}
        ).scalar()
        assert after == 0


def test_org_restrict_blocks_delete_with_samples_after_migration(
    alembic_config: Config,
    alembic_engine: Engine,
) -> None:
    """DB-level proof: ON DELETE RESTRICT on organization_id still blocks
    deleting an org that has run_samples rows post-migration.
    """
    command.upgrade(alembic_config, _OUR_REV)

    with alembic_engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = ON"))
        org_id = _insert_org(conn)
        run_id = _insert_run(conn, org_id=org_id)
        conn.execute(
            sa.text(
                "INSERT INTO run_samples "
                "(run_id, organization_id, arrays, created_at) "
                "VALUES (:run_id, :org_id, :arrays, (CURRENT_TIMESTAMP))"
            ),
            {"run_id": run_id, "org_id": org_id, "arrays": '{"base_risk": [1.0]}'},
        )

    with alembic_engine.connect() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = ON"))
        try:
            conn.execute(sa.text("DELETE FROM organizations WHERE id = :id"), {"id": org_id})
        except sa.exc.IntegrityError:
            conn.rollback()
        else:
            raise AssertionError(
                "expected ON DELETE RESTRICT to block deleting an org with run_samples rows"
            )


def test_downgrade_restores_arrays_not_null_and_drops_codec(
    alembic_config: Config,
    alembic_engine: Engine,
) -> None:
    command.upgrade(alembic_config, _OUR_REV)
    command.downgrade(alembic_config, _DOWN_REV)

    with alembic_engine.begin() as conn:
        cols = {row[1]: row for row in conn.execute(sa.text("PRAGMA table_info(run_samples)"))}
    assert "arrays_codec" not in cols
    assert cols["arrays"][3] == 1  # notnull == 1 (restored NOT NULL)


def test_round_trip_idempotent(
    alembic_config: Config,
    alembic_engine: Engine,
) -> None:
    command.upgrade(alembic_config, _DOWN_REV)
    command.upgrade(alembic_config, _OUR_REV)
    command.downgrade(alembic_config, _DOWN_REV)
    command.upgrade(alembic_config, _OUR_REV)

    with alembic_engine.begin() as conn:
        cols = {row[1] for row in conn.execute(sa.text("PRAGMA table_info(run_samples)"))}
        fks = conn.execute(sa.text("PRAGMA foreign_key_list(run_samples)")).fetchall()
    assert "arrays_codec" in cols
    assert len(fks) == 2
