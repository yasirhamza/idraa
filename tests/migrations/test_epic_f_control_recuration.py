"""Epic F (#192) control-library re-curation migration (f6a2b0d4e8c3) + parent-tag
convergence. Follows tests/migrations/test_epic_f_library_curation.py's dirty-then-run
pattern (itself following tests/migrations/test_dataquality_followups.py) and the
alembic_runner/alembic_engine fixtures (tests/migrations/conftest.py). No revision
literal appears below beyond the name of the migration module being loaded by path --
PRE/REV come from the loaded module itself (S2-2/A2-5); no migration test shells out
to git (S2-1).
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import uuid
from pathlib import Path
from types import ModuleType
from typing import cast

import sqlalchemy as sa
from alembic.config import Config
from pytest_alembic import MigrationContext
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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

# N-1 (methodology NICE): a second, prod-observed drift shape on a DIFFERENT tag
# column (nist_csf_subcategories, not cis_safeguards) and a different slug -- EDR
# is missing RS.MI-2 on a real prod-shaped DB (Task 7 review I-1 probe).
_NIST_DRIFT_SLUG = "endpoint-detection-response"
_NIST_DRIFT_DROPPED_TAG = "RS.MI-2"

# Spec NICE: the (c) snapshot compares every parent column EXCEPT these -- derived
# by exclusion from the actual row dict below, not a hardcoded inclusion list, so a
# future new column is covered automatically instead of silently skipped.
_EXCLUDED_PARENT_COLUMNS: frozenset[str] = frozenset(
    {
        "id",
        "version",
        "created_at",
        "updated_at",
        "nist_csf_subcategories",
        "cis_safeguards",
        "iso_27001_controls",
    }
)

# Spec IMPORTANT: captured via `git show 8ccac47e:data/seed_control_library_entries.json`
# (the merge-base before Task 6's citation edit) -- a literal, not a git call in this
# test (S2-1). Used to dirty SAT's Communication citation back to its PRE-Epic-F text
# so the post-upgrade "non-scoring label is gone" assertion is not vacuous.
_SAT_PRE_EPIC_F_COMMUNICATION_CITATION = (
    "Mechanism (DSC, non-scoring): Security-awareness training communicates expected "
    "secure behaviors and threat recognition to the workforce, improving human decision "
    "quality. FAIR-CAM home = Decision Support Control (situational-awareness "
    "communication). Primary source: CIS Controls v8 Safeguards 14.1/14.2; NIST CSF "
    "PR.AT-1/PR.AT-2; cross-referenced MITRE ATT&CK M1017 (User Training), "
    "https://attack.mitre.org/mitigations/M1017/ (accessed 2026-06-30). Value 0.7 expert "
    "estimate — no population efficacy figure; awareness is decision-support, "
    "deliberately NOT modeled as a vulnerability-reducing LEC scorer."
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
            if slug == "security-awareness-training":
                # Spec IMPORTANT: also roll the Communication citation back to its
                # pre-Epic-F text (the stale "(DSC, non-scoring)" label) -- the JSON's
                # assignment minus the Epic F member alone still carries the CURRENT
                # (already-fixed) citation, which would make the post-upgrade
                # "label is gone" assertion vacuous.
                pre_assignments = [
                    a.model_copy(
                        update={"capability_citations": [_SAT_PRE_EPIC_F_COMMUNICATION_CITATION]}
                    )
                    if a.sub_function.value == "dsc_prev_communication"
                    else a
                    for a in pre_assignments
                ]
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


_TAG_COLUMNS: tuple[str, ...] = ("nist_csf_subcategories", "cis_safeguards", "iso_27001_controls")


def _dirty_tag_drift(
    engine: Engine,
    validated: dict[str, ControlLibraryEntrySeed],
    slug: str,
    column: str,
    dropped: str,
) -> tuple[str, int]:
    """Drop `dropped` from `slug`'s stored `column` (one of the three framework-tag
    columns), reproducing a pre-existing pilot/T1/T2 grounding-tag drift the JSON
    still carries (migration docstring, effect 2). Returns (id, version) -- version is
    unchanged by dirtying, since this repair never bumps."""
    assert column in _TAG_COLUMNS, column
    entry_id, version = _latest_version(engine, slug)
    seed = validated[slug]
    current = list(getattr(seed, column))
    assert dropped in current, f"{slug}.{column}: {dropped!r} not in the current seed value"
    drifted = [t for t in current if t != dropped]
    assert len(drifted) == len(current) - 1
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                f"UPDATE control_library_entries SET {column} = :v "  # noqa: S608 - column is asserted against the fixed _TAG_COLUMNS allowlist above, never externally supplied
                "WHERE id = :eid AND version = :ver"
            ),
            {"v": json.dumps(drifted), "eid": entry_id, "ver": version},
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
    _dirty_tag_drift(
        alembic_engine, validated, _DRIFT_SLUG, "cis_safeguards", _DRIFT_DROPPED_CIS_TAG
    )

    # Spec IMPORTANT: prove the dirtied PRE-state actually carries the stale
    # "(DSC, non-scoring)" label -- otherwise the post-upgrade "label is gone"
    # assertion below would be vacuous (it would pass even if the migration never
    # touched the citation).
    sat_old_id, sat_old_version = before["security-awareness-training"]
    pre_sat_rows = {
        r[1]: _assignment_row_to_comparable(r)
        for r in _assignment_rows(alembic_engine, sat_old_id, sat_old_version)
    }
    pre_comm_citations = cast("tuple[str, ...]", pre_sat_rows["dsc_prev_communication"][5])
    assert any("non-scoring" in c for c in pre_comm_citations), (
        "dirtying must plant the stale (DSC, non-scoring) label before the upgrade"
    )

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

        # All (re-)inserted ids are 32 lowercase hex chars (uuid4().hex form).
        for row in _assignment_rows(alembic_engine, new_id, new_version):
            rid = str(row[0])
            assert rid == uuid.UUID(hex=rid).hex, f"{slug}: not 32 lowercase hex chars: {rid!r}"

    # SAT's Communication citation no longer carries the stale "non-scoring" label
    # (the pre-upgrade presence check above proves this assertion is not vacuous).
    sat_id, sat_version = _latest_version(alembic_engine, "security-awareness-training")
    post_sat_rows = {
        r[1]: _assignment_row_to_comparable(r)
        for r in _assignment_rows(alembic_engine, sat_id, sat_version)
    }
    post_comm_citations = cast(
        "tuple[str, ...]", post_sat_rows["dsc_prev_communication"][5]
    )  # capability_citations tuple
    assert not any("non-scoring" in c for c in post_comm_citations)


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
    drift_id, drift_version = _dirty_tag_drift(
        alembic_engine, validated, _DRIFT_SLUG, "cis_safeguards", _DRIFT_DROPPED_CIS_TAG
    )

    # Snapshot every OTHER slug's version, and the five slugs' non-tag columns, before
    # the upgrade (post-dirty).
    other_slugs = [s for s in validated if s not in EPIC_F_SLUGS]
    versions_before = {s: _latest_version(alembic_engine, s)[1] for s in other_slugs}
    parent_snapshots_before = {
        slug: {
            k: v
            for k, v in _parent_row_dict(alembic_engine, *epic_f_before[slug]).items()
            if k not in _EXCLUDED_PARENT_COLUMNS
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
# N-1 (methodology NICE): a second, prod-observed drift shape -- a NIST-column drift
# on a slug outside EPIC_F_SLUGS -- is synced with no version bump.
# ---------------------------------------------------------------------------


def test_dirty_then_upgrade_repairs_nist_drift_without_bump(
    alembic_runner: MigrationContext,
    alembic_engine: Engine,
) -> None:
    """Pins the prod-observed case directly (Task 7 review I-1 probe): EDR's stored
    nist_csf_subcategories is missing RS.MI-2 (a T2-era grounding-tag addition the
    prior re-curation migrations never synced to the stored column). The migration
    must repair it in place and must NOT bump EDR's version -- EDR is explicitly not
    one of the five re-curated slugs."""
    alembic_runner.migrate_up_to(PRE)
    validated = _validated_seed()
    entry_id, version = _dirty_tag_drift(
        alembic_engine,
        validated,
        _NIST_DRIFT_SLUG,
        "nist_csf_subcategories",
        _NIST_DRIFT_DROPPED_TAG,
    )

    alembic_runner.migrate_up_to(REV)

    new_id, new_version = _latest_version(alembic_engine, _NIST_DRIFT_SLUG)
    assert new_id == entry_id, "NIST-drift repair must not move the parent row"
    assert new_version == version, f"{_NIST_DRIFT_SLUG}: tag-only repair must not bump version"
    nist, _cis, _iso = _parent_tags(alembic_engine, new_id, new_version)
    assert nist == validated[_NIST_DRIFT_SLUG].nist_csf_subcategories
    assert _NIST_DRIFT_DROPPED_TAG in nist


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
    _dirty_tag_drift(
        alembic_engine, validated, _DRIFT_SLUG, "cis_safeguards", _DRIFT_DROPPED_CIS_TAG
    )
    alembic_runner.migrate_up_to(REV)

    entries_before, assignments_before = _dump_all_rows(alembic_engine)

    alembic_runner.migrate_down_to(PRE)

    entries_after, assignments_after = _dump_all_rows(alembic_engine)
    assert entries_after == entries_before
    assert assignments_after == assignments_before


# ---------------------------------------------------------------------------
# N-2 (methodology NICE): an adopted org control pinned to a bumped Epic F entry
# keeps its own stored values (and function assignments) byte-identical across the
# upgrade, and is resync-stale afterwards (the #438 consequence the docstring
# documents). Uses the REAL adopt_from_library / resync_info service calls (not a
# hand-rolled insert) via a short-lived AsyncSession bound to the SAME sqlite file
# the sync alembic_engine reads -- alembic's own migration runner already drives an
# async engine through asyncio.run() per step (alembic/env.py), so these helpers
# spin up their OWN separate asyncio.run() call rather than making the test function
# itself async (which would nest event loops under pytest-asyncio's auto mode and
# collide with alembic_runner.migrate_up_to's asyncio.run()).
# ---------------------------------------------------------------------------


def _row_dict(engine: Engine, table: str, id_hex: str) -> dict[str, object]:
    """Raw-SQL whole-row fetch by id. `table` is always one of the fixed literals this
    test module passes, never externally supplied."""
    with engine.connect() as conn:
        row = (
            conn.execute(
                sa.text(f"SELECT * FROM {table} WHERE id = :id"),  # noqa: S608 - table is a fixed literal, see docstring
                {"id": id_hex},
            )
            .mappings()
            .first()
        )
    assert row is not None, f"{table}: no row with id={id_hex}"
    return dict(row)


def _control_function_assignment_rows(engine: Engine, control_id: str) -> list[tuple[object, ...]]:
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT * FROM control_function_assignments WHERE control_id = :cid "
                "ORDER BY sub_function"
            ),
            {"cid": control_id},
        ).fetchall()
    return [tuple(r) for r in rows]


async def _adopt_control_for_probe(async_url: str, entry_id_hex: str, version: int) -> str:
    """Adopt `entry_id_hex`@`version` via the REAL adopt_from_library service call (not
    a hand-rolled insert), so the probe exercises production behaviour. A fresh
    Organization satisfies the RESTRICT FK; Task 7 never touches organizations or
    controls, so this is otherwise isolated from the migration under test. Returns the
    new Control's id (hex)."""
    from idraa.db import _install_sqlite_pragmas, strict_json_dumps
    from idraa.models.enums import IndustryType, OrganizationSize
    from idraa.models.organization import Organization
    from idraa.services.controls import adopt_from_library

    engine = create_async_engine(async_url, json_serializer=strict_json_dumps)
    _install_sqlite_pragmas(engine)
    try:
        sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with sm() as db:
            org = Organization(
                name="Epic F N-2 probe org",
                industry_type=IndustryType.INFORMATION,
                organization_size=OrganizationSize.MEDIUM,
            )
            db.add(org)
            await db.flush()
            control = await adopt_from_library(
                db,
                org_id=org.id,
                user_id=None,
                entry_id=uuid.UUID(hex=entry_id_hex),
                version=version,
            )
            await db.commit()
            return control.id.hex
    finally:
        await engine.dispose()


