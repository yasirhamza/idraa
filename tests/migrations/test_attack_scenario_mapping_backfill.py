"""Backfill migration tests (#475 follow-up): pre-#483 library-pinned
scenarios inherit their pinned entry-version's curated mappings as
source='library' rows; idempotent; user rows never clobbered.

Fixture rows deliberately mirror PROD shapes: uuid columns store 32-hex
(no hyphens — ORM Uuid binding), while library_pin JSON stores the
HYPHENATED str(uuid) that services/scenario_library.py writes.
"""

from __future__ import annotations

import importlib.util
import json
import uuid
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine

_PRE_REV = "c51975647c57"  # UPDATE to `uv run alembic heads` output at write time
_BACKFILL_REV = "291038b726fd"


def _seed_row(conn: sa.Connection, table: str, explicit: dict[str, object]) -> None:
    """Insert with NOT-NULL filler for unlisted columns (audit-F2 pattern)."""
    cols = conn.execute(sa.text(f"PRAGMA table_info({table})")).mappings().all()
    values: dict[str, object] = {}
    for col in cols:
        cname = col["name"]
        if cname in explicit:
            values[cname] = explicit[cname]
        elif col["notnull"] and col["dflt_value"] is None:
            values[cname] = "x"
    conn.execute(
        sa.text(
            f"INSERT INTO {table} ({', '.join(values)}) "  # noqa: S608
            f"VALUES ({', '.join(f':{c}' for c in values)})"
        ),
        values,
    )


def _seed_scenario(conn: sa.Connection, *, library_pin: str | None) -> str:
    sid = uuid.uuid4().hex
    _seed_row(
        conn,
        "scenarios",
        {
            "id": sid,
            "organization_id": uuid.uuid4().hex,
            "name": "probe",
            "scenario_type": "CUSTOM",
            "threat_category": "ransomware",
            "threat_event_frequency": '{"distribution":"PERT","low":1,"mode":2,"high":3}',
            "vulnerability": '{"distribution":"PERT","low":0.1,"mode":0.2,"high":0.3}',
            "primary_loss": '{"distribution":"PERT","low":1,"mode":2,"high":3}',
            "overlay_pins": "[]",
            "source": "expert_judgment",
            "status": "ACTIVE",
            "version": "1.0",
            "row_version": 1,
            "library_pin": library_pin,
            "created_at": "2026-06-01 00:00:00",
        },
    )
    return sid


def _seed_curated(conn: sa.Connection, *, entry_hex: str, version: int, n: int) -> list[str]:
    tech_ids = []
    for i in range(n):
        tid = uuid.uuid4().hex
        tech_ids.append(tid)
        _seed_row(
            conn,
            "library_entry_attack_mappings",
            {
                "id": uuid.uuid4().hex,
                "library_entry_id": entry_hex,
                "library_entry_version": version,
                "technique_id": tid,
                "rationale": f"curated rationale {i}",
                "provenance": "cited",
                "citations": '["https://attack.mitre.org/techniques/T1566/"]',
            },
        )
    return tech_ids


def _load_backfill():
    versions = Path(__file__).parent.parent.parent / "alembic" / "versions"
    (mig_path,) = versions.glob(f"{_BACKFILL_REV}_*.py")
    spec = importlib.util.spec_from_file_location("_attack_backfill_mig", mig_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_backfill_copies_curated_rows_and_skips_others(
    alembic_config: Config, alembic_engine: Engine
) -> None:
    command.upgrade(alembic_config, _PRE_REV)
    entry = uuid.uuid4()
    with alembic_engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = OFF"))
        tech_hexes = _seed_curated(conn, entry_hex=entry.hex, version=2, n=3)
        # Prod shape: pin holds the HYPHENATED str(uuid) + version.
        pin = json.dumps({"entry_id": str(entry), "version": 2})
        pinned = _seed_scenario(conn, library_pin=pin)
        # Pre-existing user-authored row for tech[0] on a second pinned scenario.
        pinned_with_user_row = _seed_scenario(conn, library_pin=pin)
        _seed_row(
            conn,
            "scenario_attack_mappings",
            {
                "id": uuid.uuid4().hex,
                "organization_id": uuid.uuid4().hex,
                "scenario_id": pinned_with_user_row,
                "technique_id": tech_hexes[0],
                "source": "user",
                "rationale": "hand-authored",
            },
        )
        unpinned = _seed_scenario(conn, library_pin="null")  # prod JSON-null shape
        malformed = _seed_scenario(
            conn, library_pin=json.dumps({"entry_id": "not-a-uuid", "version": 1})
        )
        wrong_version = _seed_scenario(
            conn, library_pin=json.dumps({"entry_id": str(entry), "version": 1})
        )

    command.upgrade(alembic_config, _BACKFILL_REV)

    with alembic_engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT scenario_id, technique_id, source, rationale FROM scenario_attack_mappings"
            )
        ).all()
    by_scenario: dict[str, list] = {}
    for r in rows:
        by_scenario.setdefault(str(r[0]), []).append(r)

    # Pinned: all 3 curated rows copied (adapter-iteration rule, N >= 3).
    assert len(by_scenario[pinned]) == 3
    assert {r[2] for r in by_scenario[pinned]} == {"library"}
    assert {r[3] for r in by_scenario[pinned]} == {f"curated rationale {i}" for i in range(3)}
    # Existing user row untouched, remaining 2 backfilled -> 3 total, no dupe.
    with_user = by_scenario[pinned_with_user_row]
    assert len(with_user) == 3
    assert sorted(r[2] for r in with_user) == ["library", "library", "user"]
    user_row = next(r for r in with_user if r[2] == "user")
    assert user_row[3] == "hand-authored"
    # Unpinned / malformed / wrong-version: no rows.
    for sid in (unpinned, malformed, wrong_version):
        assert sid not in by_scenario


def test_backfill_is_idempotent_and_downgrade_is_noop(
    alembic_config: Config, alembic_engine: Engine
) -> None:
    command.upgrade(alembic_config, _PRE_REV)
    entry = uuid.uuid4()
    with alembic_engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = OFF"))
        _seed_curated(conn, entry_hex=entry.hex, version=1, n=3)
        _seed_scenario(conn, library_pin=json.dumps({"entry_id": str(entry), "version": 1}))

    command.upgrade(alembic_config, _BACKFILL_REV)
    mod = _load_backfill()
    with alembic_engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = OFF"))
        assert mod._backfill(conn) == 0  # re-run inserts nothing

    command.downgrade(alembic_config, _PRE_REV)
    with alembic_engine.connect() as conn:
        n = conn.execute(sa.text("SELECT COUNT(*) FROM scenario_attack_mappings")).scalar()
    assert n == 3  # documented no-op downgrade leaves backfilled rows
