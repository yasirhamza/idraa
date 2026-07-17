"""Migration adds CHECK constraint: capability_value IS NULL OR <= 1e10 (Sec-I3)."""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine


def test_check_constraint_rejects_extreme_capability_value(
    alembic_config: Config,
    alembic_engine: Engine,
) -> None:
    """Capability_value > 1e10 must be rejected at DB level (defense vs direct ORM writes)."""
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
        ctrl_id = str(uuid.uuid4())
        conn.execute(
            sa.text(
                "INSERT INTO controls "
                "(id, created_at, updated_at, organization_id, name, "
                "description, type, annual_cost, "
                "nist_csf_functions, iso_27001_domains, "
                "compliance_mappings, skill_requirements, "
                "technology_dependencies, applicable_industries, "
                "applicable_org_sizes, status, version) VALUES "
                "(:id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, :org, "
                "'C', '', 'technical', 0, "
                "'[]', '[]', '{}', '[]', '[]', '[]', '[]', "
                "'active', '1.0')"
            ),
            {"id": ctrl_id, "org": org_id},
        )

        with pytest.raises(sa.exc.IntegrityError):
            conn.execute(
                sa.text(
                    "INSERT INTO control_function_assignments "
                    "(id, organization_id, control_id, sub_function, capability_value, "
                    " coverage, reliability, "
                    " created_at, updated_at) "
                    "VALUES (:id, :org, :ctrl, 'vmc_id_control_monitoring', 1e15, "
                    "0.8, 0.8, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "org": org_id,
                    "ctrl": ctrl_id,
                },
            )


def _insert_org_and_control(conn: sa.engine.Connection) -> tuple[str, str]:
    """Helper: insert one org + one control row, return (org_id, ctrl_id)."""
    org_id = str(uuid.uuid4())
    ctrl_id = str(uuid.uuid4())
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
            "INSERT INTO controls "
            "(id, created_at, updated_at, organization_id, name, "
            "description, type, annual_cost, "
            "nist_csf_functions, iso_27001_domains, "
            "compliance_mappings, skill_requirements, "
            "technology_dependencies, applicable_industries, "
            "applicable_org_sizes, status, version) VALUES "
            "(:id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, :org, "
            "'C', '', 'technical', 0, "
            "'[]', '[]', '{}', '[]', '[]', '[]', '[]', "
            "'active', '1.0')"
        ),
        {"id": ctrl_id, "org": org_id},
    )
    return org_id, ctrl_id


def test_check_constraint_accepts_one_e_ten(
    alembic_config: Config,
    alembic_engine: Engine,
) -> None:
    """capability_value == 1e10 (exact boundary) must be ACCEPTED (inclusive <=).

    Pins the `<=` semantic so that a future `<` flip or boundary bump to 1e9
    is caught by this test.
    """
    command.upgrade(alembic_config, "head")

    with alembic_engine.begin() as conn:
        org_id, ctrl_id = _insert_org_and_control(conn)
        # Must NOT raise — boundary value 1e10 is permitted by the constraint.
        conn.execute(
            sa.text(
                "INSERT INTO control_function_assignments "
                "(id, organization_id, control_id, sub_function, capability_value, "
                " coverage, reliability, "
                " created_at, updated_at) "
                "VALUES (:id, :org, :ctrl, 'vmc_id_control_monitoring', 1e10, "
                "0.8, 0.8, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {
                "id": str(uuid.uuid4()),
                "org": org_id,
                "ctrl": ctrl_id,
            },
        )


def test_downgrade_removes_check_constraint(
    alembic_config: Config,
    alembic_engine: Engine,
) -> None:
    """After downgrade, the upper-bound CHECK is gone and 1e15 is accepted.

    Mirrors the downgrade-coverage pattern from:
      tests/migrations/test_issue_90_migration.py::test_issue_90_downgrade_restores_domain_column
      tests/migrations/test_pr_iota_control_reshape.py::test_downgrade_restores_function_column
    """
    command.upgrade(alembic_config, "head")
    # Pin to a specific revision rather than "-1" to avoid drifting as new
    # migrations are added downstream of the PR μ.1 upper-bound migration.
    # PR μ.1b T6 added 08358cf073b8 (widen_audit_log_action_to_64) on top of
    # 1297897c44f5; "-1" from head now lands on the PR μ.1 head instead of
    # before it, leaving the CHECK constraint in place. Pin to PR μ.1's
    # down_revision (a777986e0bef) so downgrade unconditionally removes
    # 1297897c44f5's CHECK constraint regardless of future migrations.
    # (Final-Tier-1 plan-gate-round-7 fix; CLAUDE.md consumer-side-bug rule.)
    command.downgrade(alembic_config, "a777986e0bef")

    with alembic_engine.begin() as conn:
        org_id, ctrl_id = _insert_org_and_control(conn)
        # Must NOT raise — constraint has been dropped by the downgrade.
        conn.execute(
            sa.text(
                "INSERT INTO control_function_assignments "
                "(id, organization_id, control_id, sub_function, capability_value, "
                " coverage, reliability, "
                " created_at, updated_at) "
                "VALUES (:id, :org, :ctrl, 'vmc_id_control_monitoring', 1e15, "
                "0.8, 0.8, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {
                "id": str(uuid.uuid4()),
                "org": org_id,
                "ctrl": ctrl_id,
            },
        )
