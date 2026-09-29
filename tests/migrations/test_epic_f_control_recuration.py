"""Epic F (#192) control-library re-curation migration (f6a2b0d4e8c3) + parent-tag
convergence. Follows tests/migrations/test_epic_f_library_curation.py's dirty-then-run
pattern (itself following tests/migrations/test_dataquality_followups.py) and the
alembic_runner/alembic_engine fixtures (tests/migrations/conftest.py). No revision
literal appears below beyond the name of the migration module being loaded by path --
PRE/REV come from the loaded module itself (S2-2/A2-5); no migration test shells out
to git (S2-1).
"""

from __future__ import annotations

import importlib.util
import json
import uuid
from pathlib import Path
from types import ModuleType
from typing import cast

import sqlalchemy as sa
from pytest_alembic import MigrationContext
from sqlalchemy.engine import Engine

import idraa
from idraa.schemas.control_library import ControlLibraryAssignmentSeed, ControlLibraryEntrySeed

_ROOT = Path(idraa.__file__).resolve().parent.parent.parent
_VERSIONS = _ROOT / "alembic" / "versions"
_MIGRATION_PATH = _VERSIONS / "f6a2b0d4e8c3_epic_f_control_recuration.py"
_SEED_PATH = _ROOT / "data" / "seed_control_library_entries.json"


def _load_migration_from_path(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


mod = _load_migration_from_path(_MIGRATION_PATH, "_test_mig_f6a2b0d4e8c3")
PRE: str = mod.down_revision
REV: str = mod.revision
EPIC_F_SLUGS: tuple[str, ...] = mod._EPIC_F_SLUGS

# The Epic F assignment each of the five touched slugs GAINS -- "the pre-Epic-F set"
# used to dirty the DB is the current seed entry's assignments minus this one member.
_EPIC_F_ADDED_SUB_FUNCTION: dict[str, str] = {
    "security-information-event-management": "lec_det_visibility",
    "file-integrity-monitoring": "lec_det_visibility",
    "saas-security-posture-management": "lec_det_visibility",
    "secure-remote-access": "lec_prev_resistance",
    "security-awareness-training": "dsc_prev_ensure_capability",
}
assert set(_EPIC_F_ADDED_SUB_FUNCTION) == set(EPIC_F_SLUGS)

# The two slugs whose CIS tags Epic F also touches -- "the pre-Epic-F tags" used to
# dirty the DB are the current seed entry's cis_safeguards minus these members. FIM,
# SRA and SAT carry no Epic F tag change (task-7-brief / task-6-report).
_EPIC_F_ADDED_CIS_TAGS: dict[str, tuple[str, ...]] = {
    "security-information-event-management": ("8.2", "8.10"),
    "saas-security-posture-management": ("8.12",),
}

# The #437 tranche-2 drift this migration must repair generically: a slug OUTSIDE
# EPIC_F_SLUGS whose stored cis_safeguards is missing a tag the JSON carries.
_DRIFT_SLUG = "security-conscious-personnel"
_DRIFT_DROPPED_CIS_TAG = "14.2"

_PARENT_COLUMNS_EXCLUDING_TAGS_VERSION_ID_TIMESTAMPS: tuple[str, ...] = (
    "slug",
    "name",
    "description",
    "control_type",
    "reference_annual_cost",
    "compliance_mappings",
    "applicable_industries",
    "applicable_org_sizes",
    "tags",
    "source_citations",
    "status",
    "row_version",
)


def _validated_seed() -> dict[str, ControlLibraryEntrySeed]:
    payload = json.loads(_SEED_PATH.read_text(encoding="utf-8"))
    return {e["slug"]: ControlLibraryEntrySeed.model_validate(e) for e in payload["entries"]}


def _latest_version(engine: Engine, slug: str) -> tuple[str, int]:
    with engine.connect() as conn:
        row = conn.execute(
            sa.text(
                "SELECT id, version FROM control_library_entries "
                "WHERE slug = :s ORDER BY version DESC LIMIT 1"
            ),
            {"s": slug},
        ).first()
    assert row is not None, f"{slug}: no row in control_library_entries"
    return str(row[0]), int(row[1])


def _insert_assignment(
    conn: sa.Connection, entry_id: str, version: int, a: ControlLibraryAssignmentSeed, now: str
) -> None:
    conn.execute(
        sa.text(
            """
            INSERT INTO control_library_entry_assignments
              (id, library_entry_id, library_entry_version, sub_function,
               capability_default, coverage_default, reliability_default,
               capability_provenance, capability_citations,
               coverage_provenance, coverage_citations,
               reliability_provenance, reliability_citations,
               created_at, updated_at)
            VALUES
              (:id, :eid, :v, :sf, :cap, :cov, :rel, :capp, :capc, :covp, :covc,
               :relp, :relc, :now, :now)
            """
        ),
        {
            "id": uuid.uuid4().hex,
            "eid": entry_id,
            "v": version,
            "sf": a.sub_function.value,
            "cap": a.capability_default,
            "cov": a.coverage_default,
            "rel": a.reliability_default,
            "capp": a.capability_provenance,
            "capc": json.dumps(a.capability_citations),
            "covp": a.coverage_provenance,
            "covc": json.dumps(a.coverage_citations),
            "relp": a.reliability_provenance,
            "relc": json.dumps(a.reliability_citations),
            "now": now,
        },
    )


def _assignment_rows(engine: Engine, entry_id: str, version: int) -> list[tuple[object, ...]]:
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT id, sub_function, capability_default, coverage_default, "
                "       reliability_default, capability_provenance, capability_citations, "
                "       coverage_provenance, coverage_citations, "
                "       reliability_provenance, reliability_citations "
                "FROM control_library_entry_assignments "
                "WHERE library_entry_id = :eid AND library_entry_version = :v"
            ),
            {"eid": entry_id, "v": version},
        ).all()
    return [tuple(r) for r in rows]


