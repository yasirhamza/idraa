"""c7d2e9f4a1b3: issue #209 community re-maps + re-wordings (spec §3.2-3.3). Mirrors
tests/migrations/test_epic_f_library_curation.py; loads the migration module by path."""

from __future__ import annotations

import importlib.util
import json
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
from idraa.models.threat_community import canonical_threat_community_id
from scripts import build_issue_209_migration_table as gen

_ROOT = Path(idraa.__file__).resolve().parent.parent.parent
_VERSIONS = _ROOT / "alembic" / "versions"
_FIXTURES = Path(__file__).parent / "fixtures"
_COUNTER_FRESH = "issue-209 community remaps: applied=4 already_new=17 drift=0"
_COUNTER_SECOND = "issue-209 community remaps: applied=0 already_new=21 drift=0"
_EXPECTED_COLUMNS = frozenset(
    {"name", "description", "attack_vector", "tags", "canonical_fair_gap"}
)


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


(_MIG,) = _VERSIONS.glob("c7d2e9f4a1b3_*.py")
mod = _load(_MIG, "_test_mig_c7d2e9f4a1b3")
PRE, REV = mod.down_revision, mod.revision
N_CELLS = len(mod._CHANGES) + len(mod._REMAPS)


def _seed_by_slug() -> dict[str, dict[str, object]]:
    rows: list[dict[str, object]] = []
    for f in ("seed_library_entries.json", "seed_library_entries_extension.json"):
        rows += json.loads((_ROOT / "data" / f).read_text(encoding="utf-8"))
    return {e["slug"]: e for e in rows}


def _write_column(engine: Engine, slug: str, column: str, value: object) -> None:
    assert column in mod._ALLOWED_COLUMNS or column == "threat_community_id"
    stmt = f"UPDATE scenario_library_entries SET {column} = :v WHERE slug = :s AND version = 1"  # noqa: S608 - column asserted above
    with engine.begin() as conn:
        conn.execute(
            sa.text(stmt),
            {
                "v": mod._encode(column, value) if column in mod._ALLOWED_COLUMNS else value,
                "s": slug,
            },
        )


def _write_community_unchecked(engine: Engine, slug: str, raw_value: str) -> None:
    """Write a threat_community_id that does not satisfy the FK (e.g. a hyphenated spelling):
    the conftest engine enforces foreign keys, so turn them off on an AUTOCOMMIT connection
    (a PRAGMA inside a transaction is a no-op)."""
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = OFF"))
        conn.execute(
            sa.text(
                "UPDATE scenario_library_entries SET threat_community_id = :v WHERE slug = :s AND version = 1"
            ),
            {"v": raw_value, "s": slug},
        )
        conn.execute(sa.text("PRAGMA foreign_keys = ON"))


def _read_column(engine: Engine, slug: str, column: str) -> object:
    assert column in mod._ALLOWED_COLUMNS or column in (
        "threat_community_id",
        "threat_community_version",
    )
    stmt = f"SELECT {column} FROM scenario_library_entries WHERE slug = :s AND version = 1"  # noqa: S608 - column asserted above
    with engine.connect() as conn:
        row = conn.execute(sa.text(stmt), {"s": slug}).fetchone()
    return None if row is None else row[0]


def _write_all_old(engine: Engine) -> None:
    for slug, column, old, _new in mod._CHANGES:
        _write_column(engine, slug, column, old)
    for slug, old, _new, _r in mod._REMAPS:
        _write_column(engine, slug, "threat_community_id", canonical_threat_community_id(old).hex)


def _community_of(engine: Engine, slug: str) -> str:
    return uuid.UUID(str(_read_column(engine, slug, "threat_community_id"))).hex


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
    for slug, _old, new, _r in mod._REMAPS:
        assert _community_of(engine, slug) == canonical_threat_community_id(new).hex, slug
        assert _read_column(engine, slug, "threat_community_version") == 1, slug


# --- table shape -----------------------------------------------------------------------


