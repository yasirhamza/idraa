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
- ``classify_fieldset`` / ``is_copy_of`` / ``has_pre_change_identity`` /
  ``_scenario_class`` / ``_stamp_source`` / ``_override_leg`` are imported from
  ``scripts/sweep_library_secondary_response.py`` (the #175 sweep) rather than
  re-implemented -- ``_dedup_latest`` is used only transitively, inside
  ``classify_fieldset``/``has_pre_change_identity``, never called directly here.
  That script's own ``NEW_NODES`` / ``NEW_PAIRS`` for ``accidental-insider-
  exposure`` are frozen at the PRE-Epic-F (post-#175) values -- exactly
  ``EPIC_F_OLD_NODES`` / the pairs ``seeded_pair`` derives from them -- because
  Epic F's own re-split superseded that slug (see that script's
  ``SUPERSEDED_BY_EPIC_F`` docstring note, which points here). Fix round 1 (M9-4
  / spec I-1) added a test pinning both directions: ``EPIC_F_OLD_*`` to that
  script's frozen ``NEW_*`` tables, and ``EPIC_F_NEW_*`` to the current seed JSON
  via ``seeded_pair``.
- ``seeded_pair`` (node -> the wizard-seeded (p5, p95) SME pair) is imported from
  ``scripts/build_secondary_response_reclass.py`` and applied to
  ``EPIC_F_OLD_NODES`` / ``EPIC_F_NEW_NODES`` to derive ``EPIC_F_OLD_PAIRS`` /
  ``EPIC_F_NEW_PAIRS`` -- never hand-pasted, so they can never drift from the
  migration's own literals.

Sections printed:

1. accidental-insider-exposure adoptions, by class (pristine / copy-stale /
   copy-current(*) / current / stale / modified / pinned), one row per affected
   scenario (id, `(ovr)` marker when ``library_pin.override_id`` is set,
   per-fieldset class, overall class). ``pristine`` here means a scenario's sole
   surviving SME identity on a fieldset still carries the PRE-Epic-F seeded pair
   (``EPIC_F_OLD_PAIRS``) -- a candidate for a future repair migration, mirroring
   ``sweep_library_secondary_response.py``'s ``pristine``. ``copy-stale`` means
   the stored node is a verbatim copy of the pre-Epic-F entry node (the
   library-refresh path, which bypasses SME rows). ``copy-current`` means the
   stored node already matches the POST-Epic-F node; a fieldset printed
   ``copy-current*`` additionally still carries a surviving pre-Epic-F SME
   identity that a future re-estimation would rehydrate and re-fit (fix round 1
   / M9-2, mirrors #175's ``copy_current_stale_rows`` -- ``aie_copy_current_
   stale_rows`` in the SUMMARY counts scenarios, not fieldsets).
   Fix round 1 (M9-1) additionally counts rows this section cannot classify --
   an unparsable ``library_pin``/loss-node JSON, or a pin whose ``version`` is
   not 1 -- as ``aie_skipped_unparsable`` / ``aie_skipped_pin_version`` rather
   than silently dropping them; both gate (see below). Fix round 1 (M9-3a) also
   counts ``aie_pinned_stale_side``: a scenario whose overall class is ``pinned``
   (D23 -- the app refuses to refresh the WHOLE scenario when either field is
   analyst-pinned) but whose OTHER field is ``pristine``/``copy-stale``/
   ``copy-current*`` -- the repair PR needs this count even though a pinned
   scenario is never itself a repair candidate.
2. Scenarios pinned to each of the three deprecated entries
   (``data-breach-notification-regulatory-tail``, ``ddos-extortion-financial``,
   ``chemical-process-safety-attack``) -- ids only, plus (addendum M5-1) the
   count of DISTINCT organizations with at least one scenario pinned to any of
   the three (the dashboard coverage figure shifts for them; spec addendum).
3. Legacy scenarios still carrying ``asset_class = 'people'`` on one of the six
   Epic F people-relabeled slugs -- count only.
4. Org overrides (``scenario_library_overrides``, not soft-deleted) on one of
   the three deprecated entries -- count only. Fix round 1 (M9-3b) adds
   ``aie_override_one_sided``: an org override on accidental-insider-exposure
   itself that authors only ONE of primary_loss/secondary_loss. Epic F moved
   AIE's response share from PL to SL, so a one-sided override now combines
   with the moved canonical side and its adoptions/refreshes are no longer
   mean-neutral (hand-math: a PL-only override raises the SL PERT mean by
   about $30,985, ~5.0% of the $619,692 inherent PL+SL mean).
5. Adopted controls on the five re-curated entries (``RECURATED_CONTROL_SLUGS``)
   whose ``library_pin`` version is behind the entry's current version
   (resync-stale, #438) -- listed (id, slug, pinned version, current version);
   a control pinned at the current version is not listed, only counted.
   PRE-deploy, ``control_resync_stale`` reflects staleness from EARLIER #438
   bumps and ``control_current`` is the set Epic F itself will make stale (the
   new version doesn't exist in the DB yet); POST-deploy, ``control_resync_
   stale`` is the Epic F set (N9-2). Fix round 1 (M9-1) adds the controls
   equivalent of section 1's skip counters (``control_skipped_unparsable`` /
   ``control_skipped_pin_version``) -- informational, NOT gated (this whole
   section is informational).
6. (Fix round 1, N9-1 / spec §4.6 M-15) ``double_counted_orgs``: distinct orgs
   with an active scenario pinned to BOTH ``data-breach-notification-
   regulatory-tail`` and ``web-app-exploitation`` -- an overlapping loss event
   double-counted in an AGGREGATE run. Predates Epic F; informational only,
   never gated.

``--gate`` exits 1 iff any AIE gate key is nonzero: ``aie_pristine``,
``aie_copy_stale``, ``aie_skipped_unparsable``, ``aie_skipped_pin_version``,
``aie_pinned_stale_side``, ``aie_copy_current_stale_rows``, ``aie_override_
one_sided`` -- the trigger for a SEPARATE repair migration, never applied
inline by this script. This now mirrors the #175 sweep's own ``GATE_KEYS`` set
(scoped to the one AIE slug instead of all 24) -- fix round 1 corrects the
original docstring's "mirrors the #175 gate rationale" claim, which was true in
spirit but the original gate covered only 2 of the 7 keys #175 gates on. A
pre-#175 AIE adoption (a scenario still on the pre-#175 node/pair, never
touched since) lands in ``aie_stale``, not the gate -- correct for THIS
campaign because the #175 sweep's own gate already covers it (N9-3). Every
section besides 1 is informational: read the printed rows, not just the gate
exit code. A pin with no ``version`` key (``None``) is treated the same as a
wrong version (skipped, counted, gated) -- the writer always sets an int
version, so this is a defensive default, not an observed real shape (N9-4).

``library_pin`` / ``adopted_snapshot`` ``entry_id`` values are normalised with
``uuid.UUID(x).hex`` before comparison -- ``services/controls.py``'s
``adopt_control_from_library`` writes ``str(entry.id)`` (hyphenated); the ORM's
own ``Uuid`` columns bind hex. Both spellings must resolve to the same row.

Exit codes: 0 clean, 1 ``--gate`` not clean, 2 usage / missing ``--db`` file, 3
an unexpected exception during the sweep (prints only the exception type, never
its message, to stderr -- mirrors ``scripts/check_epic_f_migration.py``'s exit-4
convention; Sec2-2/Sec3-2).

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

AIE_SLUG = "accidental-insider-exposure"
DOUBLE_COUNT_PAIR: tuple[str, str] = (
    "data-breach-notification-regulatory-tail",
    "web-app-exploitation",
)


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
has_pre_change_identity = _secondary_response_sweep.has_pre_change_identity
_stamp_source = _secondary_response_sweep._stamp_source
_scenario_class = _secondary_response_sweep._scenario_class
_override_leg = _secondary_response_sweep._override_leg
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
    if slug == AIE_SLUG and column in ("primary_loss", "secondary_loss")
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


def _pin_status(raw: object) -> tuple[str, dict[str, Any] | None]:
    """Tri-state read of a ``library_pin`` column (M9-1): ``("absent", None)``
    for SQL NULL or the JSON text ``'null'``; ``("malformed", None)`` for a JSON
    decode failure or a non-dict result -- a genuinely corrupt row, counted
    towards a skip counter by callers that track one; ``("ok", dict)``
    otherwise. Mirrors the #175 sweep's own absent/malformed split
    (``_override_leg``), applied to the scenario/control pin column instead of
    an override leg."""
    if raw is None:
        return "absent", None
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, RecursionError):
        return "malformed", None
    if parsed is None:
        return "absent", None
    if not isinstance(parsed, dict):
        return "malformed", None
    return "ok", parsed


