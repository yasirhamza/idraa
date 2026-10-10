"""b00d4a63cd9c: issue #234 crosswalk-rationales repair (spec §4.2, §4.4). Mirrors
tests/migrations/test_issue_203_seed_industries.py (its twelve tests, adapted to
library_entry_attack_mappings rows keyed by (slug, domain, technique_id)) plus the
``scenario_attack_mappings`` untouched check; loads the migration module by path."""

from __future__ import annotations

import importlib.util
import json
import re
import uuid
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from pytest_alembic import MigrationContext
from sqlalchemy.engine import Engine

import idraa
from scripts import build_issue_234_migration_table as gen

_ROOT = Path(idraa.__file__).resolve().parent.parent.parent
_VERSIONS = _ROOT / "alembic" / "versions"
_FIXTURES = Path(__file__).parent / "fixtures"
_MAPPING_FILES = ("seed_attack_full_mappings.json", "seed_attack_d_iii_b_full.json")
# Fresh DB: the seeding migrations (617f5ca862c3, a5b6c7d8e9f0) load the LIVE, fixed JSON, so
# every cell is already new by the time this revision runs. Production (old values): 14 applied.
_COUNTER_FRESH = "issue-234 crosswalk rationales: applied=0 already_new=14 drift=0"
# The second-application path is write-old -> upgrade -> stamp(PRE) -> upgrade: the re-run after
# the stamp-back finds every cell already new.
_COUNTER_SECOND = "issue-234 crosswalk rationales: applied=0 already_new=14 drift=0"
_COUNTER_FROM_OLD = "issue-234 crosswalk rationales: applied=14 already_new=0 drift=0"
_EXPECTED_COLUMNS = frozenset({"rationale", "provenance", "citations"})
_EXPECTED_JSON_COLUMNS = frozenset({"citations"})
# A FOUR-row, EIGHT-cell slug (T1005 rationale; T1027 and T1567 each rationale + provenance +
# citations; T1078 rationale): the atomic-group assertions are vacuous on a one-cell slug. Drift
# is injected on one of its rationale cells; the other seven must stay put.
_BIG_SLUG = "insider-ip-theft-manufacturing"
_BIG_SLUG_DRIFT = ("enterprise", "T1027", "rationale")
_BIG_SLUG_CELLS = 8
# A ONE-cell slug (T1498 rationale) for the missing-row test.
_ONE_CELL_SLUG = "higher-ed-insider-ddos"
_ONE_CELL_ROW = ("enterprise", "T1498")

_Cell = tuple[str, str, str, str]  # (slug, domain, technique_id, column)


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


(_MIG,) = _VERSIONS.glob("b00d4a63cd9c_*.py")
mod = _load(_MIG, "_test_mig_b00d4a63cd9c")
PRE, REV = mod.down_revision, mod.revision
N_CELLS = len(mod._CHANGES)

_ROW_ID_SQL = (
    "SELECT m.id FROM library_entry_attack_mappings m "
    "JOIN scenario_library_entries e "
    "ON e.id = m.library_entry_id AND e.version = m.library_entry_version "
    "JOIN attack_techniques t ON t.id = m.technique_id "
    "WHERE e.slug = :s AND e.version = 1 AND e.source = 'seed' "
    "AND t.domain = :d AND t.technique_id = :t"
)


def _seed_by_key() -> dict[tuple[str, str, str], dict[str, object]]:
    rows: dict[tuple[str, str, str], dict[str, object]] = {}
    for name in _MAPPING_FILES:
        payload = json.loads((_ROOT / "data" / name).read_text(encoding="utf-8"))
        for m in payload["mappings"]:
            rows[(m["entry_slug"], m["domain"], m["technique_id"])] = m
    return rows


def _row_id(engine: Engine, slug: str, domain: str, technique_id: str) -> str:
    with engine.connect() as conn:
        found = conn.execute(
            sa.text(_ROW_ID_SQL), {"s": slug, "d": domain, "t": technique_id}
        ).fetchall()
    assert len(found) == 1, (slug, domain, technique_id, len(found))
    return str(found[0][0])


