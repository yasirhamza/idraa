"""Audit-F2 migration tests: scenarios.vuln_framing column + backfill
(b7d2e8a1c5f3).

Backfill cutoff: created_at < 2026-06-10 09:30:00 (the #339 cutover — Fly
release v101, startup complete 09:28:15Z) -> 'legacy_residual'; later rows
keep the 'inherent' server default. Downgrade drops the column.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine

_PRE_REV = "e3a1c4f7b2d9"  # F1 (immediately before F2)
_F2_REV = "b7d2e8a1c5f3"


def _seed_scenario(conn: sa.Connection, *, created_at: str) -> str:
    explicit: dict[str, object] = {
        "id": uuid.uuid4().hex,
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
        "created_at": created_at,
    }
    cols = conn.execute(sa.text("PRAGMA table_info(scenarios)")).mappings().all()
    values: dict[str, object] = {}
    for col in cols:
        cname = col["name"]
        if cname in explicit:
            values[cname] = explicit[cname]
        elif col["notnull"] and col["dflt_value"] is None:
            values[cname] = "x"
    conn.execute(sa.text("PRAGMA foreign_keys = OFF"))
    conn.execute(
        sa.text(
            f"INSERT INTO scenarios ({', '.join(values)}) "  # noqa: S608
            f"VALUES ({', '.join(f':{c}' for c in values)})"
        ),
        values,
    )
    return str(explicit["id"])


def test_backfill_stamps_pre_cutoff_rows_legacy(
    alembic_config: Config, alembic_engine: Engine
) -> None:
    command.upgrade(alembic_config, _PRE_REV)
    with alembic_engine.begin() as conn:
        pre = _seed_scenario(conn, created_at="2026-06-08 12:00:00")
        post = _seed_scenario(conn, created_at="2026-06-10 11:00:00")
    command.upgrade(alembic_config, _F2_REV)
    with alembic_engine.connect() as conn:
        framing = dict(conn.execute(sa.text("SELECT id, vuln_framing FROM scenarios")).fetchall())
        assert framing[pre] == "legacy_residual"
        assert framing[post] == "inherent"


def test_roundtrip_and_downgrade_drops_column(
    alembic_config: Config, alembic_engine: Engine
) -> None:
    command.upgrade(alembic_config, _F2_REV)
    with alembic_engine.connect() as conn:
        cols = [r["name"] for r in conn.execute(sa.text("PRAGMA table_info(scenarios)")).mappings()]
        assert "vuln_framing" in cols
    command.downgrade(alembic_config, _PRE_REV)
    with alembic_engine.connect() as conn:
        cols = [r["name"] for r in conn.execute(sa.text("PRAGMA table_info(scenarios)")).mappings()]
        assert "vuln_framing" not in cols
    command.upgrade(alembic_config, _F2_REV)
