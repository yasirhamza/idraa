# tests/unit/test_sweep_epic_f_adopted_rows.py
"""scripts/sweep_epic_f_adopted_rows.py -- classification + end-to-end on a hand-built
synthetic SQLite fixture (mirrors tests/unit/test_sweep_library_secondary_response.py).
NO population count, scenario/control/org name, or description from any real
deployment appears anywhere in this file -- every fixture value here is synthetic,
chosen only to exercise the sweep's branches."""

from __future__ import annotations

import hashlib
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

AIE_SLUG = mod.AIE_SLUG
OLD_PL, NEW_PL = mod.EPIC_F_OLD_NODES["pl"], mod.EPIC_F_NEW_NODES["pl"]
OLD_SL, NEW_SL = mod.EPIC_F_OLD_NODES["sl"], mod.EPIC_F_NEW_NODES["sl"]
OTHER_NODE = {"distribution": "PERT", "low": 5.0, "mode": 5.0, "high": 6.0}
PINNED_NODE = {
    "distribution": "PERT",
    "low": 1.0,
    "mode": 1.0,
    "high": 2.0,
    "distribution_fit_metadata": {"sigma_recalibration": {"source": "analyst_pin"}},
}


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
            primary_loss TEXT, secondary_loss TEXT, deleted_at TEXT);
        CREATE TABLE control_library_entries (id CHAR(32), slug TEXT, version INTEGER);
        CREATE TABLE controls (id CHAR(32) PRIMARY KEY, name TEXT, library_pin TEXT, status TEXT);
        CREATE TABLE organizations (id CHAR(32) PRIMARY KEY, name TEXT);
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
    version: int | None = 1,
    entry_id_spelling: str | None = None,
    override_id: str | None = None,
    pin_raw: str | None = None,
    pl_raw: str | None = None,
    sl_raw: str | None = None,
) -> str:
    sid = uuid.uuid4().hex
    if pin_raw is not None:
        pin = pin_raw
    elif entry_hex is None:
        pin = "null"
    else:
        spelling = entry_id_spelling or entry_hex
        payload: dict[str, object] = {"entry_id": spelling}
        if version is not None:
            payload["version"] = version
        if override_id is not None:
            payload["override_id"] = override_id
        pin = json.dumps(payload)
    c.execute(
        "INSERT INTO scenarios VALUES (?, 'ZZ-SCENARIO-NAME-SENTINEL', ?, ?, ?, ?, ?, ?)",
        (
            sid,
            pin,
            status,
            pl_raw if pl_raw is not None else json.dumps(pl if pl is not None else OTHER_NODE),
            sl_raw if sl_raw is not None else json.dumps(sl if sl is not None else OTHER_NODE),
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


def _control_entries(c: sqlite3.Connection, slug: str) -> str:
    eid = uuid.uuid4().hex
    c.execute("INSERT INTO control_library_entries VALUES (?, ?, 1)", (eid, slug))
    c.execute("INSERT INTO control_library_entries VALUES (?, ?, 2)", (eid, slug))
    return eid


def _control(
    c: sqlite3.Connection,
    *,
    pin_raw: str,
    status: str = "active",
) -> str:
    cid = uuid.uuid4().hex
    c.execute(
        "INSERT INTO controls VALUES (?, 'ZZ-CONTROL-NAME-SENTINEL', ?, ?)",
        (cid, pin_raw, status),
    )
    return cid


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
            "aie_override_one_sided": mod.sweep_aie_override_one_sided(conn),
            "deprecated": mod.sweep_deprecated_entries(conn),
            "people": mod.sweep_people_asset_class(conn),
            "overrides": mod.sweep_deprecated_overrides(conn),
            "controls": mod.sweep_resync_stale_controls(conn),
            "double_counted": mod.sweep_double_counted_orgs(conn),
        }
    finally:
        conn.close()
    assert all(v == 0 for v in summary["aie"].values())
    assert summary["aie_override_one_sided"] == 0
    assert summary["deprecated"] == ({s: [] for s in sorted(mod.DEPRECATED_SCENARIO_SLUGS)}, 0)
    assert summary["people"] == 0
    assert summary["overrides"] == 0
    assert all(v == 0 for v in summary["controls"].values())
    assert summary["double_counted"] == 0


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
    assert counts["copy_current_stale_rows"] == 0
    assert counts["pristine"] == 0 and counts["copy_stale"] == 0
    assert mod.main(["--db", str(db), "--gate"]) == 0


# ---------------------------------------------------------------------------
# M9-2: copy-current fieldset with a surviving pre-Epic-F SME row -> copy-current*
# ---------------------------------------------------------------------------


def test_copy_current_with_surviving_old_seed_row_flags_stale_and_gates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "copy_current_stale.db"
    c = _empty_db(db)
    eid = _entry(c, AIE_SLUG)
    old_pl = mod.EPIC_F_OLD_PAIRS["pl"]
    sid = _scenario(c, eid, pl=dict(NEW_PL), sl=dict(OTHER_NODE))
    _sme(c, sid, "pl", round(old_pl[0], 2), round(old_pl[1], 2))  # surviving OLD seed row
    c.commit()
    c.close()
    conn = mod._connect_ro(db)
    counts = mod.sweep_accidental_insider_exposure(conn)
    conn.close()
    assert counts["copy_current"] == 1  # top-level bucket unchanged
    assert counts["copy_current_stale_rows"] == 1  # M9-2 counter
    out = capsys.readouterr().out
    assert "copy-current*" in out
    assert mod.main(["--db", str(db), "--gate"]) == 1  # M9-2: this gates


# ---------------------------------------------------------------------------
# M9-3a: pinned scenario whose other side is stale -> pinned_stale_side
# ---------------------------------------------------------------------------


def test_pinned_scenario_with_copy_stale_other_side_counts_and_gates(tmp_path: Path) -> None:
    db = tmp_path / "pinned_stale_side.db"
    c = _empty_db(db)
    eid = _entry(c, AIE_SLUG)
    _scenario(c, eid, pl=dict(PINNED_NODE), sl=dict(OLD_SL))
    c.commit()
    c.close()
    conn = mod._connect_ro(db)
    counts = mod.sweep_accidental_insider_exposure(conn)
    conn.close()
    assert counts["pinned"] == 1
    assert counts["pinned_stale_side"] == 1
    assert mod.main(["--db", str(db), "--gate"]) == 1


def test_pinned_scenario_with_current_other_side_does_not_count_or_gate(tmp_path: Path) -> None:
    db = tmp_path / "pinned_clean_side.db"
    c = _empty_db(db)
    eid = _entry(c, AIE_SLUG)
    _scenario(c, eid, pl=dict(PINNED_NODE), sl=dict(NEW_SL))
    c.commit()
    c.close()
    conn = mod._connect_ro(db)
    counts = mod.sweep_accidental_insider_exposure(conn)
    conn.close()
    assert counts["pinned"] == 1
    assert counts["pinned_stale_side"] == 0
    assert mod.main(["--db", str(db), "--gate"]) == 0


# ---------------------------------------------------------------------------
# M9-3b: one-sided org overrides on accidental-insider-exposure
# ---------------------------------------------------------------------------


def test_aie_one_sided_override_counted_and_gates(tmp_path: Path) -> None:
    db = tmp_path / "aie_override.db"
    c = _empty_db(db)
    aie_eid = _entry(c, AIE_SLUG)
    other_eid = _entry(c, "web-app-exploitation")
    # PL-only override on AIE: counted.
    c.execute(
        "INSERT INTO scenario_library_overrides VALUES (?, ?, ?, ?, NULL)",
        (uuid.uuid4().hex, aie_eid, "{}", "null"),
    )
    # Two-sided override on AIE: not counted (both legs present).
    c.execute(
        "INSERT INTO scenario_library_overrides VALUES (?, ?, ?, ?, NULL)",
        (uuid.uuid4().hex, aie_eid, "{}", "{}"),
    )
    # Soft-deleted one-sided override on AIE: not counted.
    c.execute(
        "INSERT INTO scenario_library_overrides VALUES (?, ?, ?, ?, '2026-01-01')",
        (uuid.uuid4().hex, aie_eid, "{}", "null"),
    )
    # One-sided override on a DIFFERENT entry: not counted.
    c.execute(
        "INSERT INTO scenario_library_overrides VALUES (?, ?, ?, ?, NULL)",
        (uuid.uuid4().hex, other_eid, "{}", "null"),
    )
    c.commit()
    c.close()
    conn = mod._connect_ro(db)
    count = mod.sweep_aie_override_one_sided(conn)
    conn.close()
    assert count == 1
    assert mod.main(["--db", str(db), "--gate"]) == 1


def test_aie_override_marker_printed_on_scenario_row(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "aie_ovr_marker.db"
    c = _empty_db(db)
    eid = _entry(c, AIE_SLUG)
    sid = _scenario(c, eid, pl=dict(OLD_PL), override_id=uuid.uuid4().hex)
    c.commit()
    c.close()
    conn = mod._connect_ro(db)
    mod.sweep_accidental_insider_exposure(conn)
    conn.close()
    out = capsys.readouterr().out
    assert f"{sid} (ovr) |" in out


# ---------------------------------------------------------------------------
# M9-1: unparsable / pin-version-mismatch rows are counted, not silently dropped
# ---------------------------------------------------------------------------


def test_aie_corrupt_pin_counts_skipped_unparsable_and_gates(tmp_path: Path) -> None:
    db = tmp_path / "corrupt_pin.db"
    c = _empty_db(db)
    _entry(c, AIE_SLUG)
    _scenario(c, None, pin_raw="{not json")
    c.commit()
    c.close()
    conn = mod._connect_ro(db)
    counts = mod.sweep_accidental_insider_exposure(conn)
    conn.close()
    assert counts["skipped_unparsable"] == 1
    assert mod.main(["--db", str(db), "--gate"]) == 1


def test_aie_deleted_scenario_with_corrupt_pin_never_counted_or_gated(tmp_path: Path) -> None:
    """Soft-deleted rows never count, even when their pin is also corrupt."""
    db = tmp_path / "corrupt_pin_deleted.db"
    c = _empty_db(db)
    _scenario(c, None, pin_raw="{not json", status="deleted")
    c.commit()
    c.close()
    conn = mod._connect_ro(db)
    counts = mod.sweep_accidental_insider_exposure(conn)
    conn.close()
    assert counts["skipped_unparsable"] == 0
    assert mod.main(["--db", str(db), "--gate"]) == 0


def test_aie_corrupt_loss_json_counts_skipped_unparsable_and_gates(tmp_path: Path) -> None:
    db = tmp_path / "corrupt_loss.db"
    c = _empty_db(db)
    eid = _entry(c, AIE_SLUG)
    _scenario(c, eid, sl_raw="{not json")
    c.commit()
    c.close()
    conn = mod._connect_ro(db)
    counts = mod.sweep_accidental_insider_exposure(conn)
    conn.close()
    assert counts["skipped_unparsable"] == 1
    assert mod.main(["--db", str(db), "--gate"]) == 1


@pytest.mark.parametrize("version", [2, None])
def test_aie_pin_version_not_one_counts_skipped_pin_version_and_gates(
    tmp_path: Path, version: int | None
) -> None:
    """A version other than 1, INCLUDING a missing version key (N9-4: policy
    matches the #175 sweep -- None is treated the same as a wrong version)."""
    db = tmp_path / f"pin_version_{version}.db"
    c = _empty_db(db)
    eid = _entry(c, AIE_SLUG)
    _scenario(c, eid, pl=dict(OLD_PL), version=version)
    c.commit()
    c.close()
    conn = mod._connect_ro(db)
    counts = mod.sweep_accidental_insider_exposure(conn)
    conn.close()
    assert counts["skipped_pin_version"] == 1
    assert counts["copy_stale"] == 0  # never reaches classification
    assert mod.main(["--db", str(db), "--gate"]) == 1


def test_control_corrupt_pin_counts_but_does_not_gate(tmp_path: Path) -> None:
    db = tmp_path / "control_corrupt_pin.db"
    c = _empty_db(db)
    slug = mod.RECURATED_CONTROL_SLUGS[0]
    _control_entries(c, slug)
    _control(c, pin_raw="{not json")
    c.commit()
    c.close()
    conn = mod._connect_ro(db)
    counts = mod.sweep_resync_stale_controls(conn)
    conn.close()
    assert counts["skipped_unparsable"] == 1
    assert mod.main(["--db", str(db), "--gate"]) == 0  # controls section never gates


def test_control_non_int_pin_version_counts_but_does_not_gate(tmp_path: Path) -> None:
    db = tmp_path / "control_bad_version.db"
    c = _empty_db(db)
    slug = mod.RECURATED_CONTROL_SLUGS[0]
    eid = _control_entries(c, slug)
    _control(c, pin_raw=json.dumps({"entry_id": str(uuid.UUID(eid)), "version": "one"}))
    c.commit()
    c.close()
    conn = mod._connect_ro(db)
    counts = mod.sweep_resync_stale_controls(conn)
    conn.close()
    assert counts["skipped_pin_version"] == 1
    assert counts["resync_stale"] == 0 and counts["current"] == 0
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
# N9-1 / spec §4.6 M-15: double-counted orgs across the two overlapping slugs
# ---------------------------------------------------------------------------


def test_double_counted_orgs_across_the_named_pair(tmp_path: Path) -> None:
    db = tmp_path / "double_count.db"
    c = _empty_db(db)
    slug_a, slug_b = mod.DOUBLE_COUNT_PAIR
    e_a = _entry(c, slug_a)
    e_b = _entry(c, slug_b)
    org_both, org_a_only = uuid.uuid4().hex, uuid.uuid4().hex
    _scenario(c, e_a, org=org_both)
    _scenario(c, e_b, org=org_both)
    _scenario(c, e_a, org=org_a_only)  # only slug_a -- not double-counted
    c.commit()
    c.close()
    conn = mod._connect_ro(db)
    count = mod.sweep_double_counted_orgs(conn)
    conn.close()
    assert count == 1


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
        "INSERT INTO scenario_library_overrides VALUES (?, ?, '{}', 'null', NULL)",
        (uuid.uuid4().hex, e_dep),
    )
    # soft-deleted override on a deprecated entry: not counted
    c.execute(
        "INSERT INTO scenario_library_overrides VALUES (?, ?, '{}', 'null', '2026-01-01')",
        (uuid.uuid4().hex, e_dep),
    )
    # override on a non-deprecated entry: not counted
    c.execute(
        "INSERT INTO scenario_library_overrides VALUES (?, ?, '{}', 'null', NULL)",
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
    eid = _control_entries(c, slug)
    # services/controls.py:596 shape: str(entry.id) -> hyphenated.
    stale_pin = json.dumps({"entry_id": str(uuid.UUID(eid)), "version": 1})
    current_pin = json.dumps({"entry_id": str(uuid.UUID(eid)), "version": 2})
    stale_cid = _control(c, pin_raw=stale_pin)
    current_cid = _control(c, pin_raw=current_pin)
    c.commit()
    c.close()
    conn = mod._connect_ro(db)
    counts = mod.sweep_resync_stale_controls(conn)
    conn.close()
    assert counts["resync_stale"] == 1 and counts["current"] == 1
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


def test_sha256_unchanged_after_a_full_run(tmp_path: Path) -> None:
    """spec N-2: never writes, not just 'the one probed statement raises'."""
    db = tmp_path / "hash.db"
    c = _empty_db(db)
    eid = _entry(c, AIE_SLUG)
    _scenario(c, eid, pl=dict(OLD_PL))
    c.commit()
    c.close()
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    assert mod.main(["--db", str(db), "--gate"]) == 1  # exercise the gated path too
    after = hashlib.sha256(db.read_bytes()).hexdigest()
    assert before == after


# ---------------------------------------------------------------------------
# (i) output contract: no names/descriptions, only ids/slugs/classes/counts
# ---------------------------------------------------------------------------


def test_output_contract_no_names_anywhere(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "sentinels.db"
    c = _empty_db(db)
    c.execute("INSERT INTO organizations VALUES (?, 'ZZ-ORG-NAME-SENTINEL')", (uuid.uuid4().hex,))
    aie_eid = _entry(c, AIE_SLUG)
    dep_slug = sorted(mod.DEPRECATED_SCENARIO_SLUGS)[0]
    dep_eid = _entry(c, dep_slug)
    sid = _scenario(c, aie_eid, pl=dict(OTHER_NODE), sl=dict(OTHER_NODE))
    old_sl = mod.EPIC_F_OLD_PAIRS["sl"]
    _sme(c, sid, "sl", round(old_sl[0], 2), round(old_sl[1], 2))
    _scenario(c, dep_eid, org=uuid.uuid4().hex)
    slug = mod.RECURATED_CONTROL_SLUGS[0]
    ceid = _control_entries(c, slug)
    _control(c, pin_raw=json.dumps({"entry_id": str(uuid.UUID(ceid)), "version": 1}))
    c.commit()
    c.close()
    assert mod.main(["--db", str(db)]) == 0
    out, err = capsys.readouterr()
    sentinels = (
        "ZZ-SCENARIO-NAME-SENTINEL",
        "ZZ-CONTROL-NAME-SENTINEL",
        "ZZ-SME-SENTINEL",
        "ZZ-ORG-NAME-SENTINEL",
    )
    for sentinel in sentinels:
        assert sentinel not in out
        assert sentinel not in err


# ---------------------------------------------------------------------------
# spec N-3: an unexpected exception exits a distinct code, prints only the type
# ---------------------------------------------------------------------------


def test_unexpected_exception_exits_distinct_code_and_prints_only_type(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "broken_schema.db"
    c = sqlite3.connect(db)
    # scenario_library_entries missing entirely -> the very FIRST query sweep()
    # runs (inside sweep_accidental_insider_exposure, before any print) raises
    # sqlite3.OperationalError.
    c.executescript(
        """
        CREATE TABLE scenarios (id CHAR(32) PRIMARY KEY, name TEXT, library_pin TEXT, status TEXT,
            primary_loss TEXT, secondary_loss TEXT, asset_class TEXT, organization_id CHAR(32));
        """
    )
    c.close()
    rc = mod.main(["--db", str(db)])
    assert rc == 3
    assert rc != 1  # distinct from the gate-fail code
    out, err = capsys.readouterr()
    assert out == ""  # nothing printed before the crash
    assert "OperationalError" in err
    assert "scenario_library_entries" not in err  # only the exception TYPE, never its message


# ---------------------------------------------------------------------------
# M9-4 / spec I-1: brief Step-2 anchor-pin test
# ---------------------------------------------------------------------------


def test_epic_f_old_and_new_constants_pin_to_their_sources() -> None:
    """OLD nodes/pairs pin to the #175 sweep's own frozen post-#175 tables for
    accidental-insider-exposure (that script's own docstring says Epic F
    supersedes this slug and hands classification to THIS script). NEW
    nodes/pairs pin to the current seed JSON via seeded_pair. Both directions
    also pin to the brief's literal pairs, so a derivation bug cannot pass on
    its own."""
    sibling_new_nodes = mod._secondary_response_sweep.NEW_NODES[AIE_SLUG]
    sibling_new_pairs = mod._secondary_response_sweep.NEW_PAIRS[AIE_SLUG]
    for fs in ("pl", "sl"):
        assert mod.EPIC_F_OLD_NODES[fs] == sibling_new_nodes[fs]
        assert mod.EPIC_F_OLD_PAIRS[fs] == pytest.approx(sibling_new_pairs[fs], rel=1e-12)

    root = Path(__file__).resolve().parents[2]
    entries = json.loads((root / "data" / "seed_library_entries.json").read_text()) + json.loads(
        (root / "data" / "seed_library_entries_extension.json").read_text()
    )
    by_slug = {e["slug"]: e for e in entries}
    seed_pl = by_slug[AIE_SLUG]["primary_loss"]
    seed_sl = by_slug[AIE_SLUG]["secondary_loss"]
    assert mod.EPIC_F_NEW_NODES["pl"] == seed_pl
    assert mod.EPIC_F_NEW_NODES["sl"] == seed_sl
    assert mod.EPIC_F_NEW_PAIRS["pl"] == pytest.approx(mod.seeded_pair(seed_pl), rel=1e-12)
    assert mod.EPIC_F_NEW_PAIRS["sl"] == pytest.approx(mod.seeded_pair(seed_sl), rel=1e-12)

    # Brief's own hand-pasted literals (never re-derived) -- a derivation bug in
    # either direction above cannot slip through on internal self-consistency alone.
    assert mod.EPIC_F_OLD_PAIRS["pl"] == pytest.approx(
        (7390.1191451958, 566870.9466662525), rel=1e-12
    )
    assert mod.EPIC_F_OLD_PAIRS["sl"] == pytest.approx(
        (17243.6113390109, 1322698.8755715112), rel=1e-12
    )
    assert mod.EPIC_F_NEW_PAIRS["pl"] == pytest.approx(
        (6158.4326209593, 472392.4555523538), rel=1e-12
    )
    assert mod.EPIC_F_NEW_PAIRS["sl"] == pytest.approx(
        (18475.2978634671, 1417177.3667022558), rel=1e-12
    )


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
