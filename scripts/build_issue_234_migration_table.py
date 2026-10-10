#!/usr/bin/env python3
"""Dev-only generator for the issue #234 crosswalk-rationales migration's ``_CHANGES``
literal table (alembic/versions/<rev>_issue_234_crosswalk_rationales.py, idraa#234). NOT
imported by the app or by any migration -- a human (or an implementer agent) runs it
and pastes its stdout into the migration file by hand, so the migration itself stays
a frozen literal, never a live re-derivation from git or the ORM at
`alembic upgrade` time.

Diffs the working-tree ATT&CK mapping seed JSON (``data/seed_attack_full_mappings.json``
+ ``data/seed_attack_d_iii_b_full.json``) against the same files' blobs at
``git merge-base HEAD idraa/main``. Rows are keyed by
``(entry_slug, domain, technique_id)``; every key whose parsed value differs on a row
present in both trees becomes one ``(slug, domain, technique_id, column, old, new)`` cell.
Fails loudly (exit 1) rather than emitting a silently-wrong table when:

- a mapping row was added or removed since the merge-base (this migration only
  carries content changes to existing rows -- inserts/deletes are a
  different migration's job);
- a differing key is not one of the columns the migration is allowed to
  touch (``ALLOWED_COLUMNS`` -- a literal that mirrors the migration's
  ``_ALLOWED_COLUMNS``);
- a changed row is outside ``EXPECTED_ROWS`` (the ten rows of spec section 3;
  catches an unrelated seed edit accidentally riding along).

This repository's canonical remote is ``idraa``, not ``origin``: ``origin/main`` may be
stale or absent, so the merge-base ref is the constant ``BASE_REF`` below.

Prerequisite: run ``git fetch idraa`` before this script -- it never fetches
itself (a stale local ``idraa/main`` would silently compute the wrong base).

Re-run and re-paste after every rebase onto a moved ``idraa/main``.

Usage: ``uv run python -m scripts.build_issue_234_migration_table``
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from scripts.curation_check.config import REPO_ROOT
from scripts.curation_check.library import _git

# The canonical remote of this repository is `idraa` (not `origin`).
BASE_REF = "idraa/main"

MAPPING_FILES: tuple[Path, ...] = (
    Path("data/seed_attack_full_mappings.json"),
    Path("data/seed_attack_d_iii_b_full.json"),
)

RowKey = tuple[str, str, str]  # (entry_slug, domain, technique_id)

# The ten rows whose rationales spec section 3 re-words (R1..R10).
EXPECTED_ROWS: frozenset[RowKey] = frozenset(
    {
        ("hospitality-guest-data-insider", "enterprise", "T1078"),
        ("higher-ed-insider-ddos", "enterprise", "T1498"),
        ("insider-ip-theft-manufacturing", "enterprise", "T1005"),
        ("insider-ip-theft-manufacturing", "enterprise", "T1027"),
        ("insider-ip-theft-manufacturing", "enterprise", "T1078"),
        ("insider-ip-theft-manufacturing", "enterprise", "T1567"),
        ("ot-network-scanning-reconnaissance", "enterprise", "T1078"),
        ("ot-network-scanning-reconnaissance", "enterprise", "T1595"),
        ("ot-network-scanning-reconnaissance", "enterprise", "T1596"),
        ("ot-network-scanning-reconnaissance", "ics", "T0888"),
    }
)

# mirrors the migration's _ALLOWED_COLUMNS literal (pinned by tests/migrations/test_issue_234_crosswalk_rationales.py)
ALLOWED_COLUMNS: frozenset[str] = frozenset({"rationale", "provenance", "citations"})


def _key(row: dict[str, Any]) -> RowKey:
    return (row["entry_slug"], row["domain"], row["technique_id"])


def _by_key(rows: list[dict[str, Any]]) -> dict[RowKey, dict[str, Any]]:
    by_key: dict[RowKey, dict[str, Any]] = {}
    for row in rows:
        key = _key(row)
        if key in by_key:
            raise SystemExit(f"duplicate mapping row {key} across the mapping files")
        by_key[key] = row
    return by_key


def _load_at_base(root: Path, base: str) -> dict[RowKey, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for rel in MAPPING_FILES:
        text = _git(root, "cat-file", "blob", f"{base}:{rel.as_posix()}")
        rows += json.loads(text)["mappings"]
    return _by_key(rows)


def _load_working_tree(root: Path) -> dict[RowKey, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for rel in MAPPING_FILES:
        rows += json.loads((root / rel).read_text(encoding="utf-8"))["mappings"]
    return _by_key(rows)


def build_changes(
    root: Path = REPO_ROOT,
) -> tuple[tuple[str, str, str, str, object, object], ...]:
    base = _git(root, "merge-base", "HEAD", BASE_REF).strip()
    old_by_key = _load_at_base(root, base)
    new_by_key = _load_working_tree(root)

    added = set(new_by_key) - set(old_by_key)
    removed = set(old_by_key) - set(new_by_key)
    if added or removed:
        raise SystemExit(
            f"mapping row set changed since merge-base {base}: "
            f"added={sorted(added)} removed={sorted(removed)} -- this migration "
            "only carries content changes to existing rows"
        )

    changed_keys: set[RowKey] = set()
    changes: list[tuple[str, str, str, str, object, object]] = []
    for key in sorted(new_by_key):
        old_row = old_by_key[key]
        new_row = new_by_key[key]
        for column in sorted(set(old_row) | set(new_row)):
            old_val = old_row.get(column)
            new_val = new_row.get(column)
            if old_val == new_val:
                continue
            if column not in ALLOWED_COLUMNS:
                raise SystemExit(
                    f"{key}.{column} changed but {column!r} is not in ALLOWED_COLUMNS "
                    f"({sorted(ALLOWED_COLUMNS)})"
                )
            changed_keys.add(key)
            changes.append((*key, column, old_val, new_val))

    unexpected = changed_keys - EXPECTED_ROWS
    if unexpected:
        raise SystemExit(f"changed row(s) outside EXPECTED_ROWS: {sorted(unexpected)}")

    changes.sort(key=lambda c: (c[0], c[1], c[2], c[3]))
    return tuple(changes)


def main() -> int:
    changes = build_changes()
    slugs_touched = sorted({c[0] for c in changes})
    print(f"# {len(changes)} changes across {len(slugs_touched)} slugs: {slugs_touched}")
    print("_CHANGES: tuple[tuple[str, str, str, str, object, object], ...] = (")
    for row in changes:
        print(f"    {row!r},")
    print(")")
    return 0


if __name__ == "__main__":
    sys.exit(main())