async def _resync_stale_for_control(async_url: str, control_id_hex: str) -> bool:
    """resync_info(...).stale for the adopted control, via the REAL service call."""
    from idraa.db import _install_sqlite_pragmas, strict_json_dumps
    from idraa.models.control import Control
    from idraa.services.control_resync import resync_info

    engine = create_async_engine(async_url, json_serializer=strict_json_dumps)
    _install_sqlite_pragmas(engine)
    try:
        sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with sm() as db:
            control = await db.get(Control, uuid.UUID(hex=control_id_hex))
            assert control is not None
            info = await resync_info(db, control)
            assert info is not None
            return info.stale
    finally:
        await engine.dispose()


def test_adopted_control_survives_upgrade_unchanged_and_goes_resync_stale(
    alembic_runner: MigrationContext,
    alembic_engine: Engine,
    alembic_config: Config,
) -> None:
    alembic_runner.migrate_up_to(PRE)
    entry_id, pre_version = _latest_version(alembic_engine, "security-information-event-management")
    async_url = alembic_config.get_main_option("sqlalchemy.url")
    assert async_url is not None

    control_id = asyncio.run(_adopt_control_for_probe(async_url, entry_id, pre_version))
    controls_before = _row_dict(alembic_engine, "controls", control_id)
    assignments_before = _control_function_assignment_rows(alembic_engine, control_id)

    # Sanity control: not yet stale (pinned version == entry's current version).
    assert asyncio.run(_resync_stale_for_control(async_url, control_id)) is False

    alembic_runner.migrate_up_to(REV)

    controls_after = _row_dict(alembic_engine, "controls", control_id)
    assignments_after = _control_function_assignment_rows(alembic_engine, control_id)
    assert controls_after == controls_before, "controls row mutated by the library migration"
    assert assignments_after == assignments_before, (
        "control_function_assignments mutated by the library migration"
    )

    assert asyncio.run(_resync_stale_for_control(async_url, control_id)) is True
