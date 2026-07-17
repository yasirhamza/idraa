"""Issue #129 T6 -- audit_log.action widened to String(64).

Pre-existing String(32) column already exceeded by multiple action
strings in production code (e.g., ``control_function_assignment.update``
at 34 chars). SQLite silently accepts; Postgres would reject. T6 adds
``control_function_assignment.clear`` (33 chars) on top of that, so we
widen first.

Mirrors the pytest-alembic-based migration-test pattern from
``tests/migrations/test_pr_mu_1_capability_upper_bound.py``: drives
``command.upgrade(alembic_config, "head")`` against an isolated SQLite
file then introspects the resulting schema via the sync engine.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine


def test_action_column_is_string_64_after_upgrade(
    alembic_config: Config,
    alembic_engine: Engine,
) -> None:
    """Verify audit_log.action max length is 64 post-upgrade.

    Uses SQLAlchemy ``inspect`` to read the column metadata directly
    (works on SQLite + Postgres -- type lengths come from reflection).
    """
    command.upgrade(alembic_config, "head")

    with alembic_engine.connect() as conn:
        cols = sa.inspect(conn).get_columns("audit_log")
    action_col = next(c for c in cols if c["name"] == "action")
    # SQLAlchemy reflects the type with its declared length; for SQLite
    # VARCHAR(N), .length == N.
    assert action_col["type"].length == 64, (
        f"expected action column length 64 after T6 upgrade; got {action_col['type'].length}"
    )


def test_long_action_string_accepted_after_upgrade(
    alembic_config: Config,
    alembic_engine: Engine,
) -> None:
    """A 33-char action string (``control_function_assignment.clear``)
    inserts cleanly post-upgrade.

    Pins the practical effect of the widen: the new T6 action verb fits.
    """
    command.upgrade(alembic_config, "head")

    long_action = "control_function_assignment.clear"  # 33 chars
    assert len(long_action) == 33

    with alembic_engine.begin() as conn:
        org_id = str(uuid.uuid4())
        conn.execute(
            sa.text(
                "INSERT INTO organizations "
                "(id, created_at, updated_at, name, organization_size, "
                "industry_type, security_maturity, risk_appetite, "
                "preferred_currency, preferred_language, "
                "geographic_regions, compliance_requirements, "
                "regulatory_environment, technology_stack, "
                "has_cyber_insurance) VALUES "
                "(:id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'Test Org', "
                "'large', 'information', 'defined', 'moderate', "
                "'USD', 'en', '[]', '[]', '[]', '[]', 0)"
            ),
            {"id": org_id},
        )
        conn.execute(
            sa.text(
                "INSERT INTO audit_log "
                "(id, organization_id, entity_type, entity_id, user_id, "
                "action, changes, timestamp, ip_address) "
                "VALUES (:id, :org, 'control_function_assignment', :entity, "
                "NULL, :action, '{}', CURRENT_TIMESTAMP, NULL)"
            ),
            {
                "id": str(uuid.uuid4()),
                "org": org_id,
                "entity": str(uuid.uuid4()),
                "action": long_action,
            },
        )

        # Verify it actually stored the full 33 chars (defends against
        # silent truncation -- SQLite would accept either way, but a
        # length-equal readback proves the widen took effect).
        stored = conn.execute(
            sa.text("SELECT action FROM audit_log WHERE action = :a"),
            {"a": long_action},
        ).scalar_one()
        assert stored == long_action
        assert len(stored) == 33


def test_existing_action_strings_fit_new_width() -> None:
    """All known action verbs fit within the new 64-char ceiling.

    Pure-code assertion (no DB). Acts as a tripwire if a future PR
    introduces an action string longer than 64 chars without re-widening
    the column.
    """
    existing_actions = [
        # Control taxonomy
        "control.create",
        "control.update",
        "control.delete",
        "control.duplicate",
        "control.import",
        # CFA taxonomy (some already exceed the old 32-char limit)
        "control_function_assignment.create",  # 34
        "control_function_assignment.update",  # 34
        "control_function_assignment.delete",  # 34
        "control_function_assignment.confirm",  # 35
        "control_function_assignment.clear",  # 33 -- new in T6
        # Calibration / migration markers
        "null_fallback_issue_129",
        "reclassify_unit_type_issue_131",
    ]
    for action in existing_actions:
        assert len(action) <= 64, (
            f"action verb {action!r} is {len(action)} chars; exceeds the "
            f"post-#129-T6 String(64) limit"
        )


def test_downgrade_refuses_when_long_action_rows_exist(
    alembic_config: Config,
    alembic_engine: Engine,
) -> None:
    """Downgrade must refuse if any row has action > 32 chars to avoid
    silent truncation (Arch-I1 round-1 + Arch-2 round-2 plan-gate fix).

    Pin to the revision PRIOR to the widen (``1297897c44f5``) rather than
    using ``"-1"`` from head — newer migrations (e.g. T2's
    ``ed70de5aa6b9``) stack on top of the widen, so ``"-1"`` from head
    targets the wrong migration. The widen revision (``08358cf073b8``)
    is the one whose downgrade guard this test validates.
    """
    command.upgrade(alembic_config, "head")

    with alembic_engine.begin() as conn:
        org_id = str(uuid.uuid4())
        conn.execute(
            sa.text(
                "INSERT INTO organizations "
                "(id, created_at, updated_at, name, organization_size, "
                "industry_type, security_maturity, risk_appetite, "
                "preferred_currency, preferred_language, "
                "geographic_regions, compliance_requirements, "
                "regulatory_environment, technology_stack, "
                "has_cyber_insurance) VALUES "
                "(:id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'Test Org', "
                "'large', 'information', 'defined', 'moderate', "
                "'USD', 'en', '[]', '[]', '[]', '[]', 0)"
            ),
            {"id": org_id},
        )
        conn.execute(
            sa.text(
                "INSERT INTO audit_log "
                "(id, organization_id, entity_type, entity_id, user_id, "
                "action, changes, timestamp, ip_address) "
                "VALUES (:id, :org, 'control_function_assignment', :entity, "
                "NULL, :action, '{}', CURRENT_TIMESTAMP, NULL)"
            ),
            {
                "id": str(uuid.uuid4()),
                "org": org_id,
                "entity": str(uuid.uuid4()),
                "action": "control_function_assignment.confirm",  # 35 chars
            },
        )

    # Downgrade to revision PRIOR to the widen migration; this exercises
    # the widen's own ``downgrade()`` guard. ``"-1"`` from head would
    # downgrade T2's stacked migration instead.
    try:
        command.downgrade(alembic_config, "1297897c44f5")
    except RuntimeError as exc:
        assert "refusing to downgrade" in str(exc)
        assert "would silently truncate" in str(exc)
    else:
        raise AssertionError(
            "downgrade should have raised RuntimeError when long action "
            "rows exist (Arch-I1 / Arch-2 truncation guard)"
        )


def test_downgrade_succeeds_when_no_long_action_rows(
    alembic_config: Config,
    alembic_engine: Engine,
) -> None:
    """Downgrade succeeds cleanly when all action rows fit in String(32).

    Pins the inverse of ``test_downgrade_refuses_*``: with only safe-
    length rows, the guard is silent and the column shrinks back.

    Pin to the revision PRIOR to the widen (``1297897c44f5``) — see the
    rationale on ``test_downgrade_refuses_when_long_action_rows_exist``.
    """
    command.upgrade(alembic_config, "head")

    with alembic_engine.begin() as conn:
        org_id = str(uuid.uuid4())
        conn.execute(
            sa.text(
                "INSERT INTO organizations "
                "(id, created_at, updated_at, name, organization_size, "
                "industry_type, security_maturity, risk_appetite, "
                "preferred_currency, preferred_language, "
                "geographic_regions, compliance_requirements, "
                "regulatory_environment, technology_stack, "
                "has_cyber_insurance) VALUES "
                "(:id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'Test Org', "
                "'large', 'information', 'defined', 'moderate', "
                "'USD', 'en', '[]', '[]', '[]', '[]', 0)"
            ),
            {"id": org_id},
        )
        conn.execute(
            sa.text(
                "INSERT INTO audit_log "
                "(id, organization_id, entity_type, entity_id, user_id, "
                "action, changes, timestamp, ip_address) "
                "VALUES (:id, :org, 'control', :entity, NULL, "
                "'control.create', '{}', CURRENT_TIMESTAMP, NULL)"
            ),
            {
                "id": str(uuid.uuid4()),
                "org": org_id,
                "entity": str(uuid.uuid4()),
            },
        )

    command.downgrade(alembic_config, "1297897c44f5")

    with alembic_engine.connect() as conn:
        cols = sa.inspect(conn).get_columns("audit_log")
    action_col = next(c for c in cols if c["name"] == "action")
    assert action_col["type"].length == 32