def _assignment_row_to_comparable(row: tuple[object, ...]) -> tuple[object, ...]:
    (
        _id,
        sub_function,
        capability_default,
        coverage_default,
        reliability_default,
        capability_provenance,
        capability_citations,
        coverage_provenance,
        coverage_citations,
        reliability_provenance,
        reliability_citations,
    ) = row
    return (
        sub_function,
        capability_default,
        coverage_default,
        reliability_default,
        capability_provenance,
        tuple(json.loads(str(capability_citations))),
        coverage_provenance,
        tuple(json.loads(str(coverage_citations))),
        reliability_provenance,
        tuple(json.loads(str(reliability_citations))),
    )


def _assignment_seed_to_comparable(a: ControlLibraryAssignmentSeed) -> tuple[object, ...]:
    return (
        a.sub_function.value,
        a.capability_default,
        a.coverage_default,
        a.reliability_default,
        a.capability_provenance,
        tuple(a.capability_citations),
        a.coverage_provenance,
        tuple(a.coverage_citations),
        a.reliability_provenance,
        tuple(a.reliability_citations),
    )


def _parent_tags(
    engine: Engine, entry_id: str, version: int
) -> tuple[list[str], list[str], list[str]]:
    with engine.connect() as conn:
        row = conn.execute(
            sa.text(
                "SELECT nist_csf_subcategories, cis_safeguards, iso_27001_controls "
                "FROM control_library_entries WHERE id = :eid AND version = :v"
            ),
            {"eid": entry_id, "v": version},
        ).first()
    assert row is not None
    return (
        list(json.loads(row[0])),
        list(json.loads(row[1])),
        list(json.loads(row[2])),
    )


def _parent_row_dict(engine: Engine, entry_id: str, version: int) -> dict[str, object]:
    with engine.connect() as conn:
        row = (
            conn.execute(
                sa.text("SELECT * FROM control_library_entries WHERE id = :eid AND version = :v"),
                {"eid": entry_id, "v": version},
            )
            .mappings()
            .first()
        )
    assert row is not None
    return dict(row)


