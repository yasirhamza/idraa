"""#tef-pert-revert: the #520 TEF content UPDATE (f2a9c4e1b8d3) reads the seed
JSON verbatim. The seed's TEF was reverted lognormal->PERT (Milestone A), so this
historical migration now overwrites a DIRTY (injected) threat_event_frequency
with the seed's PERT value. Dirty-then-run so the UPDATE's effect is exercised.
(The current head's PERT state is additionally covered by the revert migration
test, test_tef_pert_revert_migration.)"""

from __future__ import annotations

import json

import sqlalchemy as sa
from pytest_alembic import MigrationContext
from sqlalchemy.engine import Engine

_DOWN = "b7c3e9d15a24"
_HEAD = "f2a9c4e1b8d3"


def _tef(engine: Engine, slug: str) -> dict:
    with engine.connect() as conn:
        r = conn.execute(
            sa.text(
                "SELECT threat_event_frequency FROM scenario_library_entries "
                "WHERE slug = :s AND version = 1"
            ),
            {"s": slug},
        ).fetchone()
    return json.loads(r[0]) if isinstance(r[0], str) else r[0]


def test_tef_migration_lands_seed_pert(
    alembic_runner: MigrationContext, alembic_engine: Engine
) -> None:
    alembic_runner.migrate_up_to(_DOWN)
    dirty = json.dumps({"distribution": "PERT", "low": 0.9, "mode": 0.95, "high": 0.99})
    with alembic_engine.begin() as conn:
        conn.execute(
            sa.text(
                "UPDATE scenario_library_entries SET threat_event_frequency = :v "
                "WHERE slug = :s AND version = 1"
            ),
            {"v": dirty, "s": "ransomware-on-ehr"},
        )
    alembic_runner.migrate_up_one()
    tef = _tef(alembic_engine, "ransomware-on-ehr")
    assert tef["distribution"] == "PERT"
    assert (tef["low"], tef["mode"], tef["high"]) == (0.5, 1.5, 4.0)


def test_tef_migration_all_entries_pert(
    alembic_runner: MigrationContext, alembic_engine: Engine
) -> None:
    alembic_runner.migrate_up_to(_HEAD)
    with alembic_engine.connect() as conn:
        rows = conn.execute(
            sa.text("SELECT threat_event_frequency FROM scenario_library_entries WHERE version = 1")
        ).fetchall()
    for (v,) in rows:
        tef = json.loads(v) if isinstance(v, str) else v
        assert tef["distribution"] == "PERT", tef
        assert tef["low"] < tef["mode"] < tef["high"], tef
