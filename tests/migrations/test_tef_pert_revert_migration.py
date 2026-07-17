"""#tef-pert-revert: the TEF lognormal->PERT content UPDATE overwrites a DIRTY
(injected lognormal) threat_event_frequency with the reverted PERT seed value."""

from __future__ import annotations

import json

import sqlalchemy as sa
from pytest_alembic import MigrationContext
from sqlalchemy.engine import Engine

_DOWN = "f2a9c4e1b8d3"
_HEAD = "c206f115c610"


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


def test_tef_pert_revert_migration_converts_to_pert(
    alembic_runner: MigrationContext, alembic_engine: Engine
) -> None:
    alembic_runner.migrate_up_to(_DOWN)
    dirty = json.dumps({"distribution": "lognormal", "mean": 9.9, "sigma": 9.9})
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


def test_tef_pert_revert_migration_all_entries_pert(
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
        # catch a malformed-seed regression at the DB layer, not just the shape key
        assert tef["low"] < tef["mode"] < tef["high"], tef