def _entry_hex_from_pin(pin: dict[str, Any]) -> str | None:
    """None when ``entry_id`` is missing or not a valid UUID -- NOT malformed,
    just not resolvable to any tracked entry (matches the #175 sweep: a pin
    dict with no/garbage ``entry_id`` simply isn't a match; it does not count
    as corrupt)."""
    try:
        return _hex(pin.get("entry_id"))
    except (ValueError, AttributeError, TypeError):
        return None


def _resolved_entry_hex(pin_raw: object) -> str | None:
    """Entry hex for sections that don't need the corrupt/absent/unmatched
    distinction (sections 2-4 and the double-count check) -- only sections 1
    and 5 track a skip counter, so only they call ``_pin_status`` directly."""
    state, pin = _pin_status(pin_raw)
    if state != "ok" or pin is None:
        return None
    return _entry_hex_from_pin(pin)


def _scenario_library_slugs(
    conn: sqlite3.Connection, *, version_1_only: bool = False
) -> dict[str, str]:
    query = "SELECT id, slug FROM scenario_library_entries"
    if version_1_only:
        query += " WHERE version = 1"
    slugs: dict[str, str] = {}
    for eid, slug in conn.execute(query):
        try:
            slugs[_hex(eid)] = slug
        except ValueError:
            continue
    return slugs


