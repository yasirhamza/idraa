# tests/unit/test_sweep_epic_f_adopted_rows.py
"""scripts/sweep_epic_f_adopted_rows.py -- classification + end-to-end on a hand-built
synthetic SQLite fixture (mirrors tests/unit/test_sweep_library_secondary_response.py).
NO population count, scenario/control/org name, or description from any real
deployment appears anywhere in this file -- every fixture value here is synthetic,
chosen only to exercise the sweep's branches."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import uuid
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "sweep_epic_f_adopted_rows.py"
_spec = importlib.util.spec_from_file_location("sweep_epic_f_adopted_rows", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

AIE_SLUG = "accidental-insider-exposure"
OLD_PL, NEW_PL = mod.EPIC_F_OLD_NODES["pl"], mod.EPIC_F_NEW_NODES["pl"]
OLD_SL, NEW_SL = mod.EPIC_F_OLD_NODES["sl"], mod.EPIC_F_NEW_NODES["sl"]
OTHER_NODE = {"distribution": "PERT", "low": 5.0, "mode": 5.0, "high": 6.0}


def _empty_db(path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(path)
    c.executescript(
        """
        CREATE TABLE scenario_library_entries (id CHAR(32) PRIMARY KEY, slug TEXT, version INTEGER);
        CREATE TABLE scenarios (id CHAR(32) PRIMARY KEY, name TEXT, library_pin TEXT, status TEXT,
            primary_loss TEXT, secondary_loss TEXT, asset_class TEXT, organization_id CHAR(32));
        CREATE TABLE scenario_sme_estimates (id CHAR(32) PRIMARY KEY, scenario_id CHAR(32), fieldset TEXT,
            sme_id CHAR(32), sme_name TEXT, low REAL, high REAL, recorded_at TEXT);
        CREATE TABLE scenario_library_overrides (id CHAR(32) PRIMARY KEY, library_entry_id CHAR(32),
            deleted_at TEXT);
        CREATE TABLE control_library_entries (id CHAR(32), slug TEXT, version INTEGER);
        CREATE TABLE controls (id CHAR(32) PRIMARY KEY, name TEXT, library_pin TEXT, status TEXT);
        """
    )
    return c


def _entry(c: sqlite3.Connection, slug: str) -> str:
    eid = uuid.uuid4().hex
    c.execute("INSERT INTO scenario_library_entries VALUES (?, ?, 1)", (eid, slug))
    return eid


def _scenario(
    c: sqlite3.Connection,
    entry_hex: str | None,
    *,
    pl: dict | None = None,
    sl: dict | None = None,
    status: str = "active",
    asset_class: str = "data",
    org: str | None = None,
    version: int = 1,
    entry_id_spelling: str | None = None,
) -> str:
    sid = uuid.uuid4().hex
    if entry_hex is None:
        pin = "null"
    else:
        spelling = entry_id_spelling or entry_hex
        pin = json.dumps({"entry_id": spelling, "version": version})
    c.execute(
        "INSERT INTO scenarios VALUES (?, 'ZZ-SCENARIO-NAME-SENTINEL', ?, ?, ?, ?, ?, ?)",
        (
            sid,
            pin,
            status,
            json.dumps(pl if pl is not None else OTHER_NODE),
            json.dumps(sl if sl is not None else OTHER_NODE),
            asset_class,
            org or uuid.uuid4().hex,
        ),
    )
    return sid


def _sme(
    c: sqlite3.Connection,
    sid: str,
    fs: str,
    low: float,
    high: float,
    when: str = "2026-01-01",
) -> None:
    c.execute(
        "INSERT INTO scenario_sme_estimates VALUES (?, ?, ?, ?, 'ZZ-SME-SENTINEL', ?, ?, ?)",
        (uuid.uuid4().hex, sid, fs, uuid.uuid4().hex, low, high, when),
    )


# ---------------------------------------------------------------------------
# (a) clean DB
# ---------------------------------------------------------------------------


def test_clean_db_gate_exit_zero_all_counters_zero(tmp_path: Path) -> None:
    db = tmp_path / "clean.db"
    _empty_db(db).close()
    assert mod.main(["--db", str(db), "--gate"]) == 0
    conn = mod._connect_ro(db)
    try:
        summary = {
            "aie": mod.sweep_accidental_insider_exposure(conn),
            "deprecated": mod.sweep_deprecated_entries(conn),
            "people": mod.sweep_people_asset_class(conn),
            "overrides": mod.sweep_deprecated_overrides(conn),
            "controls": mod.sweep_resync_stale_controls(conn),
        }
    finally:
        conn.close()
    assert all(v == 0 for v in summary["aie"].values())
    assert summary["deprecated"] == ({s: [] for s in sorted(mod.DEPRECATED_SCENARIO_SLUGS)}, 0)
    assert summary["people"] == 0
    assert summary["overrides"] == 0
    assert summary["controls"] == (0, 0)


# ---------------------------------------------------------------------------
# (b) accidental-insider-exposure classification + gate
# ---------------------------------------------------------------------------


def test_copy_stale_node_classifies_copy_stale_and_gates(tmp_path: Path) -> None:
    db = tmp_path / "copy_stale.db"
    c = _empty_db(db)
    eid = _entry(c, AIE_SLUG)
    _scenario(c, eid, pl=dict(OLD_PL), sl=dict(OTHER_NODE))
    c.commit()
    c.close()
    conn = mod._connect_ro(db)
    counts = mod.sweep_accidental_insider_exposure(conn)
    conn.close()
    assert counts["copy_stale"] == 1
    assert counts["pristine"] == 0
    assert mod.main(["--db", str(db), "--gate"]) == 1


def test_sole_sme_pair_matches_old_pairs_classifies_pristine_and_gates(tmp_path: Path) -> None:
    db = tmp_path / "pristine.db"
    c = _empty_db(db)
    eid = _entry(c, AIE_SLUG)
    sid = _scenario(c, eid, pl=dict(OTHER_NODE), sl=dict(OTHER_NODE))
    old_pl = mod.EPIC_F_OLD_PAIRS["pl"]
    _sme(c, sid, "pl", round(old_pl[0], 2), round(old_pl[1], 2))
    c.commit()
    c.close()
    conn = mod._connect_ro(db)
    counts = mod.sweep_accidental_insider_exposure(conn)
    conn.close()
    assert counts["pristine"] == 1
    assert counts["copy_stale"] == 0
    assert mod.main(["--db", str(db), "--gate"]) == 1


def test_new_node_classifies_copy_current_and_does_not_gate(tmp_path: Path) -> None:
    db = tmp_path / "copy_current.db"
    c = _empty_db(db)
    eid = _entry(c, AIE_SLUG)
    _scenario(c, eid, pl=dict(NEW_PL), sl=dict(OTHER_NODE))
    c.commit()
    c.close()
    conn = mod._connect_ro(db)
    counts = mod.sweep_accidental_insider_exposure(conn)
    conn.close()
    assert counts["copy_current"] == 1
    assert counts["pristine"] == 0 and counts["copy_stale"] == 0
    assert mod.main(["--db", str(db), "--gate"]) == 0


# ---------------------------------------------------------------------------
# (c) scenarios pinned to each deprecated entry, ids only
# ---------------------------------------------------------------------------


def test_deprecated_entry_scenarios_listed_by_slug(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "deprecated.db"
    c = _empty_db(db)
    slugs = sorted(mod.DEPRECATED_SCENARIO_SLUGS)
    entry_ids = {slug: _entry(c, slug) for slug in slugs}
    sids = {slug: _scenario(c, entry_ids[slug]) for slug in slugs}
    # A soft-deleted pin must not be listed.
    deleted_eid = entry_ids[slugs[0]]
    _scenario(c, deleted_eid, status="deleted")
    c.commit()
    c.close()
    conn = mod._connect_ro(db)
    by_slug, _orgs = mod.sweep_deprecated_entries(conn)
    conn.close()
    for slug in slugs:
        assert by_slug[slug] == [sids[slug]]
    out = capsys.readouterr().out
    for slug, sid in sids.items():
        assert f"{sid} | {slug}" in out
    assert "ZZ-SCENARIO-NAME-SENTINEL" not in out


# ---------------------------------------------------------------------------
# Addendum M5-1: distinct org count across the three deprecated entries
# ---------------------------------------------------------------------------


def test_m5_1_distinct_org_count_across_deprecated_entries(tmp_path: Path) -> None:
    db = tmp_path / "orgs.db"
    c = _empty_db(db)
    slugs = sorted(mod.DEPRECATED_SCENARIO_SLUGS)
    org_a, org_b = uuid.uuid4().hex, uuid.uuid4().hex
    e0 = _entry(c, slugs[0])
    e1 = _entry(c, slugs[1])
    e2 = _entry(c, slugs[2])
    # org_a has scenarios on two DIFFERENT deprecated entries -> counted once.
    _scenario(c, e0, org=org_a)
    _scenario(c, e1, org=org_a)
    # org_b has one scenario on the third deprecated entry.
    _scenario(c, e2, org=org_b)
    c.commit()
    c.close()
    conn = mod._connect_ro(db)
    _by_slug, org_count = mod.sweep_deprecated_entries(conn)
    conn.close()
    assert org_count == 2


# ---------------------------------------------------------------------------
# (d) asset_class = 'people' scenarios counted
# ---------------------------------------------------------------------------


def test_people_asset_class_scenario_counted_only_on_relabeled_slug(tmp_path: Path) -> None:
    db = tmp_path / "people.db"
    c = _empty_db(db)
    people_slug = sorted(mod.PEOPLE_RELABELED_SLUGS)[0]
    e_people = _entry(c, people_slug)
    e_other = _entry(c, "web-app-exploitation")  # not a people-relabeled slug
    _scenario(c, e_people, asset_class="people")  # counted
    _scenario(c, e_people, asset_class="data")  # not counted: not asset_class=people
    _scenario(c, e_other, asset_class="people")  # not counted: not a relabeled slug
    c.commit()
    c.close()
    conn = mod._connect_ro(db)
    count = mod.sweep_people_asset_class(conn)
    conn.close()
    assert count == 1


# ---------------------------------------------------------------------------
# (e) org override on a deprecated entry counted
# ---------------------------------------------------------------------------


def test_override_on_deprecated_entry_counted(tmp_path: Path) -> None:
    db = tmp_path / "overrides.db"
    c = _empty_db(db)
    slugs = sorted(mod.DEPRECATED_SCENARIO_SLUGS)
    e_dep = _entry(c, slugs[0])
    e_other = _entry(c, "web-app-exploitation")
    c.execute(
        "INSERT INTO scenario_library_overrides VALUES (?, ?, NULL)",
        (uuid.uuid4().hex, e_dep),
    )
    # soft-deleted override on a deprecated entry: not counted
    c.execute(
        "INSERT INTO scenario_library_overrides VALUES (?, ?, '2026-01-01')",
        (uuid.uuid4().hex, e_dep),
    )
    # override on a non-deprecated entry: not counted
    c.execute(
        "INSERT INTO scenario_library_overrides VALUES (?, ?, NULL)",
        (uuid.uuid4().hex, e_other),
    )
    c.commit()
    c.close()
    conn = mod._connect_ro(db)
    count = mod.sweep_deprecated_overrides(conn)
    conn.close()
    assert count == 1


# ---------------------------------------------------------------------------
# (f)/(g) adopted controls resync-stale, and hyphenated/uppercase pin normalisation
# ---------------------------------------------------------------------------


def test_control_pinned_behind_current_version_is_resync_stale(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "controls.db"
    c = _empty_db(db)
    slug = mod.RECURATED_CONTROL_SLUGS[0]  # e.g. security-information-event-management
    eid = uuid.uuid4().hex
    c.execute("INSERT INTO control_library_entries VALUES (?, ?, 1)", (eid, slug))
    c.execute("INSERT INTO control_library_entries VALUES (?, ?, 2)", (eid, slug))  # current
    # services/controls.py:596 shape: str(entry.id) -> hyphenated.
    stale_pin = json.dumps({"entry_id": str(uuid.UUID(eid)), "version": 1})
    current_pin = json.dumps({"entry_id": str(uuid.UUID(eid)), "version": 2})
    stale_cid = uuid.uuid4().hex
    current_cid = uuid.uuid4().hex
    c.execute(
        "INSERT INTO controls VALUES (?, 'ZZ-CONTROL-NAME-SENTINEL', ?, 'active')",
        (stale_cid, stale_pin),
    )
    c.execute(
        "INSERT INTO controls VALUES (?, 'ZZ-CONTROL-NAME-SENTINEL', ?, 'active')",
        (current_cid, current_pin),
    )
    c.commit()
    c.close()
    conn = mod._connect_ro(db)
    resync_stale, current = mod.sweep_resync_stale_controls(conn)
    conn.close()
    assert resync_stale == 1 and current == 1
    out = capsys.readouterr().out
    assert f"{stale_cid} | {slug} | 1 | 2" in out
    assert current_cid not in out  # a current pin is not listed, only counted
    assert "ZZ-CONTROL-NAME-SENTINEL" not in out


def test_pin_entry_id_hyphenated_or_uppercase_still_resolves(tmp_path: Path) -> None:
    """(g): library_pin.entry_id stored hyphenated or uppercase still matches the
    hex library id after uuid.UUID(x).hex normalisation."""
    eid = uuid.uuid4().hex
    assert mod._hex(str(uuid.UUID(eid))) == eid  # hyphenated
    assert mod._hex(eid.upper()) == eid  # uppercase hex

    db = tmp_path / "spellings.db"
    c = _empty_db(db)
    aie_eid = _entry(c, AIE_SLUG)
    hyph_sid = _scenario(c, aie_eid, pl=dict(OLD_PL), entry_id_spelling=str(uuid.UUID(aie_eid)))
    upper_sid = _scenario(c, aie_eid, pl=dict(OLD_PL), entry_id_spelling=aie_eid.upper())
    c.commit()
    c.close()
    conn = mod._connect_ro(db)
    counts = mod.sweep_accidental_insider_exposure(conn)
    conn.close()
    assert counts["copy_stale"] == 2  # both spellings resolved to the same entry
    assert hyph_sid and upper_sid  # both scenarios were built (no silent skip)


# ---------------------------------------------------------------------------
# (h) read-only: a write attempt raises sqlite3.OperationalError, never writes
# ---------------------------------------------------------------------------


def test_connect_ro_refuses_writes(tmp_path: Path) -> None:
    db = tmp_path / "ro.db"
    _empty_db(db).close()
    conn = mod._connect_ro(db)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO scenarios (id) VALUES ('x')")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# (i) output contract: no names/descriptions, only ids/slugs/classes/counts
# ---------------------------------------------------------------------------


def test_output_contract_no_names_anywhere(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "sentinels.db"
    c = _empty_db(db)
    aie_eid = _entry(c, AIE_SLUG)
    dep_slug = sorted(mod.DEPRECATED_SCENARIO_SLUGS)[0]
    dep_eid = _entry(c, dep_slug)
    sid = _scenario(c, aie_eid, pl=dict(OTHER_NODE), sl=dict(OTHER_NODE))
    old_sl = mod.EPIC_F_OLD_PAIRS["sl"]
    _sme(c, sid, "sl", round(old_sl[0], 2), round(old_sl[1], 2))
    _scenario(c, dep_eid, org=uuid.uuid4().hex)
    slug = mod.RECURATED_CONTROL_SLUGS[0]
    ceid = uuid.uuid4().hex
    c.execute("INSERT INTO control_library_entries VALUES (?, ?, 1)", (ceid, slug))
    c.execute("INSERT INTO control_library_entries VALUES (?, ?, 2)", (ceid, slug))
    c.execute(
        "INSERT INTO controls VALUES (?, 'ZZ-CONTROL-NAME-SENTINEL', ?, 'active')",
        (
            uuid.uuid4().hex,
            json.dumps({"entry_id": str(uuid.UUID(ceid)), "version": 1}),
        ),
    )
    c.commit()
    c.close()
    assert mod.main(["--db", str(db)]) == 0
    out, err = capsys.readouterr()
    for sentinel in ("ZZ-SCENARIO-NAME-SENTINEL", "ZZ-CONTROL-NAME-SENTINEL", "ZZ-SME-SENTINEL"):
        assert sentinel not in out
        assert sentinel not in err


# ---------------------------------------------------------------------------
# CLI plumbing
# ---------------------------------------------------------------------------


def test_main_missing_db_flag_or_file_returns_two(tmp_path: Path) -> None:
    assert mod.main([]) == 2
    assert mod.main(["--db", str(tmp_path / "missing.db")]) == 2


def test_deprecated_slugs_and_people_slugs_are_derived_not_empty() -> None:
    assert {
        "chemical-process-safety-attack",
        "data-breach-notification-regulatory-tail",
        "ddos-extortion-financial",
    } == mod.DEPRECATED_SCENARIO_SLUGS
    assert len(mod.PEOPLE_RELABELED_SLUGS) == 6
    assert len(mod.RECURATED_CONTROL_SLUGS) == 5
