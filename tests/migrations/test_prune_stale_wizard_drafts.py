"""One-time prune-stale-wizard-drafts migration test (47c4064a2c1e).

Destructive data migration: DELETE FROM wizard_drafts WHERE updated_at <
now - 7 days, run once at upgrade time (drafts-surfaced spec §4). Seeds two
raw rows at the prior revision (10 days idle / 1 day idle), upgrades, and
asserts only the 1-day row survives.
"""

from __future__ import annotations

import datetime
import uuid

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine

_PRE_REV = "26444158e537"  # current head immediately before the prune migration
_PRUNE_REV = "47c4064a2c1e"


def _seed_draft(conn: sa.Connection, *, updated_at: str) -> str:
    tx_id = uuid.uuid4().hex
    conn.execute(sa.text("PRAGMA foreign_keys = OFF"))
    conn.execute(
        sa.text(
            "INSERT INTO wizard_drafts "
            "(user_id, tx_id, organization_id, state_json, version_token, updated_at) "
            "VALUES (:user_id, :tx_id, :organization_id, :state_json, :version_token, :updated_at)"
        ),
        {
            "user_id": uuid.uuid4().hex,
            "tx_id": tx_id,
            "organization_id": uuid.uuid4().hex,
            "state_json": '{"tx_id": "' + tx_id + '", "current_step": 3}',
            "version_token": 0,
            "updated_at": updated_at,
        },
    )
    return tx_id


def test_prune_deletes_stale_keeps_recent(alembic_config: Config, alembic_engine: Engine) -> None:
    command.upgrade(alembic_config, _PRE_REV)
    now = datetime.datetime.now(datetime.UTC)
    old_ts = (now - datetime.timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S.%f")
    recent_ts = (now - datetime.timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S.%f")
    with alembic_engine.begin() as conn:
        old_tx = _seed_draft(conn, updated_at=old_ts)
        recent_tx = _seed_draft(conn, updated_at=recent_ts)

    command.upgrade(alembic_config, _PRUNE_REV)

    with alembic_engine.connect() as conn:
        remaining = [
            r[0] for r in conn.execute(sa.text("SELECT tx_id FROM wizard_drafts")).fetchall()
        ]
    assert remaining == [recent_tx]
    assert old_tx not in remaining


def test_downgrade_is_a_noop(alembic_config: Config, alembic_engine: Engine) -> None:
    command.upgrade(alembic_config, _PRE_REV)
    now = datetime.datetime.now(datetime.UTC)
    old_ts = (now - datetime.timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S.%f")
    with alembic_engine.begin() as conn:
        _seed_draft(conn, updated_at=old_ts)

    command.upgrade(alembic_config, _PRUNE_REV)
    with alembic_engine.connect() as conn:
        cols = [
            r["name"] for r in conn.execute(sa.text("PRAGMA table_info(wizard_drafts)")).mappings()
        ]
        assert cols  # table still exists post-upgrade — schema unaffected (data-only migration)

    command.downgrade(alembic_config, _PRE_REV)
    with alembic_engine.connect() as conn:
        cols_after = [
            r["name"] for r in conn.execute(sa.text("PRAGMA table_info(wizard_drafts)")).mappings()
        ]
        assert cols_after == cols
    command.upgrade(alembic_config, _PRUNE_REV)
