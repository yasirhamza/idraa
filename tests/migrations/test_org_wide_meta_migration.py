"""#299 -- migration a7c2e9f14b63 (controls.org_wide_meta, runs.inherited_control_ids_per_scenario).

Upgrade writes no data: an existing control reads ``org_wide_meta`` False (stays
scenario-scoped, so no result changes) and an existing run's new column is SQL NULL
("predates the feature"). Downgrade (spec R1) flags every COMPLETED run whose column
is NOT NULL as stale BEFORE the column is dropped -- the last chance to identify
feature-era rows -- and then drops both columns. The flagged set is a deliberate
superset (a run that inherited nothing is flagged too); a NULL run, and a run that is
not COMPLETED, are untouched. Each run the downgrade flips also gets one ``run.flagged_stale``
audit row (system actor, no-hyphen hex ids, a reason: the c4d2e8f1a6b9 shape); a run that was
already stale is neither rewritten nor given a row.

Rows are seeded with raw SQL at the pre-revision schema: the real ORM now writes the
new columns, which that schema lacks. Revisions are pinned explicitly (not "head"), so
the test stays valid when a later migration lands on top.
"""

from __future__ import annotations

import json
import uuid

import sqlalchemy as sa
from pytest_alembic import MigrationContext

_PRE = "3e0c10ecedb3"
_REV = "a7c2e9f14b63"
_STAMP = "2026-10-29 00:00:00"


def _hex() -> str:
    return uuid.uuid4().hex


def _seed_control(engine: sa.Engine) -> str:
    """One org + one control at the pre-revision schema. Returns the ORG id (hex)."""
    org_id, control_id = _hex(), _hex()
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO organizations (id, name, industry_type, organization_size, "
                "geographic_regions, security_maturity, has_cyber_insurance, risk_appetite, "
                "compliance_requirements, regulatory_environment, technology_stack, "
                "preferred_currency, preferred_language) "
                "VALUES (:id, 'org-wide-meta probe org', 'information', 'medium', '[]', "
                "'basic', :f, 'moderate', '[]', '[]', '[]', 'USD', 'en')"
            ),
            {"id": org_id, "f": False},
        )
        conn.execute(
            sa.text(
                "INSERT INTO controls (id, organization_id, name, type, nist_csf_functions, "
                "iso_27001_domains, compliance_mappings, skill_requirements, "
                "technology_dependencies, applicable_industries, applicable_org_sizes, "
                "status, version) "
                "VALUES (:id, :org, 'probe control', 'technical', '[]', '[]', '{}', '[]', "
                "'[]', '[]', '[]', 'active', '1.0')"
            ),
            {"id": control_id, "org": org_id},
        )
    return org_id


_RUN_PRE = (
    "INSERT INTO risk_analysis_runs (id, organization_id, run_type, status, mc_iterations, "
    "inputs_hash, controls_snapshot, control_ids_used, created_at, updated_at, is_stale) "
    "VALUES (:id, :org, 'single', :st, 100, 'h', '[]', '[]', :ts, :ts, :stale)"
)
_RUN_FEATURE = (
    "INSERT INTO risk_analysis_runs (id, organization_id, run_type, status, mc_iterations, "
    "inputs_hash, controls_snapshot, control_ids_used, created_at, updated_at, is_stale, "
    "inherited_control_ids_per_scenario) "
    "VALUES (:id, :org, 'single', :st, 100, 'h', '[]', '[]', :ts, :ts, :stale, :inh)"
)


def _insert_pre_feature_run(engine: sa.Engine, org_id: str, status: str) -> str:
    """A run at the pre-revision schema (the new column does not exist yet)."""
    run_id = _hex()
    params = {"id": run_id, "org": org_id, "st": status, "ts": _STAMP, "stale": False}
    with engine.begin() as conn:
        conn.execute(sa.text(_RUN_PRE), params)
    return run_id


def _insert_feature_run(
    engine: sa.Engine,
    org_id: str,
    status: str,
    inherited: dict[str, list[str]],
    *,
    stale: bool = False,
) -> str:
    """A run at the post-revision schema with a NON-NULL (JSON text) column value."""
    run_id = _hex()
    params = {
        "id": run_id,
        "org": org_id,
        "st": status,
        "ts": _STAMP,
        "stale": stale,
        "inh": json.dumps(inherited),
    }
    with engine.begin() as conn:
        conn.execute(sa.text(_RUN_FEATURE), params)
    return run_id


def _is_stale(engine: sa.Engine, run_id: str) -> bool:
    with engine.connect() as conn:
        return bool(
            conn.execute(
                sa.text("SELECT is_stale FROM risk_analysis_runs WHERE id = :id"), {"id": run_id}
            ).scalar_one()
        )


def _audit_rows(engine: sa.Engine) -> list[sa.RowMapping]:
    with engine.connect() as conn:
        return list(
            conn.execute(
                sa.text(
                    "SELECT id, organization_id, entity_type, entity_id, user_id, action, changes "
                    "FROM audit_log WHERE action = 'run.flagged_stale' ORDER BY entity_id"
                )
            )
            .mappings()
            .all()
        )


def _columns(engine: sa.Engine, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(engine).get_columns(table)}


