#!/usr/bin/env python3
"""Read-only dry-run for the Epic F scenario-library migration
(``alembic/versions/e5f1a9c3d7b2_epic_f_library_curation.py``, plan Task 4).

Opens a SQLite DB URI-mode read-only (never writes -- ``_connect_ro`` uses the
same ``?mode=ro`` idiom as ``scripts/sweep_library_secondary_response.py``)
and classifies every ``_CHANGES`` row against the DB's current values,
printing a one-line-per-non-apply-row report plus a summary, without ever
touching the DB. Intended for a read-only pass against a production BACKUP
copy before a real deploy -- never run against a live production DB or its
volume directly; only a local copy under ``~/idraa-backups/``.

Phases:
  - ``pre`` (default): the DB must be stamped at exactly this migration's
    ``down_revision`` (the state before it has run). Reports what the real
    ``alembic upgrade`` would do: exit 0 if every row would cleanly apply or
    is already at ``new``; exit 1 if any row is drifted.
  - ``post``: the DB must be stamped at this migration's own revision or any
    descendant of it (the state after it has run, or after it plus later
    migrations). Every row must classify as "already" (at ``new``); exit 1
    otherwise -- a row still needing "apply" post-deploy means the real
    migration didn't converge it (or drifted since).

Exit codes:
  0  clean (see phase semantics above)
  1  drift (pre) or not-all-already (post)
  2  DB unreadable (missing file, missing alembic_version table, or any
     other sqlite3.Error)
  3  revision precondition failed (wrong phase, or an alembic_version value
     unknown to this checkout's script directory)
  4  any other exception (message and traceback are never printed -- only
     the exception's type name, so a monkeypatched/corrupted internal state
     can't leak cell values into the log)

Usage: ``uv run python scripts/check_epic_f_migration.py --db PATH [--phase pre|post]``
"""

from __future__ import annotations

import argparse
import importlib.util
import sqlite3
import sys
from pathlib import Path
from types import ModuleType

from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.script.revision import ResolutionError
from alembic.util import CommandError

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MIGRATION_PATH = _REPO_ROOT / "alembic" / "versions" / "e5f1a9c3d7b2_epic_f_library_curation.py"


def _connect_ro(path: Path) -> sqlite3.Connection:
    """Read-only SQLite connection (the sweep_library_secondary_response.py /
    sweep_run_samples_finite.py idiom): percent-encodes spaces/``#``/``?`` in the
    resolved path via ``Path.as_uri()`` and never creates a missing file."""
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_epic_f_migration_e5f1a9c3d7b2", _MIGRATION_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load migration module from {_MIGRATION_PATH}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _script_directory() -> ScriptDirectory:
    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "alembic"))
    return ScriptDirectory.from_config(cfg)


def _check_revision(conn: sqlite3.Connection, mod: ModuleType, phase: str) -> str | None:
    """Returns None on a satisfied precondition, else the failure message to print
    (caller exits 3). Never raises past this function for the unknown-revision case."""
    row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    found = row[0] if row else None

    if phase == "pre":
        if found == mod.down_revision:
            return None
        expected = mod.down_revision
    elif found is None:
        expected = f"{mod.revision} (or a descendant of it)"
    else:
        try:
            ancestry = {r.revision for r in _script_directory().iterate_revisions(found, "base")}
        except (ResolutionError, CommandError):
            ancestry = set()
        if mod.revision in ancestry:
            return None
        expected = f"{mod.revision} (or a descendant of it)"

    return (
        f"revision precondition failed: expected {expected}, found {found!r} — "
        "see spec §8 for the lagging-prod procedure"
    )


def _classify_rows(
    conn: sqlite3.Connection, mod: ModuleType
) -> tuple[list[tuple[str, str, str]], dict[str, int]]:
    mod._validate_changes(mod._CHANGES)
    non_apply: list[tuple[str, str, str]] = []
    counts = {"apply": 0, "already": 0, "drift": 0}
    for slug, column, old, new in mod._CHANGES:
        row = conn.execute(
            f"SELECT {column} FROM scenario_library_entries "  # noqa: S608 - column is a vetted literal, validated above
            "WHERE slug = ? AND version = 1 AND source = 'seed'",
            (slug,),
        ).fetchone()
        if row is None:
            counts["drift"] += 1
            non_apply.append((slug, column, "drift"))
            continue
        classification = mod._classify(column, row[0], old, new)
        counts[classification] += 1
        if classification != "already":
            non_apply.append((slug, column, classification))
    return non_apply, counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--phase", choices=("pre", "post"), default="pre")
    args = parser.parse_args(argv)

    try:
        conn = _connect_ro(args.db)
        try:
            mod = _load_migration()
            precondition_error = _check_revision(conn, mod, args.phase)
            if precondition_error is not None:
                print(precondition_error)
                return 3

            non_apply, counts = _classify_rows(conn, mod)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        print(f"cannot read database: {exc}")
        return 2
    except Exception as exc:
        print(f"internal error: {type(exc).__name__}")
        return 4

    for slug, column, classification in non_apply:
        print(f"{classification}: {slug} {column}")
    print(f"apply={counts['apply']} already={counts['already']} drift={counts['drift']}")

    if args.phase == "pre":
        return 1 if counts["drift"] else 0
    return 0 if counts["apply"] == 0 and counts["drift"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
