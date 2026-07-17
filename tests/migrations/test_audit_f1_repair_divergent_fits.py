"""Audit-F1 migration tests: repair_divergent_lognorm_fits (e3a1c4f7b2d9).

Per docs/superpowers/specs/2026-06-10-audit-remediation-f1-f2-design.md:
- corrupt-signature node -> repaired to the closed-form native lognormal,
  sidecar repair provenance, audit row (no-hyphen hex ids, user_id NULL),
  row_version bumped;
- converged legacy node -> byte-identical (repair-only, not migrate-all);
- guards: no SME rows / sigma > 10 -> untouched + skipped;
- malformed JSON in one candidate row -> skipped, others still repaired;
- idempotency property: fitter == "lognorm_native" rows are non-candidates.

Uses the shared alembic_config/alembic_engine fixtures (sync engine queries,
async-driver alembic URL) per tests/migrations/conftest.py.
"""

from __future__ import annotations

import json
import math
import uuid

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine

_PRE_REV = "d6b8e2f0a719"  # head before F1
_F1_REV = "e3a1c4f7b2d9"  # the repair migration

Z_0_95 = 1.6448536269514722

# The prod corruption signature (verified 2026-06-10): analyst entered
# (388920.4, 20158366.58) for PL; the divergent fitter stored low=mode~=5e-8.
_SME_LOW, _SME_HIGH = 388920.4, 20158366.58
_CORRUPT_PL = {
    "low": 5.26062872707381e-08,
    "mode": 5.26062872707381e-08,
    "high": 20158366.58326771,
    "distribution_fit_metadata": {
        "source": "quantile_lognormal_pool",
        "fitter": "lognorm_trunc",
        "schema_version": 2,
        "n_smes": 1,
    },
}

# Expected hand-math (side-by-side convention):
#   meanlog = (ln 388920.4 + ln 20158366.58) / 2          ~= 14.8447
#   sigma   = (ln 20158366.58 - ln 388920.4) / (2*Z_0.95) ~= 1.2003
_EXPECTED_MEANLOG = (math.log(_SME_LOW) + math.log(_SME_HIGH)) / 2.0
_EXPECTED_SIGMA = (math.log(_SME_HIGH) - math.log(_SME_LOW)) / (2.0 * Z_0_95)


def _seed_scenario(
    conn: sa.Connection,
    *,
    pl_node: dict,
    name: str = "probe",
    row_version: int = 1,
) -> str:
    """Insert a minimal schema-valid scenario row (FKs off) and return its id.

    PRAGMA-driven placeholder fill (precedent: test_scenario_source_file_import)
    so the insert stays durable as later migrations add NOT NULL columns.
    """
    explicit: dict[str, object] = {
        "id": uuid.uuid4().hex,
        "organization_id": uuid.uuid4().hex,
        "name": name,
        "scenario_type": "CUSTOM",
        "threat_category": "ransomware",
        "threat_event_frequency": '{"distribution":"PERT","low":1,"mode":2,"high":3}',
        "vulnerability": '{"distribution":"PERT","low":0.1,"mode":0.2,"high":0.3}',
        "primary_loss": json.dumps(pl_node),
        "overlay_pins": "[]",
        "source": "expert_judgment",
        "status": "ACTIVE",
        "version": "1.0",
        "row_version": row_version,
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
    conn.execute(sa.text("PRAGMA foreign_keys = OFF"))
    conn.execute(
        sa.text(f"INSERT INTO scenarios ({column_list}) VALUES ({placeholders})"),  # noqa: S608
        values,
    )
    return str(explicit["id"])


def _seed_sme_row(
    conn: sa.Connection,
    *,
    scenario_id: str,
    fieldset: str,
    low: float,
    high: float,
) -> None:
    explicit: dict[str, object] = {
        "id": uuid.uuid4().hex,
        "organization_id": uuid.uuid4().hex,
        "scenario_id": scenario_id,
        "fieldset": fieldset,
        "sme_id": uuid.uuid4().hex,
        "low": low,
        "high": high,
        "recorded_at": "2026-06-08 10:00:00",
        "recorded_by": uuid.uuid4().hex,
    }
    cols = conn.execute(sa.text("PRAGMA table_info(scenario_sme_estimates)")).mappings().all()
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
        sa.text(
            f"INSERT INTO scenario_sme_estimates ({column_list}) VALUES ({placeholders})"  # noqa: S608
        ),
        values,
    )


def _get_scenario(conn: sa.Connection, sid: str) -> sa.RowMapping:
    return (
        conn.execute(
            sa.text("SELECT primary_loss, row_version FROM scenarios WHERE id = :id"),
            {"id": sid},
        )
        .mappings()
        .one()
    )


