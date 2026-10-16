"""scripts/sweep_issue_199_prod_rows.py — read-only pre-deploy sweep on a hand-built SQLite
fixture (mirrors tests/unit/test_sweep_epic_f_adopted_rows.py). Pre-bump AND post-bump fixtures
(plan-gate S-B2): before the migration every pinned control is still at the entry's version.
Every value is synthetic."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
import sys
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "sweep_issue_199_prod_rows.py"
_spec = importlib.util.spec_from_file_location("sweep_issue_199_prod_rows", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
# The script declares a @dataclass under `from __future__ import annotations`; dataclasses
# resolves string annotations through sys.modules[cls.__module__], so register first (A4-I3).
sys.modules[_spec.name] = mod
_spec.loader.exec_module(mod)

NAME = "ZZ-CONTROL-NAME-SENTINEL"
TS = "vmc_corr_treatment_selection"
IMPL = "vmc_corr_implementation"
VIS = "lec_det_visibility"
MON = "lec_det_monitoring"
ORG = uuid.uuid4().hex


def _empty_db(path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(path)
    c.executescript(
        """
        CREATE TABLE control_library_entries (id CHAR(32), slug TEXT, version INTEGER);
        CREATE TABLE control_library_entry_assignments (library_entry_id CHAR(32),
            library_entry_version INTEGER, sub_function TEXT, capability_default REAL,
            coverage_default REAL, reliability_default REAL);
        CREATE TABLE controls (id CHAR(32) PRIMARY KEY, name TEXT, status TEXT, library_pin TEXT,
            adopted_snapshot TEXT, organization_id CHAR(32));
        CREATE TABLE control_function_assignments (id CHAR(32) PRIMARY KEY, control_id CHAR(32),
            organization_id CHAR(32), sub_function TEXT, coverage REAL, confirmed_by_user_at TEXT);
        CREATE TABLE risk_analysis_runs (id CHAR(32) PRIMARY KEY, status TEXT, is_stale INTEGER,
            control_ids_used TEXT, controls_snapshot TEXT, organization_id CHAR(32));
        """
    )
    return c


def _entry_version(
    c: sqlite3.Connection, eid: str, version: int, vis_cov: float, mon_cov: float = 0.8
) -> None:
    c.execute("INSERT INTO control_library_entries VALUES (?, ?, ?)", (eid, mod.SIEM_SLUG, version))
    for sf, cap, cov in ((MON, None, mon_cov), (VIS, 0.7, vis_cov)):
        c.execute(
            "INSERT INTO control_library_entry_assignments VALUES (?, ?, ?, ?, ?, ?)",
            (eid, version, sf, cap, cov, 0.8),
        )


def _control(
    c: sqlite3.Connection,
    *,
    pin: object = None,
    snapshot: object = None,
    status: str = "active",
    assigns: Sequence[tuple[str, float, str | None]] = (),
    org: str = ORG,
) -> str:
    cid = uuid.uuid4().hex
    c.execute(
        "INSERT INTO controls VALUES (?, ?, ?, ?, ?, ?)",
        (
            cid,
            NAME,
            status,
            json.dumps(pin) if pin is not None else None,
            json.dumps(snapshot) if snapshot is not None else None,
            org,
        ),
    )
    for sf, cov, confirmed in assigns:
        c.execute(
            "INSERT INTO control_function_assignments VALUES (?, ?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, cid, org, sf, cov, confirmed),
        )
    return cid


def _run(
    c: sqlite3.Connection,
    controls: list[str],
    status: str = "completed",
    stale: int = 0,
    snapshot: object = None,
    org: str = ORG,
) -> str:
    rid = uuid.uuid4().hex
    c.execute(
        "INSERT INTO risk_analysis_runs VALUES (?, ?, ?, ?, ?, ?)",
        (
            rid,
            status,
            stale,
            json.dumps([str(uuid.UUID(hex=h)) for h in controls]),
            json.dumps(snapshot) if snapshot is not None else json.dumps([]),
            org,
        ),
    )
    return rid


def _snap(vis_cov: float, mon_cov: float = 0.8) -> dict[str, Any]:
    return {
        "version": 1,
        "assignments": [
            {"sub_function": MON, "capability": None, "coverage": mon_cov, "reliability": 0.8},
            {"sub_function": VIS, "capability": 0.7, "coverage": vis_cov, "reliability": 0.8},
        ],
    }


def _fixture(path: Path, *, post_bump: bool) -> dict[str, str]:
    """Pre-bump: entry v1 at 0.8, pins at 1. Post-bump: + entry v2 at 0.3, one control pinned at 2."""
    c = _empty_db(path)
    ids: dict[str, str] = {}
    eid = uuid.uuid4().hex
    _entry_version(c, eid, 1, 0.8)
    if post_bump:
        _entry_version(c, eid, 2, 0.3)
    # (a) C8
    ids["ts"] = _control(c, assigns=[(TS, 0.8, None), (IMPL, 0.8, None)])
    ids["impl_only"] = _control(c, assigns=[(IMPL, 0.8, None)])
    v3_ts = [
        {
            "control_id": str(uuid.UUID(hex=ids["ts"])),
            "assignments": [{"sub_function": TS}, {"sub_function": IMPL}],
        }
    ]
    ids["run_ts"] = _run(c, [ids["ts"]], snapshot=v3_ts)
    _run(c, [ids["ts"]], stale=1, snapshot=v3_ts)  # already stale: not counted
    _run(c, [ids["ts"]], status="failed", snapshot=v3_ts)  # wrong status: not counted
    _run(c, [ids["impl_only"]])  # no TS carrier, empty snapshot: not counted
    ids["run_v1"] = _run(c, [ids["ts"]])  # empty snapshot -> fallback on current carriers
    # (b) SIEM
    pin1 = {"entry_id": str(uuid.UUID(hex=eid)), "version": 1}
    base = [(VIS, 0.8, None), (MON, 0.8, None)]
    ids["A"] = _control(c, pin=pin1, snapshot=_snap(0.8), assigns=base)
    ids["B"] = _control(
        c, pin=pin1, snapshot=_snap(0.8), assigns=[(VIS, 0.8, "2026-01-01"), base[1]]
    )
    ids["C"] = _control(c, pin=pin1, snapshot=_snap(0.8), assigns=[(VIS, 0.5, None), base[1]])
    ids["D"] = _control(c, pin=pin1, snapshot=None, assigns=base)
    ids["X"] = _control(
        c, pin=pin1, snapshot=_snap(0.8, mon_cov=0.7), assigns=base
    )  # other cell differs
    if post_bump:
        ids["cur"] = _control(
            c,
            pin={"entry_id": eid, "version": 2},
            snapshot=_snap(0.3),
            assigns=[(VIS, 0.3, None), base[1]],
        )
    _control(c, pin={"entry_id": "garbage"}, snapshot=None)  # unparsable pin -> skipped
    _control(c, pin=pin1, snapshot=_snap(0.8), assigns=base, status="deleted")  # ignored
    ids["run_A"] = _run(c, [ids["A"]])
    ids["run_A_and_ts"] = _run(
        c, [ids["A"], ids["ts"]], snapshot=v3_ts
    )  # flagged by C8 too -> net excludes it
    _run(c, [ids["B"]])
    c.commit()
    c.close()
    return ids


def _expected(post_bump: bool) -> dict[str, int]:
    return {
        "ts_assignment_rows": 1,
        "ts_controls": 1,
        "ts_runs_completed_unstale": 3,  # run_ts, run_v1, run_A_and_ts
        "siem_entry_present": 1,
        "siem_A": 1,
        "siem_B": 1,
        "siem_C": 1,
        "siem_D": 1,
        "siem_X": 1,
        "siem_current": 1 if post_bump else 0,
        "siem_A_runs_completed": 2,
        "siem_A_runs_net_of_c8": 1,
        "skipped_unparsable": 1,
    }


@pytest.mark.parametrize("post_bump", [False, True], ids=["pre-bump", "post-bump"])
def test_sweep_counts_and_ids_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], post_bump: bool
) -> None:
    db = tmp_path / "s.db"
    ids = _fixture(db, post_bump=post_bump)
    result = mod.sweep(db)
    out = capsys.readouterr().out
    assert result.entry_state == ("post-bump" if post_bump else "pre-bump")
    assert result.counts == _expected(post_bump)
    assert ids["ts"] in out and f"run {ids['run_ts']}" in out and f"run {ids['run_v1']}" in out
    assert f"run {ids['run_A']}" in out
    for key in ("A", "B", "C", "D", "X"):
        assert f"{ids[key]} {key}" in out
    if post_bump:
        assert f"{ids['cur']} current" in out
    assert "SUMMARY siem_entry_state=" in out
    assert NAME not in out and "0.8" not in out and "0.5" not in out and "0.7" not in out


def test_drift_state_classifies_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "d.db"
    c = _empty_db(db)
    eid = uuid.uuid4().hex
    _entry_version(c, eid, 1, 0.55)
    _control(
        c,
        pin={"entry_id": eid, "version": 1},
        snapshot=_snap(0.55),
        assigns=[(VIS, 0.55, None), (MON, 0.8, None)],
    )
    c.commit()
    c.close()
    result = mod.sweep(db)
    assert result.entry_state == "drift"
    assert result.counts["siem_entry_present"] == 1
    assert all(result.counts[f"siem_{k}"] == 0 for k in ("A", "B", "C", "D", "X", "current"))
    assert "0.55" not in capsys.readouterr().out


def test_connect_ro_refuses_writes(tmp_path: Path) -> None:
    db = tmp_path / "ro.db"
    _empty_db(db).close()
    conn = mod._connect_ro(db)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO controls (id) VALUES ('x')")
    finally:
        conn.close()


def test_sha256_unchanged_after_a_full_run(tmp_path: Path) -> None:
    db = tmp_path / "h.db"
    _fixture(db, post_bump=True)
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    assert mod.main(["--db", str(db)]) == 0
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before


def test_missing_siem_entry_reports_absent_without_crashing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "m.db"
    c = _empty_db(db)
    c.commit()
    c.close()
    result = mod.sweep(db)
    assert result.entry_state == "absent"
    assert result.counts["siem_entry_present"] == 0 and result.counts["siem_A"] == 0
    assert "SUMMARY siem_entry_state=absent" in capsys.readouterr().out


def test_internal_error_prints_type_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "bad.db"
    db.write_bytes(b"not a database")
    assert mod.main(["--db", str(db)]) == 3
    err = capsys.readouterr().err
    assert "internal error:" in err and "DatabaseError" in err
    assert "not a database" not in err  # never the message


def test_usage_error(tmp_path: Path) -> None:
    assert mod.main([]) == 2
    assert mod.main(["--db", str(tmp_path / "absent.db")]) == 2