# ---------------------------------------------------------------------------
# Section 1: accidental-insider-exposure adoptions.
# ---------------------------------------------------------------------------


def _aie_fieldset_class(
    node: dict[str, Any] | None,
    rows: list[tuple[str | None, str | None, float, float]],
    fs: str,
) -> tuple[str, bool]:
    """Returns (class, stale_rows). ``stale_rows`` is only ever True for a
    ``copy-current*`` class (M9-2): a fieldset already refreshed to the new
    node that STILL carries a surviving pre-Epic-F SME identity, which a
    future re-estimation would rehydrate and re-fit (#175's
    ``copy_current_stale_rows``)."""
    if _stamp_source(node) == "analyst_pin":
        return "pinned", False
    if is_copy_of(node, EPIC_F_OLD_NODES[fs]):
        return "copy-stale", False
    if is_copy_of(node, EPIC_F_NEW_NODES[fs]):
        if has_pre_change_identity(rows, EPIC_F_OLD_PAIRS[fs]):
            return "copy-current*", True
        return "copy-current", False
    return str(classify_fieldset(rows, EPIC_F_OLD_PAIRS[fs], EPIC_F_NEW_PAIRS[fs])), False


def _aie_class(pl: str, sl: str) -> str:
    """Overall scenario class. Reuses _scenario_class's precedence (pinned >
    copy-stale > pristine > modified > current/stale; it also folds
    "copy-current*" the same as "copy-current" internally), then relabels a
    "current" result back to "copy-current" when a field actually copy-matched
    the new node verbatim -- this sweep keeps that distinction in its reported
    top-level class (the "*" staleness detail stays in the per-field columns
    and the separate ``copy_current_stale_rows`` counter, not the top-level
    class name, so the SUMMARY keeps a fixed 7-bucket shape)."""
    klass = str(_scenario_class(pl, sl))
    if klass == "current" and ("copy-current" in (pl, sl) or "copy-current*" in (pl, sl)):
        return "copy-current"
    return klass


def sweep_accidental_insider_exposure(conn: sqlite3.Connection) -> dict[str, int]:
    slug_by_entry_hex = _scenario_library_slugs(conn, version_1_only=True)

    counts = {
        "pristine": 0,
        "copy_stale": 0,
        "copy_current": 0,
        "current": 0,
        "stale": 0,
        "modified": 0,
        "pinned": 0,
        "pinned_stale_side": 0,
        "copy_current_stale_rows": 0,
        "skipped_unparsable": 0,
        "skipped_pin_version": 0,
    }
    print("scenario_id | pl | sl | class")
    for sid, pin_raw, status, pl_raw, sl_raw in conn.execute(
        "SELECT id, library_pin, status, primary_loss, secondary_loss FROM scenarios"
    ):
        if status == "deleted":
            continue
        pin_state, pin = _pin_status(pin_raw)
        if pin_state == "malformed":
            counts["skipped_unparsable"] += 1
            continue
        if pin_state == "absent" or pin is None:
            continue
        entry_hex = _entry_hex_from_pin(pin)
        if entry_hex is None or slug_by_entry_hex.get(entry_hex) != AIE_SLUG:
            continue
        if pin.get("version") != 1:
            counts["skipped_pin_version"] += 1
            continue
        try:
            nodes = {
                fs: (json.loads(raw) if isinstance(raw, str) else raw)
                for fs, raw in (("pl", pl_raw), ("sl", sl_raw))
            }
        except (ValueError, RecursionError):
            counts["skipped_unparsable"] += 1
            continue
        try:
            sid_hex = _hex(sid)
        except ValueError:
            counts["skipped_unparsable"] += 1
            continue

        classes: dict[str, str] = {}
        stale_rows_under_current = False
        for fs in ("pl", "sl"):
            rows = conn.execute(
                "SELECT sme_id, sme_name, low, high FROM scenario_sme_estimates "
                "WHERE lower(replace(scenario_id, '-', '')) = :sid AND fieldset = :fs "
                "ORDER BY recorded_at ASC, id ASC",
                {"sid": sid_hex, "fs": fs},
            ).fetchall()
            fs_class, stale = _aie_fieldset_class(nodes[fs], rows, fs)
            classes[fs] = fs_class
            stale_rows_under_current = stale_rows_under_current or stale

        klass = _aie_class(classes["pl"], classes["sl"])
        key = klass.replace("-", "_")
        counts[key] = counts.get(key, 0) + 1
        if klass == "pinned" and {"pristine", "copy-stale", "copy-current*"} & set(
            classes.values()
        ):
            counts["pinned_stale_side"] += 1
        if stale_rows_under_current:
            counts["copy_current_stale_rows"] += 1
        marker = " (ovr)" if pin.get("override_id") else ""
        print(f"{sid_hex}{marker} | {classes['pl']} | {classes['sl']} | {klass}")

    print("accidental_insider_exposure: " + " ".join(f"{k}={v}" for k, v in counts.items()))
    return counts


