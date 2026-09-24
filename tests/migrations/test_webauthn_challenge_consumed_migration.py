"""Advisory GHSA-46jj-823j-mjj9 B5 — ``webauthn_challenge_consumed`` migration.

Mirrors the pytest-alembic-based migration-test pattern from
``tests/migrations/test_audit_action_widen.py`` / ``test_pr_mu_1_capability_upper_bound.py``:
drives ``command.upgrade(alembic_config, "head")`` against an isolated SQLite
file then introspects the resulting schema via the sync engine.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine


def test_table_created_with_unique_digest_and_indexed_expiry(
    alembic_config: Config,
    alembic_engine: Engine,
) -> None:
    """Post-upgrade: table exists with a UNIQUE ``challenge_digest`` and an
    index on ``expires_at`` (the retention sweep's scan column)."""
    command.upgrade(alembic_config, "head")

    with alembic_engine.connect() as conn:
        inspector = sa.inspect(conn)
        cols = {c["name"] for c in inspector.get_columns("webauthn_challenge_consumed")}
        assert {
            "id",
            "challenge_digest",
            "purpose",
            "consumed_at",
            "expires_at",
            "replay_audited",
        } <= cols

        # SQLite may reflect a UNIQUE column as a unique INDEX instead of (or
        # in addition to) a UniqueConstraint — check both surfaces.
        unique_constraints = inspector.get_unique_constraints("webauthn_challenge_consumed")
        indexes = inspector.get_indexes("webauthn_challenge_consumed")
        unique_cols = {tuple(uc["column_names"]) for uc in unique_constraints} | {
            tuple(ix["column_names"]) for ix in indexes if ix.get("unique")
        }
        assert ("challenge_digest",) in unique_cols

        expires_indexed = any("expires_at" in ix["column_names"] for ix in indexes)
        assert expires_indexed, f"expected an index on expires_at; got indexes={indexes}"


def test_challenge_digest_uniqueness_enforced(
    alembic_config: Config,
    alembic_engine: Engine,
) -> None:
    """A second row with the same ``challenge_digest`` is rejected at the DB level."""
    command.upgrade(alembic_config, "head")

    with alembic_engine.begin() as conn:
        digest = "a" * 64
        conn.execute(
            sa.text(
                "INSERT INTO webauthn_challenge_consumed "
                "(id, challenge_digest, purpose, consumed_at, expires_at, replay_audited) "
                "VALUES (:id, :digest, 'login', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 0)"
            ),
            {"id": uuid.uuid4().hex, "digest": digest},
        )
        with pytest.raises(sa.exc.IntegrityError):
            conn.execute(
                sa.text(
                    "INSERT INTO webauthn_challenge_consumed "
                    "(id, challenge_digest, purpose, consumed_at, expires_at, replay_audited) "
                    "VALUES (:id, :digest, 'stepup', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 0)"
                ),
                {"id": uuid.uuid4().hex, "digest": digest},
            )


def test_downgrade_drops_table(
    alembic_config: Config,
    alembic_engine: Engine,
) -> None:
    """Downgrade -1 removes the table cleanly."""
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "b5e2c7a9d413")

    with alembic_engine.connect() as conn:
        table_names = sa.inspect(conn).get_table_names()
    assert "webauthn_challenge_consumed" not in table_names
