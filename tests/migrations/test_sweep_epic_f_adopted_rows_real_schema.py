# tests/migrations/test_sweep_epic_f_adopted_rows_real_schema.py
"""N-5 (final whole-branch review, 2026-09-29): a real alembic-built-schema smoke
test for ``scripts/sweep_epic_f_adopted_rows.py``. The sweep's own unit tests
(``tests/unit/test_sweep_epic_f_adopted_rows.py``) build a 7-table hand-rolled
SQLite fixture by DDL; nothing previously ran ``sweep()`` against the ACTUAL
migrated schema, so a column rename in ``scenarios``/``controls``/
``scenario_library_overrides``/``scenario_sme_estimates`` would surface only at
deploy time (fixtures-must-mirror-prod-shapes rule).

Upgrades a scratch DB to ``head`` (real schema + the real Epic F-curated seed
content), seeds one org-owned scenario pinned to a deprecated entry
(``data-breach-notification-regulatory-tail``), and asserts the sweep's
per-key counts, the full-summary printout, and ``--gate`` exit code.

xdist-safe: ``alembic_config``/``alembic_engine`` (``tests/migrations/
conftest.py``) give every test its own ``tmp_path``-scoped SQLite file --
no shared state between workers.
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

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "sweep_epic_f_adopted_rows.py"
_spec = importlib.util.spec_from_file_location("sweep_epic_f_adopted_rows_realschema", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

# One of the three entries e5f1a9c3d7b2 deprecates -- chosen arbitrarily among
# the three; the sweep classifies all of DEPRECATED_SCENARIO_SLUGS the same way.
_DEPRECATED_SLUG = "data-breach-notification-regulatory-tail"


def _seed_org(conn: sa.Connection) -> str:
    """Minimal schema-valid organization row (same column list as
    tests/migrations/test_pr_mu_1_capability_upper_bound.py's precedent,
    which also inserts at ``head``)."""
    org_id = uuid.uuid4().hex
    conn.execute(
        sa.text(
            "INSERT INTO organizations "
            "(id, created_at, updated_at, name, organization_size, "
            "industry_type, security_maturity, risk_appetite, "
            "preferred_currency, preferred_language, "
            "geographic_regions, compliance_requirements, "
            "regulatory_environment, technology_stack, "
            "has_cyber_insurance) VALUES "
            "(:id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'ZZ-ORG-NAME-SENTINEL', "
            "'large', 'information', 'defined', 'moderate', "
            "'USD', 'en', '[]', '[]', '[]', '[]', 0)"
        ),
        {"id": org_id},
    )
    return org_id


def _seed_scenario_pinned_to(
    conn: sa.Connection, *, organization_id: str, entry_id_hex: str
) -> str:
    """Insert a minimal schema-valid scenario, PRAGMA-driven placeholder fill for
    any NOT NULL column this dict doesn't name (mirrors tests/migrations/
    test_capacity_max_backfill.py's ``_seed_scenario``), pinned to the given
    library entry's version-1 row."""
    explicit: dict[str, object] = {
        "id": uuid.uuid4().hex,
        "organization_id": organization_id,
        "name": "ZZ-SCENARIO-NAME-SENTINEL",
        "scenario_type": "CUSTOM",
        "threat_category": "ransomware",
        "threat_event_frequency": '{"distribution":"PERT","low":1,"mode":2,"high":3}',
        "vulnerability": '{"distribution":"PERT","low":0.1,"mode":0.2,"high":0.3}',
        "primary_loss": '{"distribution":"PERT","low":1000,"mode":2000,"high":3000}',
        "secondary_loss": '{"distribution":"PERT","low":500,"mode":1000,"high":1500}',
        "library_pin": json.dumps({"entry_id": entry_id_hex, "version": 1}),
        "source": "expert_judgment",
        "status": "ACTIVE",
        "version": "1.0",
        "row_version": 1,
    }
    cols = conn.execute(sa.text("PRAGMA table_info(scenarios)")).mappings().all()
    values: dict[str, object] = {}
    for col in cols:
        cname = col["name"]
        if cname in explicit:
            values[cname] = explicit[cname]
        elif col["notnull"] and col["dflt_value"] is None:
            values[cname] = "x"
    column_list = ", ".join(values)
    placeholders = ", ".join(f":{c}" for c in values)
    conn.execute(
        sa.text(f"INSERT INTO scenarios ({column_list}) VALUES ({placeholders})"),  # noqa: S608
        values,
    )
    return str(explicit["id"])


def test_sweep_against_alembic_built_head_schema(
    alembic_config: Config, alembic_engine: Engine
) -> None:
    command.upgrade(alembic_config, "head")

    with alembic_engine.begin() as conn:
        entry_id_hex = str(
            conn.execute(
                sa.text(
                    "SELECT id FROM scenario_library_entries "
                    "WHERE slug = :slug AND version = 1 AND source = 'seed'"
                ),
                {"slug": _DEPRECATED_SLUG},
            ).scalar_one()
        )
        org_id = _seed_org(conn)
        _seed_scenario_pinned_to(conn, organization_id=org_id, entry_id_hex=entry_id_hex)

    config_url = alembic_config.get_main_option("sqlalchemy.url")
    assert config_url is not None
    db_path = Path(config_url.removeprefix("sqlite+aiosqlite://"))
    assert db_path.is_file()

    summary = mod.sweep(db_path)

    # The seeded scenario is pinned to a DEPRECATED entry, not
    # accidental-insider-exposure -- every AIE-scoped (gated) counter, and
    # every other gate/non-gate counter this seed doesn't touch, must be 0.
    for key in mod.GATE_KEYS:
        assert summary[key] == 0, f"{key} expected 0, got {summary[key]}"
    assert summary["aie_copy_current"] == 0
    assert summary["aie_current"] == 0
    assert summary["aie_stale"] == 0
    assert summary["aie_modified"] == 0
    assert summary["aie_pinned"] == 0
    assert summary["people_asset_class_scenarios"] == 0
    assert summary["deprecated_overrides"] == 0
    assert summary["control_resync_stale"] == 0
    assert summary["control_current"] == 0
    assert summary["control_skipped_unparsable"] == 0
    assert summary["control_skipped_pin_version"] == 0
    assert summary["double_counted_orgs"] == 0
    # The one row this test actually seeded: informational, never gated.
    assert summary["deprecated_orgs"] == 1

    assert mod.main(["--db", str(db_path), "--gate"]) == 0
