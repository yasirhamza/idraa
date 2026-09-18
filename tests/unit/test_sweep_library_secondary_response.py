# tests/unit/test_sweep_library_secondary_response.py
"""scripts/sweep_library_secondary_response.py -- classification + end-to-end on a
synthetic SQLite fixture (one scenario per class). NO population count or scenario
name from any deployment appears anywhere in this file -- every fixture value here
is synthetic, chosen only to exercise the classifier's branches."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import uuid
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "sweep_library_secondary_response.py"
_spec = importlib.util.spec_from_file_location("sweep_library_secondary_response", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

OLD = (40000.0, 3400000.0)  # synthetic "pre-change seeded pair"
NEW = (36000.0, 3000000.0)  # synthetic "post-change seeded pair"
LIB = "a" * 32


def test_pair_tables_cover_the_24_slugs() -> None:
    assert set(mod.OLD_PAIRS) == set(mod.NEW_PAIRS)
    assert len(mod.OLD_PAIRS) == 24
    for slug, d in mod.OLD_PAIRS.items():
        assert set(d) == {"pl", "sl"}, slug
        assert d["pl"] != mod.NEW_PAIRS[slug]["pl"], slug
        for fs in ("pl", "sl"):  # distinguishable at cent resolution, or pristine would lie
            o, n = d[fs], mod.NEW_PAIRS[slug][fs]
            assert not mod._matches((round(n[0], 2), round(n[1], 2)), o), (slug, fs)


def test_old_nodes_cover_the_24_slugs_and_differ_from_the_shipped_seed() -> None:
    root = Path(__file__).resolve().parents[2]
    entries = json.loads((root / "data" / "seed_library_entries.json").read_text()) + json.loads(
        (root / "data" / "seed_library_entries_extension.json").read_text()
    )
    by_slug = {e["slug"]: e for e in entries}
    assert set(mod.OLD_NODES) == set(mod.OLD_PAIRS)
    for slug, d in mod.OLD_NODES.items():
        assert set(d) == {"pl", "sl"}, slug
        assert d["pl"] != by_slug[slug]["primary_loss"], slug  # old node is not the current node
        assert d["sl"] != by_slug[slug]["secondary_loss"], slug
    # OLD_PAIRS and OLD_NODES are outputs of the same function on the same objects -> exact pin
    rspec = importlib.util.spec_from_file_location(
        "build_secondary_response_reclass", root / "scripts" / "build_secondary_response_reclass.py"
    )
    assert rspec is not None and rspec.loader is not None
    reclass = importlib.util.module_from_spec(rspec)
    rspec.loader.exec_module(reclass)
    import math

    env = {
        r["sector"]: r for r in json.loads((root / "data" / "loss_form_envelopes.json").read_text())
    }
    for slug, d in mod.OLD_PAIRS.items():
        for fs in ("pl", "sl"):
            assert tuple(d[fs]) == reclass.seeded_pair(mod.OLD_NODES[slug][fs]), (slug, fs)
        # OLD_NODES re-derive from the shipped seed: old sum = new sum +/- the entry's response/secondary share
        e = by_slug[slug]
        s = sum(
            p["share"]
            for p in e["loss_form_profile"]
            if p["form"] == "response" and p["kind"] == "secondary"
        )
        sp = (
            sum((p.get("share") or 0) for p in e["loss_form_profile"] if p["kind"] == "primary") + s
        )
        ss = (
            sum((p.get("share") or 0) for p in e["loss_form_profile"] if p["kind"] == "secondary")
            - s
        )
        mu_s = env[reclass.sector(e)]["mean"]
        for fs, key, share_sum in (("pl", "primary_loss", sp), ("sl", "secondary_loss", ss)):
            node = mod.OLD_NODES[slug][fs]
            mu = (
                node["mean"]
                if node["distribution"] == "lognormal"
                else (math.log(node["low"]) + math.log(node["high"])) / 2
            )
            assert mu == pytest.approx(mu_s + math.log(round(share_sum, 4)), rel=1e-9), (slug, fs)
            sig = (
                node["sigma"]
                if node["distribution"] == "lognormal"
                else math.log(node["high"] / node["low"]) / (2 * reclass.Z_0_95)
            )
            assert sig == pytest.approx(reclass.SIGMA, rel=1e-9), (slug, fs)
            assert node["distribution"] == by_slug[slug][key]["distribution"], (slug, fs)


def test_new_nodes_are_the_shipped_seed_nodes() -> None:
    """Pins the hand-pasted NEW_NODES table to the current seed JSON (copy-current
    detection must recognise a node refreshed after b5e2c7a9d413 exactly)."""
    root = Path(__file__).resolve().parents[2]
    entries = json.loads((root / "data" / "seed_library_entries.json").read_text()) + json.loads(
        (root / "data" / "seed_library_entries_extension.json").read_text()
    )
    by_slug = {e["slug"]: e for e in entries}
    assert set(mod.NEW_NODES) == set(mod.OLD_NODES)
    for slug, d in mod.NEW_NODES.items():
        assert d["pl"] == by_slug[slug]["primary_loss"], slug
        assert d["sl"] == by_slug[slug]["secondary_loss"], slug
        assert d["pl"] != mod.OLD_NODES[slug]["pl"] and d["sl"] != mod.OLD_NODES[slug]["sl"], slug


def test_has_pre_change_identity_sees_the_seed_row_inside_a_pool() -> None:
    old = (10.0, 20.0)
    seed = ("a" * 32, None, 10.0, 20.0)
    alice = (None, "Alice", 1.0, 2.0)
    assert mod.has_pre_change_identity([seed], old)
    assert mod.has_pre_change_identity([seed, alice], old)
    assert not mod.has_pre_change_identity([alice], old)
    assert not mod.has_pre_change_identity([(seed[0], None, 99.0, 99.0)], old)
    assert not mod.has_pre_change_identity([], old)


def _empty_db(path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(path)
    c.executescript(
        """
        CREATE TABLE scenario_library_entries (id CHAR(32) PRIMARY KEY, slug TEXT, version INTEGER);
        CREATE TABLE scenarios (id CHAR(32) PRIMARY KEY, name TEXT, library_pin TEXT, status TEXT,
            primary_loss TEXT, secondary_loss TEXT);
        CREATE TABLE scenario_sme_estimates (id CHAR(32) PRIMARY KEY, scenario_id CHAR(32), fieldset TEXT,
            sme_id CHAR(32), sme_name TEXT, low REAL, high REAL, recorded_at TEXT);
        CREATE TABLE scenario_library_overrides (id CHAR(32) PRIMARY KEY, library_entry_id CHAR(32),
            library_entry_version INTEGER, primary_loss TEXT, secondary_loss TEXT, deleted_at TEXT);
        """
    )
    return c


def test_gate_exit_is_zero_on_a_clean_db(tmp_path: Path) -> None:
    db = tmp_path / "clean.db"
    _empty_db(db).close()
    assert mod.main(["--gate", str(db)]) == 0


def _one_scenario_db(
    path: Path, pl_node: str, sl_node: str, sme_scenario_id: str | None
) -> tuple[sqlite3.Connection, str, str]:
    """One renumbered-entry scenario; returns (conn, slug, scenario_id) for further inserts."""
    slug = sorted(mod.OLD_PAIRS)[0]
    c = _empty_db(path)
    eid, sid = uuid.uuid4().hex, uuid.uuid4().hex
    c.execute("INSERT INTO scenario_library_entries VALUES (?, ?, 1)", (eid, slug))
    c.execute(
        "INSERT INTO scenarios VALUES (?, ?, ?, ?, ?, ?)",
        (sid, "n", json.dumps({"entry_id": eid, "version": 1}), "active", pl_node, sl_node),
    )
    old = mod.OLD_PAIRS[slug]["sl"]
    c.execute(
        "INSERT INTO scenario_sme_estimates VALUES (?, ?, 'sl', ?, NULL, ?, ?, '2026-01-01')",
        (
            uuid.uuid4().hex,
            sme_scenario_id or sid,
            uuid.uuid4().hex,
            round(old[0], 2),
            round(old[1], 2),
        ),
    )
    c.commit()
    return c, slug, sid


def test_sme_join_tolerates_uppercase_and_hyphenated_scenario_ids(tmp_path: Path) -> None:
    other = json.dumps({"distribution": "PERT", "low": 1.0, "mode": 1.0, "high": 2.0})
    for spelling in ("upper", "hyphen"):
        db = tmp_path / f"{spelling}.db"
        c = _empty_db(db)
        c.close()
        db.unlink()
        sid_hex = uuid.uuid4().hex
        spelt = sid_hex.upper() if spelling == "upper" else str(uuid.UUID(sid_hex))
        c, _slug, _sid = _one_scenario_db(db, other, other, spelt)
        # re-point the scenario row at the same id in canonical hex
        c.execute("UPDATE scenarios SET id = ?", (sid_hex,))
        c.commit()
        c.close()
        summary = mod.sweep(db)
        assert summary["pristine"] == 1, spelling  # a mis-spelt join would report stale


def test_gate_fails_on_pinned_scenario_with_pristine_other_side(tmp_path: Path) -> None:
    pinned = json.dumps(
        {
            "distribution": "PERT",
            "low": 1.0,
            "mode": 1.0,
            "high": 2.0,
            "distribution_fit_metadata": {"sigma_recalibration": {"source": "analyst_pin"}},
        }
    )
    other = json.dumps({"distribution": "PERT", "low": 1.0, "mode": 1.0, "high": 2.0})
    db = tmp_path / "pinned.db"
    c, _slug, _sid = _one_scenario_db(db, pinned, other, None)
    c.close()
    summary = mod.sweep(db)
    assert summary["pristine"] == 0 and summary["pinned_stale_side"] == 1
    assert mod.main(["--gate", str(db)]) == 1  # judgment tier still fails the gate


def test_deleted_row_with_corrupt_pin_does_not_drive_the_gate(tmp_path: Path) -> None:
    db = tmp_path / "deleted.db"
    c = _empty_db(db)
    c.execute(
        "INSERT INTO scenarios VALUES (?, 'n', '{not json', 'deleted', NULL, NULL)",
        (uuid.uuid4().hex,),
    )
    c.commit()
    c.close()
    assert mod.sweep(db)["skipped_unparsable"] == 0
    assert mod.main(["--gate", str(db)]) == 0


def test_scenario_class_copy_stale_is_total() -> None:
    assert mod._scenario_class("copy-stale", "stale") == "copy-stale"
    assert mod._scenario_class("none", "copy-stale") == "copy-stale"
    assert mod._scenario_class("pinned", "copy-stale") == "pinned"


def test_scenario_class_copy_current_counts_as_current() -> None:
    assert mod._scenario_class("copy-current*", "none") == "current"
    assert mod._scenario_class("copy-current", "none") == "current"
    assert mod._scenario_class("copy-current", "copy-current") == "current"
    assert mod._scenario_class("copy-current", "pristine") == "pristine"
    assert mod._scenario_class("stale", "copy-current") == "stale"


def test_new_pairs_are_the_seeded_pairs_of_the_shipped_seed() -> None:
    """Pins the hand-pasted NEW_PAIRS table to the wizard-seeded pairs of the current seed
    JSON, so a wrong-basis (PERT bounds) or mis-pasted table cannot ship green."""
    root = Path(__file__).resolve().parents[2]
    rspec = importlib.util.spec_from_file_location(
        "build_secondary_response_reclass", root / "scripts" / "build_secondary_response_reclass.py"
    )
    assert rspec is not None and rspec.loader is not None
    reclass = importlib.util.module_from_spec(rspec)
    rspec.loader.exec_module(reclass)
    entries = json.loads((root / "data" / "seed_library_entries.json").read_text()) + json.loads(
        (root / "data" / "seed_library_entries_extension.json").read_text()
    )
    by_slug = {e["slug"]: e for e in entries}
    for slug, d in mod.NEW_PAIRS.items():
        for fs, key in (("pl", "primary_loss"), ("sl", "secondary_loss")):
            assert tuple(d[fs]) == reclass.seeded_pair(by_slug[slug][key]), (slug, fs)


def test_classify_pristine_matches_old_pair_within_a_cent() -> None:
    assert mod.classify_fieldset([(LIB, None, 40000.01, 3399999.99)], OLD, NEW) == "pristine"


def test_classify_current_matches_new_pair() -> None:
    assert mod.classify_fieldset([(LIB, None, 36000.0, 3000000.0)], OLD, NEW) == "current"


def test_classify_stale_single_row_matches_neither() -> None:
    assert mod.classify_fieldset([(LIB, None, 100000.0, 20000000.0)], OLD, NEW) == "stale"


def test_classify_modified_when_more_than_one_identity() -> None:
    rows = [(LIB, None, 40000.0, 3400000.0), (None, "Alice", 50000.0, 9000000.0)]
    assert mod.classify_fieldset(rows, OLD, NEW) == "modified"


def test_classify_dedups_latest_per_identity() -> None:
    rows = [(LIB, None, 1.0, 2.0), (LIB, None, 40000.0, 3400000.0)]
    assert mod.classify_fieldset(rows, OLD, NEW) == "pristine"


def test_classify_treats_hyphenated_and_hex_sme_ids_as_one_identity() -> None:
    # Raw-text seed UUID foot-gun: both spellings of one FK are one SME, so a
    # pristine fieldset is never mis-reported as modified.
    sid = "0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b"
    rows = [(sid, None, 1.0, 2.0), (sid.replace("-", ""), None, 10.0, 20.0)]
    assert mod.classify_fieldset(rows, (10.0, 20.0), (99.0, 99.0)) == "pristine"


def test_scenario_class_pinned_wins_over_every_other_class() -> None:
    for other in ("pristine", "current", "stale", "modified", "none", "pinned"):
        assert mod._scenario_class("pinned", other) == "pinned"
        assert mod._scenario_class(other, "pinned") == "pinned"


def test_classify_none_without_rows() -> None:
    assert mod.classify_fieldset([], OLD, NEW) == "none"


def test_is_copy_of_ignores_sidecar_and_rejects_other_nodes() -> None:
    old = {"distribution": "PERT", "low": 1.0, "mode": 1.0, "high": 2.0}
    assert mod.is_copy_of({**old, "distribution_fit_metadata": {"x": 1}}, old)
    assert mod.is_copy_of(dict(old), old)
    assert not mod.is_copy_of({**old, "high": 3.0}, old)
    assert not mod.is_copy_of(None, old)
    # library refresh mints a capacity max onto lognormal fields (loss_pinning._resolve_refresh)
    cat = {"distribution": "lognormal", "mean": 12.0, "sigma": 1.7}
    assert mod.is_copy_of({**cat, "max": 1e8}, cat)
    assert not mod.is_copy_of({**cat, "mean": 12.5, "max": 1e8}, cat)


def _fixture_db(tmp_path: Path, slug: str) -> Path:
    db = tmp_path / "t.db"
    c = sqlite3.connect(db)
    c.executescript(
        """
        CREATE TABLE scenario_library_entries (id CHAR(32) PRIMARY KEY, slug TEXT, version INTEGER);
        CREATE TABLE scenarios (id CHAR(32) PRIMARY KEY, name TEXT, library_pin TEXT, status TEXT,
            primary_loss TEXT, secondary_loss TEXT);
        CREATE TABLE scenario_sme_estimates (id CHAR(32) PRIMARY KEY, scenario_id CHAR(32), fieldset TEXT,
            sme_id CHAR(32), sme_name TEXT, low REAL, high REAL, recorded_at TEXT);
        CREATE TABLE scenario_library_overrides (id CHAR(32) PRIMARY KEY, library_entry_id CHAR(32),
            library_entry_version INTEGER, primary_loss TEXT, secondary_loss TEXT, deleted_at TEXT);
        """
    )
    affected = uuid.uuid4().hex
    other = uuid.uuid4().hex
    c.execute("INSERT INTO scenario_library_entries VALUES (?, ?, 1)", (affected, slug))
    c.execute(
        "INSERT INTO scenario_library_entries VALUES (?, 'ot-network-scanning-reconnaissance', 1)",
        (other,),
    )
    cat_slug = next(
        s for s in sorted(mod.OLD_NODES) if mod.OLD_NODES[s]["pl"]["distribution"] == "lognormal"
    )
    cat_entry = uuid.uuid4().hex
    c.execute("INSERT INTO scenario_library_entries VALUES (?, ?, 1)", (cat_entry, cat_slug))
    old_pl, old_sl = mod.OLD_PAIRS[slug]["pl"], mod.OLD_PAIRS[slug]["sl"]
    other_node = json.dumps({"distribution": "PERT", "low": 5.0, "mode": 5.0, "high": 6.0})

    def scenario(
        entry_hex: str | None,
        pl_node: str = other_node,
        sl_node: str = other_node,
        version: int = 1,
        status: str = "active",
    ) -> str:
        sid = uuid.uuid4().hex
        pin = (
            json.dumps({"entry_id": str(uuid.UUID(entry_hex)), "version": version})
            if entry_hex
            else "null"
        )
        c.execute(
            "INSERT INTO scenarios VALUES (?, ?, ?, ?, ?, ?)",
            (sid, "Acme Q3 Breach", pin, status, pl_node, sl_node),
        )
        return sid

    def sme(
        sid: str,
        fs: str,
        low: float,
        high: float,
        when: str,
        sme_id: str | None = LIB,
        name: str | None = None,
    ) -> None:
        c.execute(
            "INSERT INTO scenario_sme_estimates VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, sid, fs, sme_id, name, low, high, when),
        )

    s1 = scenario(affected)  # pristine: both fieldsets on the old seeded pair
    sme(s1, "pl", round(old_pl[0], 2), round(old_pl[1], 2), "2026-01-01")
    sme(s1, "sl", round(old_sl[0], 2), round(old_sl[1], 2), "2026-01-01")
    s2 = scenario(affected)  # stale: synthetic pair matching neither
    sme(s2, "pl", 123.0, 456.0, "2026-01-01")
    sme(s2, "sl", 789.0, 1011.0, "2026-01-01")
    s3 = scenario(affected)  # pristine via sl: analyst row added on pl only
    sme(s3, "pl", round(old_pl[0], 2), round(old_pl[1], 2), "2026-01-01")
    sme(s3, "pl", 50000.0, 9000000.0, "2026-01-02", sme_id=None, name="Alice")
    sme(s3, "sl", round(old_sl[0], 2), round(old_sl[1], 2), "2026-01-01")
    s3b = scenario(affected)  # modified: analyst rows on both fieldsets
    sme(s3b, "pl", round(old_pl[0], 2), round(old_pl[1], 2), "2026-01-01")
    sme(s3b, "pl", 50000.0, 9000000.0, "2026-01-02", sme_id=None, name="Alice")
    sme(s3b, "sl", round(old_sl[0], 2), round(old_sl[1], 2), "2026-01-01")
    sme(s3b, "sl", 70000.0, 8000000.0, "2026-01-02", sme_id=None, name="Alice")
    s4 = scenario(other)  # unaffected entry
    sme(s4, "pl", 1.0, 2.0, "2026-01-01")
    scenario(None)  # unpinned
    scenario(
        affected, pl_node=json.dumps(mod.OLD_NODES[slug]["pl"])
    )  # copy-stale (pl only): library-refresh path, no SME rows
    # catastrophic copy-stale: the refresh path minted a capacity max onto the lognormal node
    scenario(cat_entry, sl_node=json.dumps({**mod.OLD_NODES[cat_slug]["sl"], "max": 2.5e8}))
    # pinned (pl): analyst_pin stamp on the stored pl node -> "pinned", never "pristine";
    # sl is pristine via SME rows -> scenario class is "pristine" through sl alone
    pinned_pl_node = json.dumps(
        {
            "distribution": "PERT",
            "low": 1.0,
            "mode": 1.0,
            "high": 2.0,  # deliberately not OLD_NODES[slug]["pl"] -> is_copy_of() is False
            "distribution_fit_metadata": {"sigma_recalibration": {"source": "analyst_pin"}},
        }
    )
    s5 = scenario(affected, pl_node=pinned_pl_node)
    sme(s5, "pl", round(old_pl[0], 2), round(old_pl[1], 2), "2026-01-01")  # ignored: pl is pinned
    # pinned beats copy-stale: a pinned pl next to a verbatim pre-change sl copy is still "pinned"
    s5b = scenario(affected, pl_node=pinned_pl_node, sl_node=json.dumps(mod.OLD_NODES[slug]["sl"]))
    sme(s5b, "sl", round(old_sl[0], 2), round(old_sl[1], 2), "2026-01-01")
    # copy-current: both nodes refreshed AFTER the migration (verbatim post-change entry
    # nodes) while the wizard's pre-change seed rows are still on the table -> current,
    # never pristine (T4c-Meth-3)
    s8 = scenario(
        affected,
        pl_node=json.dumps(mod.NEW_NODES[slug]["pl"]),
        sl_node=json.dumps(mod.NEW_NODES[slug]["sl"]),
    )
    sme(s8, "pl", round(old_pl[0], 2), round(old_pl[1], 2), "2026-01-01")
    sme(s8, "sl", round(old_sl[0], 2), round(old_sl[1], 2), "2026-01-01")
    # copy-current with POST-change rows: refreshed AND re-estimated -> nothing to sweep,
    # so copy_current_stale_rows must NOT move (pins the discriminating direction).
    s9 = scenario(
        affected,
        pl_node=json.dumps(mod.NEW_NODES[slug]["pl"]),
        sl_node=json.dumps(mod.NEW_NODES[slug]["sl"]),
    )
    new_pl, new_sl = mod.NEW_PAIRS[slug]["pl"], mod.NEW_PAIRS[slug]["sl"]
    sme(s9, "pl", round(new_pl[0], 2), round(new_pl[1], 2), "2026-01-01")
    sme(s9, "sl", round(new_sl[0], 2), round(new_sl[1], 2), "2026-01-01")
    # copy-current with a MODIFIED pool that still contains the pre-change seed row:
    # the re-fit would pool it back in, so it counts (T4e MR5-2).
    s10 = scenario(affected, pl_node=json.dumps(mod.NEW_NODES[slug]["pl"]))
    sme(s10, "pl", round(old_pl[0], 2), round(old_pl[1], 2), "2026-01-01")
    sme(s10, "pl", 1.0, 2.0, "2026-01-02", sme_id=None, name="Alice")
    # pinned pl + copy-current sl whose rows still hold the pre-change pair: the other
    # side is read and starred, counted in pinned_stale_side (never in copy_current_stale_rows)
    s11 = scenario(affected, pl_node=pinned_pl_node, sl_node=json.dumps(mod.NEW_NODES[slug]["sl"]))
    sme(s11, "sl", round(old_sl[0], 2), round(old_sl[1], 2), "2026-01-01")
    # pinned pl + a clean copy-current sl (POST-change rows): pinned counts, but
    # pinned_stale_side must NOT move (pins the discriminating direction, cf. s9)
    s12 = scenario(affected, pl_node=pinned_pl_node, sl_node=json.dumps(mod.NEW_NODES[slug]["sl"]))
    sme(s12, "sl", round(new_sl[0], 2), round(new_sl[1], 2), "2026-01-01")
    sme(s5, "sl", round(old_sl[0], 2), round(old_sl[1], 2), "2026-01-01")
    # skipped_pin_version: pin.version != 1 must not count towards affected_scenarios/pristine
    # even though the SME rows below would otherwise classify pristine on both sides
    s6 = scenario(affected, version=2)
    sme(s6, "pl", round(old_pl[0], 2), round(old_pl[1], 2), "2026-01-01")
    sme(s6, "sl", round(old_sl[0], 2), round(old_sl[1], 2), "2026-01-01")
    # override_one_sided: PL-only override on the renumbered entry counts; a two-sided one and a
    # soft-deleted one-sided one do not; an override on an unaffected entry does not
    # The ORM stores an unset leg as the JSON text 'null' (SQLAlchemy JSON, none_as_null
    # False) -- o1 mirrors that producer shape; o5 pins the raw SQL NULL shape too.
    for oid, eid, pl_o, sl_o, deleted in (
        ("o1", affected, "{}", "null", None),
        ("o2", affected, "{}", "{}", None),
        ("o3", affected, "null", "{}", "2026-01-01"),
        ("o4", other, "{}", "null", None),
        ("o5", affected, None, "{}", None),
    ):
        c.execute(
            "INSERT INTO scenario_library_overrides VALUES (?, ?, 1, ?, ?, ?)",
            (uuid.uuid5(uuid.NAMESPACE_DNS, oid).hex, eid, pl_o, sl_o, deleted),
        )
    # (ovr) marker: a scenario adopted through an override prints it after the slug
    s13 = uuid.uuid4().hex
    c.execute(
        "INSERT INTO scenarios VALUES (?, ?, ?, ?, ?, ?)",
        (
            s13,
            "Acme Q3 Breach",
            json.dumps({"entry_id": affected, "version": 1, "override_id": "o1"}),
            "active",
            other_node,
            other_node,
        ),
    )
    # skipped_unparsable: a corrupt JSON column is unclassified, not clean
    c.execute(
        "INSERT INTO scenarios VALUES (?, ?, ?, ?, ?, ?)",
        (
            uuid.uuid4().hex,
            "Acme Q3 Breach",
            json.dumps({"entry_id": affected, "version": 1}),
            "active",
            "{not json",
            other_node,
        ),
    )
    # skipped_deleted: a soft-deleted row must not count even though it would otherwise be pristine
    s7 = scenario(affected, status="deleted")
    sme(s7, "pl", round(old_pl[0], 2), round(old_pl[1], 2), "2026-01-01")
    sme(s7, "sl", round(old_sl[0], 2), round(old_sl[1], 2), "2026-01-01")
    c.commit()
    c.close()
    return db


def test_sweep_end_to_end_counts_each_class(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    slug = sorted(mod.OLD_PAIRS)[0]
    db = _fixture_db(tmp_path, slug)
    summary = mod.sweep(db)
    assert summary == {
        "affected_scenarios": 10,
        "pristine": 2,
        "current": 3,
        "stale": 2,
        "modified": 1,
        "copy_stale": 2,
        "pinned": 4,
        "pinned_stale_side": 3,
        "copy_current_stale_rows": 2,
        "skipped_pin_version": 1,
        "skipped_deleted": 1,
        "skipped_unparsable": 1,
        "override_one_sided": 2,
    }
    out = capsys.readouterr().out
    assert slug in out and "pristine" in out
    assert "| modified | pristine | pristine" in out  # s3: pristine via sl only
    assert "| copy-stale | none | copy-stale" in out  # pl-only copy, sl untouched
    assert "| none | copy-stale | copy-stale" in out  # catastrophic sl copy carrying a minted max
    assert "| pinned | pristine | pinned" in out  # s5: pl pinned -> whole scenario out (D23)
    assert "| pinned | copy-stale | pinned" in out  # s5b: pinned wins over copy-stale
    assert "| copy-current* | copy-current* | current" in out  # s8: refresh, pre-change rows
    assert "| copy-current | copy-current | current" in out  # s9: refresh + re-estimate
    assert "| copy-current* | none | current" in out  # s10: modified pool holds the seed row
    assert "copy_current_stale_rows=2" in out
    assert "| pinned | copy-current* | pinned" in out  # s11: read, starred, not in the row counter
    assert "| pinned | copy-current | pinned" in out  # s12: clean other side, not stale
    assert "Alice" not in out  # no user content in the output
    assert "Acme Q3 Breach" not in out  # scenario names never reach stdout either
    assert f"| {slug} (ovr) | none | none | stale" in out  # s13: override-adopted marker
    assert "skipped_unparsable=1 override_one_sided=2" in out
    assert mod.main([str(db)]) == 0
    assert mod.main(["--gate", str(db)]) == 1  # pristine/copy-stale/override rows present
    assert mod.main([]) == 2
    assert mod.main([str(tmp_path / "missing.db")]) == 2
