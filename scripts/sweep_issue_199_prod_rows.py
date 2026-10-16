# scripts/sweep_issue_199_prod_rows.py
"""READ-ONLY pre-deploy diagnostic for #199 slice 1 (spec §7.1, R-DEP-1, R-DEP-1a).

Opens the SQLite file with ``mode=ro`` (a write raises ``sqlite3.OperationalError``) and
prints counts and canonical 32-hex ids ONLY — never a control/org/scenario name, a
description or a stored value (the sweep_epic_f_adopted_rows.py disclosure rule).

Sections:
  (a) C8: the COMPLETED, not-yet-stale runs migration c4d2e8f1a6b9 will flag, decided by
      THAT migration's own ``run_is_affected`` (loaded by path, never re-implemented):
      the run's controls_snapshot shows a vmc_corr_treatment_selection carrier, or — with
      no snapshot detail — control_ids_used names a current carrier of the run's org.
      Also the current carrier rows and their distinct controls.
  (b) SIEM: deployed controls pinned to the SIEM entry, partitioned with the SAME pure
      classifier the operator CLI uses (imported from
      idraa.services.siem_visibility_resync), plus the COMPLETED runs that used any
      class-A control and that count NET of the C8 set (what the post-deploy CLI's
      ``runs_flagged`` is compared with, since c4d2e8f1a6b9 flags the overlap first).
      DECLARED UPPER BOUND (plan-gate Sec2-I2): the CLI additionally demotes A/B to X when an
      entry-level field (name, description, annual_cost, NIST/ISO/CIS tags, industries,
      org sizes) differs between the snapshot and the latest entry — the consent page's
      ``_entry_field_values`` oracle. This sweep reads raw sqlite3 rows and would have to
      re-implement the ORM-shaped field set (Decimal / JSON normalisation) to mirror it, so
      it does NOT: ``siem_A`` here is an upper bound on the CLI's ``A`` (the CLI's ``X`` may
      exceed ``siem_X`` by the field-delta demotions). The authoritative pre-apply count is
      the CLI's own dry run on the machine (runbook A.3 step 2).

Bump state (plan-gate S-B2): the sweep runs BEFORE the migration, when every pinned
control is still at the entry's version. It reads the stored entry's latest Visibility
coverage: OLD_COVERAGE -> ``pre-bump`` (classify pin == latest against the entry AS THE
BUMP WILL LEAVE IT, i.e. the Visibility cell moved to NEW_COVERAGE; ``current`` is
empty); NEW_COVERAGE -> ``post-bump`` (exactly the CLI's dry run); anything else ->
``drift`` (classify nothing); no entry -> ``absent``.

Run against a backup copy opened read-only (the copy must still carry its -shm sidecar
for ``mode=ro`` on a WAL file; otherwise take a working copy first)::

    uv run --frozen python scripts/sweep_issue_199_prod_rows.py --db <work copy>.db
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import types
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from idraa.services.siem_visibility_resync import (
    NEW_COVERAGE,
    OLD_COVERAGE,
    SIEM_SLUG,
    SUB_FUNCTION,
    Triple,
    classify_siem_control,
    close_enough,
    pin_matches_entry,
    pin_version,
    version_class,
)

_MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "c4d2e8f1a6b9_issue_199_c8_stale_runs.py"
)
TS_SUB_FUNCTION = "vmc_corr_treatment_selection"
COUNT_KEYS: tuple[str, ...] = (
    "ts_assignment_rows",
    "ts_controls",
    "ts_runs_completed_unstale",
    "siem_entry_present",
    "siem_A",
    "siem_B",
    "siem_C",
    "siem_D",
    "siem_X",
    "siem_current",
    "siem_A_runs_completed",
    "siem_A_runs_net_of_c8",
    "skipped_unparsable",
)


def _load_module(path: Path, name: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load the stale-runs migration module")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


_c8 = _load_module(_MIGRATION, "_issue_199_c8_for_sweep")
run_is_affected = _c8.run_is_affected


@dataclass
class SweepResult:
    counts: dict[str, int] = field(default_factory=lambda: dict.fromkeys(COUNT_KEYS, 0))
    entry_state: str = "absent"


def _connect_ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)


def _hex(raw: object) -> str | None:
    try:
        return uuid.UUID(str(raw)).hex
    except (ValueError, TypeError, AttributeError):
        return None


def _json(raw: object) -> Any:
    if raw is None:
        return None
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except (ValueError, RecursionError):
        return None


def _completed_unstale(
    conn: sqlite3.Connection,
) -> list[tuple[str, str | None, object, object, set[str]]]:
    """(run_hex, org_hex, raw_ids, raw_snapshot, used_control_hexes) for every candidate run."""
    out: list[tuple[str, str | None, object, object, set[str]]] = []
    for rid, org_id, raw_ids, raw_snapshot in conn.execute(
        "SELECT id, organization_id, control_ids_used, controls_snapshot FROM risk_analysis_runs "
        "WHERE status = 'completed' AND is_stale = 0"
    ):
        h = _hex(rid)
        if h is None:
            continue
        ids = _json(raw_ids)
        used = (
            {u for u in (_hex(x) for x in ids) if u is not None} if isinstance(ids, list) else set()
        )
        out.append((h, _hex(org_id), raw_ids, raw_snapshot, used))
    return out


def _entry_target(conn: sqlite3.Connection, entry_hex: str, version: int) -> dict[str, Triple]:
    rows = conn.execute(
        "SELECT sub_function, capability_default, coverage_default, reliability_default "
        "FROM control_library_entry_assignments "
        "WHERE library_entry_id = ? AND library_entry_version = ?",
        (entry_hex, version),
    ).fetchall()
    if not rows:  # ids may be stored hyphenated on old rows; retry with that spelling
        rows = conn.execute(
            "SELECT sub_function, capability_default, coverage_default, reliability_default "
            "FROM control_library_entry_assignments "
            "WHERE library_entry_id = ? AND library_entry_version = ?",
            (str(uuid.UUID(hex=entry_hex)), version),
        ).fetchall()
    return {
        str(sf): (
            None if cap is None else float(cap),
            None if cov is None else float(cov),
            None if rel is None else float(rel),
        )
        for sf, cap, cov, rel in rows
    }


def sweep(db_path: Path) -> SweepResult:
    conn = _connect_ro(db_path)
    result = SweepResult()
    counts = result.counts
    try:
        candidates = _completed_unstale(conn)

        # (a) C8 — the migration's own predicate decides.
        print("## C8 treatment-selection carriers")
        carriers: set[tuple[str, str]] = set()
        ts_controls: set[str] = set()
        for cid, org_id in conn.execute(
            "SELECT control_id, organization_id FROM control_function_assignments WHERE sub_function = ?",
            (TS_SUB_FUNCTION,),
        ):
            counts["ts_assignment_rows"] += 1
            c, o = _hex(cid), _hex(org_id)
            if c is not None:
                ts_controls.add(c)
            if c is not None and o is not None:
                carriers.add((o, c))
        counts["ts_controls"] = len(ts_controls)
        for h in sorted(ts_controls):
            print(h)
        c8_runs = {
            h
            for h, org, raw_ids, raw_snapshot, _ in candidates
            if run_is_affected(raw_snapshot, raw_ids, org, carriers)
        }
        counts["ts_runs_completed_unstale"] = len(c8_runs)
        for r in sorted(c8_runs):
            print(f"run {r}")

        # (b) SIEM
        print("## SIEM controls")
        entry = conn.execute(
            "SELECT id, version FROM control_library_entries WHERE slug = ? ORDER BY version DESC LIMIT 1",
            (SIEM_SLUG,),
        ).fetchone()
        if entry is None:
            print(
                "SUMMARY siem_entry_state=absent " + " ".join(f"{k}={v}" for k, v in counts.items())
            )
            return result
        entry_hex, entry_version = _hex(entry[0]), int(entry[1])
        counts["siem_entry_present"] = 1
        if entry_hex is None:
            result.entry_state = "drift"
            print(
                "SUMMARY siem_entry_state=drift " + " ".join(f"{k}={v}" for k, v in counts.items())
            )
            return result
        stored = _entry_target(conn, entry_hex, entry_version)
        stored_vis = stored.get(SUB_FUNCTION, (None, None, None))[1]
        if close_enough(stored_vis, OLD_COVERAGE):
            result.entry_state = "pre-bump"
            pre_bump = True
            target: dict[str, Triple] = {
                sf: ((cap, NEW_COVERAGE, rel) if sf == SUB_FUNCTION else (cap, cov, rel))
                for sf, (cap, cov, rel) in stored.items()
            }
        elif close_enough(stored_vis, NEW_COVERAGE):
            result.entry_state = "post-bump"
            pre_bump = False
            target = stored
        else:
            result.entry_state = "drift"
            print(
                "SUMMARY siem_entry_state=drift " + " ".join(f"{k}={v}" for k, v in counts.items())
            )
            return result

        class_a: set[str] = set()
        for cid, status, pin_raw, snap_raw in conn.execute(
            "SELECT id, status, library_pin, adopted_snapshot FROM controls"
        ):
            if status == "deleted":
                continue
            pin = _json(pin_raw)
            if pin is None:
                continue
            if not isinstance(pin, dict) or _hex(pin.get("entry_id")) is None:
                counts["skipped_unparsable"] += 1
                continue
            if not pin_matches_entry(pin, entry_hex):
                continue
            chex = _hex(cid)
            if chex is None:
                counts["skipped_unparsable"] += 1
                continue
            vc = version_class(pin_version(pin), entry_version, pre_bump=pre_bump)
            if vc == "current":
                counts["siem_current"] += 1
                print(f"{chex} current")
                continue
            snapshot = _json(snap_raw)
            rows = [
                (str(sf), float(cov), confirmed is not None)
                for sf, cov, confirmed in conn.execute(
                    "SELECT sub_function, coverage, confirmed_by_user_at "
                    "FROM control_function_assignments WHERE control_id = ?",
                    (cid,),
                )
            ]
            cls = (
                "X"
                if vc == "X"
                else classify_siem_control(
                    snapshot if isinstance(snapshot, dict) else None, rows, target=target
                )
            )
            counts[f"siem_{cls}"] += 1
            if cls == "A":
                class_a.add(chex)
            print(f"{chex} {cls}")
        a_runs = sorted(h for h, _, _, _, used in candidates if used & class_a)
        counts["siem_A_runs_completed"] = len(a_runs)
        counts["siem_A_runs_net_of_c8"] = len([r for r in a_runs if r not in c8_runs])
        for r in a_runs:
            print(f"run {r}")
        print(
            f"SUMMARY siem_entry_state={result.entry_state} "
            + " ".join(f"{k}={v}" for k, v in counts.items())
        )
        return result
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or args[0] != "--db" or not Path(args[1]).is_file():
        print(__doc__ if len(args) != 2 or args[0] != "--db" else "no such file", file=sys.stderr)
        return 2
    try:
        sweep(Path(args[1]))
    except Exception as exc:  # disclosure rule: TYPE only, never the message
        print(f"internal error: {type(exc).__name__}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
