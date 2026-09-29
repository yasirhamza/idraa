"""Epic F (#192) scenario-library curation migration (e5f1a9c3d7b2) + its read-only
dry-run script (scripts/check_epic_f_migration.py). Follows the tests/migrations/
test_dataquality_followups.py dirty-then-run pattern and the alembic_runner/
alembic_engine fixtures. No revision literal appears below beyond the names of the
migration modules being loaded by path (PRE/REV come from the loaded module itself,
S2-2/A2-5).
"""

from __future__ import annotations

import importlib.util
import json
import re
import sqlite3
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from pytest_alembic import MigrationContext
from sqlalchemy.engine import Engine

import idraa
from scripts import check_epic_f_migration

_ROOT = Path(idraa.__file__).resolve().parent.parent.parent
_VERSIONS = _ROOT / "alembic" / "versions"
_MIGRATION_PATH = _VERSIONS / "e5f1a9c3d7b2_epic_f_library_curation.py"


def _load_migration_from_path(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _load_migration_by_revision(rev: str) -> ModuleType:
    (path,) = _VERSIONS.glob(f"{rev}_*.py")
    return _load_migration_from_path(path, f"_test_mig_{rev}")


mod = _load_migration_from_path(_MIGRATION_PATH, "_test_mig_e5f1a9c3d7b2")
PRE = mod.down_revision
REV = mod.revision

# A later migration that legitimately changes one of these (slug, column) cells adds it
# here; test (e) and (f) then skip it (A2-3). Empty today: no migration after this one
# touches any of the 38 cells in mod._CHANGES.
SUPERSEDED: frozenset[tuple[str, str]] = frozenset()

_CHANGES_BY_KEY: dict[tuple[str, str], tuple[object, object]] = {
    (slug, column): (old, new) for slug, column, old, new in mod._CHANGES
}


def _seed_by_slug() -> dict[str, dict[str, object]]:
    base = json.loads((_ROOT / "data" / "seed_library_entries.json").read_text(encoding="utf-8"))
    ext = json.loads(
        (_ROOT / "data" / "seed_library_entries_extension.json").read_text(encoding="utf-8")
    )
    return {e["slug"]: e for e in base + ext}


def _write_column(engine: Engine, slug: str, column: str, value: object) -> None:
    """Write `value` into `column` for `slug`'s version=1 row, through the migration's
    own _encode (so the JSON/text codec matches what upgrade()/downgrade() itself uses)."""
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                f"UPDATE scenario_library_entries SET {column} = :v "  # noqa: S608 - column is one of mod._ALLOWED_COLUMNS, never interpolated from external input
                "WHERE slug = :s AND version = 1"
            ),
            {"v": mod._encode(column, value), "s": slug},
        )


def _write_all_old(engine: Engine) -> None:
    for slug, column, old, _new in mod._CHANGES:
        _write_column(engine, slug, column, old)


def _read_column(engine: Engine, slug: str, column: str) -> object:
    with engine.connect() as conn:
        row = conn.execute(
            sa.text(
                f"SELECT {column} FROM scenario_library_entries "  # noqa: S608 - see _write_column
                "WHERE slug = :s AND version = 1"
            ),
            {"s": slug},
        ).fetchone()
    return None if row is None else row[0]


def _dump_all_rows(engine: Engine) -> list[tuple[object, ...]]:
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text("SELECT * FROM scenario_library_entries ORDER BY id, version")
        ).fetchall()
    return [tuple(r) for r in rows]


def _db_path_from_config(alembic_config: Config) -> Path:
    url = alembic_config.get_main_option("sqlalchemy.url")
    assert url is not None
    prefix = "sqlite+aiosqlite:///"
    assert url.startswith(prefix), url
    return Path(url[len(prefix) :])


# ---------------------------------------------------------------------------
# (a) old written first, upgrade converges to new
# ---------------------------------------------------------------------------