def test_corrupt_node_repaired_to_closed_form(
    alembic_config: Config, alembic_engine: Engine
) -> None:
    command.upgrade(alembic_config, _PRE_REV)
    with alembic_engine.begin() as conn:
        sid = _seed_scenario(conn, pl_node=_CORRUPT_PL, row_version=3)
        _seed_sme_row(conn, scenario_id=sid, fieldset="pl", low=_SME_LOW, high=_SME_HIGH)
    command.upgrade(alembic_config, _F1_REV)

    with alembic_engine.connect() as conn:
        row = _get_scenario(conn, sid)
        node = json.loads(row["primary_loss"])
        # Repaired to the closed-form native lognormal (hand-math pinned above).
        assert node["distribution"] == "lognormal"
        assert node["mean"] == pytest.approx(_EXPECTED_MEANLOG, rel=1e-12)
        assert node["sigma"] == pytest.approx(_EXPECTED_SIGMA, rel=1e-12)
        assert 0 < node["sigma"] <= 10
        meta = node["distribution_fit_metadata"]
        assert meta["fitter"] == "lognorm_native"
        assert meta["repaired_from_fitter"] == "lognorm_trunc"
        assert meta["repair_reason"] == "divergent_optimizer_fit"
        assert meta["repaired_by_migration"] == _F1_REV
        assert meta["n_smes"] == 1
        # row_version bumped (CAS consumers see motion).
        assert row["row_version"] == 4
        # Audit row: action + no-hyphen hex ids + user_id NULL (system actor).
        audit = (
            conn.execute(
                sa.text(
                    "SELECT id, entity_id, user_id, changes FROM audit_log "
                    "WHERE action = 'scenario.repair_distribution'"
                )
            )
            .mappings()
            .all()
        )
        assert len(audit) == 1
        assert audit[0]["entity_id"] == sid  # exact stored format, no re-hex
        assert "-" not in audit[0]["id"]
        assert audit[0]["user_id"] is None
        changes = json.loads(audit[0]["changes"])
        assert changes["primary_loss"][0]["low"] == _CORRUPT_PL["low"]  # old node preserved
        assert changes["primary_loss"][1]["mean"] == pytest.approx(_EXPECTED_MEANLOG)


def test_converged_legacy_node_left_byte_identical(
    alembic_config: Config, alembic_engine: Engine
) -> None:
    """A lognorm_trunc node whose stored low ~= the re-derived p5 is NOT touched
    (repair-only, not migrate-all)."""
    rederived_p5 = math.exp(_EXPECTED_MEANLOG - Z_0_95 * _EXPECTED_SIGMA)
    converged = dict(_CORRUPT_PL)
    converged = json.loads(json.dumps(_CORRUPT_PL))
    converged["low"] = rederived_p5  # ratio ~= 1 -> under the ln(10) threshold
    converged["mode"] = 1_000_000.0
    command.upgrade(alembic_config, _PRE_REV)
    with alembic_engine.begin() as conn:
        sid = _seed_scenario(conn, pl_node=converged, row_version=2)
        _seed_sme_row(conn, scenario_id=sid, fieldset="pl", low=_SME_LOW, high=_SME_HIGH)
    command.upgrade(alembic_config, _F1_REV)
    with alembic_engine.connect() as conn:
        row = _get_scenario(conn, sid)
        assert json.loads(row["primary_loss"]) == converged  # byte-identical content
        assert row["row_version"] == 2  # no bump
        n_audit = conn.execute(
            sa.text("SELECT count(*) FROM audit_log WHERE action='scenario.repair_distribution'")
        ).scalar_one()
        assert n_audit == 0


def test_repair_threshold_boundary(alembic_config: Config, alembic_engine: Engine) -> None:
    """Meth-N-1: pin the |ln(ratio)| > ln(10) boundary — a stored low 9x off
    the re-derived p5 is left alone; 11x off is repaired."""
    rederived_p5 = math.exp(_EXPECTED_MEANLOG - Z_0_95 * _EXPECTED_SIGMA)
    near = json.loads(json.dumps(_CORRUPT_PL))
    near["low"] = rederived_p5 * 9.0  # |ln 9| < ln 10 -> converged, no repair
    far = json.loads(json.dumps(_CORRUPT_PL))
    far["low"] = rederived_p5 * 11.0  # |ln 11| > ln 10 -> repaired
    command.upgrade(alembic_config, _PRE_REV)
    with alembic_engine.begin() as conn:
        near_sid = _seed_scenario(conn, pl_node=near, name="near")
        _seed_sme_row(conn, scenario_id=near_sid, fieldset="pl", low=_SME_LOW, high=_SME_HIGH)
        far_sid = _seed_scenario(conn, pl_node=far, name="far")
        _seed_sme_row(conn, scenario_id=far_sid, fieldset="pl", low=_SME_LOW, high=_SME_HIGH)
    command.upgrade(alembic_config, _F1_REV)
    with alembic_engine.connect() as conn:
        assert json.loads(_get_scenario(conn, near_sid)["primary_loss"]) == near
        far_node = json.loads(_get_scenario(conn, far_sid)["primary_loss"])
        assert far_node["distribution_fit_metadata"]["fitter"] == "lognorm_native"


