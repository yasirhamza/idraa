"""Seed-migration tests for e6882513a026_qualitative_mapping_bands (epic #34 P1b).

Mirrors ``tests/migrations/test_audit_f2_vuln_framing.py`` /
``test_library_entry_source_column.py`` conventions: ``alembic_config`` +
``alembic_engine`` fixtures, ``command.upgrade``/``command.downgrade`` against
an isolated SQLite file, PRAGMA-introspected column lists.

The ORM read-back (not just a raw SQL row count) guards the recurring #303
raw-UUID foot-gun: a migration that bound ``str(uuid.uuid4())`` (36-char,
hyphenated) instead of ``.hex`` (32-char) would insert rows the ORM's
``Uuid(as_uuid=True)`` column can silently never look up by id — a raw
``SELECT count(*)`` would still pass while every ORM-side query 404s.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

_PRE_REV = "b7d3f1a9c4e2"  # immediately before this migration
_REV = "e6882513a026"


def test_upgrade_seeds_ten_bands_five_per_kind(
    alembic_config: Config, alembic_engine: Engine
) -> None:
    command.upgrade(alembic_config, _REV)
    with alembic_engine.connect() as conn:
        total = conn.execute(sa.text("SELECT count(*) FROM qualitative_mapping_bands")).scalar_one()
        freq = conn.execute(
            sa.text("SELECT count(*) FROM qualitative_mapping_bands WHERE kind = 'frequency'")
        ).scalar_one()
        mag = conn.execute(
            sa.text("SELECT count(*) FROM qualitative_mapping_bands WHERE kind = 'magnitude'")
        ).scalar_one()
    assert total == 10
    assert freq == 5
    assert mag == 5


def test_orm_read_back_by_id_guards_hex_uuid_format(
    alembic_config: Config, alembic_engine: Engine
) -> None:
    """A seeded row must be look-up-able through the ORM's Uuid(as_uuid=True)
    column — proves the migration bound .hex (32-char, no hyphen) ids, not
    str(uuid.uuid4()) (36-char, hyphenated) which the ORM type cannot bind."""
    command.upgrade(alembic_config, _REV)
    from idraa.models.qualitative_mapping import QualitativeMappingBand

    with alembic_engine.connect() as conn:
        raw_id = conn.execute(
            sa.text(
                "SELECT id FROM qualitative_mapping_bands WHERE kind = 'frequency' "
                "AND label = 'moderate'"
            )
        ).scalar_one()

    # The .hex id came back from raw SQL as a plain string; the ORM's
    # Uuid(as_uuid=True) column binds a uuid.UUID instance, not a str — this
    # conversion succeeding (vs. raising ValueError on a hyphenated 36-char
    # string in the wrong shape) is itself part of the guard.
    with Session(alembic_engine) as session:
        got = session.get(QualitativeMappingBand, uuid.UUID(raw_id))
        assert got is not None, "ORM read-back by id failed — check for the raw-UUID foot-gun"
        assert got.kind == "frequency"
        assert got.label == "moderate"
        assert got.mode == 3.2


def test_qualitative_mapping_org_bands_table_empty_on_seed(
    alembic_config: Config, alembic_engine: Engine
) -> None:
    """The org-override table ships empty — no seed rows, admin CRUD only."""
    command.upgrade(alembic_config, _REV)
    with alembic_engine.connect() as conn:
        count = conn.execute(
            sa.text("SELECT count(*) FROM qualitative_mapping_org_bands")
        ).scalar_one()
    assert count == 0


def test_source_column_widened_to_27_and_narrowed_on_downgrade(
    alembic_config: Config, alembic_engine: Engine
) -> None:
    """Spec-T1-I: `qualitative_register_import` (27 chars) overflows the
    scenarios.source DDL created VARCHAR(15) in 1a3794c327d4 — SQLite
    tolerates, Postgres would reject inserts. The migration widens it
    dialect-aware (08358cf073b8 precedent); downgrade narrows back (with a
    truncation guard, not exercised here — the test DB has no scenarios)."""
    command.upgrade(alembic_config, _REV)
    with alembic_engine.connect() as conn:
        cols = {
            r["name"]: r["type"]
            for r in conn.execute(sa.text("PRAGMA table_info(scenarios)")).mappings()
        }
    assert cols["source"] == "VARCHAR(27)"

    command.downgrade(alembic_config, _PRE_REV)
    with alembic_engine.connect() as conn:
        cols = {
            r["name"]: r["type"]
            for r in conn.execute(sa.text("PRAGMA table_info(scenarios)")).mappings()
        }
    assert cols["source"] == "VARCHAR(15)"

    command.upgrade(alembic_config, _REV)


def test_downgrade_drops_both_tables_and_scenarios_column(
    alembic_config: Config, alembic_engine: Engine
) -> None:
    command.upgrade(alembic_config, _REV)
    with alembic_engine.connect() as conn:
        tables = {
            r[0]
            for r in conn.execute(
                sa.text("SELECT name FROM sqlite_master WHERE type = 'table'")
            ).fetchall()
        }
        assert "qualitative_mapping_bands" in tables
        assert "qualitative_mapping_org_bands" in tables
        cols = [r["name"] for r in conn.execute(sa.text("PRAGMA table_info(scenarios)")).mappings()]
        assert "conversion_metadata" in cols

    command.downgrade(alembic_config, _PRE_REV)
    with alembic_engine.connect() as conn:
        tables = {
            r[0]
            for r in conn.execute(
                sa.text("SELECT name FROM sqlite_master WHERE type = 'table'")
            ).fetchall()
        }
        assert "qualitative_mapping_bands" not in tables
        assert "qualitative_mapping_org_bands" not in tables
        cols = [r["name"] for r in conn.execute(sa.text("PRAGMA table_info(scenarios)")).mappings()]
        assert "conversion_metadata" not in cols

    # Re-upgrade to prove the migration is idempotent-safe for the next test run.
    command.upgrade(alembic_config, _REV)