def test_tables_have_the_expected_shape() -> None:
    assert len(mod._CHANGES) == 17 and len(mod._REMAPS) == 4
    assert sorted(s for s, _o, _n, _r in mod._REMAPS) == [
        "crop-science-ip-exfiltration",
        "higher-ed-insider-ddos",
        "hospitality-guest-data-insider",
        "insider-ip-theft-manufacturing",
    ]
    for _s, old, new, rationale in mod._REMAPS:
        assert old != new and rationale.strip(), (old, new)
    allowed = mod._ALLOWED_COLUMNS
    assert allowed == _EXPECTED_COLUMNS
    used = {c for _s, c, _o, _n in mod._CHANGES}
    assert used <= allowed


def test_generator_mirrors_the_migration() -> None:
    migration_columns = mod._ALLOWED_COLUMNS
    migration_slugs = {s for s, _c, _o, _n in mod._CHANGES} | {s for s, _o, _n, _r in mod._REMAPS}
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


def test_upgrade_touches_only_the_changed_cell_set(
    alembic_runner: MigrationContext, alembic_engine: Engine
) -> None:
    alembic_runner.migrate_up_to(PRE)
    _write_all_old(alembic_engine)
    before = _dump_all_rows(alembic_engine)
    alembic_runner.migrate_up_to(REV)
    after = _dump_all_rows(alembic_engine)
    assert before.keys() == after.keys()
    changed = {(s, c) for s, c, _o, _n in mod._CHANGES} | {
        (s, "threat_community_id") for s, _o, _n, _r in mod._REMAPS
    }
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
    drifted = canonical_threat_community_id("hacktivists").hex
    _write_column(alembic_engine, "higher-ed-insider-ddos", "threat_community_id", drifted)
    capsys.readouterr()
    alembic_runner.migrate_down_to(PRE)
    err = capsys.readouterr().err
    assert _community_of(alembic_engine, "higher-ed-insider-ddos") == drifted
    for slug, old, _new, _r in mod._REMAPS:
        if slug != "higher-ed-insider-ddos":
            assert _community_of(alembic_engine, slug) == canonical_threat_community_id(old).hex
    for slug, column, old, _new in mod._CHANGES:
        if slug == "higher-ed-insider-ddos":
            continue  # atomic group: the drifted slug's text cells stay at `new` too
        assert mod._decode(column, _read_column(alembic_engine, slug, column)) == old
    n_group = 1 + sum(1 for s, _c, _o, _n in mod._CHANGES if s == "higher-ed-insider-ddos")
    assert (
        f"issue-209 community remaps downgrade: applied={N_CELLS - n_group} already_old=0 drift={n_group}"
        in err
    )


def test_downgrade_continues_through_p1(
    alembic_runner: MigrationContext, alembic_engine: Engine
) -> None:
    """Review Focus 5: after this migration's downgrade, P1's own downgrade (which restores
    threat_actor_type from its frozen legacy map) must still run."""
    alembic_runner.migrate_up_to(REV)
    alembic_runner.migrate_down_to(PRE)
    alembic_runner.migrate_down_one()
    with alembic_engine.connect() as conn:
        cols = {r[1] for r in conn.execute(sa.text("PRAGMA table_info(scenario_library_entries)"))}
    assert "threat_actor_type" in cols and "threat_community_id" not in cols


# --- (d) drift handling -----------------------------------------------------------------------