def _write_column(
    engine: Engine, slug: str, domain: str, technique_id: str, column: str, value: object
) -> None:
    assert column in mod._ALLOWED_COLUMNS
    row_id = _row_id(engine, slug, domain, technique_id)
    stmt = f"UPDATE library_entry_attack_mappings SET {column} = :v WHERE id = :i"  # noqa: S608 - column asserted above
    with engine.begin() as conn:
        conn.execute(sa.text(stmt), {"v": mod._encode(column, value), "i": row_id})


def _read_column(engine: Engine, slug: str, domain: str, technique_id: str, column: str) -> object:
    assert column in mod._ALLOWED_COLUMNS
    row_id = _row_id(engine, slug, domain, technique_id)
    stmt = f"SELECT {column} FROM library_entry_attack_mappings WHERE id = :i"  # noqa: S608 - column asserted above
    with engine.connect() as conn:
        row = conn.execute(sa.text(stmt), {"i": row_id}).fetchone()
    return None if row is None else row[0]


def _write_all_old(engine: Engine) -> None:
    for slug, domain, technique_id, column, old, _new in mod._CHANGES:
        _write_column(engine, slug, domain, technique_id, column, old)


def _dump_mappings(engine: Engine) -> dict[object, dict[str, object]]:
    with engine.connect() as conn:
        rows = (
            conn.execute(sa.text("SELECT * FROM library_entry_attack_mappings ORDER BY id"))
            .mappings()
            .all()
        )
    return {r["id"]: dict(r) for r in rows}