def test_guard_no_sme_rows_leaves_node_untouched(
    alembic_config: Config, alembic_engine: Engine
) -> None:
    command.upgrade(alembic_config, _PRE_REV)
    with alembic_engine.begin() as conn:
        sid = _seed_scenario(conn, pl_node=_CORRUPT_PL)
        # no SME rows seeded
    command.upgrade(alembic_config, _F1_REV)
    with alembic_engine.connect() as conn:
        row = _get_scenario(conn, sid)
        assert json.loads(row["primary_loss"]) == _CORRUPT_PL
        assert row["row_version"] == 1


def test_guard_sigma_over_ten_not_written(alembic_config: Config, alembic_engine: Engine) -> None:
    """SME rows spanning >14 orders re-derive sigma > 10 — the migration must
    NOT write a node the Sec-I2 storage guard would reject."""
    command.upgrade(alembic_config, _PRE_REV)
    with alembic_engine.begin() as conn:
        sid = _seed_scenario(conn, pl_node=_CORRUPT_PL)
        _seed_sme_row(conn, scenario_id=sid, fieldset="pl", low=1000.0, high=1.0e18)
    command.upgrade(alembic_config, _F1_REV)
    with alembic_engine.connect() as conn:
        row = _get_scenario(conn, sid)
        assert json.loads(row["primary_loss"]) == _CORRUPT_PL  # untouched
        assert row["row_version"] == 1


def test_malformed_json_row_skipped_others_repaired(
    alembic_config: Config, alembic_engine: Engine
) -> None:
    """Plan-gate SC-B2: a single malformed candidate must not abort the run."""
    command.upgrade(alembic_config, _PRE_REV)
    with alembic_engine.begin() as conn:
        bad_sid = _seed_scenario(conn, pl_node=_CORRUPT_PL, name="bad")
        conn.execute(
            sa.text("UPDATE scenarios SET primary_loss = :junk WHERE id = :id"),
            {"junk": "{not valid json", "id": bad_sid},
        )
        good_sid = _seed_scenario(conn, pl_node=_CORRUPT_PL, name="good")
        _seed_sme_row(conn, scenario_id=good_sid, fieldset="pl", low=_SME_LOW, high=_SME_HIGH)
    command.upgrade(alembic_config, _F1_REV)
    with alembic_engine.connect() as conn:
        good = json.loads(_get_scenario(conn, good_sid)["primary_loss"])
        assert good["distribution_fit_metadata"]["fitter"] == "lognorm_native"
        bad = _get_scenario(conn, bad_sid)["primary_loss"]
        assert bad == "{not valid json"  # skipped verbatim, migration completed


def test_lognorm_native_rows_are_non_candidates(
    alembic_config: Config, alembic_engine: Engine
) -> None:
    """Idempotency property: repaired rows (fitter == lognorm_native) are
    structurally excluded from the candidate scan."""
    native = json.loads(json.dumps(_CORRUPT_PL))
    native["distribution_fit_metadata"]["fitter"] = "lognorm_native"
    command.upgrade(alembic_config, _PRE_REV)
    with alembic_engine.begin() as conn:
        sid = _seed_scenario(conn, pl_node=native)
        _seed_sme_row(conn, scenario_id=sid, fieldset="pl", low=_SME_LOW, high=_SME_HIGH)
    command.upgrade(alembic_config, _F1_REV)
    with alembic_engine.connect() as conn:
        row = _get_scenario(conn, sid)
        assert json.loads(row["primary_loss"]) == native
        assert row["row_version"] == 1


def test_audit_action_strings_fit_string_64() -> None:
    """Arch-N2: both new audit action verbs fit the String(64) column."""
    assert len("scenario.repair_distribution") <= 64
    assert len("scenario.confirm_vuln_framing") <= 64