def sweep_aie_override_one_sided(conn: sqlite3.Connection) -> int:
    """M9-3b: org overrides on accidental-insider-exposure (not soft-deleted)
    that author only ONE of primary_loss/secondary_loss. Epic F moved AIE's
    response share from PL to SL, so a one-sided override now combines with
    the moved canonical side -- no longer mean-neutral."""
    slug_by_entry_hex = _scenario_library_slugs(conn)
    aie_hexes = {h for h, s in slug_by_entry_hex.items() if s == AIE_SLUG}
    count = 0
    for eid, pl_raw, sl_raw in conn.execute(
        "SELECT library_entry_id, primary_loss, secondary_loss FROM scenario_library_overrides "
        "WHERE deleted_at IS NULL"
    ):
        try:
            entry_hex = _hex(eid)
        except ValueError:
            continue
        if entry_hex not in aie_hexes:
            continue
        legs = (_override_leg(pl_raw), _override_leg(sl_raw))
        if "unparsable" in legs:
            continue
        if legs[0] != legs[1]:
            count += 1
    print(f"aie_override_one_sided={count}")
    return count


# ---------------------------------------------------------------------------
# Section 2: scenarios (and orgs) pinned to a deprecated entry.
# ---------------------------------------------------------------------------


def sweep_deprecated_entries(
    conn: sqlite3.Connection,
) -> tuple[dict[str, list[str]], int]:
    """Returns (slug -> [scenario id hex, ...], distinct org count across the
    union of the three deprecated slugs -- addendum M5-1)."""
    slug_by_entry_hex = _scenario_library_slugs(conn)

    by_slug: dict[str, list[str]] = {slug: [] for slug in sorted(DEPRECATED_SCENARIO_SLUGS)}
    org_ids: set[str] = set()
    print("scenario_id | deprecated_slug")
    for sid, pin_raw, status, org_raw in conn.execute(
        "SELECT id, library_pin, status, organization_id FROM scenarios"
    ):
        if status == "deleted":
            continue
        entry_hex = _resolved_entry_hex(pin_raw)
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
    slug_by_entry_hex = _scenario_library_slugs(conn)

    count = 0
    for _sid, pin_raw, status, asset_class in conn.execute(
        "SELECT id, library_pin, status, asset_class FROM scenarios"
    ):
        if status == "deleted" or asset_class != "people":
            continue
        entry_hex = _resolved_entry_hex(pin_raw)
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
    slug_by_entry_hex = _scenario_library_slugs(conn)

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


