"""Round-trip test for the issue #66 cost_model → annual_cost migration.

Three observed cost_model shapes in the dev DB:
  - {"annual_cost": 0.0}      (importer default)
  - {"annual_cost": <number>} (user-edited)
  - {}                        (legacy; defensive — none observed in the wild)

Upgrade must backfill all three to a non-null Decimal on annual_cost.
Downgrade must re-create cost_model with shape {"annual_cost": <value>}.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import Engine


def _seed_pre_migration_rows(engine: sa.Engine) -> list[uuid.UUID]:
    """Insert four controls covering the observed cost_model shapes plus a
    defensive 'lenient CAST' fixture row.

    Raw SQL INSERT bypasses SQLAlchemy ORM defaults — every NOT NULL column
    on `controls` and `organizations` must be supplied explicitly with valid
    JSON / enum values, or the insert raises IntegrityError.
    """
    ids = [uuid.uuid4() for _ in range(4)]
    shapes = [
        {"annual_cost": 0.0},
        {"annual_cost": 12000.50},
        {},  # legacy defensive shape — no annual_cost key
        # Security-review #2: malicious / non-numeric payload. SQLite's
        # CAST(... AS NUMERIC) is lenient and returns 0 for non-parseable
        # strings; this fixture pins that behavior as an acceptance criterion.
        {"annual_cost": "not-a-number"},
    ]
    with engine.begin() as conn:
        org_id = uuid.uuid4()
        # Seed organization with all NOT NULL columns. The Organization model
        # has many JSON-list / dict columns that default to []/{} in the ORM
        # but require explicit values in raw INSERT.
        conn.execute(
            text(
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
            {"id": str(org_id)},
        )
        # Seed controls — every NOT NULL JSON-list column gets '[]'; the
        # cost_model column gets the shape under test. status/version use
        # documented defaults from the model (status='active', version='1.0').
        for cid, cm in zip(ids, shapes, strict=True):
            conn.execute(
                text(
                    "INSERT INTO controls "
                    "(id, created_at, updated_at, organization_id, name, "
                    "description, domain, type, cost_model, "
                    "nist_csf_functions, iso_27001_domains, "
                    "compliance_mappings, skill_requirements, "
                    "technology_dependencies, applicable_industries, "
                    "applicable_org_sizes, status, version) VALUES "
                    "(:id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, :org_id, "
                    ":name, '', 'loss_event', 'administrative', "
                    ":cost_model, '[]', '[]', '{}', '[]', '[]', '[]', '[]', "
                    "'active', '1.0')"
                ),
                {
                    "id": str(cid),
                    "org_id": str(org_id),
                    "name": f"Control {cid}",
                    "cost_model": json.dumps(cm),
                },
            )
    return ids


def test_issue_66_upgrade_backfills_all_four_shapes(
    alembic_config: Config,
    alembic_engine: Engine,
) -> None:
    # Bring DB up to the revision BEFORE issue-66 (the current head before this PR).
    command.upgrade(alembic_config, "a7c19e84f3b2")
    ids = _seed_pre_migration_rows(alembic_engine)

    # Apply the issue-66 migration (will be the new head after this PR).
    command.upgrade(alembic_config, "head")

    inspector = sa.inspect(alembic_engine)
    cols = {c["name"]: c for c in inspector.get_columns("controls")}
    assert "cost_model" not in cols
    assert "annual_cost" in cols
    # server_default survives the migration (architect review fix): the
    # introspected default on annual_cost must equal "0". SQLite returns
    # the literal as it appears in the column DDL ('0' with the quotes
    # SQLAlchemy emits around the server_default string). Pattern mirrors
    # tests/migrations/test_issue_89_migration.py:26 (column-introspection).
    assert cols["annual_cost"]["default"] == "'0'"

    with alembic_engine.begin() as conn:
        # Backfill: $0 for the existing 0.0 row, the empty-dict legacy row, and
        # the malicious non-numeric payload row (SQLite CAST is lenient → 0).
        # The user-edited 12000.50 round-trips with Decimal precision.
        rows = conn.execute(text("SELECT id, annual_cost FROM controls ORDER BY name")).fetchall()
        ac_by_id = {uuid.UUID(r[0]): Decimal(str(r[1])) for r in rows}
        assert ac_by_id[ids[0]] == Decimal("0")
        assert ac_by_id[ids[1]] == Decimal("12000.50")
        assert ac_by_id[ids[2]] == Decimal("0")  # legacy {} → 0 via COALESCE
        assert ac_by_id[ids[3]] == Decimal("0")  # "not-a-number" → 0 via lenient CAST


def test_issue_66_downgrade_recreates_cost_model(
    alembic_config: Config,
    alembic_engine: Engine,
) -> None:
    command.upgrade(alembic_config, "head")

    # Seed at HEAD using the new column shape.
    org_id = uuid.uuid4()
    ctrl_id = uuid.uuid4()
    with alembic_engine.begin() as conn:
        conn.execute(
            text(
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
            {"id": str(org_id)},
        )
        conn.execute(
            text(
                # Note: the 'domain' column was dropped at HEAD by the
                # issue #90 migration (a777986e0bef), which is in the chain
                # ABOVE the issue #66 migration this test exercises.
                # Seeding at HEAD therefore omits the column. The downgrade
                # being tested here (issue #66) reshapes annual_cost; it has
                # nothing to do with the domain column.
                "INSERT INTO controls "
                "(id, created_at, updated_at, organization_id, name, "
                "description, type, annual_cost, "
                "nist_csf_functions, iso_27001_domains, "
                "compliance_mappings, skill_requirements, "
                "technology_dependencies, applicable_industries, "
                "applicable_org_sizes, status, version) VALUES "
                "(:id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, :org_id, "
                "'Down Test', '', 'administrative', "
                ":ac, '[]', '[]', '{}', '[]', '[]', '[]', '[]', "
                "'active', '1.0')"
            ),
            {"id": str(ctrl_id), "org_id": str(org_id), "ac": "8750.00"},
        )

    # Downgrade past issue #66 specifically — the issue #90 migration is
    # now stacked on top of issue #66 at HEAD, so a relative "-1" would
    # only undo issue #90's domain-drop, not the annual_cost reshape this
    # test exercises. Target issue #66's down_revision directly so the
    # downgrade chain re-applies issue #90's downgrade THEN issue #66's
    # downgrade, leaving the schema at the pre-issue-66 cost_model shape.
    command.downgrade(alembic_config, "a7c19e84f3b2")

    with alembic_engine.begin() as conn:
        cols = [row[1] for row in conn.execute(text("PRAGMA table_info(controls)"))]
        assert "annual_cost" not in cols
        assert "cost_model" in cols
        row = conn.execute(
            text("SELECT cost_model FROM controls WHERE id = :id"),
            {"id": str(ctrl_id)},
        ).scalar_one()
        parsed = json.loads(row)
        # JSON downgrade serializes Decimal via CAST(... AS REAL) — accept
        # float comparison at cent precision.
        assert parsed["annual_cost"] == pytest.approx(8750.00)