def test_upgrade_leaves_existing_control_scoped_and_existing_run_sql_null(
    alembic_runner: MigrationContext, alembic_engine: sa.Engine
) -> None:
    alembic_runner.migrate_up_to(_PRE)
    org_id = _seed_control(alembic_engine)
    old_run = _insert_pre_feature_run(alembic_engine, org_id, "completed")

    alembic_runner.migrate_up_to(_REV)

    col = {c["name"]: c for c in sa.inspect(alembic_engine).get_columns("controls")}[
        "org_wide_meta"
    ]
    assert col["nullable"] is False
    run_col = {c["name"]: c for c in sa.inspect(alembic_engine).get_columns("risk_analysis_runs")}[
        "inherited_control_ids_per_scenario"
    ]
    assert run_col["nullable"] is True

    with alembic_engine.connect() as conn:
        stored: int = conn.execute(sa.text("SELECT org_wide_meta FROM controls")).scalar_one()
        as_bool = conn.execute(
            sa.select(sa.column("org_wide_meta", sa.Boolean())).select_from(sa.table("controls"))
        ).scalar_one()
        run_is_sql_null: int = conn.execute(
            sa.text(
                "SELECT inherited_control_ids_per_scenario IS NULL "
                "FROM risk_analysis_runs WHERE id = :id"
            ),
            {"id": old_run},
        ).scalar_one()
    assert stored == 0
    assert as_bool is False
    assert run_is_sql_null == 1  # SQL NULL, not the JSON text 'null'
    assert _is_stale(alembic_engine, old_run) is False  # upgrade flags nothing


def test_downgrade_flags_feature_era_completed_runs_then_drops_both_columns(
    alembic_runner: MigrationContext, alembic_engine: sa.Engine
) -> None:
    alembic_runner.migrate_up_to(_PRE)
    org_id = _seed_control(alembic_engine)
    pre_feature = _insert_pre_feature_run(alembic_engine, org_id, "completed")
    alembic_runner.migrate_up_to(_REV)

    nothing_inherited = _insert_feature_run(alembic_engine, org_id, "completed", {})
    empty_list = _insert_feature_run(alembic_engine, org_id, "completed", {"s": []})
    inherited = _insert_feature_run(alembic_engine, org_id, "completed", {"s": ["c-1", "c-2"]})
    failed = _insert_feature_run(alembic_engine, org_id, "failed", {"s": ["c-1"]})

    alembic_runner.migrate_down_to(_PRE)

    assert _is_stale(alembic_engine, pre_feature) is False  # NULL: predates the feature
    assert _is_stale(alembic_engine, nothing_inherited) is True  # deliberate superset
    assert _is_stale(alembic_engine, empty_list) is True  # deliberate superset
    assert _is_stale(alembic_engine, inherited) is True
    assert _is_stale(alembic_engine, failed) is False  # not COMPLETED
    assert "org_wide_meta" not in _columns(alembic_engine, "controls")
    assert "inherited_control_ids_per_scenario" not in _columns(
        alembic_engine, "risk_analysis_runs"
    )


def test_downgrade_writes_one_audit_row_per_flagged_run(
    alembic_runner: MigrationContext, alembic_engine: sa.Engine
) -> None:
    """The stale flag is never set without a trail (precedent c4d2e8f1a6b9): one
    ``run.flagged_stale`` row per run the downgrade flips -- system actor, the run's own
    organization, no-hyphen hex ids, the ``[false, true]`` transition and a reason. A run that
    was already stale is not rewritten and gets no row; a NULL run and a non-COMPLETED run get
    none either."""
    alembic_runner.migrate_up_to(_PRE)
    org_id = _seed_control(alembic_engine)
    pre_feature = _insert_pre_feature_run(alembic_engine, org_id, "completed")
    alembic_runner.migrate_up_to(_REV)

    nothing_inherited = _insert_feature_run(alembic_engine, org_id, "completed", {})
    empty_list = _insert_feature_run(alembic_engine, org_id, "completed", {"s": []})
    inherited = _insert_feature_run(alembic_engine, org_id, "completed", {"s": ["c-1", "c-2"]})
    already_stale = _insert_feature_run(
        alembic_engine, org_id, "completed", {"s": ["c-1"]}, stale=True
    )
    failed = _insert_feature_run(alembic_engine, org_id, "failed", {"s": ["c-1"]})
    assert _audit_rows(alembic_engine) == []  # upgrade writes nothing

    alembic_runner.migrate_down_to(_PRE)

    flagged = {nothing_inherited, empty_list, inherited}
    rows = _audit_rows(alembic_engine)
    assert {str(r["entity_id"]) for r in rows} == flagged  # exactly one per flipped run
    assert len(rows) == len(flagged)
    assert {pre_feature, already_stale, failed}.isdisjoint(str(r["entity_id"]) for r in rows)
    assert _is_stale(alembic_engine, already_stale) is True  # still stale, no row
    for row in rows:
        assert row["entity_type"] == "risk_analysis_run"
        assert str(row["organization_id"]) == org_id  # org-scoped system row
        assert row["user_id"] is None
        assert str(row["id"]) == uuid.UUID(hex=str(row["id"])).hex  # no-hyphen hex
        assert json.loads(str(row["changes"])) == {
            "is_stale": [False, True],
            "reason": ["", "issue-299 org-wide meta controls rollback"],
        }