def _dump_scenario_mappings(engine: Engine) -> list[dict[str, object]]:
    with engine.connect() as conn:
        rows = (
            conn.execute(sa.text("SELECT * FROM scenario_attack_mappings ORDER BY id"))
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


def _assert_all_new(engine: Engine) -> None:
    for slug, domain, technique_id, column, _old, new in mod._CHANGES:
        current = mod._decode(column, _read_column(engine, slug, domain, technique_id, column))
        assert current == new, (slug, technique_id, column)


def _delete_mapping_row(engine: Engine, slug: str, domain: str, technique_id: str) -> None:
    row_id = _row_id(engine, slug, domain, technique_id)
    with engine.begin() as conn:
        conn.execute(
            sa.text("DELETE FROM library_entry_attack_mappings WHERE id = :i"), {"i": row_id}
        )


def _seed_row(conn: sa.Connection, table: str, explicit: dict[str, object]) -> None:
    """Insert with NOT-NULL filler for unlisted columns (the audit-F2 pattern used by
    tests/migrations/test_attack_scenario_mapping_backfill.py)."""
    cols = conn.execute(sa.text(f"PRAGMA table_info({table})")).mappings().all()
    values: dict[str, object] = {}
    for col in cols:
        cname = col["name"]
        if cname in explicit:
            values[cname] = explicit[cname]
        elif col["notnull"] and col["dflt_value"] is None:
            values[cname] = "x"
    conn.execute(
        sa.text(
            f"INSERT INTO {table} ({', '.join(values)}) "  # noqa: S608
            f"VALUES ({', '.join(f':{c}' for c in values)})"
        ),
        values,
    )


def _old_rationale(slug: str, domain: str, technique_id: str) -> str:
    return next(
        o
        for s, d, t, c, o, _n in mod._CHANGES
        if (s, d, t, c) == (slug, domain, technique_id, "rationale")
    )


# --- table shape -----------------------------------------------------------------------


def test_tables_have_the_expected_shape() -> None:
    assert len(mod._CHANGES) == 14
    assert len({s for s, _d, _t, _c, _o, _n in mod._CHANGES}) == 4
    allowed = mod._ALLOWED_COLUMNS
    assert allowed == _EXPECTED_COLUMNS
    assert mod._JSON_COLUMNS == _EXPECTED_JSON_COLUMNS
    assert {c for _s, _d, _t, c, _o, _n in mod._CHANGES} <= allowed
    # sorted by (slug, domain, technique_id, column), no duplicate cell
    keys = [(s, d, t, c) for s, d, t, c, _o, _n in mod._CHANGES]
    assert keys == sorted(set(keys))
    for slug, _domain, technique_id, column, old, new in mod._CHANGES:
        if column == "citations":
            assert type(old) is list and type(new) is list, (slug, technique_id)
            assert len(new) == 1 and isinstance(new[0], str), (slug, technique_id)
        else:
            assert type(old) is str and type(new) is str, (slug, technique_id, column)
        assert old != new, (slug, technique_id, column)
    by_column = {
        c: sorted((s, t) for s, _d, t, cc, _o, _n in mod._CHANGES if cc == c) for c in allowed
    }
    # exactly two provenance cells and two citations cells: R6 (T1027) and R8 (T1567)
    gt = "insider-ip-theft-manufacturing"
    assert by_column["provenance"] == [(gt, "T1027"), (gt, "T1567")]
    assert by_column["citations"] == [(gt, "T1027"), (gt, "T1567")]
    assert len(by_column["rationale"]) == 10
    for _s, _d, _t, column, old, new in mod._CHANGES:
        if column == "provenance":
            assert (old, new) == ("cited", "expert-estimate")
    per_slug = {s: sum(1 for k in keys if k[0] == s) for s in {s for s, _d, _t, _c in keys}}
    assert sorted(per_slug.values()) == [1, 1, 4, _BIG_SLUG_CELLS]
    assert per_slug[_BIG_SLUG] == _BIG_SLUG_CELLS and per_slug[_ONE_CELL_SLUG] == 1


def test_generator_mirrors_the_migration() -> None:
    migration_columns = mod._ALLOWED_COLUMNS
    migration_rows = {(s, d, t) for s, d, t, _c, _o, _n in mod._CHANGES}
    assert migration_columns == gen.ALLOWED_COLUMNS
    assert migration_rows == gen.EXPECTED_ROWS


# --- (a) old written first, then upgrade converges ---------------------------------------


def test_old_written_first_then_upgrade_converges_to_new(
    alembic_runner: MigrationContext, alembic_engine: Engine
) -> None:
    alembic_runner.migrate_up_to(PRE)
    _write_all_old(alembic_engine)
    alembic_runner.migrate_up_to(REV)
    _assert_all_new(alembic_engine)


def test_upgrade_from_all_old_applies_every_cell(
    alembic_runner: MigrationContext, alembic_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    alembic_runner.migrate_up_to(PRE)
    _write_all_old(alembic_engine)
    capsys.readouterr()
    alembic_runner.migrate_up_to(REV)
    assert _COUNTER_FROM_OLD in capsys.readouterr().err


def test_upgrade_touches_only_the_changed_cell_set(
    alembic_runner: MigrationContext, alembic_engine: Engine
) -> None:
    alembic_runner.migrate_up_to(PRE)
    _write_all_old(alembic_engine)
    before = _dump_mappings(alembic_engine)
    changed: set[tuple[str, str]] = {
        (_row_id(alembic_engine, s, d, t), c) for s, d, t, c, _o, _n in mod._CHANGES
    }
    assert len(changed) == N_CELLS == 14
    alembic_runner.migrate_up_to(REV)
    after = _dump_mappings(alembic_engine)
    assert before.keys() == after.keys()
    for pk, b in before.items():
        a = after[pk]
        assert b.keys() == a.keys()
        for column in b:
            if (str(pk), column) in changed:
                continue
            assert b[column] == a[column], (pk, column)


# --- (b) second application is a no-op ------------------------------------------------------


def test_second_application_is_a_no_op(
    alembic_runner: MigrationContext,
    alembic_engine: Engine,
    alembic_config: Config,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # capsys, not caplog: alembic/env.py re-runs logging.config.fileConfig() on every step
    # (see tests/migrations/test_epic_f_library_curation.py::test_second_application_is_a_no_op).
    alembic_runner.migrate_up_to(PRE)
    _write_all_old(alembic_engine)
    alembic_runner.migrate_up_to(REV)
    before = _dump_mappings(alembic_engine)
    command.stamp(alembic_config, PRE)  # moves the alembic_version pointer only
    capsys.readouterr()
    alembic_runner.migrate_up_to(REV)
    err = capsys.readouterr().err
    assert _dump_mappings(alembic_engine) == before
    assert _COUNTER_SECOND in err


# --- (c) guarded downgrade ------------------------------------------------------------------


def test_guarded_downgrade_restores_old_but_skips_drifted_slug(
    alembic_runner: MigrationContext, alembic_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    alembic_runner.migrate_up_to(PRE)
    _write_all_old(alembic_engine)
    alembic_runner.migrate_up_to(REV)
    # Drift on the eight-cell slug: one rationale is neither new nor old.
    drift_domain, drift_tech, drift_col = _BIG_SLUG_DRIFT
    _write_column(alembic_engine, _BIG_SLUG, drift_domain, drift_tech, drift_col, "drifted")
    capsys.readouterr()
    alembic_runner.migrate_down_to(PRE)
    err = capsys.readouterr().err
    assert _read_column(alembic_engine, _BIG_SLUG, drift_domain, drift_tech, drift_col) == "drifted"
    seen_group = 0
    for slug, domain, technique_id, column, old, new in mod._CHANGES:
        current = mod._decode(
            column, _read_column(alembic_engine, slug, domain, technique_id, column)
        )
        if slug == _BIG_SLUG:
            seen_group += 1
            # atomic group: the drifted slug's other seven cells stay at `new` too
            if (domain, technique_id, column) != _BIG_SLUG_DRIFT:
                assert current == new, (slug, technique_id, column)
            continue
        assert current == old, (slug, technique_id, column)
    assert seen_group == _BIG_SLUG_CELLS
    assert (
        f"issue-234 crosswalk rationales downgrade: applied={N_CELLS - _BIG_SLUG_CELLS} "
        f"already_old=0 drift={_BIG_SLUG_CELLS}" in err
    )
    assert "skipping whole slug" in err
    assert "T1027.rationale" in err


# --- (d) drift handling -----------------------------------------------------------------------


def test_drift_row_skipped_others_applied(
    alembic_runner: MigrationContext, alembic_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    alembic_runner.migrate_up_to(PRE)
    _write_all_old(alembic_engine)
    drift_domain, drift_tech, drift_col = _BIG_SLUG_DRIFT
    _write_column(alembic_engine, _BIG_SLUG, drift_domain, drift_tech, drift_col, "drifted")
    capsys.readouterr()
    alembic_runner.migrate_up_to(REV)
    err = capsys.readouterr().err
    assert _read_column(alembic_engine, _BIG_SLUG, drift_domain, drift_tech, drift_col) == "drifted"
    for slug, domain, technique_id, column, old, new in mod._CHANGES:
        current = mod._decode(
            column, _read_column(alembic_engine, slug, domain, technique_id, column)
        )
        if slug == _BIG_SLUG:
            # atomic group: the drifted slug's other seven cells stay at `old`
            if (domain, technique_id, column) != _BIG_SLUG_DRIFT:
                assert current == old, (slug, technique_id, column)
            continue
        assert current == new, (slug, technique_id, column)
    assert (
        f"issue-234 crosswalk rationales: applied={N_CELLS - _BIG_SLUG_CELLS} "
        f"already_new=0 drift={_BIG_SLUG_CELLS}" in err
    )
    assert "skipping whole slug" in err


def test_missing_row_is_counted_as_drift(
    alembic_runner: MigrationContext, alembic_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    alembic_runner.migrate_up_to(PRE)
    _write_all_old(alembic_engine)
    # a ONE-cell slug, so drift == 1
    _delete_mapping_row(alembic_engine, _ONE_CELL_SLUG, *_ONE_CELL_ROW)
    capsys.readouterr()
    alembic_runner.migrate_up_to(REV)
    err = capsys.readouterr().err
    assert f"issue-234 crosswalk rationales: applied={N_CELLS - 1} already_new=0 drift=1" in err


# --- (e) literal pins -------------------------------------------------------------------------


def test_new_literals_pin_to_current_seed_json() -> None:
    seed = _seed_by_key()
    for slug, domain, technique_id, column, _old, new in mod._CHANGES:
        value = seed[(slug, domain, technique_id)][column]
        assert value == new and type(value) is type(new), (slug, technique_id, column)


def test_old_literals_pin_to_the_committed_merge_base_snapshot() -> None:
    rows = json.loads(
        (_FIXTURES / "issue_234_merge_base_old_values.json").read_text(encoding="utf-8")
    )
    sentinel = [r for r in rows if r["slug"] == "_merge_base"]
    assert len(sentinel) == 1 and sentinel[0]["column"] == "sha"
    assert re.fullmatch(r"[0-9a-f]{40}", sentinel[0]["old"])
    by_key = {
        (r["slug"], r["domain"], r["technique_id"], r["column"]): r["old"]
        for r in rows
        if r["slug"] != "_merge_base"
    }
    expected_keys = {(s, d, t, c) for s, d, t, c, _o, _n in mod._CHANGES}
    assert set(by_key) == expected_keys
    for slug, domain, technique_id, column, old, _new in mod._CHANGES:
        got = by_key[(slug, domain, technique_id, column)]
        assert got == old and type(got) is type(old), (slug, technique_id, column)


# --- (f) fresh path ---------------------------------------------------------------------------


def test_fresh_db_migrates_to_head_with_expected_counters(
    alembic_runner: MigrationContext, alembic_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    alembic_runner.migrate_up_to(REV)
    err = capsys.readouterr().err
    # The seeding migrations read the mapping JSON live (already fixed), so on a fresh database
    # every cell is already new when this revision runs.
    assert _COUNTER_FRESH in err
    _assert_all_new(alembic_engine)


# --- (g) organisation copies are never touched --------------------------------------------------


def test_scenario_attack_mappings_are_byte_identical_across_the_upgrade(
    alembic_runner: MigrationContext, alembic_engine: Engine
) -> None:
    alembic_runner.migrate_up_to(PRE)
    _write_all_old(alembic_engine)
    # One organisation scenario whose mapping row carries an OLD rationale verbatim, so the
    # before/after comparison is not over an empty table.
    slug, domain, technique_id = "ot-network-scanning-reconnaissance", "enterprise", "T1595"
    with alembic_engine.connect() as conn:
        tech = conn.execute(
            sa.text("SELECT id FROM attack_techniques WHERE domain = :d AND technique_id = :t"),
            {"d": domain, "t": technique_id},
        ).fetchone()
    assert tech is not None
    scenario_id = uuid.uuid4().hex
    with alembic_engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = OFF"))
        _seed_row(
            conn,
            "scenarios",
            {
                "id": scenario_id,
                "organization_id": uuid.uuid4().hex,
                "name": "probe",
                "scenario_type": "CUSTOM",
                "threat_category": "ransomware",
                "threat_event_frequency": '{"distribution":"PERT","low":1,"mode":2,"high":3}',
                "vulnerability": '{"distribution":"PERT","low":0.1,"mode":0.2,"high":0.3}',
                "primary_loss": '{"distribution":"PERT","low":1,"mode":2,"high":3}',
                "overlay_pins": "[]",
                "source": "expert_judgment",
                "status": "ACTIVE",
                "version": "1.0",
                "row_version": 1,
                "library_pin": None,
                "created_at": "2026-06-01 00:00:00",
            },
        )
        _seed_row(
            conn,
            "scenario_attack_mappings",
            {
                "id": uuid.uuid4().hex,
                "organization_id": uuid.uuid4().hex,
                "scenario_id": scenario_id,
                "technique_id": str(tech[0]),
                "source": "library",
                "rationale": _old_rationale(slug, domain, technique_id),
            },
        )
    before = _dump_scenario_mappings(alembic_engine)
    assert len(before) == 1
    alembic_runner.migrate_up_to(REV)
    _assert_all_new(alembic_engine)
    assert _dump_scenario_mappings(alembic_engine) == before
