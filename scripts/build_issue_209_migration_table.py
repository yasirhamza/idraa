#!/usr/bin/env python3
"""Dev-only generator for the issue #209 scenario-library migration's ``_CHANGES``
and ``_REMAPS`` literal tables (alembic/versions/c7d2e9f4a1b3_issue_209_community_remaps.py,
idraa#209). NOT imported by the app or by any migration -- a human (or an
implementer agent) runs it and pastes its stdout into the migration file by
hand, so the migration itself stays a frozen literal, never a live
re-derivation from git or the ORM at `alembic upgrade` time.

Diffs the working-tree scenario seed JSON (``data/seed_library_entries.json``
+ ``data/seed_library_entries_extension.json``) against the same files' blobs
at ``git merge-base HEAD origin/main``. Every top-level key whose parsed value
differs on a slug present in both trees becomes one ``(slug, key, old, new)``
row, except ``threat_community``, which becomes one ``(slug, old, new, "")``
re-map row (the rationale is filled in by hand from the spec). Fails loudly
(exit 1) rather than emitting a silently-wrong table when:

- a slug was added or removed since the merge-base (this migration only
  carries content changes to existing rows -- inserts/deletes are a
  different migration's job);
- a differing key is not one of the columns the migration is allowed to
  touch (``ALLOWED_COLUMNS`` -- a literal that mirrors the migration's
  ``_ALLOWED_COLUMNS``) and is not ``threat_community``;
- a changed slug is outside ``EXPECTED_SLUGS`` (the known issue #209 set;
  catches an unrelated seed edit accidentally riding along).

Prerequisite: run ``git fetch origin`` before this script -- it never fetches
itself (a stale local ``origin/main`` would silently compute the wrong base).

Re-run and re-paste after every rebase onto a moved ``origin/main``.

Usage: ``uv run python scripts/build_issue_209_migration_table.py``
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from scripts.curation_check.config import REPO_ROOT
from scripts.curation_check.library import SCENARIO_FILES, _git

EXPECTED_SLUGS: frozenset[str] = frozenset(
    {
        "accidental-insider-exposure",
        "credential-stuffing-consumer-portal",
        "crop-science-ip-exfiltration",
        "higher-ed-insider-ddos",
        "hospitality-guest-data-insider",
        "insider-ip-theft-manufacturing",
        "ot-network-scanning-reconnaissance",
    }
)

# mirrors the migration's _ALLOWED_COLUMNS literal (pinned by tests/migrations/test_issue_209_community_remaps.py)
ALLOWED_COLUMNS: frozenset[str] = frozenset(
    {"name", "description", "attack_vector", "tags", "canonical_fair_gap"}
)


def _load_at_base(root: Path, base: str) -> dict[str, dict[str, Any]]:
    by_slug: dict[str, dict[str, Any]] = {}
    for rel in SCENARIO_FILES:
        text = _git(root, "cat-file", "blob", f"{base}:{rel.as_posix()}")
        for row in json.loads(text):
            by_slug[row["slug"]] = row
    return by_slug


def _load_working_tree(root: Path) -> dict[str, dict[str, Any]]:
    by_slug: dict[str, dict[str, Any]] = {}
    for rel in SCENARIO_FILES:
        for row in json.loads((root / rel).read_text(encoding="utf-8")):
            by_slug[row["slug"]] = row
    return by_slug


def build_changes(
    root: Path = REPO_ROOT,
) -> tuple[
    tuple[tuple[str, str, object, object], ...],
    tuple[tuple[str, str, str, str], ...],
]:
    base = _git(root, "merge-base", "HEAD", "origin/main").strip()
    old_by_slug = _load_at_base(root, base)
    new_by_slug = _load_working_tree(root)

    old_slugs = set(old_by_slug)
    new_slugs = set(new_by_slug)
    added = new_slugs - old_slugs
    removed = old_slugs - new_slugs
    if added or removed:
        raise SystemExit(
            f"slug set changed since merge-base {base}: "
            f"added={sorted(added)} removed={sorted(removed)} -- this migration "
            "only carries content changes to existing rows"
        )

    changed_slugs: set[str] = set()
    changes: list[tuple[str, str, object, object]] = []
    remaps: list[tuple[str, str, str, str]] = []
    for slug in sorted(new_slugs):
        old_row = old_by_slug[slug]
        new_row = new_by_slug[slug]
        for key in sorted(set(old_row) | set(new_row)):
            old_val = old_row.get(key)
            new_val = new_row.get(key)
            if old_val == new_val:
                continue
            if key == "threat_community":
                if not isinstance(old_val, str) or not isinstance(new_val, str):
                    raise SystemExit(
                        f"{slug}.threat_community must be a string on both sides of the "
                        f"merge-base diff (old={old_val!r}, new={new_val!r})"
                    )
                changed_slugs.add(slug)
                remaps.append((slug, old_val, new_val, ""))
                continue
            if key not in ALLOWED_COLUMNS:
                raise SystemExit(
                    f"{slug}.{key} changed but {key!r} is not in ALLOWED_COLUMNS "
                    f"({sorted(ALLOWED_COLUMNS)})"
                )
            changed_slugs.add(slug)
            changes.append((slug, key, old_val, new_val))

    unexpected = changed_slugs - EXPECTED_SLUGS
    if unexpected:
        raise SystemExit(f"changed slug(s) outside EXPECTED_SLUGS: {sorted(unexpected)}")

    changes.sort(key=lambda c: (c[0], c[1]))
    remaps.sort(key=lambda r: r[0])
    return tuple(changes), tuple(remaps)


def main() -> int:
    changes, remaps = build_changes()
    slugs_touched = sorted({c[0] for c in changes} | {r[0] for r in remaps})
    print(
        f"# {len(changes)} changes + {len(remaps)} re-maps across "
        f"{len(slugs_touched)} slugs: {slugs_touched}"
    )
    print("_CHANGES: tuple[tuple[str, str, object, object], ...] = (")
    for row in changes:
        print(f"    {row!r},")
    print(")")
    print()
    print("# fill the rationale (4th element) from spec §2 before pasting")
    print("_REMAPS: tuple[tuple[str, str, str, str], ...] = (")
    for remap in remaps:
        print(f"    {remap!r},")
    print(")")
    return 0


if __name__ == "__main__":
    sys.exit(main())
