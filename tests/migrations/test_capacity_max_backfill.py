"""capacity-max backfill migration tests (ffed7c509563).

Per docs/superpowers/plans/2026-07-25-capacity-bound-pr2.md Task 5: backfills
the PR2 capacity-bound cap (``max = K_CAPACITY * annual_revenue``, D13) onto
pre-existing ``scenarios.primary_loss``/``secondary_loss`` fields whose stored
distribution is ``lognormal``/``lognormal_mixture`` and does not already carry
an explicit ``max``.

Mirrors ``tests/migrations/test_sigma_recalibration_migration.py``'s fixture
seeding + forward/assert style (audit-first ordering, ``eid.hex`` for raw
UUIDs, direct-module import for the loud-raise case). NO population count or
scenario name from any deployment appears anywhere in this file -- every
fixture value here is synthetic, chosen only to exercise the migration's
branch logic.
"""

from __future__ import annotations

import importlib.util
import json
import math
import uuid
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine

import idraa

_PRE_REV = "b3f8a2d94c1e"  # single head before this migration
_REV = "ffed7c509563"  # the capacity-max-backfill migration under test

_K_CAPACITY = 1.0
_Z95 = 1.6448536269514722


def _ln_p95(mean: float, sigma: float) -> float:
    return mean + _Z95 * sigma


def _versions_dir() -> Path:
    return Path(idraa.__file__).resolve().parent.parent.parent / "alembic" / "versions"


