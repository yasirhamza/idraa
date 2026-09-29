#!/usr/bin/env python3
"""Dev-only generator for the Epic F scenario-library migration's ``_CHANGES``
literal table (alembic/versions/e5f1a9c3d7b2_epic_f_library_curation.py,
plan Task 4, brief Step 1). NOT imported by the app or by any migration --
a human (or an implementer agent) runs it and pastes its stdout into the
migration file by hand, so the migration itself stays a frozen literal, never
a live re-derivation from git or the ORM at `alembic upgrade` time.

Diffs the working-tree scenario seed JSON (``data/seed_library_entries.json``
+ ``data/seed_library_entries_extension.json``) against the same files' blobs
at ``git merge-base HEAD origin/main``. Every top-level key whose parsed value
differs on a slug present in both trees becomes one ``(slug, key, old, new)``
row. Fails loudly (exit 1) rather than emitting a silently-wrong table when:

- a slug was added or removed since the merge-base (this migration only
  carries content changes to existing rows -- inserts/deletes are a
  different migration's job);
- a differing key is not one of the columns the migration is allowed to
  touch (``_ALLOWED_COLUMNS`` -- computed here the same way the migration's
  literal frozenset is derived: ``set(ScenarioLibraryEntry.__table__.columns
  .keys()) & set(LibraryEntrySeed.model_fields) - {"slug"}``, so this script
  never drifts from the ORM/DTO independently of the migration);
- a changed slug is outside ``EXPECTED_SLUGS`` (the known Epic F campaign
  set; catches an unrelated seed edit accidentally riding along).

Prerequisite: run ``git fetch origin`` before this script -- it never fetches
itself (a stale local ``origin/main`` would silently compute the wrong base).

Re-run and re-paste after every rebase onto a moved ``origin/main`` (A-5/
S-13); Task 11 re-checks the pasted table against a fresh run of this script.

Usage: ``uv run python scripts/build_epic_f_migration_table.py``
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from idraa.models.scenario_library import ScenarioLibraryEntry
from idraa.services.seed_library_loader import LibraryEntrySeed
from scripts.curation_check.config import REPO_ROOT
from scripts.curation_check.library import SCENARIO_FILES, _git

EXPECTED_SLUGS: frozenset[str] = frozenset(
    {
        "accidental-insider-exposure",
        "bec-fraud-financial",
        "credential-stuffing-consumer-portal",
        "energy-billing-system-tamper",
        "energy-settlement-platform-tampering-offtaker-liability",
        "financial-transaction-tampering",
        "gov-citizen-portal-ddos",
        "gov-records-tampering",
        "healthcare-record-alteration",
        "education-student-records-insider",
        "gov-employee-insider-leak",
        "healthcare-staff-credential-phish",
        "competitor-trade-secret-recruit",
        "financial-call-center-social-eng",
        "telecom-sim-swap-fraud",
        "data-breach-notification-regulatory-tail",
        "web-app-exploitation",
        "ddos-extortion-financial",
        "ddos-financial-seasonal-peak",
        "chemical-process-safety-attack",
        "safety-system-bypass",
        "professional-payroll-bec",
        "retail-ecommerce-checkout-ddos",
        "grid-protective-relay-manipulation",
    }
)


def allowed_columns() -> frozenset[str]:
    """Mirrors the migration's ``_ALLOWED_COLUMNS`` derivation exactly (ORM ∩ DTO
    field-sync rule) -- computed live here so this generator never drifts from
    the migration independently; the migration itself keeps the result as a
    frozen literal (never a live ORM/DTO import) per migration-immutability."""
    return frozenset(
        (set(ScenarioLibraryEntry.__table__.columns.keys()) & set(LibraryEntrySeed.model_fields))
        - {"slug"}
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


def build_changes(root: Path = REPO_ROOT) -> tuple[tuple[str, str, object, object], ...]:
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

    allowed = allowed_columns()
    changed_slugs: set[str] = set()
    changes: list[tuple[str, str, object, object]] = []
    for slug in sorted(new_slugs):
        old_row = old_by_slug[slug]
        new_row = new_by_slug[slug]
        for key in sorted(set(old_row) | set(new_row)):
            old_val = old_row.get(key)
            new_val = new_row.get(key)
            if old_val == new_val:
                continue
            if key not in allowed:
                raise SystemExit(
                    f"{slug}.{key} changed but {key!r} is not in _ALLOWED_COLUMNS "
                    f"({sorted(allowed)})"
                )
            changed_slugs.add(slug)
            changes.append((slug, key, old_val, new_val))

    unexpected = changed_slugs - EXPECTED_SLUGS
    if unexpected:
        raise SystemExit(f"changed slug(s) outside EXPECTED_SLUGS: {sorted(unexpected)}")

    changes.sort(key=lambda c: (c[0], c[1]))
    return tuple(changes)


def main() -> int:
    changes = build_changes()
    slugs_touched = sorted({c[0] for c in changes})
    print(f"# {len(changes)} changes across {len(slugs_touched)} slugs: {slugs_touched}")
    print("_CHANGES: tuple[tuple[str, str, object, object], ...] = (")
    for row in changes:
        print(f"    {row!r},")
    print(")")
    return 0


if __name__ == "__main__":
    sys.exit(main())