def _dump_all_rows(engine: Engine) -> tuple[list[tuple[object, ...]], list[tuple[object, ...]]]:
    with engine.connect() as conn:
        entries = conn.execute(
            sa.text("SELECT * FROM control_library_entries ORDER BY id, version")
        ).fetchall()
        assignments = conn.execute(
            sa.text(
                "SELECT * FROM control_library_entry_assignments "
                "ORDER BY library_entry_id, library_entry_version, sub_function"
            )
        ).fetchall()
    return [tuple(r) for r in entries], [tuple(r) for r in assignments]


def _dirty_epic_f_slugs(
    engine: Engine, validated: dict[str, ControlLibraryEntrySeed]
) -> dict[str, tuple[str, int]]:
    """Rewrite each of the five slugs' CURRENT-version assignment rows and (SIEM/SSPM
    only) tags to the pre-Epic-F state: the current seed's assignment set minus the ONE
    Epic F addition, and cis_safeguards minus the Epic F tag(s). Returns slug -> (id,
    version) as captured before the rewrite (the version is unchanged by dirtying)."""
    before: dict[str, tuple[str, int]] = {}
    now = "2026-01-01T00:00:00+00:00"
    with engine.begin() as conn:
        for slug in EPIC_F_SLUGS:
            entry_id, version = _latest_version(engine, slug)
            before[slug] = (entry_id, version)
            seed = validated[slug]
            dropped = _EPIC_F_ADDED_SUB_FUNCTION[slug]
            pre_assignments = [a for a in seed.assignments if a.sub_function.value != dropped]
            assert len(pre_assignments) == len(seed.assignments) - 1, (
                f"{slug}: dropped sub_function {dropped!r} not found in seed assignments"
            )
            conn.execute(
                sa.text(
                    "DELETE FROM control_library_entry_assignments "
                    "WHERE library_entry_id = :eid AND library_entry_version = :v"
                ),
                {"eid": entry_id, "v": version},
            )
            for a in pre_assignments:
                _insert_assignment(conn, entry_id, version, a, now)
            if slug in _EPIC_F_ADDED_CIS_TAGS:
                dropped_tags = _EPIC_F_ADDED_CIS_TAGS[slug]
                pre_cis = [t for t in seed.cis_safeguards if t not in dropped_tags]
                assert len(pre_cis) == len(seed.cis_safeguards) - len(dropped_tags)
                conn.execute(
                    sa.text(
                        "UPDATE control_library_entries SET cis_safeguards = :cis "
                        "WHERE id = :eid AND version = :v"
                    ),
                    {"cis": json.dumps(pre_cis), "eid": entry_id, "v": version},
                )
    return before


def _dirty_t2_drift_entry(
    engine: Engine, validated: dict[str, ControlLibraryEntrySeed]
) -> tuple[str, int]:
    """Reproduce the pre-existing #437 tranche-2 drift on an UNTOUCHED entry
    (security-conscious-personnel): drop "14.2" from its stored cis_safeguards, which
    the JSON still carries. Returns (id, version) (version is unchanged by dirtying)."""
    entry_id, version = _latest_version(engine, _DRIFT_SLUG)
    seed = validated[_DRIFT_SLUG]
    assert _DRIFT_DROPPED_CIS_TAG in seed.cis_safeguards
    drifted_cis = [t for t in seed.cis_safeguards if t != _DRIFT_DROPPED_CIS_TAG]
    assert len(drifted_cis) == len(seed.cis_safeguards) - 1
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "UPDATE control_library_entries SET cis_safeguards = :cis "
                "WHERE id = :eid AND version = :v"
            ),
            {"cis": json.dumps(drifted_cis), "eid": entry_id, "v": version},
        )
    return entry_id, version


# ---------------------------------------------------------------------------
# (a)/(b) dirty-then-upgrade: the five slugs bump version and reconverge to the
# JSON assignment set; old-version children are gone; SAT's citation is repaired.
# ---------------------------------------------------------------------------