def _import_migration_module() -> Any:
    mig_path = next(_versions_dir().glob(f"{_REV}_*.py"))
    spec = importlib.util.spec_from_file_location("_capacity_max_backfill_mig", mig_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed_org(conn: sa.Connection, *, revenue: float | None, name: str = "Test Org") -> str:
    """Insert a minimal schema-valid organization row and return its id
    (no-hyphen hex, mirroring the raw-text-seed-UUID convention used
    everywhere else in this test suite)."""
    org_id = uuid.uuid4().hex
    conn.execute(
        sa.text(
            "INSERT INTO organizations "
            "(id, created_at, updated_at, name, organization_size, "
            "industry_type, security_maturity, risk_appetite, "
            "preferred_currency, preferred_language, "
            "geographic_regions, compliance_requirements, "
            "regulatory_environment, technology_stack, "
            "has_cyber_insurance, annual_revenue) VALUES "
            "(:id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, :name, "
            "'large', 'information', 'defined', 'moderate', "
            "'USD', 'en', '[]', '[]', '[]', '[]', 0, :revenue)"
        ),
        {"id": org_id, "name": name, "revenue": revenue},
    )
    return org_id


def _seed_scenario(
    conn: sa.Connection,
    *,
    organization_id: str,
    pl_node: dict[str, Any] | None = None,
    sl_node: dict[str, Any] | None = None,
    sl_raw: str | None = None,
    name: str = "probe",
    row_version: int = 1,
) -> str:
    """Insert a minimal schema-valid scenario row (FKs off) and return its id.

    PRAGMA-driven placeholder fill (precedent:
    tests/migrations/test_sigma_recalibration_migration.py) so the insert
    stays durable as later migrations add NOT NULL columns. ``sl_raw``
    bypasses ``json.dumps`` entirely -- for seeding the literal 4-char JSON
    text ``"null"``.
    """
    explicit: dict[str, object] = {
        "id": uuid.uuid4().hex,
        "organization_id": organization_id,
        "name": name,
        "scenario_type": "CUSTOM",
        "threat_category": "ransomware",
        "threat_event_frequency": '{"distribution":"PERT","low":1,"mode":2,"high":3}',
        "vulnerability": '{"distribution":"PERT","low":0.1,"mode":0.2,"high":0.3}',
        "primary_loss": json.dumps(
            pl_node
            if pl_node is not None
            else {"distribution": "PERT", "low": 1000, "mode": 2000, "high": 3000}
        ),
        "overlay_pins": "[]",
        "source": "expert_judgment",
        "status": "ACTIVE",
        "version": "1.0",
        "row_version": row_version,
    }
    if sl_raw is not None:
        assert sl_node is None, "sl_node and sl_raw are mutually exclusive"
        explicit["secondary_loss"] = sl_raw
    elif sl_node is not None:
        explicit["secondary_loss"] = json.dumps(sl_node)
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


def _get_scenario(conn: sa.Connection, sid: str) -> sa.RowMapping:
    return (
        conn.execute(
            sa.text(
                "SELECT primary_loss, secondary_loss, row_version FROM scenarios WHERE id = :id"
            ),
            {"id": sid},
        )
        .mappings()
        .one()
    )


def _audit_rows(conn: sa.Connection, sid: str | None = None) -> list[sa.RowMapping]:
    q = (
        "SELECT id, organization_id, entity_type, entity_id, user_id, action, changes, "
        "timestamp FROM audit_log WHERE action = 'scenario.backfill_capacity_max'"
    )
    if sid is not None:
        q += " AND entity_id = :sid"
        return list(conn.execute(sa.text(q), {"sid": sid}).mappings().all())
    return list(conn.execute(sa.text(q)).mappings().all())


# ---------------------------------------------------------------------------
# Point 1: own-org capacity via a JOIN, never first-org-wins.
# ---------------------------------------------------------------------------


def test_two_org_fixture_each_scenario_gets_its_own_org_capacity(
    alembic_config: Config, alembic_engine: Engine
) -> None:
    catastrophic_pl = {"distribution": "lognormal", "mean": math.log(1_000_000.0), "sigma": 1.7}
    command.upgrade(alembic_config, _PRE_REV)
    with alembic_engine.begin() as conn:
        org_a = _seed_org(conn, revenue=1_000_000_000.0, name="Org A")
        org_b = _seed_org(conn, revenue=5_000_000_000.0, name="Org B")
        sid_a = _seed_scenario(conn, organization_id=org_a, pl_node=dict(catastrophic_pl))
        sid_b = _seed_scenario(conn, organization_id=org_b, pl_node=dict(catastrophic_pl))
    command.upgrade(alembic_config, _REV)

    with alembic_engine.connect() as conn:
        node_a = json.loads(_get_scenario(conn, sid_a)["primary_loss"])
        node_b = json.loads(_get_scenario(conn, sid_b)["primary_loss"])
        assert node_a["max"] == pytest.approx(_K_CAPACITY * 1_000_000_000.0)
        assert node_b["max"] == pytest.approx(_K_CAPACITY * 5_000_000_000.0)
        assert node_a["max"] != node_b["max"]  # never first-org-wins


# ---------------------------------------------------------------------------
# Point 2: Postgres-safe JSON parse -- unexpected type fails loud, and that
# raise is NOT swallowed by the per-row skip handler.
# ---------------------------------------------------------------------------


def test_unexpected_column_type_raises_not_swallowed(
    alembic_config: Config, alembic_engine: Engine
) -> None:
    command.upgrade(alembic_config, _PRE_REV)
    with alembic_engine.begin() as conn:
        org_id = _seed_org(conn, revenue=1_000_000_000.0)
        sid = _seed_scenario(conn, organization_id=org_id)
        # Bind an INTEGER (not str/bytes/dict/None) into the JSON-typed
        # column via a raw parameterised UPDATE -- SQLite's NUMERIC-affinity
        # JSON column preserves the INTEGER storage class untouched (empirically
        # verified: a str value inserted this way IS converted to TEXT, but an
        # int bound directly is stored -- and read back -- as a Python int).
        # This is what a genuinely unexpected driver-returned type looks like.
        conn.execute(
            sa.text("UPDATE scenarios SET primary_loss = :v WHERE id = :id"),
            {"v": 12345, "id": sid},
        )

    mod = _import_migration_module()
    with (
        alembic_engine.connect() as conn,
        pytest.raises(mod._UnexpectedJsonColumnTypeError),
        conn.begin(),
    ):
        mod._upgrade_scenarios(conn)


# ---------------------------------------------------------------------------
# Point 3: literal JSON text "null" -> None -> skipped, no raise.
# ---------------------------------------------------------------------------


def test_literal_null_text_secondary_loss_skipped_no_raise(
    alembic_config: Config, alembic_engine: Engine
) -> None:
    catastrophic_pl = {"distribution": "lognormal", "mean": math.log(1_000_000.0), "sigma": 1.7}
    command.upgrade(alembic_config, _PRE_REV)
    with alembic_engine.begin() as conn:
        org_id = _seed_org(conn, revenue=1_000_000_000.0)
        sid = _seed_scenario(conn, organization_id=org_id, pl_node=catastrophic_pl, sl_raw="null")
    command.upgrade(alembic_config, _REV)  # must complete without raising

    with alembic_engine.connect() as conn:
        row = _get_scenario(conn, sid)
        pl_node = json.loads(row["primary_loss"])
        assert pl_node["max"] == pytest.approx(1_000_000_000.0)  # PL still minted
        assert row["secondary_loss"] == "null"  # SL untouched, still literal text
        rows = _audit_rows(conn, sid)
        assert len(rows) == 1  # only PL's audit row -- no SL row, no crash
        assert json.loads(rows[0]["changes"])["field"] == "primary_loss"


# ---------------------------------------------------------------------------
# Point 4: idempotent; never overwrites an explicit max; PERT untouched.
# ---------------------------------------------------------------------------


def test_idempotent_second_run_mints_nothing_more(
    alembic_config: Config, alembic_engine: Engine
) -> None:
    catastrophic_pl = {"distribution": "lognormal", "mean": math.log(1_000_000.0), "sigma": 1.7}
    command.upgrade(alembic_config, _PRE_REV)
    with alembic_engine.begin() as conn:
        org_id = _seed_org(conn, revenue=1_000_000_000.0)
        sid = _seed_scenario(conn, organization_id=org_id, pl_node=dict(catastrophic_pl))
    command.upgrade(alembic_config, _REV)

    with alembic_engine.connect() as conn:
        first_pass = json.loads(_get_scenario(conn, sid)["primary_loss"])
        first_row_version = _get_scenario(conn, sid)["row_version"]
        first_audit_count = len(_audit_rows(conn, sid))

    mod = _import_migration_module()
    with alembic_engine.connect() as conn, conn.begin():
        swept_again = mod._upgrade_scenarios(conn)
    assert swept_again == 0

    with alembic_engine.connect() as conn:
        second_pass = json.loads(_get_scenario(conn, sid)["primary_loss"])
        assert second_pass == first_pass
        assert _get_scenario(conn, sid)["row_version"] == first_row_version
        assert len(_audit_rows(conn, sid)) == first_audit_count


def test_explicit_max_never_overwritten(alembic_config: Config, alembic_engine: Engine) -> None:
    authored_pl = {
        "distribution": "lognormal",
        "mean": math.log(1_000_000.0),
        "sigma": 1.7,
        "max": 42_000_000.0,
    }
    command.upgrade(alembic_config, _PRE_REV)
    with alembic_engine.begin() as conn:
        org_id = _seed_org(conn, revenue=1_000_000_000.0)
        sid = _seed_scenario(conn, organization_id=org_id, pl_node=dict(authored_pl))
    command.upgrade(alembic_config, _REV)

    with alembic_engine.connect() as conn:
        row = _get_scenario(conn, sid)
        assert json.loads(row["primary_loss"]) == authored_pl  # byte-for-byte unchanged
        assert row["row_version"] == 1  # never bumped
        assert _audit_rows(conn, sid) == []


def test_pert_field_untouched(alembic_config: Config, alembic_engine: Engine) -> None:
    pert_pl = {"distribution": "PERT", "low": 100_000.0, "mode": 200_000.0, "high": 500_000.0}
    command.upgrade(alembic_config, _PRE_REV)
    with alembic_engine.begin() as conn:
        org_id = _seed_org(conn, revenue=1_000_000_000.0)
        sid = _seed_scenario(conn, organization_id=org_id, pl_node=dict(pert_pl))
    command.upgrade(alembic_config, _REV)

    with alembic_engine.connect() as conn:
        row = _get_scenario(conn, sid)
        node = json.loads(row["primary_loss"])
        assert node == pert_pl  # no max key emitted
        assert "max" not in node
        assert row["row_version"] == 1
        assert _audit_rows(conn, sid) == []


# ---------------------------------------------------------------------------
# Point 5: lognormal_mixture backfilled with ONE shared top-level max, floor
# checked against EVERY component's p95.
# ---------------------------------------------------------------------------


def test_mixture_backfilled_with_one_shared_max_across_every_component(
    alembic_config: Config, alembic_engine: Engine
) -> None:
    # comp0: smaller sigma, larger mean. comp1: larger sigma, smaller mean --
    # but its p95 is the BINDING (worst) one, proving "every component" is
    # actually checked rather than the largest-mean component.
    comp0 = {"mean": math.log(1_000_000.0), "sigma": 1.0, "weight": 0.5}
    comp1 = {"mean": math.log(200_000.0), "sigma": 2.5, "weight": 0.5}
    assert _ln_p95(comp1["mean"], comp1["sigma"]) > _ln_p95(comp0["mean"], comp0["sigma"])
    mixture_pl = {
        "distribution": "lognormal_mixture",
        "components": [dict(comp0), dict(comp1)],
    }
    command.upgrade(alembic_config, _PRE_REV)
    with alembic_engine.begin() as conn:
        org_id = _seed_org(conn, revenue=1_000_000_000.0)
        sid = _seed_scenario(conn, organization_id=org_id, pl_node=dict(mixture_pl))
    command.upgrade(alembic_config, _REV)

    with alembic_engine.connect() as conn:
        node = json.loads(_get_scenario(conn, sid)["primary_loss"])
        assert node["max"] == pytest.approx(1_000_000_000.0)  # top-level, shared
        assert "max" not in node["components"][0]  # not per-component
        assert "max" not in node["components"][1]
        assert node["components"][0] == comp0  # components otherwise unchanged
        assert node["components"][1] == comp1


def test_mixture_floor_conflict_on_worst_component_skips(
    alembic_config: Config, alembic_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    # comp1's p95 sits ABOVE the candidate cap (revenue) even though comp0's
    # p95 alone would clear it -- proves the floor is checked against the
    # WORST component, not just one arbitrary component.
    #
    # NOTE on capsys-vs-caplog: alembic/env.py calls logging.config.fileConfig()
    # on every command.upgrade() call, which (per stdlib logging.config's
    # documented child-logger reset -- ANY already-existing child of a
    # configured logger has its OWN handlers/level wiped and propagate reset
    # to True, even with disable_existing_loggers=False) strips any handler
    # caplog attaches to "alembic.runtime.migration" mid-test. The WARNING
    # still reaches the ini's console handler -> sys.stderr, so capsys (which
    # captures the sys.stderr Python object, resolved fresh by StreamHandler
    # at each fileConfig call) sees it where caplog cannot. Verified
    # empirically against this repo's alembic.ini before writing this test.
    comp0 = {"mean": math.log(1_000.0), "sigma": 0.5, "weight": 0.5}
    comp1 = {"mean": math.log(2_000_000.0), "sigma": 1.7, "weight": 0.5}
    mixture_pl = {
        "distribution": "lognormal_mixture",
        "components": [dict(comp0), dict(comp1)],
    }
    command.upgrade(alembic_config, _PRE_REV)
    with alembic_engine.begin() as conn:
        # revenue picked so k*revenue's log sits below comp1's p95 alone.
        org_id = _seed_org(conn, revenue=1_000.0)
        sid = _seed_scenario(conn, organization_id=org_id, pl_node=dict(mixture_pl))
    command.upgrade(alembic_config, _REV)

    with alembic_engine.connect() as conn:
        node = json.loads(_get_scenario(conn, sid)["primary_loss"])
        assert "max" not in node
        assert _audit_rows(conn, sid) == []
    assert "floor conflict" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Point 6: NULL / <= 0 revenue -> skip + WARNING.
# ---------------------------------------------------------------------------


def test_null_revenue_skips_with_warning(
    alembic_config: Config, alembic_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    catastrophic_pl = {"distribution": "lognormal", "mean": math.log(1_000_000.0), "sigma": 1.7}
    command.upgrade(alembic_config, _PRE_REV)
    with alembic_engine.begin() as conn:
        org_id = _seed_org(conn, revenue=None)
        sid = _seed_scenario(conn, organization_id=org_id, pl_node=dict(catastrophic_pl))
    command.upgrade(alembic_config, _REV)

    with alembic_engine.connect() as conn:
        node = json.loads(_get_scenario(conn, sid)["primary_loss"])
        assert "max" not in node
        assert _audit_rows(conn, sid) == []
    assert "NULL or <= 0" in capsys.readouterr().err, "expected a NULL/<=0 revenue WARNING"


def test_nonpositive_revenue_skips_with_warning(
    alembic_config: Config, alembic_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    catastrophic_pl = {"distribution": "lognormal", "mean": math.log(1_000_000.0), "sigma": 1.7}
    command.upgrade(alembic_config, _PRE_REV)
    with alembic_engine.begin() as conn:
        org_id = _seed_org(conn, revenue=0.0)
        sid = _seed_scenario(conn, organization_id=org_id, pl_node=dict(catastrophic_pl))
    command.upgrade(alembic_config, _REV)

    with alembic_engine.connect() as conn:
        node = json.loads(_get_scenario(conn, sid)["primary_loss"])
        assert "max" not in node
        assert _audit_rows(conn, sid) == []
    assert "NULL or <= 0" in capsys.readouterr().err


def test_lognormal_floor_conflict_skips_with_warning(
    alembic_config: Config, alembic_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    # revenue = 1e6 -> candidate = 1e6, well below this field's own p95
    # (~1.647e7 at mean=ln(1e6), sigma=1.7).
    conflicting_pl = {"distribution": "lognormal", "mean": math.log(1_000_000.0), "sigma": 1.7}
    command.upgrade(alembic_config, _PRE_REV)
    with alembic_engine.begin() as conn:
        org_id = _seed_org(conn, revenue=1_000_000.0)
        sid = _seed_scenario(conn, organization_id=org_id, pl_node=dict(conflicting_pl))
    command.upgrade(alembic_config, _REV)

    with alembic_engine.connect() as conn:
        node = json.loads(_get_scenario(conn, sid)["primary_loss"])
        assert "max" not in node
        assert _audit_rows(conn, sid) == []
    assert "floor conflict" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Point 7: audit row FIRST, full-column INSERT, json.dumps payload, eid.hex
# raw UUIDs; THEN the UPDATE with SQL-side row_version increment.
# ---------------------------------------------------------------------------


def test_audit_row_full_shape_and_row_version_sql_side_increment(
    alembic_config: Config, alembic_engine: Engine
) -> None:
    catastrophic_pl = {"distribution": "lognormal", "mean": math.log(1_000_000.0), "sigma": 1.7}
    command.upgrade(alembic_config, _PRE_REV)
    with alembic_engine.begin() as conn:
        org_id = _seed_org(conn, revenue=1_000_000_000.0)
        sid = _seed_scenario(
            conn, organization_id=org_id, pl_node=dict(catastrophic_pl), row_version=5
        )
    command.upgrade(alembic_config, _REV)

    with alembic_engine.connect() as conn:
        rows = _audit_rows(conn, sid)
        assert len(rows) == 1
        audit = rows[0]
        assert audit["entity_type"] == "scenario"
        assert audit["entity_id"] == sid
        assert audit["user_id"] is None
        assert audit["action"] == "scenario.backfill_capacity_max"
        assert audit["organization_id"] == org_id
        assert audit["timestamp"] is not None
        assert "-" not in audit["id"]  # eid.hex -- no-hyphen hex id
        changes = json.loads(audit["changes"])
        assert changes["field"] == "primary_loss"
        assert changes["prior"] == catastrophic_pl  # full prior dict, max-less
        assert changes["max"] == pytest.approx(1_000_000_000.0)

        row = _get_scenario(conn, sid)
        assert row["row_version"] == 6  # bumped exactly once, SQL-side


def test_audit_action_string_fits_string_64() -> None:
    """Arch-N2 precedent: the new audit action verb fits the String(64) column."""
    assert len("scenario.backfill_capacity_max") <= 64


# ---------------------------------------------------------------------------
# End-to-end wiring: upgrade() actually calls the sweep (not helper-only).
# ---------------------------------------------------------------------------


def test_upgrade_wires_the_sweep(alembic_config: Config, alembic_engine: Engine) -> None:
    catastrophic_pl = {"distribution": "lognormal", "mean": math.log(1_000_000.0), "sigma": 1.7}
    command.upgrade(alembic_config, _PRE_REV)
    with alembic_engine.begin() as conn:
        org_id = _seed_org(conn, revenue=1_000_000_000.0)
        sid = _seed_scenario(conn, organization_id=org_id, pl_node=dict(catastrophic_pl))

    command.upgrade(alembic_config, _REV)  # single end-to-end command.upgrade call

    with alembic_engine.connect() as conn:
        node = json.loads(_get_scenario(conn, sid)["primary_loss"])
        assert node["max"] == pytest.approx(1_000_000_000.0)
        assert len(_audit_rows(conn, sid)) == 1
