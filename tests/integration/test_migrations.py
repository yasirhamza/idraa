"""Alembic migration reversibility smoke.

Runs the full migration chain against a disposable SQLite file: upgrade
to ``head``, assert the expected tables exist, downgrade to ``base``,
assert the tables are gone, then re-upgrade to ``head`` and assert the
tables are back. Catches asymmetry between ``upgrade()`` and
``downgrade()`` the moment it ships, and smoke-tests the cross-dialect
``Uuid`` / ``Enum(native_enum=False)`` / ``JSON`` column types on the
SQLite side (Postgres compatibility is covered at deploy time).

The migration config is driven programmatically via ``alembic.config.Config``
so the test owns its own ``sqlalchemy.url`` and doesn't leak into the
repo's ``alembic.ini`` database pointer.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

EXPECTED_TABLES = {
    "audit_log",
    "auth_sessions",
    # calibration_overrides + calibration_override_revisions: dropped in
    # PR pi (revision ae67f3cda318) -- the calibration runtime was excised
    # in that schema-day commit.
    "control_function_assignments",
    "controls",
    "csv_import_preview",
    "organizations",
    "overlay_definition_revisions",
    "overlay_definitions",
    "risk_analysis_runs",
    "scenario_controls",
    "scenario_library_entries",
    "scenario_library_overrides",
    "scenarios",
    "users",
    "wizard_drafts",
}

# F5 revision ID — the phase_1_5a_scenario_library_taxonomy migration.
_F5_REVISION = "b8e0334b7f43"

# Revision immediately before F5 — used to verify F5 drops tables on downgrade.
# Hardcoded so the test stays correct as further migrations (F25, etc.) are added.
_PRE_F5_REVISION = "922b63358719"

# Tables added exclusively in F5 (phase_1_5a).
_F5_TABLES = frozenset(
    {
        "scenario_library_entries",
        "scenario_library_overrides",
        "wizard_drafts",
    }
)


def _alembic_cfg(db_url: str) -> Config:
    repo_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(repo_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(repo_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def _user_tables(sqlite_path: Path) -> set[str]:
    conn = sqlite3.connect(sqlite_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    finally:
        conn.close()
    return {r[0] for r in rows} - {"alembic_version"}


def test_phase_1_5a_alembic_roundtrip(tmp_path: Path) -> None:
    """F5 migration roundtrip: upgrade -> downgrade below F5 -> upgrade.

    Verifies that the phase_1_5a_scenario_library_taxonomy revision:
    - Creates the three new tables on upgrade.
    - Drops them cleanly on downgrade (no orphan rows or indexes).
    - Re-creates them on a second upgrade (idempotent schema shape).

    Uses an explicit revision target (``b8e0334b7f43~1``) rather than ``-1``
    so the test remains correct even as further migrations are added on top
    of F5.

    PR pi caveat: PR pi (ae67f3cda318) raises NotImplementedError on
    downgrade -- the calibration runtime was excised wholesale. So this
    test upgrades only as far as the F5+immediate-followup line (the
    ``seed_library_entries`` revision ``c1d2e3f4a5b6``, which is the
    head of F5's lineage just before later migrations stack on) and
    exercises the F5 roundtrip from there.
    """
    db_path = tmp_path / "f5-smoke.db"
    db_url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    cfg = _alembic_cfg(db_url)

    # Upgrade to F5 + its immediate follow-up seed migration (the deepest
    # we can reach without crossing PR pi's no-downgrade boundary).
    f5_plus_seed = "c1d2e3f4a5b6"  # seed_library_entries (immediate F5 follow-up)
    command.upgrade(cfg, f5_plus_seed)
    tables_at_target = _user_tables(db_path)
    assert _F5_TABLES.issubset(tables_at_target), (
        f"F5 tables missing after upgrade: {_F5_TABLES - tables_at_target}"
    )

    # Downgrade to just before F5 — F5 tables gone; pre-F5 tables still present.
    command.downgrade(cfg, _PRE_F5_REVISION)
    tables_after_downgrade = _user_tables(db_path)
    assert not _F5_TABLES.intersection(tables_after_downgrade), (
        f"F5 tables still present after downgrade to {_PRE_F5_REVISION}: "
        f"{_F5_TABLES.intersection(tables_after_downgrade)}"
    )
    # scenarios table survives downgrade (it existed before F5).
    assert "scenarios" in tables_after_downgrade

    # Re-upgrade — F5 tables recreated.
    command.upgrade(cfg, f5_plus_seed)
    tables_after_re_upgrade = _user_tables(db_path)
    assert _F5_TABLES.issubset(tables_after_re_upgrade), (
        f"F5 tables missing after re-upgrade: {_F5_TABLES - tables_after_re_upgrade}"
    )


def test_migrations_upgrade_downgrade_roundtrip(tmp_path: Path) -> None:
    """End-to-end migration roundtrip up to the deepest reversible target.

    PR pi (revision ae67f3cda318) raises NotImplementedError on downgrade
    -- the calibration runtime was excised wholesale and the framework
    code needed to repopulate the dropped tables / columns is gone. So
    every migration from base up through ``36b9b732585a``
    (omicron_1_add_run_name, the immediate predecessor of PR pi) IS
    reversible; PR pi itself is forward-only by design. This test
    exercises the reversible portion: upgrade to _PRE_PI_REVISION,
    downgrade to base, re-upgrade to _PRE_PI_REVISION, then upgrade the
    rest of the way to head as a forward-only smoke.
    """
    db_path = tmp_path / "migration-smoke.db"
    # Sync SQLite URL — Alembic's default env.py runs async, but the app's
    # env.py already handles sync + async through ``async_engine_from_config``.
    # We point at an ``sqlite+aiosqlite`` URL so the app env.py path is the
    # one under test.
    db_url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    cfg = _alembic_cfg(db_url)

    pre_pi_revision = "36b9b732585a"  # omicron_1_add_run_name

    # Up to the deepest reversible target.
    command.upgrade(cfg, pre_pi_revision)
    tables_at_pre_pi = _user_tables(db_path)
    # The calibration_overrides + calibration_override_revisions tables
    # exist at this revision (they were dropped by PR pi).
    assert {"calibration_overrides", "calibration_override_revisions"}.issubset(tables_at_pre_pi)

    # Down to base — every reversible migration from _PRE_PI_REVISION to
    # base is exercised here.
    command.downgrade(cfg, "base")
    assert _user_tables(db_path) == set()

    # Back up to pre_pi_revision — reversibility demonstrated end-to-end.
    command.upgrade(cfg, pre_pi_revision)
    assert {"calibration_overrides", "calibration_override_revisions"}.issubset(
        _user_tables(db_path)
    )

    # Forward-only upgrade through PR pi to head (the PR pi migration
    # drops the calibration tables; not reversible by design).
    command.upgrade(cfg, "head")
    assert EXPECTED_TABLES.issubset(_user_tables(db_path))
