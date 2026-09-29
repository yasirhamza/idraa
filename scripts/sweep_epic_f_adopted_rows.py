# scripts/sweep_epic_f_adopted_rows.py
"""READ-ONLY diagnostic for Epic F (#192): which org-owned rows are affected by the
scenario-library curation migration (``e5f1a9c3d7b2``) and the control-library
re-curation migration (``f6a2b0d4e8c3``)?

This sweep never writes -- it opens the database with SQLite URI ``mode=ro`` (a
write attempt raises ``sqlite3.OperationalError``) -- and prints only canonical
UUID hex ids, library slugs, version numbers, classes and counts. It NEVER prints
a scenario/control/organization name, a description, or any other user-content
field (spec addendum, Sec2-2).

Interfaces (deliberately not duplicated here -- imported by file path, per the
plan's Task 9 brief):

- ``_CHANGES`` (the (slug, column, old, new) literal table) is read from the
  scenario migration, ``alembic/versions/e5f1a9c3d7b2_epic_f_library_curation.py``.
  Three derived constants come from it:
    * ``DEPRECATED_SCENARIO_SLUGS`` -- every slug with a ``status`` cell whose
      ``new`` is ``"deprecated"``.
    * ``PEOPLE_RELABELED_SLUGS`` -- every slug with an ``asset_class`` cell whose
      ``old`` is ``"people"`` (the six entries Epic F relabeled off ``people``;
      spec §5's "legacy org scenarios cloned from the six people entries").
    * ``EPIC_F_OLD_NODES`` / ``EPIC_F_NEW_NODES`` -- accidental-insider-exposure's
      ``primary_loss`` / ``secondary_loss`` cells (old/new PERT dicts).
- ``_EPIC_F_SLUGS`` (the five re-versioned control slugs) is read from the control
  migration, ``alembic/versions/f6a2b0d4e8c3_epic_f_control_recuration.py``, as
  ``RECURATED_CONTROL_SLUGS``.
- ``classify_fieldset`` / ``is_copy_of`` / ``_dedup_latest`` / ``_scenario_class`` /
  ``_stamp_source`` are imported from ``scripts/sweep_library_secondary_response.py``
  (the #175 sweep) rather than re-implemented. That script's own ``NEW_NODES`` /
  ``NEW_PAIRS`` for ``accidental-insider-exposure`` are frozen at the PRE-Epic-F
  (post-#175) values -- exactly ``EPIC_F_OLD_NODES`` / the pairs ``seeded_pair``
  derives from them -- because Epic F's own re-split superseded that slug (see that
  script's ``SUPERSEDED_BY_EPIC_F`` docstring note, which points here).
- ``seeded_pair`` (node -> the wizard-seeded (p5, p95) SME pair) is imported from
  ``scripts/build_secondary_response_reclass.py`` and applied to
  ``EPIC_F_OLD_NODES`` / ``EPIC_F_NEW_NODES`` to derive ``EPIC_F_OLD_PAIRS`` /
  ``EPIC_F_NEW_PAIRS`` -- never hand-pasted, so they can never drift from the
  migration's own literals.

Sections printed:

1. accidental-insider-exposure adoptions, by class (pristine / copy-stale /
   copy-current / current / stale / modified / pinned), one row per affected
   scenario (id + per-fieldset class + overall class). ``pristine`` here means a
   scenario's sole surviving SME identity on a fieldset still carries the
   PRE-Epic-F seeded pair (``EPIC_F_OLD_PAIRS``) -- a candidate for a future
   repair migration, mirroring ``sweep_library_secondary_response.py``'s
   ``pristine``. ``copy-stale`` means the stored node is a verbatim copy of the
   pre-Epic-F entry node (the library-refresh path, which bypasses SME rows).
   ``copy-current`` means the stored node already matches the POST-Epic-F node.
2. Scenarios pinned to each of the three deprecated entries
   (``data-breach-notification-regulatory-tail``, ``ddos-extortion-financial``,
   ``chemical-process-safety-attack``) -- ids only, plus (addendum M5-1) the
   count of DISTINCT organizations with at least one scenario pinned to any of
   the three (the dashboard coverage figure shifts for them; spec addendum).
3. Legacy scenarios still carrying ``asset_class = 'people'`` on one of the six
   Epic F people-relabeled slugs -- count only.
4. Org overrides (``scenario_library_overrides``, not soft-deleted) on one of
   the three deprecated entries -- count only.
5. Adopted controls on the five re-curated entries (``RECURATED_CONTROL_SLUGS``)
   whose ``library_pin`` version is behind the entry's current version
   (resync-stale, #438) -- listed (id, slug, pinned version, current version);
   a control pinned at the current version is not listed.

``--gate`` exits 1 iff (accidental-insider-exposure) ``pristine + copy_stale >
0`` -- the trigger for a SEPARATE repair migration, never applied inline by this
script (mirrors the #175 sweep's own gate rationale). Every other section is
informational: read the printed rows, not just the gate exit code.

``library_pin`` / ``adopted_snapshot`` ``entry_id`` values are normalised with
``uuid.UUID(x).hex`` before comparison -- ``services/controls.py``'s
``adopt_control_from_library`` writes ``str(entry.id)`` (hyphenated); the ORM's
own ``Uuid`` columns bind hex. Both spellings must resolve to the same row.

Usage:
    uv run python scripts/sweep_epic_f_adopted_rows.py --db /path/to/idraa.db [--gate]

Run against a clean online-backup copy or the live DB, not a raw cp of a WAL
database. Output carries production ids -- keep it in the operator's terminal or
a private note (beside the backup, ``~/idraa-backups/``); only the summary
counters belong in a PR or issue body (Sec2-2).
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import sqlite3
import sys
import types
import uuid
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent


def _load_module(path: Path, name: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError(f"cannot load module from {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_scenario_migration = _load_module(
    _ROOT / "alembic" / "versions" / "e5f1a9c3d7b2_epic_f_library_curation.py",
    "_epic_f_scenario_migration",
)
_control_migration = _load_module(
    _ROOT / "alembic" / "versions" / "f6a2b0d4e8c3_epic_f_control_recuration.py",
    "_epic_f_control_migration",
)
_secondary_response_sweep = _load_module(
    _ROOT / "scripts" / "sweep_library_secondary_response.py",
    "_epic_f_secondary_response_sweep",
)
_reclass_builder = _load_module(
    _ROOT / "scripts" / "build_secondary_response_reclass.py",
    "_epic_f_reclass_builder",
)

classify_fieldset = _secondary_response_sweep.classify_fieldset
is_copy_of = _secondary_response_sweep.is_copy_of
_stamp_source = _secondary_response_sweep._stamp_source
_scenario_class = _secondary_response_sweep._scenario_class
seeded_pair = _reclass_builder.seeded_pair

# ---------------------------------------------------------------------------
# Constants derived from the two migrations -- never hand-duplicated.
# ---------------------------------------------------------------------------

_CHANGES: tuple[tuple[str, str, object, object], ...] = _scenario_migration._CHANGES

DEPRECATED_SCENARIO_SLUGS: frozenset[str] = frozenset(
    slug for slug, column, _old, new in _CHANGES if column == "status" and new == "deprecated"
)

PEOPLE_RELABELED_SLUGS: frozenset[str] = frozenset(
    slug for slug, column, old, _new in _CHANGES if column == "asset_class" and old == "people"
)

_AIE_CELLS: dict[str, tuple[object, object]] = {
    column: (old, new)
    for slug, column, old, new in _CHANGES
    if slug == "accidental-insider-exposure" and column in ("primary_loss", "secondary_loss")
}


def _as_node(value: object) -> dict[str, Any]:
    """Narrow a migration cell's `object`-typed old/new literal to a loss-node
    dict. The migration's own ``_CHANGES`` type is intentionally loose
    (``tuple[str, str, object, object]`` -- cells span JSON and text/enum
    columns alike); this is a real runtime guard, not just a mypy cast --
    a malformed cell must never silently become an unclassifiable node."""
    if not isinstance(value, dict):  # pragma: no cover - defensive
        raise TypeError(f"expected a loss-node dict in _CHANGES, got {type(value).__name__}")
    return value


EPIC_F_OLD_NODES: dict[str, dict[str, Any]] = {
    "pl": _as_node(_AIE_CELLS["primary_loss"][0]),
    "sl": _as_node(_AIE_CELLS["secondary_loss"][0]),
}
EPIC_F_NEW_NODES: dict[str, dict[str, Any]] = {
    "pl": _as_node(_AIE_CELLS["primary_loss"][1]),
    "sl": _as_node(_AIE_CELLS["secondary_loss"][1]),
}
# Wizard-seeded (p5, p95) pairs, derived (never hand-pasted) from the nodes above.
EPIC_F_OLD_PAIRS: dict[str, tuple[float, float]] = {
    fs: seeded_pair(EPIC_F_OLD_NODES[fs]) for fs in ("pl", "sl")
}
EPIC_F_NEW_PAIRS: dict[str, tuple[float, float]] = {
    fs: seeded_pair(EPIC_F_NEW_NODES[fs]) for fs in ("pl", "sl")
}

RECURATED_CONTROL_SLUGS: tuple[str, ...] = _control_migration._EPIC_F_SLUGS


def _hex(raw: object) -> str:
    """Canonical 32-char hex form of a UUID spelled any way (hyphenated, upper,
    braced, or already hex). Raises ValueError on anything that isn't a UUID --
    callers catch it as "unparsable", never crash the sweep."""
    return uuid.UUID(str(raw)).hex


def _connect_ro(path: Path) -> sqlite3.Connection:
    """Open ``path`` read-only via the SQLite URI idiom (mode=ro) -- a write
    attempt through the returned connection raises sqlite3.OperationalError."""
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)


def _parse_pin(raw: object) -> dict[str, Any] | None:
    """Best-effort JSON decode of a library_pin column. Returns None for NULL,
    the JSON text 'null', or anything that fails to parse -- callers treat None
    as "not pinned to a tracked entry", never as an error."""
    if raw is None:
        return None
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, RecursionError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _entry_hex(pin: dict[str, Any] | None) -> str | None:
    if pin is None:
        return None
    try:
        return _hex(pin.get("entry_id"))
    except (ValueError, AttributeError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Section 1: accidental-insider-exposure adoptions.
# ---------------------------------------------------------------------------


def _aie_fieldset_class(
    node: dict[str, Any] | None,
    rows: list[tuple[str | None, str | None, float, float]],
    fs: str,
) -> str:
    if _stamp_source(node) == "analyst_pin":
        return "pinned"
    if is_copy_of(node, EPIC_F_OLD_NODES[fs]):
        return "copy-stale"
    if is_copy_of(node, EPIC_F_NEW_NODES[fs]):
        return "copy-current"
    return str(classify_fieldset(rows, EPIC_F_OLD_PAIRS[fs], EPIC_F_NEW_PAIRS[fs]))


def _aie_class(pl: str, sl: str) -> str:
    """Overall scenario class. Reuses _scenario_class's precedence (pinned >
    copy-stale > pristine > modified > current/stale), then relabels a "current"
    result back to "copy-current" when a field actually copy-matched the new
    node verbatim (_scenario_class folds copy-current* into current for its own
    fold-precedence; this sweep keeps the distinction in its reported class)."""
    klass = str(_scenario_class(pl, sl))
    if klass == "current" and "copy-current" in (pl, sl):
        return "copy-current"
    return klass


def sweep_accidental_insider_exposure(conn: sqlite3.Connection) -> dict[str, int]:
    slug_by_entry_hex: dict[str, str] = {}
    for eid, slug in conn.execute(
        "SELECT id, slug FROM scenario_library_entries WHERE version = 1"
    ):
        try:
            slug_by_entry_hex[_hex(eid)] = slug
        except ValueError:
            continue

    counts = {
        "pristine": 0,
        "copy_stale": 0,
        "copy_current": 0,
        "current": 0,
        "stale": 0,
        "modified": 0,
        "pinned": 0,
    }
    print("scenario_id | pl | sl | class")
    for sid, pin_raw, status, pl_raw, sl_raw in conn.execute(
        "SELECT id, library_pin, status, primary_loss, secondary_loss FROM scenarios"
    ):
        if status == "deleted":
            continue
        pin = _parse_pin(pin_raw)
        entry_hex = _entry_hex(pin)
        if entry_hex is None or slug_by_entry_hex.get(entry_hex) != "accidental-insider-exposure":
            continue
        if pin is not None and pin.get("version") not in (None, 1):
            continue
        try:
            nodes = {
                fs: (json.loads(raw) if isinstance(raw, str) else raw)
                for fs, raw in (("pl", pl_raw), ("sl", sl_raw))
            }
        except (ValueError, RecursionError):
            continue
        try:
            sid_hex = _hex(sid)
        except ValueError:
            continue

        classes: dict[str, str] = {}
        for fs in ("pl", "sl"):
            rows = conn.execute(
                "SELECT sme_id, sme_name, low, high FROM scenario_sme_estimates "
                "WHERE lower(replace(scenario_id, '-', '')) = :sid AND fieldset = :fs "
                "ORDER BY recorded_at ASC, id ASC",
                {"sid": sid_hex, "fs": fs},
            ).fetchall()
            classes[fs] = _aie_fieldset_class(nodes[fs], rows, fs)

        klass = _aie_class(classes["pl"], classes["sl"])
        counts[klass.replace("-", "_")] = counts.get(klass.replace("-", "_"), 0) + 1
        print(f"{sid_hex} | {classes['pl']} | {classes['sl']} | {klass}")

    print("accidental_insider_exposure: " + " ".join(f"{k}={v}" for k, v in counts.items()))
    return counts


# ---------------------------------------------------------------------------
# Section 2: scenarios (and orgs) pinned to a deprecated entry.
# ---------------------------------------------------------------------------


def sweep_deprecated_entries(
    conn: sqlite3.Connection,
) -> tuple[dict[str, list[str]], int]:
    """Returns (slug -> [scenario id hex, ...], distinct org count across the
    union of the three deprecated slugs -- addendum M5-1)."""
    slug_by_entry_hex: dict[str, str] = {}
    for eid, slug in conn.execute("SELECT id, slug FROM scenario_library_entries"):
        try:
            slug_by_entry_hex[_hex(eid)] = slug
        except ValueError:
            continue

    by_slug: dict[str, list[str]] = {slug: [] for slug in sorted(DEPRECATED_SCENARIO_SLUGS)}
    org_ids: set[str] = set()
    print("scenario_id | deprecated_slug")
    for sid, pin_raw, status, org_raw in conn.execute(
        "SELECT id, library_pin, status, organization_id FROM scenarios"
    ):
        if status == "deleted":
            continue
        entry_hex = _entry_hex(_parse_pin(pin_raw))
        if entry_hex is None:
            continue
        slug = slug_by_entry_hex.get(entry_hex)
        if slug not in DEPRECATED_SCENARIO_SLUGS:
            continue
        try:
            sid_hex = _hex(sid)
        except ValueError:
            continue
        by_slug[slug].append(sid_hex)
        with contextlib.suppress(ValueError):
            org_ids.add(_hex(org_raw))
        print(f"{sid_hex} | {slug}")

    print(
        "deprecated_pinned: "
        + " ".join(f"{slug}={len(ids)}" for slug, ids in sorted(by_slug.items()))
        + f" total={sum(len(ids) for ids in by_slug.values())}"
        + f" orgs={len(org_ids)}"
    )
    return by_slug, len(org_ids)


# ---------------------------------------------------------------------------
# Section 3: legacy people-asset-class scenarios.
# ---------------------------------------------------------------------------


def sweep_people_asset_class(conn: sqlite3.Connection) -> int:
    slug_by_entry_hex: dict[str, str] = {}
    for eid, slug in conn.execute("SELECT id, slug FROM scenario_library_entries"):
        try:
            slug_by_entry_hex[_hex(eid)] = slug
        except ValueError:
            continue

    count = 0
    for _sid, pin_raw, status, asset_class in conn.execute(
        "SELECT id, library_pin, status, asset_class FROM scenarios"
    ):
        if status == "deleted" or asset_class != "people":
            continue
        entry_hex = _entry_hex(_parse_pin(pin_raw))
        if entry_hex is None:
            continue
        if slug_by_entry_hex.get(entry_hex) in PEOPLE_RELABELED_SLUGS:
            count += 1
    print(f"people_asset_class_scenarios={count}")
    return count


# ---------------------------------------------------------------------------
# Section 4: org overrides on a deprecated entry.
# ---------------------------------------------------------------------------


def sweep_deprecated_overrides(conn: sqlite3.Connection) -> int:
    slug_by_entry_hex: dict[str, str] = {}
    for eid, slug in conn.execute("SELECT id, slug FROM scenario_library_entries"):
        try:
            slug_by_entry_hex[_hex(eid)] = slug
        except ValueError:
            continue

    count = 0
    for (eid,) in conn.execute(
        "SELECT library_entry_id FROM scenario_library_overrides WHERE deleted_at IS NULL"
    ):
        try:
            entry_hex = _hex(eid)
        except ValueError:
            continue
        if slug_by_entry_hex.get(entry_hex) in DEPRECATED_SCENARIO_SLUGS:
            count += 1
    print(f"deprecated_overrides={count}")
    return count


# ---------------------------------------------------------------------------
# Section 5: adopted controls on the five re-curated entries.
# ---------------------------------------------------------------------------


def sweep_resync_stale_controls(conn: sqlite3.Connection) -> tuple[int, int]:
    slug_by_entry_hex: dict[str, str] = {}
    current_version: dict[str, int] = {}
    for eid, slug, version in conn.execute("SELECT id, slug, version FROM control_library_entries"):
        try:
            h = _hex(eid)
        except ValueError:
            continue
        slug_by_entry_hex[h] = slug
        if slug in RECURATED_CONTROL_SLUGS:
            current_version[slug] = max(current_version.get(slug, 0), version)

    resync_stale = 0
    current = 0
    print("control_id | slug | pinned_version | current_version")
    for cid, pin_raw, status in conn.execute("SELECT id, library_pin, status FROM controls"):
        if status == "deleted":
            continue
        pin = _parse_pin(pin_raw)
        entry_hex = _entry_hex(pin)
        if entry_hex is None:
            continue
        slug = slug_by_entry_hex.get(entry_hex)
        if slug not in RECURATED_CONTROL_SLUGS:
            continue
        pinned_version = pin.get("version") if pin is not None else None
        cur = current_version.get(slug)
        if cur is None or not isinstance(pinned_version, int):
            continue
        try:
            cid_hex = _hex(cid)
        except ValueError:
            continue
        if pinned_version < cur:
            resync_stale += 1
            print(f"{cid_hex} | {slug} | {pinned_version} | {cur}")
        else:
            current += 1
    print(f"adopted_controls: resync_stale={resync_stale} current={current}")
    return resync_stale, current


# ---------------------------------------------------------------------------
# Orchestration.
# ---------------------------------------------------------------------------


def sweep(db_path: Path) -> dict[str, int]:
    conn = _connect_ro(db_path)
    try:
        aie = sweep_accidental_insider_exposure(conn)
        _by_slug, deprecated_orgs = sweep_deprecated_entries(conn)
        people = sweep_people_asset_class(conn)
        deprecated_overrides = sweep_deprecated_overrides(conn)
        resync_stale, resync_current = sweep_resync_stale_controls(conn)
    finally:
        conn.close()

    summary = {
        "aie_pristine": aie["pristine"],
        "aie_copy_stale": aie["copy_stale"],
        "aie_copy_current": aie["copy_current"],
        "aie_current": aie["current"],
        "aie_stale": aie["stale"],
        "aie_modified": aie["modified"],
        "aie_pinned": aie["pinned"],
        "deprecated_orgs": deprecated_orgs,
        "people_asset_class_scenarios": people,
        "deprecated_overrides": deprecated_overrides,
        "control_resync_stale": resync_stale,
        "control_current": resync_current,
    }
    print("SUMMARY " + " ".join(f"{k}={v}" for k, v in summary.items()))
    return summary


GATE_KEYS = ("aie_pristine", "aie_copy_stale")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    gate = "--gate" in args
    rest = [a for a in args if a != "--gate"]
    if len(rest) != 2 or rest[0] != "--db" or not Path(rest[1]).is_file():
        print(
            __doc__ if len(rest) != 2 or rest[0] != "--db" else f"no such file: {rest[1]}",
            file=sys.stderr,
        )
        return 2
    summary = sweep(Path(rest[1]))
    if gate and sum(summary[k] for k in GATE_KEYS) > 0:
        print("GATE: not clean -- " + " ".join(f"{k}={summary[k]}" for k in GATE_KEYS))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
