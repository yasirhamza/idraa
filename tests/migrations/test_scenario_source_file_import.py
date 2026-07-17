"""P1: ScenarioSource.FILE_IMPORT is accepted by an Alembic-built DB.

The #303 lesson: a model that omits ``create_constraint`` can still face a
CHECK constraint emitted by the schema-creating migration, so the model alone
is never sufficient evidence for CHECK behaviour. ``scenarios.source`` was
created in ``1a3794c327d4`` with ``sa.Enum(..., native_enum=False)`` and NO
``create_constraint=`` — so we EXPECT no CHECK. This test confirms that on a
real head-migrated DB (not ``create_all``, which never emits the constraint
anyway): an INSERT with ``source='file_import'`` must be accepted.

If this test fails with ``CHECK constraint failed`` the zero-migration
assumption is wrong and a widening migration (mirror ``7e29245a1930``) is
required.

Matches the existing migration-test harness (see
``tests/migrations/test_library_uuid_format_fix.py`` +
``tests/migrations/conftest.py``): ``alembic_config`` (Config) +
``alembic_engine`` (SYNC engine) fixtures, ``command.upgrade(config, "head")``,
then query/insert via the sync engine.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine

from idraa.models.enums import ScenarioSource


def test_file_import_enum_value_exists() -> None:
    assert ScenarioSource.FILE_IMPORT.value == "file_import"


# Placeholder values for the columns the test sets explicitly. ``source`` is
# the column under test. The rest just need to satisfy NOT NULL / shape; this
# is NOT a semantically valid scenario. Any OTHER NOT NULL column the head
# schema requires (and that lacks a server default) is filled with a generic
# placeholder discovered via PRAGMA table_info, so the test stays durable as
# later migrations add/drop scenario columns.
_EXPLICIT_VALUES: dict[str, object] = {
    "id": uuid.uuid4().hex,
    "organization_id": uuid.uuid4().hex,
    "name": "probe",
    "scenario_type": "CUSTOM",
    "threat_category": "ransomware",
    "threat_event_frequency": '{"distribution":"PERT","low":1,"mode":2,"high":3}',
    "vulnerability": '{"distribution":"PERT","low":0.1,"mode":0.2,"high":0.3}',
    "primary_loss": '{"distribution":"PERT","low":1,"mode":2,"high":3}',
    "overlay_pins": "[]",
    "source": "file_import",  # <-- the value under test
    "status": "ACTIVE",
    "version": "1.0",
    "row_version": 1,
}


def test_scenarios_source_accepts_file_import_on_migrated_db(
    alembic_config: Config,
    alembic_engine: Engine,
) -> None:
    """Insert a scenario row with ``source='file_import'`` on a head-migrated DB.

    Minimal insert exercising the ``source`` CHECK surface. NOT NULL columns get
    placeholder values; the assertion is that ``source`` accepts the new value,
    not that this is a semantically valid scenario.
    """
    command.upgrade(alembic_config, "head")

    # Introspect the actual head schema so the insert satisfies every NOT NULL
    # column without a server default, regardless of which columns later
    # migrations added/dropped. (1a3794c327d4's iris_calibration_year/industry/
    # revenue_tier were later dropped; binding to the historical column set
    # would make this test brittle.)
    with alembic_engine.connect() as conn:
        cols = conn.execute(sa.text("PRAGMA table_info(scenarios)")).mappings().all()

    values: dict[str, object] = {}
    for col in cols:
        name = col["name"]
        if name in _EXPLICIT_VALUES:
            values[name] = _EXPLICIT_VALUES[name]
        elif col["notnull"] and col["dflt_value"] is None:
            # A required column the test doesn't model explicitly — give it a
            # type-agnostic placeholder. SQLite is permissive about typing, so a
            # string satisfies TEXT/JSON/NUMERIC NOT NULL columns alike.
            values[name] = "x"

    column_list = ", ".join(values)
    placeholder_list = ", ".join(f":{name}" for name in values)

    # FK enforcement is OFF for this probe insert: the test isolates the
    # ``source`` CHECK surface, NOT referential integrity. organization_id/
    # created_by FKs point at unseeded parent rows by design (inserting a full
    # valid organization graph would only obscure what this test asserts). A
    # CHECK constraint on ``source`` is NOT suppressed by foreign_keys=OFF, so
    # the assertion still proves the new enum value is accepted.
    with alembic_engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = OFF"))
        # S608 is suppressed below: column_list/placeholder_list are built from
        # PRAGMA-derived schema column names (trusted), not user input; the
        # actual values bind via parameters.
        conn.execute(
            sa.text(f"INSERT INTO scenarios ({column_list}) VALUES ({placeholder_list})"),  # noqa: S608
            values,
        )

    with alembic_engine.connect() as conn:
        count = conn.execute(
            sa.text("SELECT count(*) FROM scenarios WHERE source = 'file_import'")
        ).scalar_one()
    assert count == 1, (
        "scenario row with source='file_import' was not accepted on a "
        "head-migrated DB — the zero-migration assumption may be wrong"
    )