def test_dirty_then_upgrade_bumps_and_reinserts_epic_f_assignments(
    alembic_runner: MigrationContext,
    alembic_engine: Engine,
) -> None:
    alembic_runner.migrate_up_to(PRE)
    validated = _validated_seed()
    before = _dirty_epic_f_slugs(alembic_engine, validated)
    _dirty_t2_drift_entry(alembic_engine, validated)

    alembic_runner.migrate_up_to(REV)

    for slug in EPIC_F_SLUGS:
        old_id, old_version = before[slug]
        new_id, new_version = _latest_version(alembic_engine, slug)
        assert new_id == old_id, f"{slug}: parent id changed across the bump"
        assert new_version == old_version + 1, f"{slug}: version not bumped by exactly 1"

        # No assignment rows remain at the old version.
        assert _assignment_rows(alembic_engine, old_id, old_version) == []

        # The new-version assignment set equals the current seed JSON exactly.
        got = {
            r[1]: _assignment_row_to_comparable(r)
            for r in _assignment_rows(alembic_engine, new_id, new_version)
        }
        want = {
            a.sub_function.value: _assignment_seed_to_comparable(a)
            for a in validated[slug].assignments
        }
        assert set(got) == set(want), f"{slug}: sub_function set mismatch"
        for sf, want_tuple in want.items():
            assert got[sf] == want_tuple, f"{slug}/{sf}: assignment values diverge from JSON"

        # All (re-)inserted ids are 32-char no-hyphen hex.
        for row in _assignment_rows(alembic_engine, new_id, new_version):
            rid = str(row[0])
            assert len(rid) == 32 and "-" not in rid, f"{slug}: bad id format {rid!r}"

    # SAT's Communication citation no longer carries the stale "non-scoring" label.
    sat_id, sat_version = _latest_version(alembic_engine, "security-awareness-training")
    sat_rows = {
        r[1]: _assignment_row_to_comparable(r)
        for r in _assignment_rows(alembic_engine, sat_id, sat_version)
    }
    comm_citations = cast(
        "tuple[str, ...]", sat_rows["dsc_prev_communication"][5]
    )  # capability_citations tuple
    assert not any("non-scoring" in c for c in comm_citations)


# ---------------------------------------------------------------------------
# (c) parent tags: every seed entry's latest-version tags parse-equal the JSON;
# the T2-drift entry is repaired in place (version unchanged); untouched entries'
# versions are unchanged; the five slugs' non-tag/version/id/timestamp columns
# are unchanged across the bump.
# ---------------------------------------------------------------------------


def test_dirty_then_upgrade_syncs_all_parent_tags_and_repairs_t2_drift(
    alembic_runner: MigrationContext,
    alembic_engine: Engine,
) -> None:
    alembic_runner.migrate_up_to(PRE)
    validated = _validated_seed()
    epic_f_before = _dirty_epic_f_slugs(alembic_engine, validated)
    drift_id, drift_version = _dirty_t2_drift_entry(alembic_engine, validated)

    # Snapshot every OTHER slug's version, and the five slugs' non-tag columns, before
    # the upgrade (post-dirty).
    other_slugs = [s for s in validated if s not in EPIC_F_SLUGS]
    versions_before = {s: _latest_version(alembic_engine, s)[1] for s in other_slugs}
    parent_snapshots_before = {
        slug: {
            k: v
            for k, v in _parent_row_dict(alembic_engine, *epic_f_before[slug]).items()
            if k in _PARENT_COLUMNS_EXCLUDING_TAGS_VERSION_ID_TIMESTAMPS
        }
        for slug in EPIC_F_SLUGS
    }

    alembic_runner.migrate_up_to(REV)

    # Every seed entry's latest-version tags parse-equal the current JSON.
    for slug, seed in validated.items():
        entry_id, version = _latest_version(alembic_engine, slug)
        nist, cis, iso = _parent_tags(alembic_engine, entry_id, version)
        assert nist == seed.nist_csf_subcategories, f"{slug}: nist_csf_subcategories drift"
        assert cis == seed.cis_safeguards, f"{slug}: cis_safeguards drift"
        assert iso == seed.iso_27001_controls, f"{slug}: iso_27001_controls drift"

    # The T2-drift entry is repaired with its version UNCHANGED.
    repaired_id, repaired_version = _latest_version(alembic_engine, _DRIFT_SLUG)
    assert repaired_id == drift_id
    assert repaired_version == drift_version

    # Every OTHER (non-Epic-F) slug's version is unchanged.
    for slug in other_slugs:
        _id, version_after = _latest_version(alembic_engine, slug)
        assert version_after == versions_before[slug], f"{slug}: version changed unexpectedly"

    # The five slugs' non-tag/version/id/timestamp parent columns are unchanged.
    for slug in EPIC_F_SLUGS:
        old_id, old_version = epic_f_before[slug]
        new_id, new_version = _latest_version(alembic_engine, slug)
        after = _parent_row_dict(alembic_engine, new_id, new_version)
        for column, before_value in parent_snapshots_before[slug].items():
            assert after[column] == before_value, (
                f"{slug}.{column}: changed by the re-curation bump (id {old_id}, "
                f"{old_version}->{new_version})"
            )


