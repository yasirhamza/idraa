#!/usr/bin/env python3
"""Read-only dry-run for the Epic F scenario-library migration
(``alembic/versions/e5f1a9c3d7b2_epic_f_library_curation.py``, plan Task 4).

Opens a SQLite DB URI-mode read-only (never writes -- ``_connect_ro`` uses the
same ``?mode=ro`` idiom as ``scripts/sweep_library_secondary_response.py``)
and classifies every ``_CHANGES`` cell against the DB's current values,
grouped per slug with the same atomic-group guard the migration itself uses
(spec §4.1 amendment, M4-1: a slug with any drifted cell is reported as
drifted in full -- see the migration's module docstring "Per-slug atomicity"
/ "Counter semantics" sections, which this script shares via
``mod._group_by_slug`` / ``mod._classify_slug_group``). Prints a report line
per notable cell plus a summary, without ever writing to the DB. Intended
for a read-only pass against a production BACKUP copy before a real deploy
-- never run against a live production DB or its volume directly; only a
local copy under ``~/idraa-backups/``.

Phases:
  - ``pre`` (default): the DB must be stamped at exactly this migration's
    ``down_revision`` (the state before it has run). Reports what the real
    ``alembic upgrade`` would do: exit 0 if every cell would cleanly apply or
    is already at ``new``; exit 1 if any cell (or its slug sibling) is
    drifted. Prints every non-"apply" cell ("already" and "drift") -- "apply"
    is the expected default before a deploy, so it is the one phase omits.
  - ``post``: the DB must be stamped at this migration's own revision or any
    descendant of it (the state after it has run, or after it plus later
    migrations). Every cell must classify as "already" (at ``new``); exit 1
    otherwise -- a cell still needing "apply" post-deploy means the real
    migration didn't converge it (or drifted since). Prints every
    non-"already" cell ("apply" and "drift") -- "already" is the expected
    default after a deploy, so it is the one phase omits.

Exit codes:
  0  clean (see phase semantics above)
  1  drift (pre) or not-all-already (post)
  2  DB unreadable -- scoped to opening the DB and its FIRST query (the
     ``alembic_version`` read): a missing file, a missing ``alembic_version``
     table, or any other ``sqlite3.Error`` raised by ``_connect_ro`` or that
     first query. A ``sqlite3.Error`` raised LATER (during classification) is
     an unexpected mid-run failure and maps to exit 4 instead (Sec3-2).
  3  revision precondition failed (wrong phase, or an alembic_version value
     unknown to this checkout's script directory)
  4  any other exception, including a ``sqlite3.Error`` raised after the
     first query (message and traceback are never printed -- only the
     exception's type name, so a monkeypatched/corrupted internal state
     can't leak cell values into the log)

Lagging-prod procedure: if the backup's ``alembic_version`` predates this
migration's ``down_revision`` (``08e3f1cd45b8``), run
``alembic upgrade 08e3f1cd45b8`` on this checkout first, then re-run this
dry-run. ``already=3`` afterward is expected, not drift: that catch-up run
includes ``b5e2c7a9d413``, which converges accidental-insider-exposure's
``primary_loss``/``secondary_loss``/``loss_form_profile`` straight from the
(already Epic-F) seed JSON, leaving only its 4th cell, ``threat_event_type``,
to ``apply`` here.

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
        'see this module docstring\'s "Lagging-prod procedure" section for the fix'
    )


def _classify_rows(
    conn: sqlite3.Connection, mod: ModuleType, phase: str
) -> tuple[list[tuple[str, str, str]], dict[str, int], list[tuple[str, tuple[str, ...]]]]:
    """Groups ``mod._CHANGES`` by slug and classifies each group with
    ``mod._classify_slug_group`` -- the same atomic-group guard ``upgrade()`` itself
    uses -- so this script's counts and report match what a real ``alembic upgrade``
    would do. Classification always uses the forward (old, new) direction regardless of
    `phase` (the dry-run never simulates a downgrade); `phase` only controls which
    classification is considered "expected" and thus omitted from the printed report
    (see module docstring). Counters are CELL-level (see the migration module
    docstring's "Counter semantics" note): a cell demoted from "apply" to "drift" by a
    poisoned sibling counts toward ``drift``, not ``apply``.

    Also returns, per slug with at least one genuinely-drifted (or missing) cell, that
    cell's column name(s) -- the same ``drifted_columns`` tuple
    ``mod._classify_slug_group`` already computes for the migration's own WARNING, so an
    operator reading this script's output does not have to diff every cell in a poisoned
    slug's group to find the one that actually drifted (R4-N2). Never includes values.
    """
    mod._validate_changes(mod._CHANGES)
    report_rows: list[tuple[str, str, str]] = []
    drifted_slugs: list[tuple[str, tuple[str, ...]]] = []
    counts = {"apply": 0, "already": 0, "drift": 0}
    omit = "apply" if phase == "pre" else "already"
    for slug, cells in mod._group_by_slug(mod._CHANGES).items():
        found_raw: dict[str, tuple[bool, object]] = {}
        for column, _old, _new in cells:
            row = conn.execute(
                f"SELECT {column} FROM scenario_library_entries "  # noqa: S608 - column is a vetted literal, validated above
                "WHERE slug = ? AND version = 1 AND source = 'seed'",
                (slug,),
            ).fetchone()
            found_raw[column] = (row is not None, row[0] if row is not None else None)
        classification, drifted = mod._classify_slug_group(cells, found_raw)
        if drifted:
            drifted_slugs.append((slug, drifted))
        for column, cls in classification.items():
            counts[cls] += 1
            if cls != omit:
                report_rows.append((slug, column, cls))
    return report_rows, counts, drifted_slugs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--phase", choices=("pre", "post"), default="pre")
    args = parser.parse_args(argv)

    conn: sqlite3.Connection | None = None
    try:
        # Exit 2 is scoped to opening the DB and its FIRST query (the alembic_version
        # read inside _check_revision). A sqlite3.Error from a LATER query (inside
        # _classify_rows) is an unexpected mid-run failure, not "the DB file itself is
        # unreadable", so it falls through to the generic `except Exception` below and
        # maps to exit 4 instead (Sec3-2 / spec N-3).
        try:
            conn = _connect_ro(args.db)
            mod = _load_migration()
            precondition_error = _check_revision(conn, mod, args.phase)
        except sqlite3.Error as exc:
            print(f"cannot read database: {exc}")
            return 2

        if precondition_error is not None:
            print(precondition_error)
            return 3

        report_rows, counts, drifted_slugs = _classify_rows(conn, mod, args.phase)
    except Exception as exc:
        print(f"internal error: {type(exc).__name__}")
        return 4
    finally:
        if conn is not None:
            conn.close()

    for slug, column, classification in report_rows:
        print(f"{classification}: {slug} {column}")
    for slug, drifted_columns in drifted_slugs:
        # R4-N2: names only the cell(s) that are themselves neither `old` nor `new` (or
        # missing) within this drifted slug's atomic group -- never the whole group's
        # columns, and never a value.
        print(f"drift detail: {slug} drifted column(s): {', '.join(drifted_columns)}")
    print(f"apply={counts['apply']} already={counts['already']} drift={counts['drift']}")

    if args.phase == "pre":
        return 1 if counts["drift"] else 0
    return 0 if counts["apply"] == 0 and counts["drift"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
