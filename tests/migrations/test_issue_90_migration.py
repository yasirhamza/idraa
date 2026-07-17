"""Round-trip test for the issue #90 Control.domain column drop.

Pre-migration: controls.domain is a NOT NULL ENUM (loss_event,
variance_management, decision_support).
Post-migration: column is gone; domains derived from
ControlFunctionAssignment.sub_function via Control.domains property.

Downgrade re-adds the column with a server_default; data is unrecoverable
(the original denormalization was lossy).
"""

from __future__ import annotations

import uuid

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import Engine


def test_issue_90_upgrade_drops_domain_column(
    alembic_config: Config,
    alembic_engine: Engine,
) -> None:
    # Bring DB up to the revision BEFORE issue-90 (the current head pre-PR).
    command.upgrade(alembic_config, "4af391c766a9")

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
                "INSERT INTO controls "
                "(id, created_at, updated_at, organization_id, name, "
                "description, domain, type, annual_cost, "
                "nist_csf_functions, iso_27001_domains, "
                "compliance_mappings, skill_requirements, "
                "technology_dependencies, applicable_industries, "
                "applicable_org_sizes, status, version) VALUES "
                "(:id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, :org_id, "
                "'Test Control', '', 'loss_event', 'administrative', "
                "0, '[]', '[]', '{}', '[]', '[]', '[]', '[]', "
                "'active', '1.0')"
            ),
            {"id": str(ctrl_id), "org_id": str(org_id)},
        )

    command.upgrade(alembic_config, "head")

    with alembic_engine.begin() as conn:
        cols = [row[1] for row in conn.execute(text("PRAGMA table_info(controls)"))]
        assert "domain" not in cols
        row = conn.execute(
            text("SELECT id, name FROM controls WHERE id = :id"),
            {"id": str(ctrl_id)},
        ).fetchone()
        assert row is not None
        assert row[1] == "Test Control"


def test_issue_90_downgrade_restores_domain_column(
    alembic_config: Config,
    alembic_engine: Engine,
) -> None:
    # Pin to the exact issue-90 revision so this test is not sensitive to
    # migrations added after issue-90 (e.g. pr_mu_1_capability_value_upper_bound
    # which extended head past a777986e0bef).
    command.upgrade(alembic_config, "a777986e0bef")

    # Insert WITHOUT domain (post-issue-90-upgrade state)
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
                "INSERT INTO controls "
                "(id, created_at, updated_at, organization_id, name, "
                "description, type, annual_cost, "
                "nist_csf_functions, iso_27001_domains, "
                "compliance_mappings, skill_requirements, "
                "technology_dependencies, applicable_industries, "
                "applicable_org_sizes, status, version) VALUES "
                "(:id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, :org_id, "
                "'Down Test', '', 'administrative', 0, "
                "'[]', '[]', '{}', '[]', '[]', '[]', '[]', "
                "'active', '1.0')"
            ),
            {"id": str(ctrl_id), "org_id": str(org_id)},
        )

    # Downgrade to issue-90's predecessor (4af391c766a9 — issue_66_controls_annual_cost),
    # which is the state where the domain column still existed.
    command.downgrade(alembic_config, "4af391c766a9")

    with alembic_engine.begin() as conn:
        cols = [row[1] for row in conn.execute(text("PRAGMA table_info(controls)"))]
        assert "domain" in cols
        row = conn.execute(
            text("SELECT domain FROM controls WHERE id = :id"),
            {"id": str(ctrl_id)},
        ).fetchone()
        assert row[0] == "loss_event"