def sweep_resync_stale_controls(conn: sqlite3.Connection) -> dict[str, int]:
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

    counts = {
        "resync_stale": 0,
        "current": 0,
        "skipped_unparsable": 0,
        "skipped_pin_version": 0,
    }
    print("control_id | slug | pinned_version | current_version")
    for cid, pin_raw, status in conn.execute("SELECT id, library_pin, status FROM controls"):
        if status == "deleted":
            continue
        pin_state, pin = _pin_status(pin_raw)
        if pin_state == "malformed":
            counts["skipped_unparsable"] += 1
            continue
        if pin_state == "absent" or pin is None:
            continue
        entry_hex = _entry_hex_from_pin(pin)
        if entry_hex is None:
            continue
        slug = slug_by_entry_hex.get(entry_hex)
        if slug not in RECURATED_CONTROL_SLUGS:
            continue
        pinned_version = pin.get("version")
        cur = current_version.get(slug)
        if cur is None or not isinstance(pinned_version, int):
            counts["skipped_pin_version"] += 1
            continue
        try:
            cid_hex = _hex(cid)
        except ValueError:
            counts["skipped_unparsable"] += 1
            continue
        if pinned_version < cur:
            counts["resync_stale"] += 1
            print(f"{cid_hex} | {slug} | {pinned_version} | {cur}")
        else:
            counts["current"] += 1
    print("adopted_controls: " + " ".join(f"{k}={v}" for k, v in counts.items()))
    return counts


# ---------------------------------------------------------------------------
# Section 6: M-15 double-counted orgs (N9-1).
# ---------------------------------------------------------------------------


def sweep_double_counted_orgs(conn: sqlite3.Connection) -> int:
    """Distinct orgs with an active scenario pinned to BOTH slugs in
    DOUBLE_COUNT_PAIR -- an overlapping loss event double-counted in an
    AGGREGATE run (spec §4.6 M-15). Predates Epic F; informational, never
    gated."""
    slug_by_entry_hex = _scenario_library_slugs(conn)
    orgs_by_slug: dict[str, set[str]] = {slug: set() for slug in DOUBLE_COUNT_PAIR}
    for pin_raw, status, org_raw in conn.execute(
        "SELECT library_pin, status, organization_id FROM scenarios"
    ):
        if status == "deleted":
            continue
        entry_hex = _resolved_entry_hex(pin_raw)
        if entry_hex is None:
            continue
        slug = slug_by_entry_hex.get(entry_hex)
        if slug not in DOUBLE_COUNT_PAIR:
            continue
        with contextlib.suppress(ValueError):
            orgs_by_slug[slug].add(_hex(org_raw))
    double_counted = len(orgs_by_slug[DOUBLE_COUNT_PAIR[0]] & orgs_by_slug[DOUBLE_COUNT_PAIR[1]])
    print(f"double_counted_orgs={double_counted}")
    return double_counted


# ---------------------------------------------------------------------------
# Orchestration.
# ---------------------------------------------------------------------------


def sweep(db_path: Path) -> dict[str, int]:
    conn = _connect_ro(db_path)
    try:
        aie = sweep_accidental_insider_exposure(conn)
        aie_override_one_sided = sweep_aie_override_one_sided(conn)
        _by_slug, deprecated_orgs = sweep_deprecated_entries(conn)
        people = sweep_people_asset_class(conn)
        deprecated_overrides = sweep_deprecated_overrides(conn)
        controls = sweep_resync_stale_controls(conn)
        double_counted_orgs = sweep_double_counted_orgs(conn)
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
        "aie_pinned_stale_side": aie["pinned_stale_side"],
        "aie_copy_current_stale_rows": aie["copy_current_stale_rows"],
        "aie_skipped_unparsable": aie["skipped_unparsable"],
        "aie_skipped_pin_version": aie["skipped_pin_version"],
        "aie_override_one_sided": aie_override_one_sided,
        "deprecated_orgs": deprecated_orgs,
        "people_asset_class_scenarios": people,
        "deprecated_overrides": deprecated_overrides,
        "control_resync_stale": controls["resync_stale"],
        "control_current": controls["current"],
        "control_skipped_unparsable": controls["skipped_unparsable"],
        "control_skipped_pin_version": controls["skipped_pin_version"],
        "double_counted_orgs": double_counted_orgs,
    }
    print("SUMMARY " + " ".join(f"{k}={v}" for k, v in summary.items()))
    return summary


GATE_KEYS = (
    "aie_pristine",
    "aie_copy_stale",
    "aie_skipped_unparsable",
    "aie_skipped_pin_version",
    "aie_pinned_stale_side",
    "aie_copy_current_stale_rows",
    "aie_override_one_sided",
)


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
    try:
        summary = sweep(Path(rest[1]))
    except Exception as exc:
        # Sec2-2/Sec3-2: only the exception TYPE, never its message (which could
        # embed a row value) -- mirrors check_epic_f_migration.py's exit-4 convention,
        # exit 3 here so it is never confused with "--gate: not clean" (exit 1).
        print(f"internal error: {type(exc).__name__}", file=sys.stderr)
        return 3
    if gate and sum(summary[k] for k in GATE_KEYS) > 0:
        print("GATE: not clean -- " + " ".join(f"{k}={summary[k]}" for k in GATE_KEYS))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