def test_drift_row_skipped_others_applied(
    alembic_runner: MigrationContext, alembic_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    alembic_runner.migrate_up_to(PRE)
    _write_all_old(alembic_engine)
    drifted = canonical_threat_community_id("hacktivists").hex
    _write_column(alembic_engine, "crop-science-ip-exfiltration", "threat_community_id", drifted)
    capsys.readouterr()
    alembic_runner.migrate_up_to(REV)
    err = capsys.readouterr().err
    assert _community_of(alembic_engine, "crop-science-ip-exfiltration") == drifted
    old_name = next(
        o for s, c, o, _n in mod._CHANGES if s == "crop-science-ip-exfiltration" and c == "name"
    )
    assert _read_column(alembic_engine, "crop-science-ip-exfiltration", "name") == old_name, (
        "atomic group"
    )
    assert (
        _community_of(alembic_engine, "hospitality-guest-data-insider")
        == canonical_threat_community_id("nonprivileged_insider").hex
    )
    n_group = 1 + sum(1 for s, _c, _o, _n in mod._CHANGES if s == "crop-science-ip-exfiltration")
    assert (
        f"issue-209 community remaps: applied={N_CELLS - n_group} already_new=0 drift={n_group}"
        in err
    )
    assert "skipping whole slug" in err


def test_missing_row_is_counted_as_drift(
    alembic_runner: MigrationContext, alembic_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    alembic_runner.migrate_up_to(PRE)
    _write_all_old(alembic_engine)
    with alembic_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = OFF"))
        conn.execute(
            sa.text(
                "DELETE FROM scenario_library_entries WHERE slug = 'credential-stuffing-consumer-portal'"
            )
        )
        conn.execute(sa.text("PRAGMA foreign_keys = ON"))
    capsys.readouterr()
    alembic_runner.migrate_up_to(REV)
    err = capsys.readouterr().err
    assert f"issue-209 community remaps: applied={N_CELLS - 1} already_new=0 drift=1" in err


def test_hyphenated_stored_uuid_still_classifies(
    alembic_runner: MigrationContext, alembic_engine: Engine
) -> None:
    alembic_runner.migrate_up_to(PRE)
    _write_all_old(alembic_engine)
    _write_community_unchecked(
        alembic_engine,
        "hospitality-guest-data-insider",
        str(canonical_threat_community_id("privileged_insider")),
    )
    alembic_runner.migrate_up_to(REV)
    assert (
        _community_of(alembic_engine, "hospitality-guest-data-insider")
        == canonical_threat_community_id("nonprivileged_insider").hex
    )


def test_norm_uuid_accepts_every_spelling() -> None:
    u = canonical_threat_community_id("nation_state")
    assert (
        mod._norm_uuid(u.hex)
        == mod._norm_uuid(str(u))
        == mod._norm_uuid(u)
        == mod._norm_uuid(u.bytes)
        == u.hex
    )
    assert mod._norm_uuid(None) is None


# --- (e) literal pins -------------------------------------------------------------------------


def test_new_literals_pin_to_current_seed_json() -> None:
    seed = _seed_by_slug()
    for slug, column, _old, new in mod._CHANGES:
        assert seed[slug][column] == new and type(seed[slug][column]) is type(new), (slug, column)
    for slug, _old, new, _r in mod._REMAPS:
        assert seed[slug]["threat_community"] == new, slug


def test_old_literals_pin_to_the_committed_merge_base_snapshot() -> None:
    rows = json.loads(
        (_FIXTURES / "issue_209_merge_base_old_values.json").read_text(encoding="utf-8")
    )
    by_key = {(r["slug"], r["column"]): r["old"] for r in rows if r["slug"] != "_merge_base"}
    expected_keys = {(s, c) for s, c, _o, _n in mod._CHANGES} | {
        (s, "threat_community") for s, _o, _n, _r in mod._REMAPS
    }
    assert set(by_key) == expected_keys
    for slug, column, old, _new in mod._CHANGES:
        assert by_key[(slug, column)] == old and type(by_key[(slug, column)]) is type(old), (
            slug,
            column,
        )
    for slug, old, _new, _r in mod._REMAPS:
        assert by_key[(slug, "threat_community")] == old


# --- (f) fresh path ---------------------------------------------------------------------------


def test_fresh_db_migrates_to_head_with_expected_counters(
    alembic_runner: MigrationContext, alembic_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    alembic_runner.migrate_up_to(REV)
    err = capsys.readouterr().err
    # The seed migrations read the 17 text cells from the LIVE JSON (already new); P1 writes
    # the communities from its FROZEN map (old), so this migration applies the 4 re-maps.
    assert _COUNTER_FRESH in err
    _assert_all_new(alembic_engine)
