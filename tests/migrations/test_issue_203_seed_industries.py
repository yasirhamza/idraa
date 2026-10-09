"""512d010c5760: issue #203 seed-industries repair (spec §3.2-3.4). Mirrors
tests/migrations/test_issue_209_community_remaps.py (minus its re-map / uuid tests); loads the
migration module by path."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from pytest_alembic import MigrationContext
from sqlalchemy.engine import Engine

import idraa
from scripts import build_issue_203_migration_table as gen

_ROOT = Path(idraa.__file__).resolve().parent.parent.parent
_VERSIONS = _ROOT / "alembic" / "versions"
_FIXTURES = Path(__file__).parent / "fixtures"
# Fresh DB: the extension seed migration (0897a0ff350e) loads the LIVE, fixed JSON, so every
# cell is already new by the time this revision runs. Production (old values): 27 applied.
_COUNTER_FRESH = "issue-203 seed industries: applied=0 already_new=27 drift=0"
# The second-application path is write-old -> upgrade -> stamp(PRE) -> upgrade: the re-run after
# the stamp-back finds every cell already new.
_COUNTER_SECOND = "issue-203 seed industries: applied=0 already_new=27 drift=0"
_COUNTER_FROM_OLD = "issue-203 seed industries: applied=27 already_new=0 drift=0"
_EXPECTED_COLUMNS = frozenset({"applicable_industries", "calibration_anchor"})
# A TWO-cell slug (applicable_industries + calibration_anchor): the atomic-group assertions are
# vacuous on a one-cell slug. A ONE-cell slug (applicable_industries only) for the missing-row test.
_TWO_CELL_SLUG = "branch-atm-physical-tamper"
_ONE_CELL_SLUG = "gov-citizen-portal-ddos"


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


(_MIG,) = _VERSIONS.glob("512d010c5760_*.py")
mod = _load(_MIG, "_test_mig_512d010c5760")
PRE, REV = mod.down_revision, mod.revision
N_CELLS = len(mod._CHANGES)


def _seed_by_slug() -> dict[str, dict[str, object]]:
    rows: list[dict[str, object]] = []
    for f in ("seed_library_entries.json", "seed_library_entries_extension.json"):
        rows += json.loads((_ROOT / "data" / f).read_text(encoding="utf-8"))
    return {e["slug"]: e for e in rows}


def _write_column(engine: Engine, slug: str, column: str, value: object) -> None:
    assert column in mod._ALLOWED_COLUMNS
    stmt = f"UPDATE scenario_library_entries SET {column} = :v WHERE slug = :s AND version = 1"  # noqa: S608 - column asserted above
    with engine.begin() as conn:
        conn.execute(sa.text(stmt), {"v": mod._encode(column, value), "s": slug})


def _read_column(engine: Engine, slug: str, column: str) -> object:
    assert column in mod._ALLOWED_COLUMNS
    stmt = f"SELECT {column} FROM scenario_library_entries WHERE slug = :s AND version = 1"  # noqa: S608 - column asserted above
    with engine.connect() as conn:
        row = conn.execute(sa.text(stmt), {"s": slug}).fetchone()
    return None if row is None else row[0]


def _write_all_old(engine: Engine) -> None:
    for slug, column, old, _new in mod._CHANGES:
        _write_column(engine, slug, column, old)


def _dump_all_rows(engine: Engine) -> dict[tuple[object, object], dict[str, object]]:
    with engine.connect() as conn:
        rows = (
            conn.execute(sa.text("SELECT * FROM scenario_library_entries ORDER BY id, version"))
            .mappings()
            .all()
        )
    return {(r["id"], r["version"]): dict(r) for r in rows}


def _assert_all_new(engine: Engine) -> None:
    for slug, column, _old, new in mod._CHANGES:
        assert mod._decode(column, _read_column(engine, slug, column)) == new, (slug, column)


def _delete_row_unchecked(engine: Engine, slug: str) -> None:
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = OFF"))
        conn.execute(sa.text("DELETE FROM scenario_library_entries WHERE slug = :s"), {"s": slug})
        conn.execute(sa.text("PRAGMA foreign_keys = ON"))


# --- table shape -----------------------------------------------------------------------


def test_tables_have_the_expected_shape() -> None:
    assert len(mod._CHANGES) == 27
    assert len({s for s, _c, _o, _n in mod._CHANGES}) == 17
    allowed = mod._ALLOWED_COLUMNS
    assert allowed == _EXPECTED_COLUMNS
    assert mod._JSON_COLUMNS == _EXPECTED_COLUMNS
    assert {c for _s, c, _o, _n in mod._CHANGES} <= allowed
    # sorted by (slug, column), no duplicate cell
    keys = [(s, c) for s, c, _o, _n in mod._CHANGES]
    assert keys == sorted(set(keys))
    for slug, column, old, new in mod._CHANGES:
        want = list if column == "applicable_industries" else dict
        assert type(old) is want and type(new) is want, (slug, column)
        assert old != new, (slug, column)
        if column == "calibration_anchor":
            assert isinstance(old, dict) and isinstance(new, dict)
            # only `industry` changes; revenue_tier / loss_anchor / vuln_posture carried verbatim
            assert set(old) == set(new), slug
            assert {k for k in old if old[k] != new[k]} == {"industry"}, slug
    per_slug = {s: sum(1 for k in keys if k[0] == s) for s in {s for s, _c in keys}}
    assert sorted(per_slug.values()) == [1] * 7 + [2] * 10
    assert per_slug[_TWO_CELL_SLUG] == 2 and per_slug[_ONE_CELL_SLUG] == 1


def test_generator_mirrors_the_migration() -> None:
    migration_columns = mod._ALLOWED_COLUMNS
    migration_slugs = {s for s, _c, _o, _n in mod._CHANGES}
    assert migration_columns == gen.ALLOWED_COLUMNS
    assert migration_slugs == gen.EXPECTED_SLUGS


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
    before = _dump_all_rows(alembic_engine)
    alembic_runner.migrate_up_to(REV)
    after = _dump_all_rows(alembic_engine)
    assert before.keys() == after.keys()
    changed = {(s, c) for s, c, _o, _n in mod._CHANGES}
    for pk, b in before.items():
        a = after[pk]
        for column in b:
            if (b["slug"], column) in changed:
                continue
            assert b[column] == a[column], (b["slug"], column)


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
    before = _dump_all_rows(alembic_engine)
    command.stamp(alembic_config, PRE)  # moves the alembic_version pointer only
    capsys.readouterr()
    alembic_runner.migrate_up_to(REV)
    err = capsys.readouterr().err
    assert _dump_all_rows(alembic_engine) == before
    assert _COUNTER_SECOND in err


# --- (c) guarded downgrade ------------------------------------------------------------------


def test_guarded_downgrade_restores_old_but_skips_drifted_row(
    alembic_runner: MigrationContext, alembic_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    alembic_runner.migrate_up_to(PRE)
    _write_all_old(alembic_engine)
    alembic_runner.migrate_up_to(REV)
    # Drift on a TWO-cell slug: its applicable_industries is neither new nor old.
    _write_column(alembic_engine, _TWO_CELL_SLUG, "applicable_industries", ["other"])
    capsys.readouterr()
    alembic_runner.migrate_down_to(PRE)
    err = capsys.readouterr().err
    assert mod._decode(
        "applicable_industries",
        _read_column(alembic_engine, _TWO_CELL_SLUG, "applicable_industries"),
    ) == ["other"]
    for slug, column, old, new in mod._CHANGES:
        current = mod._decode(column, _read_column(alembic_engine, slug, column))
        if slug == _TWO_CELL_SLUG:
            # atomic group: the drifted slug's anchor cell stays at `new` too
            if column == "calibration_anchor":
                assert current == new, (slug, column)
            continue
        assert current == old, (slug, column)
    n_group = sum(1 for s, _c, _o, _n in mod._CHANGES if s == _TWO_CELL_SLUG)
    assert n_group == 2
    assert (
        f"issue-203 seed industries downgrade: applied={N_CELLS - n_group} already_old=0 drift={n_group}"
        in err
    )
    assert "skipping whole slug" in err


# --- (d) drift handling -----------------------------------------------------------------------


def test_drift_row_skipped_others_applied(
    alembic_runner: MigrationContext, alembic_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    alembic_runner.migrate_up_to(PRE)
    _write_all_old(alembic_engine)
    _write_column(alembic_engine, _TWO_CELL_SLUG, "applicable_industries", ["other"])
    capsys.readouterr()
    alembic_runner.migrate_up_to(REV)
    err = capsys.readouterr().err
    assert mod._decode(
        "applicable_industries",
        _read_column(alembic_engine, _TWO_CELL_SLUG, "applicable_industries"),
    ) == ["other"]
    old_anchor = next(
        o for s, c, o, _n in mod._CHANGES if s == _TWO_CELL_SLUG and c == "calibration_anchor"
    )
    assert (
        mod._decode(
            "calibration_anchor", _read_column(alembic_engine, _TWO_CELL_SLUG, "calibration_anchor")
        )
        == old_anchor
    ), "atomic group"
    for slug, column, _old, new in mod._CHANGES:
        if slug != _TWO_CELL_SLUG:
            assert mod._decode(column, _read_column(alembic_engine, slug, column)) == new, (
                slug,
                column,
            )
    n_group = sum(1 for s, _c, _o, _n in mod._CHANGES if s == _TWO_CELL_SLUG)
    assert (
        f"issue-203 seed industries: applied={N_CELLS - n_group} already_new=0 drift={n_group}"
        in err
    )
    assert "skipping whole slug" in err


def test_missing_row_is_counted_as_drift(
    alembic_runner: MigrationContext, alembic_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    alembic_runner.migrate_up_to(PRE)
    _write_all_old(alembic_engine)
    _delete_row_unchecked(alembic_engine, _ONE_CELL_SLUG)  # a ONE-cell slug, so drift == 1
    capsys.readouterr()
    alembic_runner.migrate_up_to(REV)
    err = capsys.readouterr().err
    assert f"issue-203 seed industries: applied={N_CELLS - 1} already_new=0 drift=1" in err


# --- (e) literal pins -------------------------------------------------------------------------


def test_new_literals_pin_to_current_seed_json() -> None:
    seed = _seed_by_slug()
    for slug, column, _old, new in mod._CHANGES:
        assert seed[slug][column] == new and type(seed[slug][column]) is type(new), (slug, column)


def test_old_literals_pin_to_the_committed_merge_base_snapshot() -> None:
    rows = json.loads(
        (_FIXTURES / "issue_203_merge_base_old_values.json").read_text(encoding="utf-8")
    )
    sentinel = [r for r in rows if r["slug"] == "_merge_base"]
    assert len(sentinel) == 1 and sentinel[0]["column"] == "sha"
    assert re.fullmatch(r"[0-9a-f]{40}", sentinel[0]["old"])
    by_key = {(r["slug"], r["column"]): r["old"] for r in rows if r["slug"] != "_merge_base"}
    expected_keys = {(s, c) for s, c, _o, _n in mod._CHANGES}
    assert set(by_key) == expected_keys
    for slug, column, old, _new in mod._CHANGES:
        assert by_key[(slug, column)] == old and type(by_key[(slug, column)]) is type(old), (
            slug,
            column,
        )


# --- (f) fresh path ---------------------------------------------------------------------------


def test_fresh_db_migrates_to_head_with_expected_counters(
    alembic_runner: MigrationContext, alembic_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    alembic_runner.migrate_up_to(REV)
    err = capsys.readouterr().err
    # The seed migrations read the extension JSON live through the loader (already fixed), so on
    # a fresh database every cell is already new when this revision runs.
    assert _COUNTER_FRESH in err
    _assert_all_new(alembic_engine)