# ---------------------------------------------------------------------------
# (d) fresh path: an un-dirtied DB migrated straight through PRE then REV converges
# to the identical assignments/tags/version numbers.
# ---------------------------------------------------------------------------


def test_fresh_path_converges_to_the_same_state(
    alembic_runner: MigrationContext,
    alembic_engine: Engine,
) -> None:
    alembic_runner.migrate_up_to(PRE)
    validated = _validated_seed()
    pre_versions = {slug: _latest_version(alembic_engine, slug)[1] for slug in EPIC_F_SLUGS}
    other_slugs = [s for s in validated if s not in EPIC_F_SLUGS]
    pre_other_versions = {s: _latest_version(alembic_engine, s)[1] for s in other_slugs}

    alembic_runner.migrate_up_to(REV)

    for slug in EPIC_F_SLUGS:
        entry_id, version = _latest_version(alembic_engine, slug)
        assert version == pre_versions[slug] + 1, f"{slug}: fresh-path version mismatch"
        got = {
            r[1]: _assignment_row_to_comparable(r)
            for r in _assignment_rows(alembic_engine, entry_id, version)
        }
        want = {
            a.sub_function.value: _assignment_seed_to_comparable(a)
            for a in validated[slug].assignments
        }
        assert got == want, f"{slug}: fresh-path assignment set mismatch"
        nist, cis, iso = _parent_tags(alembic_engine, entry_id, version)
        seed = validated[slug]
        assert (nist, cis, iso) == (
            seed.nist_csf_subcategories,
            seed.cis_safeguards,
            seed.iso_27001_controls,
        )

    for slug in other_slugs:
        entry_id, version = _latest_version(alembic_engine, slug)
        assert version == pre_other_versions[slug], f"{slug}: fresh-path version changed"
        nist, cis, iso = _parent_tags(alembic_engine, entry_id, version)
        seed = validated[slug]
        assert (nist, cis, iso) == (
            seed.nist_csf_subcategories,
            seed.cis_safeguards,
            seed.iso_27001_controls,
        )


# ---------------------------------------------------------------------------
# (e) downgrade: documented no-op -- every control row and assignment is byte-identical
# before and after migrate_down_to(PRE).
# ---------------------------------------------------------------------------


def test_downgrade_is_a_documented_noop(
    alembic_runner: MigrationContext,
    alembic_engine: Engine,
) -> None:
    alembic_runner.migrate_up_to(PRE)
    validated = _validated_seed()
    _dirty_epic_f_slugs(alembic_engine, validated)
    _dirty_t2_drift_entry(alembic_engine, validated)
    alembic_runner.migrate_up_to(REV)

    entries_before, assignments_before = _dump_all_rows(alembic_engine)

    alembic_runner.migrate_down_to(PRE)

    entries_after, assignments_after = _dump_all_rows(alembic_engine)
    assert entries_after == entries_before
    assert assignments_after == assignments_before