def test_old_written_first_then_upgrade_converges_to_new(
    alembic_runner: MigrationContext, alembic_engine: Engine
) -> None:
    alembic_runner.migrate_up_to(PRE)
    _write_all_old(alembic_engine)

    alembic_runner.migrate_up_to(REV)

    for slug, column, _old, new in mod._CHANGES:
        raw = _read_column(alembic_engine, slug, column)
        actual = mod._decode(column, raw)
        assert actual == new, f"{slug}.{column}: expected {new!r}, got {actual!r}"


# ---------------------------------------------------------------------------
# (b) second application is a no-op
# ---------------------------------------------------------------------------


def test_second_application_is_a_no_op(
    alembic_runner: MigrationContext,
    alembic_engine: Engine,
    alembic_config: Config,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # NOTE on capsys-vs-caplog (see tests/migrations/test_capacity_max_backfill.py):
    # alembic/env.py calls logging.config.fileConfig() on every migration step, which
    # strips any handler caplog attaches to "alembic.runtime.migration" mid-test. The
    # INFO/WARNING lines still reach the ini's console handler -> sys.stderr, so capsys
    # sees them where caplog cannot.
    alembic_runner.migrate_up_to(PRE)
    _write_all_old(alembic_engine)
    alembic_runner.migrate_up_to(REV)
    before = _dump_all_rows(alembic_engine)

    command.stamp(alembic_config, PRE)  # moves the alembic_version pointer only
    capsys.readouterr()  # drain everything printed so far

    alembic_runner.migrate_up_to(REV)
    err = capsys.readouterr().err

    after = _dump_all_rows(alembic_engine)
    assert before == after

    n = len(mod._CHANGES)
    assert f"epic-f library curation: applied=0 already_new={n} drift=0" in err


# ---------------------------------------------------------------------------
# (c) guarded downgrade
# ---------------------------------------------------------------------------


def test_guarded_downgrade_restores_old_but_skips_drifted_row(
    alembic_runner: MigrationContext, alembic_engine: Engine
) -> None:
    alembic_runner.migrate_up_to(PRE)
    _write_all_old(alembic_engine)
    alembic_runner.migrate_up_to(REV)

    drift_slug, drift_column = "professional-payroll-bec", "description"
    third_value = "DRIFT SENTINEL — neither old nor new"
    _write_column(alembic_engine, drift_slug, drift_column, third_value)

    alembic_runner.migrate_down_to(PRE)

    for slug, column, old, _new in mod._CHANGES:
        raw = _read_column(alembic_engine, slug, column)
        actual = mod._decode(column, raw)
        if (slug, column) == (drift_slug, drift_column):
            assert actual == third_value
        else:
            assert actual == old, f"{slug}.{column}: expected old {old!r}, got {actual!r}"


# ---------------------------------------------------------------------------
# (d) drift on upgrade: row skipped, others applied, one warning + one summary line
# ---------------------------------------------------------------------------


def test_drift_row_skipped_others_applied(
    alembic_runner: MigrationContext,
    alembic_engine: Engine,
    capsys: pytest.CaptureFixture[str],
) -> None:
    alembic_runner.migrate_up_to(PRE)
    _write_all_old(alembic_engine)

    drift_slug, drift_column = "web-app-exploitation", "description"
    third_value = "DRIFT SENTINEL — neither old nor new"
    _write_column(alembic_engine, drift_slug, drift_column, third_value)

    capsys.readouterr()  # drain everything printed so far (see capsys-vs-caplog note above)
    alembic_runner.migrate_up_to(REV)
    err = capsys.readouterr().err

    for slug, column, _old, new in mod._CHANGES:
        raw = _read_column(alembic_engine, slug, column)
        actual = mod._decode(column, raw)
        if (slug, column) == (drift_slug, drift_column):
            assert actual == third_value
        else:
            assert actual == new, f"{slug}.{column}: expected new {new!r}, got {actual!r}"

    n = len(mod._CHANGES)
    warning_lines = [line for line in err.splitlines() if "WARNI" in line]
    assert len(warning_lines) == 1, warning_lines
    assert drift_slug in warning_lines[0]
    assert drift_column in warning_lines[0]
    assert f"epic-f library curation: applied={n - 1} already_new=0 drift=1" in err


# ---------------------------------------------------------------------------
# (e) drift guard on `new`: every new (not superseded) equals current seed JSON
# ---------------------------------------------------------------------------


def test_new_literals_pin_to_current_seed_json_except_superseded() -> None:
    seed = _seed_by_slug()
    for slug, column, _old, new in mod._CHANGES:
        if (slug, column) in SUPERSEDED:
            continue
        current = seed[slug][column]
        assert current == new, (
            f"{slug}.{column}: _CHANGES 'new' literal ({new!r}) no longer matches the "
            f"current seed JSON ({current!r}); if a later migration changed this cell on "
            "purpose, add it to SUPERSEDED"
        )


# ---------------------------------------------------------------------------
# (f) fresh path: empty-DB migrate to REV
# ---------------------------------------------------------------------------


def test_fresh_db_migrates_to_head_with_expected_counters(
    alembic_runner: MigrationContext,
    alembic_engine: Engine,
    capsys: pytest.CaptureFixture[str],
) -> None:
    alembic_runner.migrate_up_to(REV)
    err = capsys.readouterr().err

    expected_drift = len({(s, c) for s, c, _o, _n in mod._CHANGES if (s, c) in SUPERSEDED})
    expected_applied = 0 if ("bec-fraud-financial", "asset_class") in SUPERSEDED else 1

    m = re.search(r"epic-f library curation: applied=(\d+) already_new=(\d+) drift=(\d+)", err)
    assert m is not None, err
    applied, already_new, drift = (int(x) for x in m.groups())
    assert applied == expected_applied, (
        f"applied={applied}, expected {expected_applied}; if a different _CHANGES row "
        "applied on a fresh DB, an intermediate migration writes a literal (see module "
        "docstring / report)"
    )
    assert drift == expected_drift
    assert already_new == len(mod._CHANGES) - applied - drift

    seed = _seed_by_slug()
    for slug, column, _old, new in mod._CHANGES:
        if (slug, column) in SUPERSEDED:
            continue
        raw = _read_column(alembic_engine, slug, column)
        actual = mod._decode(column, raw)
        assert actual == new
        assert actual == seed[slug][column]

    for slug in (
        "data-breach-notification-regulatory-tail",
        "ddos-extortion-financial",
        "chemical-process-safety-attack",
    ):
        if (slug, "status") in SUPERSEDED:
            continue
        raw = _read_column(alembic_engine, slug, "status")
        assert raw == "deprecated"


# ---------------------------------------------------------------------------
# (g) ORM <-> DTO field-sync
# ---------------------------------------------------------------------------


def test_allowed_and_json_columns_match_orm_dto_intersection() -> None:
    from idraa.models.scenario_library import ScenarioLibraryEntry
    from idraa.services.seed_library_loader import LibraryEntrySeed

    expected_allowed = (
        set(ScenarioLibraryEntry.__table__.columns.keys()) & set(LibraryEntrySeed.model_fields)
    ) - {"slug"}
    assert expected_allowed == mod._ALLOWED_COLUMNS

    expected_json = {
        c
        for c in expected_allowed
        if isinstance(ScenarioLibraryEntry.__table__.columns[c].type, sa.JSON)
    }
    assert expected_json == mod._JSON_COLUMNS


# ---------------------------------------------------------------------------
# (h) unsafe column name rejected before any bind is obtained
# ---------------------------------------------------------------------------


def test_unsafe_column_name_rejected_before_bind(monkeypatch: pytest.MonkeyPatch) -> None:
    bad_changes = (("bec-fraud-financial", "status; DROP TABLE x", "a", "b"),)
    monkeypatch.setattr(mod, "_CHANGES", bad_changes)

    def _fail_get_bind() -> None:
        raise AssertionError("op.get_bind() must not be called when validation fails")

    monkeypatch.setattr(mod.op, "get_bind", _fail_get_bind)

    with pytest.raises(RuntimeError):
        mod.upgrade()
    with pytest.raises(RuntimeError):
        mod.downgrade()


# ---------------------------------------------------------------------------
# (i) source guard: an 'imported' row sharing a seed slug is left untouched
# ---------------------------------------------------------------------------


def test_imported_source_row_not_updated(
    alembic_runner: MigrationContext, alembic_engine: Engine
) -> None:
    alembic_runner.migrate_up_to(PRE)
    _write_all_old(alembic_engine)

    target_slug, target_column, target_old, _new = mod._CHANGES[0]
    with alembic_engine.begin() as conn:
        conn.execute(
            sa.text(
                "UPDATE scenario_library_entries SET source = 'imported' "
                "WHERE slug = :s AND version = 1"
            ),
            {"s": target_slug},
        )

    alembic_runner.migrate_up_to(REV)

    raw = _read_column(alembic_engine, target_slug, target_column)
    assert mod._decode(target_column, raw) == target_old


# ---------------------------------------------------------------------------
# (j) dry-run script
# ---------------------------------------------------------------------------


def test_dry_run_pre_phase_clean_then_drift_then_post_phase(
    alembic_runner: MigrationContext,
    alembic_engine: Engine,
    alembic_config: Config,
    capsys: pytest.CaptureFixture[str],
) -> None:
    alembic_runner.migrate_up_to(PRE)
    _write_all_old(alembic_engine)
    db_path = _db_path_from_config(alembic_config)
    n = len(mod._CHANGES)

    rc = check_epic_f_migration.main(["--db", str(db_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert f"apply={n} already=0 drift=0" in out

    drift_slug, drift_column = "web-app-exploitation", "description"
    _write_column(alembic_engine, drift_slug, drift_column, "DRIFT SENTINEL TEXT")

    rc = check_epic_f_migration.main(["--db", str(db_path)])
    out = capsys.readouterr().out
    assert rc == 1
    assert f"drift: {drift_slug} {drift_column}" in out

    # Undo the drift, run the real migration, then check the post phase.
    old_val = _CHANGES_BY_KEY[(drift_slug, drift_column)][0]
    _write_column(alembic_engine, drift_slug, drift_column, old_val)
    alembic_runner.migrate_up_to(REV)

    rc = check_epic_f_migration.main(["--db", str(db_path), "--phase", "post"])
    out = capsys.readouterr().out
    assert rc == 0
    assert f"already={n}" in out

    reset_slug, reset_column, reset_old, _new = mod._CHANGES[0]
    _write_column(alembic_engine, reset_slug, reset_column, reset_old)

    rc = check_epic_f_migration.main(["--db", str(db_path), "--phase", "post"])
    out = capsys.readouterr().out
    assert rc == 1
    assert f"apply: {reset_slug} {reset_column}" in out
    assert "apply=1" in out


def test_connect_ro_rejects_writes(tmp_path: Path) -> None:
    db_path = tmp_path / "ro_write_test.db"
    setup_conn = sqlite3.connect(db_path)
    setup_conn.execute("CREATE TABLE t (x INTEGER)")
    setup_conn.commit()
    setup_conn.close()

    ro = check_epic_f_migration._connect_ro(db_path)
    try:
        with pytest.raises(sqlite3.OperationalError):
            ro.execute("INSERT INTO t VALUES (1)")
    finally:
        ro.close()


def test_connect_ro_path_with_space_and_hash_opens(tmp_path: Path) -> None:
    special_dir = tmp_path / "weird # dir with space"
    special_dir.mkdir()
    db_path = special_dir / "db.sqlite"
    setup_conn = sqlite3.connect(db_path)
    setup_conn.execute("CREATE TABLE t (x INTEGER)")
    setup_conn.commit()
    setup_conn.close()

    ro = check_epic_f_migration._connect_ro(db_path)
    try:
        assert ro.execute("SELECT x FROM t").fetchall() == []
    finally:
        ro.close()


def test_missing_db_path_exits_2_and_creates_nothing(tmp_path: Path) -> None:
    db_path = tmp_path / "does-not-exist.db"
    rc = check_epic_f_migration.main(["--db", str(db_path)])
    assert rc == 2
    assert not db_path.exists()


# ---------------------------------------------------------------------------
# (j') revision precondition
# ---------------------------------------------------------------------------


def test_revision_precondition_wrong_pre_revision(
    alembic_runner: MigrationContext,
    alembic_engine: Engine,
    alembic_config: Config,
    capsys: pytest.CaptureFixture[str],
) -> None:
    alembic_runner.migrate_up_to(PRE)
    two_back = _load_migration_by_revision(PRE).down_revision
    command.stamp(alembic_config, two_back)

    db_path = _db_path_from_config(alembic_config)
    rc = check_epic_f_migration.main(["--db", str(db_path)])
    out = capsys.readouterr().out
    assert rc == 3
    assert f"expected {PRE}" in out
    assert f"found {two_back!r}" in out
    assert "apply=" not in out


def test_revision_precondition_post_phase_on_pre_db(
    alembic_runner: MigrationContext,
    alembic_engine: Engine,
    alembic_config: Config,
    capsys: pytest.CaptureFixture[str],
) -> None:
    alembic_runner.migrate_up_to(PRE)
    db_path = _db_path_from_config(alembic_config)

    rc = check_epic_f_migration.main(["--db", str(db_path), "--phase", "post"])
    out = capsys.readouterr().out
    assert rc == 3
    assert "apply=" not in out


def test_revision_precondition_post_phase_unknown_revision(
    alembic_runner: MigrationContext,
    alembic_engine: Engine,
    alembic_config: Config,
    capsys: pytest.CaptureFixture[str],
) -> None:
    alembic_runner.migrate_up_to(REV)
    with alembic_engine.begin() as conn:
        conn.execute(sa.text("UPDATE alembic_version SET version_num = 'deadbeef0000'"))

    db_path = _db_path_from_config(alembic_config)
    rc = check_epic_f_migration.main(["--db", str(db_path), "--phase", "post"])
    out = capsys.readouterr().out
    assert rc == 3
    assert "revision precondition failed" in out
    assert "Traceback" not in out


def test_revision_precondition_resolves_from_different_cwd(
    alembic_runner: MigrationContext,
    alembic_engine: Engine,
    alembic_config: Config,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    alembic_runner.migrate_up_to(REV)
    db_path = _db_path_from_config(alembic_config)
    other_cwd = tmp_path / "elsewhere"
    other_cwd.mkdir()
    monkeypatch.chdir(other_cwd)

    rc = check_epic_f_migration.main(["--db", str(db_path), "--phase", "post"])
    out = capsys.readouterr().out
    assert rc == 0, out


# ---------------------------------------------------------------------------
# (j'') unexpected error: redacted message, no traceback
# ---------------------------------------------------------------------------


def test_unexpected_error_exits_4_and_redacts(
    alembic_runner: MigrationContext,
    alembic_engine: Engine,
    alembic_config: Config,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    alembic_runner.migrate_up_to(PRE)
    _write_all_old(alembic_engine)
    db_path = _db_path_from_config(alembic_config)

    real_load_migration = check_epic_f_migration._load_migration

    def _poisoned_load_migration() -> ModuleType:
        poisoned = real_load_migration()

        def _boom(*_args: object, **_kwargs: object) -> str:
            raise RuntimeError("SECRET-CELL")

        setattr(poisoned, "_classify", _boom)  # noqa: B010 - ModuleType attr assignment for the test seam
        return poisoned

    monkeypatch.setattr(check_epic_f_migration, "_load_migration", _poisoned_load_migration)

    capsys.readouterr()  # drain the migrate_up_to(PRE) bootstrap output
    rc = check_epic_f_migration.main(["--db", str(db_path)])
    out, err = capsys.readouterr()
    assert rc == 4
    assert out.strip() == "internal error: RuntimeError"
    assert "SECRET-CELL" not in out
    assert "SECRET-CELL" not in err
    assert "Traceback" not in out
    assert "Traceback" not in err
