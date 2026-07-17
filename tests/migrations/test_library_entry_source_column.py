"""P3: scenario_library_entries.source column — added, backfills 'seed', accepts 'imported'.

#303 discipline: confirm on an Alembic-built DB (not create_all) that no CHECK
rejects 'imported'. The column is ``native_enum=False`` with NO
``create_constraint`` (mirrors ``scenarios.source`` in ``1a3794c327d4``), so no
CHECK is expected — this test proves it on a head-migrated DB. If the
'imported' insert fails with ``CHECK constraint failed`` the zero-CHECK
assumption is wrong and a widening migration is required.

Matches the migration-test harness (see ``tests/migrations/conftest.py`` +
``tests/migrations/test_scenario_source_file_import.py``): ``alembic_config``
(Config) + ``alembic_engine`` (SYNC engine) fixtures,
``command.upgrade(config, "head")``, then query/insert via the sync engine. The
INSERT column list is discovered via ``PRAGMA table_info`` rather than
hardcoded so the test stays durable as later migrations add/drop entry columns.

The #303 raw-UUID foot-gun: every UUID in raw SQL is ``uuid.uuid4().hex``
(32-char no-hyphen) so it matches the ORM's ``Uuid(as_uuid=True)`` binding.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine

# Values the test sets explicitly. ``source`` is the column under test. The rest
# satisfy NOT NULL / shape; this is NOT a semantically valid library entry. Any
# OTHER NOT NULL column the head schema requires (and that lacks a server
# default) is filled with a generic placeholder discovered via PRAGMA
# table_info, so the test stays durable as later migrations add/drop columns.
_EXPLICIT_VALUES: dict[str, object] = {
    "id": uuid.uuid4().hex,
    "version": 1,
    "slug": f"probe-{uuid.uuid4().hex[:8]}",
    "name": "probe",
    "status": "published",
    "threat_event_type": "ransomware",
    "threat_actor_type": "cybercriminals",
    "asset_class": "systems",
    "tags": "[]",
    "description": "probe description over twenty chars",
    "source_citations": "[]",
    "canonical_fair_gap": "probe canonical fair gap over twenty chars",
    "threat_event_frequency": '{"distribution":"PERT","low":1,"mode":2,"high":3}',
    "vulnerability": '{"distribution":"PERT","low":0.1,"mode":0.2,"high":0.3}',
    "primary_loss": '{"distribution":"PERT","low":1,"mode":2,"high":3}',
    "suggested_control_ids": "[]",
    "calibration_anchor": '{"industry":"other","revenue_tier":"100m_to_1b"}',
    "source": "imported",  # <-- the value under test
    "row_version": 1,
    "created_at": "2026-06-03T00:00:00+00:00",
    "updated_at": "2026-06-03T00:00:00+00:00",
}


def test_source_column_backfills_seed_and_accepts_imported(
    alembic_config: Config,
    alembic_engine: Engine,
) -> None:
    """source backfills seeded rows to 'seed' and accepts an 'imported' insert.

    (a) The 44 migration-seeded entries (31 base + 13 extension) backfill to
        'seed' via the column's ``server_default``.
    (b) An INSERT with ``source='imported'`` is NOT rejected by any CHECK.
    """
    command.upgrade(alembic_config, "head")

    # (a) Existing seeded rows backfilled to 'seed'.
    with alembic_engine.connect() as conn:
        seed_count = conn.execute(
            sa.text("SELECT count(*) FROM scenario_library_entries WHERE source = 'seed'")
        ).scalar_one()
    assert seed_count >= 44, (
        f"expected >=44 migration-seeded rows backfilled to source='seed', got {seed_count}"
    )

    # Introspect the actual head schema so the insert satisfies every NOT NULL
    # column without a server default, regardless of which columns later
    # migrations add/drop.
    with alembic_engine.connect() as conn:
        cols = conn.execute(sa.text("PRAGMA table_info(scenario_library_entries)")).mappings().all()

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

    # (b) The 'imported' insert must be accepted (no CHECK rejection). FK
    # enforcement is OFF: this probe isolates the ``source`` CHECK surface, not
    # referential integrity. A CHECK on ``source`` is NOT suppressed by
    # foreign_keys=OFF, so the assertion still proves the new value is accepted.
    with alembic_engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = OFF"))
        # S608: column_list/placeholder_list are built from PRAGMA-derived
        # schema column names (trusted), not user input; values bind via params.
        conn.execute(
            sa.text(
                f"INSERT INTO scenario_library_entries ({column_list}) "  # noqa: S608
                f"VALUES ({placeholder_list})"
            ),
            values,
        )

    with alembic_engine.connect() as conn:
        imported = conn.execute(
            sa.text("SELECT count(*) FROM scenario_library_entries WHERE source = 'imported'")
        ).scalar_one()
    assert imported == 1, (
        "library entry with source='imported' was not accepted on a head-migrated "
        "DB — the zero-CHECK assumption may be wrong (a widening migration is needed)"
    )
