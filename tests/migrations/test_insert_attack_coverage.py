"""Attack-coverage gap-fill migration test: the two insert-if-absent
migrations (rev1 entries, rev2 ATT&CK mappings) land the 9 new attack-coverage
library entries + their ATT&CK mappings (+ 3 ICS-twin rows on EXISTING
entries) on an EXISTING DB that already ran through ``d9e5a3c7f2b4``.

**CRITICAL -- un-masking the test:** on a fresh alembic test DB, an ancestor
migration (``0897a0ff350e``) reads the LIVE ``seed_library_entries_extension.json``
and already inserts all 71 entries incl. the 9 new attack-coverage slugs, and
``d9e5a3c7f2b4`` PERT-converts them -- so rev1's INSERT would be a no-op and
this test would assert nothing. We reproduce the genuine PROD state by
DELETING the 9 new slugs (version=1) + their overrides + any of their
mapping rows AFTER migrating to ``d9e5a3c7f2b4``, then running ONLY the two
attack-coverage migrations -- exactly the state a real prod DB that ran
``d9e5a3c7f2b4`` before Task 1/2 appended the 9 entries + their mappings
would be in. The 3 ICS-twin HOST entries (``watering-hole-industry-targeted``,
``it-ot-bridge-compromise``, ``oem-remote-maintenance-abuse``) + their
historical mapping rows STAY untouched (they already exist in prod).

**BLOCKER regression coverage:** rev2's downgrade must be technique-scoped,
NOT entry-scoped -- the 3 ICS-twin hosts carry historical mapping rows
(3 / 4 / 3, per ``data/seed_attack_full_mappings.json``) that an entry-scoped
delete-all would destroy on downgrade. This test captures each host's mapping
count BEFORE rev2 runs and asserts the EXACT same count is retained after
rev2's downgrade.

**Task-3 review [Important] regression coverage:**
``test_fresh_boot_destructive_wiper_lands_catastrophic`` below exercises the
genuine FRESH-BOOT path (migrate an empty DB straight to head -- NOT the
delete-then-reinsert prod-simulation the main test above uses) and asserts
``destructive-wiper-nationstate`` lands ``loss_shape == 'catastrophic'``.
This is the path the main test above does NOT cover: on a fresh DB, ancestor
``0897a0ff350e`` inserts the 9 new slugs before the ``loss_shape`` column
exists, so rev1's already-present branch must insert-OR-CORRECT rather than
bare-skip, or ``destructive-wiper-nationstate`` silently ships
``loss_shape='capped'``.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import sqlalchemy as sa
from pytest_alembic import MigrationContext
from sqlalchemy.engine import Engine

import idraa
from idraa.services.seed_library_loader import LibraryEntrySeed

_PRE_REV = "d9e5a3c7f2b4"  # single head before this epic's migrations
_ENTRIES_REV = "63cfe62ef5a7"
_MAPPINGS_REV = "e7d8e05ede6b"
_HEAD = _MAPPINGS_REV  # currently the single alembic head

_NEW_SLUGS = (
    "edge-ransomware-perimeter-gateway",
    "edge-espionage-nationstate",
    "edge-device-orb-foothold",
    "transient-cyber-asset-ot-intrusion",
    "browser-zeroday-driveby",
    "email-client-zeroclick-espionage",
    "removable-media-airgap-ot",
    "ot-wireless-field-network-compromise",
    "destructive-wiper-nationstate",
)

_CATASTROPHIC_SLUGS = frozenset({"destructive-wiper-nationstate"})

# ICS-twin host entries: pre-existing entries that gain exactly one new ICS
# mapping row each from this epic, per design doc Sec 6.1.
_ICS_TWIN_HOSTS: dict[str, tuple[str, str]] = {
    "watering-hole-industry-targeted": ("ics", "T0817"),
    "it-ot-bridge-compromise": ("ics", "T0865"),
    "oem-remote-maintenance-abuse": ("ics", "T0886"),
}


def _root() -> Path:
    return Path(idraa.__file__).resolve().parent.parent.parent


def _versions_dir() -> Path:
    return _root() / "alembic" / "versions"


def _extension() -> list[dict]:
    return json.loads((_root() / "data" / "seed_library_entries_extension.json").read_text())


def _avgapfill_mappings() -> list[dict]:
    payload = json.loads((_root() / "data" / "seed_attack_avgapfill_full.json").read_text())
    return payload["mappings"]


def _count_entries(engine: Engine) -> int:
    with engine.connect() as conn:
        return conn.execute(
            sa.text("SELECT COUNT(*) FROM scenario_library_entries WHERE version = 1")
        ).scalar_one()


def _slugs(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        return {
            r[0]
            for r in conn.execute(
                sa.text("SELECT slug FROM scenario_library_entries WHERE version = 1")
            ).fetchall()
        }


def _entry_id(engine: Engine, slug: str) -> str:
    with engine.connect() as conn:
        row = conn.execute(
            sa.text(
                "SELECT id FROM scenario_library_entries "
                "WHERE slug = :slug ORDER BY version DESC LIMIT 1"
            ),
            {"slug": slug},
        ).first()
    assert row is not None, f"entry slug {slug!r} not found"
    return str(row[0])


def _mapping_count_for_entry(engine: Engine, entry_id: str) -> int:
    with engine.connect() as conn:
        return conn.execute(
            sa.text(
                "SELECT COUNT(*) FROM library_entry_attack_mappings WHERE library_entry_id = :eid"
            ),
            {"eid": entry_id},
        ).scalar_one()


def _delete_new_slugs_and_their_data(engine: Engine) -> None:
    """Reproduce the genuine prod state: delete the 9 new slugs (version=1),
    any overrides referencing them, and any mapping rows referencing them, so
    the attack-coverage migrations have something real to insert. The 3
    ICS-twin HOST entries + their historical mappings are untouched -- they
    already existed in prod before this epic."""
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "DELETE FROM library_entry_attack_mappings "
                "WHERE library_entry_id IN ("
                "  SELECT id FROM scenario_library_entries "
                "  WHERE slug IN :slugs AND version = 1"
                ")"
            ).bindparams(sa.bindparam("slugs", _NEW_SLUGS, expanding=True))
        )
        conn.execute(
            sa.text(
                "DELETE FROM scenario_library_overrides "
                "WHERE library_entry_id IN ("
                "  SELECT id FROM scenario_library_entries "
                "  WHERE slug IN :slugs AND version = 1"
                ")"
            ).bindparams(sa.bindparam("slugs", _NEW_SLUGS, expanding=True))
        )
        conn.execute(
            sa.text(
                "DELETE FROM scenario_library_entries WHERE slug IN :slugs AND version = 1"
            ).bindparams(sa.bindparam("slugs", _NEW_SLUGS, expanding=True))
        )


def test_insert_attack_coverage_entries_and_mappings_on_existing_db(
    alembic_runner: MigrationContext,
    alembic_engine: Engine,
) -> None:
    # Migrate to the pre-epic head, then reproduce the genuine prod state by
    # deleting the 9 new slugs that a fresh-DB ancestor migration would
    # already have inserted from the (now-updated) live extension JSON.
    alembic_runner.migrate_up_to(_PRE_REV)
    _delete_new_slugs_and_their_data(alembic_engine)
    pre_count = _count_entries(alembic_engine)
    assert set(_NEW_SLUGS).isdisjoint(_slugs(alembic_engine)), (
        "attack-coverage slugs still present after simulated-prod delete"
    )

    # Capture each ICS-twin host's PRE-EXISTING mapping count before rev2
    # runs -- this is the retention baseline for the BLOCKER regression
    # check at downgrade time.
    host_pre_counts = {
        slug: _mapping_count_for_entry(alembic_engine, _entry_id(alembic_engine, slug))
        for slug in _ICS_TWIN_HOSTS
    }
    assert host_pre_counts == {
        "watering-hole-industry-targeted": 3,
        "it-ot-bridge-compromise": 4,
        "oem-remote-maintenance-abuse": 3,
    }, f"unexpected historical ICS-twin host mapping counts: {host_pre_counts}"

    # --- rev1: insert the 9 entries ---
    alembic_runner.migrate_up_to(_ENTRIES_REV)
    assert _count_entries(alembic_engine) == pre_count + 9
    assert set(_NEW_SLUGS) <= _slugs(alembic_engine), "not all 9 new slugs inserted"

    with alembic_engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT id, slug, loss_tier, loss_shape, loss_form_profile, "
                "primary_loss, secondary_loss FROM scenario_library_entries "
                "WHERE slug IN :slugs AND version = 1"
            ).bindparams(sa.bindparam("slugs", _NEW_SLUGS, expanding=True))
        ).fetchall()
    assert len(rows) == 9
    ext_by_slug = {e["slug"]: e for e in _extension() if e["slug"] in _NEW_SLUGS}
    for row in rows:
        eid, slug, loss_tier, loss_shape, loss_form_profile, primary_loss, secondary_loss = row
        eid_str = str(eid)
        assert len(eid_str) == 32 and "-" not in eid_str, (
            f"{slug}: id not 32-hex no-hyphen: {eid_str!r}"
        )
        assert loss_tier == "paginated", (
            f"{slug}: expected loss_tier='paginated', got {loss_tier!r}"
        )
        expected_shape = "catastrophic" if slug in _CATASTROPHIC_SLUGS else "capped"
        assert loss_shape == expected_shape, (
            f"{slug}: expected loss_shape={expected_shape!r}, got {loss_shape!r}"
        )
        lfp = (
            json.loads(loss_form_profile)
            if isinstance(loss_form_profile, str)
            else loss_form_profile
        )
        assert lfp, f"{slug}: loss_form_profile must be non-empty"
        pl = json.loads(primary_loss) if isinstance(primary_loss, str) else primary_loss
        assert pl == ext_by_slug[slug]["primary_loss"], (
            f"{slug}: primary_loss mismatch vs seed JSON"
        )
        sl = json.loads(secondary_loss) if isinstance(secondary_loss, str) else secondary_loss
        assert sl == ext_by_slug[slug]["secondary_loss"], (
            f"{slug}: secondary_loss mismatch vs seed JSON"
        )
        LibraryEntrySeed.model_validate(ext_by_slug[slug])

    # --- rev2: insert the mappings ---
    alembic_runner.migrate_up_to(_MAPPINGS_REV)

    slug_by_id = {str(r[0]): r[1] for r in rows}
    with alembic_engine.connect() as conn:
        mapping_rows = conn.execute(
            sa.text(
                "SELECT library_entry_id FROM library_entry_attack_mappings "
                "WHERE library_entry_id IN :ids"
            ).bindparams(sa.bindparam("ids", tuple(slug_by_id), expanding=True))
        ).fetchall()
    mapped_slugs = {slug_by_id[str(r[0])] for r in mapping_rows}
    assert mapped_slugs == set(_NEW_SLUGS), (
        f"not every new slug has a mapping row; missing: {set(_NEW_SLUGS) - mapped_slugs}"
    )
    all_mappings = _avgapfill_mappings()
    expected_new_entry_rows = sum(1 for m in all_mappings if m["entry_slug"] in _NEW_SLUGS)
    assert len(mapping_rows) == expected_new_entry_rows

    # Each ICS-twin host gained EXACTLY its one ICS technique row.
    with alembic_engine.connect() as conn:
        for slug, (domain, technique_id) in _ICS_TWIN_HOSTS.items():
            host_id = _entry_id(alembic_engine, slug)
            new_count = _mapping_count_for_entry(alembic_engine, host_id)
            assert new_count == host_pre_counts[slug] + 1, (
                f"{slug}: expected {host_pre_counts[slug] + 1} mapping rows after rev2, "
                f"got {new_count}"
            )
            tech_row = conn.execute(
                sa.text("SELECT id FROM attack_techniques WHERE domain = :d AND technique_id = :t"),
                {"d": domain, "t": technique_id},
            ).first()
            assert tech_row is not None, f"catalog technique {domain}/{technique_id} not found"
            got = conn.execute(
                sa.text(
                    "SELECT COUNT(*) FROM library_entry_attack_mappings "
                    "WHERE library_entry_id = :eid AND technique_id = :tid"
                ),
                {"eid": host_id, "tid": str(tech_row[0])},
            ).scalar_one()
            assert got == 1, (
                f"{slug}: expected exactly 1 row for {domain}/{technique_id}, got {got}"
            )

    # --- Idempotency: genuinely re-running rev1+rev2 inserts once (relative
    # counts, not pinned literals). pytest-alembic's migrate_up_to is a no-op
    # when the DB is already at/past the target revision, so a genuine
    # re-execution of upgrade() requires a real downgrade first, then
    # pre-seeding one duplicate-shaped row to exercise the insert-if-absent
    # guard, then re-upgrading.
    alembic_runner.migrate_down_to(_PRE_REV)
    assert _count_entries(alembic_engine) == pre_count
    assert set(_NEW_SLUGS).isdisjoint(_slugs(alembic_engine))

    sample_slug = _NEW_SLUGS[0]
    sample_ext = next(e for e in _extension() if e["slug"] == sample_slug)
    now = "2026-01-01T00:00:00+00:00"
    with alembic_engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO scenario_library_entries "
                "(id, version, slug, name, status, threat_event_type, threat_actor_type, "
                " asset_class, attack_vector, tags, description, example_incidents, "
                " source_citations, canonical_fair_gap, applicable_industries, "
                " applicable_sub_sectors, applicable_org_sizes, threat_event_frequency, "
                " vulnerability, primary_loss, secondary_loss, suggested_control_ids, "
                " standards_references, calibration_anchor, loss_tier, loss_shape, "
                " loss_form_profile, source, row_version, created_at, updated_at) "
                "VALUES "
                "(:id, 1, :slug, :name, :status, :threat_event_type, :threat_actor_type, "
                " :asset_class, :attack_vector, :tags, :description, :example_incidents, "
                " :source_citations, :canonical_fair_gap, :applicable_industries, "
                " :applicable_sub_sectors, :applicable_org_sizes, :threat_event_frequency, "
                " :vulnerability, :primary_loss, :secondary_loss, :suggested_control_ids, "
                " :standards_references, :calibration_anchor, :loss_tier, :loss_shape, "
                " :loss_form_profile, 'seed', 1, :now, :now)"
            ),
            {
                "id": "aa" * 16,
                "slug": sample_slug,
                "name": sample_ext["name"],
                "status": sample_ext["status"],
                "threat_event_type": sample_ext["threat_event_type"],
                "threat_actor_type": sample_ext["threat_actor_type"],
                "asset_class": sample_ext["asset_class"],
                "attack_vector": sample_ext.get("attack_vector"),
                "tags": json.dumps(sample_ext.get("tags", [])),
                "description": sample_ext["description"],
                "example_incidents": sample_ext.get("example_incidents"),
                "source_citations": json.dumps(sample_ext.get("source_citations", [])),
                "canonical_fair_gap": sample_ext["canonical_fair_gap"],
                "applicable_industries": json.dumps(sample_ext.get("applicable_industries")),
                "applicable_sub_sectors": json.dumps(sample_ext.get("applicable_sub_sectors")),
                "applicable_org_sizes": json.dumps(sample_ext.get("applicable_org_sizes")),
                "threat_event_frequency": json.dumps(sample_ext["threat_event_frequency"]),
                "vulnerability": json.dumps(sample_ext["vulnerability"]),
                "primary_loss": json.dumps(sample_ext["primary_loss"]),
                "secondary_loss": json.dumps(sample_ext.get("secondary_loss")),
                "suggested_control_ids": json.dumps(sample_ext.get("suggested_control_ids", [])),
                "standards_references": json.dumps(sample_ext.get("standards_references")),
                "calibration_anchor": json.dumps(sample_ext["calibration_anchor"]),
                "loss_tier": sample_ext.get("loss_tier", "anecdotal"),
                "loss_shape": sample_ext.get("loss_shape", "capped"),
                "loss_form_profile": json.dumps(sample_ext.get("loss_form_profile", [])),
                "now": now,
            },
        )

    # Re-run rev1: must skip the pre-seeded slug (no duplicate, no error) and
    # insert the remaining 8.
    alembic_runner.migrate_up_to(_ENTRIES_REV)
    assert _count_entries(alembic_engine) == pre_count + 9
    with alembic_engine.connect() as conn:
        dup = conn.execute(
            sa.text(
                "SELECT COUNT(*) FROM scenario_library_entries WHERE slug = :slug AND version = 1"
            ),
            {"slug": sample_slug},
        ).scalar_one()
    assert dup == 1, f"expected no duplicate for pre-seeded slug {sample_slug!r}, got {dup} rows"

    # The downgrade+reupgrade cycle regenerated fresh uuid4() ids for the 8
    # non-pre-seeded slugs (the pre-seeded one kept its manually-assigned
    # "aaaa..." id) -- refresh the slug->id map against the current DB state
    # before checking mapping counts by id.
    with alembic_engine.connect() as conn:
        refreshed_rows = conn.execute(
            sa.text(
                "SELECT id, slug FROM scenario_library_entries WHERE slug IN :slugs AND version = 1"
            ).bindparams(sa.bindparam("slugs", _NEW_SLUGS, expanding=True))
        ).fetchall()
    slug_by_id = {str(r[0]): r[1] for r in refreshed_rows}
    assert len(slug_by_id) == 9

    # Re-run rev2: must skip any already-present mapping and insert the rest.
    alembic_runner.migrate_up_to(_MAPPINGS_REV)
    with alembic_engine.connect() as conn:
        remapped = conn.execute(
            sa.text(
                "SELECT COUNT(*) FROM library_entry_attack_mappings WHERE library_entry_id IN :ids"
            ).bindparams(sa.bindparam("ids", tuple(slug_by_id), expanding=True))
        ).scalar_one()
    assert remapped == expected_new_entry_rows, "re-running the mapping migration duplicated rows"
    for slug in _ICS_TWIN_HOSTS:
        host_id = _entry_id(alembic_engine, slug)
        assert _mapping_count_for_entry(alembic_engine, host_id) == host_pre_counts[slug] + 1, (
            f"{slug}: idempotent re-run produced a wrong mapping count"
        )

    # --- [BLOCKER regression] rev2 downgrade must be technique-scoped: the 3
    # ICS-twin hosts must RETAIN their exact pre-rev2 mapping count. An
    # entry-scoped delete-all would wipe their historical rows too. ---
    alembic_runner.migrate_down_to(_ENTRIES_REV)
    for slug, expected in host_pre_counts.items():
        host_id = _entry_id(alembic_engine, slug)
        got = _mapping_count_for_entry(alembic_engine, host_id)
        assert got == expected, (
            f"BLOCKER regression: {slug} expected to retain {expected} historical mapping "
            f"rows after rev2 downgrade, got {got} -- rev2 downgrade is not technique-scoped"
        )
    with alembic_engine.connect() as conn:
        leftover_new = conn.execute(
            sa.text(
                "SELECT COUNT(*) FROM library_entry_attack_mappings WHERE library_entry_id IN :ids"
            ).bindparams(sa.bindparam("ids", tuple(slug_by_id), expanding=True))
        ).scalar_one()
    assert leftover_new == 0, "rev2 downgrade left orphaned mapping rows for the 9 new entries"

    # --- rev1 downgrade: removes exactly the 9 entries + their overrides;
    # leaves all other entries untouched. ---
    alembic_runner.migrate_down_to(_PRE_REV)
    assert _count_entries(alembic_engine) == pre_count
    assert set(_NEW_SLUGS).isdisjoint(_slugs(alembic_engine)), (
        "attack-coverage slugs survived rev1 downgrade"
    )
    for slug, expected in host_pre_counts.items():
        host_id = _entry_id(alembic_engine, slug)
        got = _mapping_count_for_entry(alembic_engine, host_id)
        assert got == expected, f"{slug}: rev1 downgrade disturbed unrelated host mapping rows"


def test_fresh_boot_destructive_wiper_lands_catastrophic(
    alembic_runner: MigrationContext,
    alembic_engine: Engine,
) -> None:
    """[Task-3 review regression, methodology + architect [Important]] --
    genuine FRESH-BOOT path (migrate an EMPTY DB straight to head), NOT the
    delete-then-reinsert prod-simulation the test above exercises.

    On a fresh DB, ancestor migration ``0897a0ff350e`` reads the live
    extension JSON and inserts all 9 new attack-coverage slugs *before* the
    ``loss_shape`` column exists (added later by ``b8c4f2e6a1d3``, whose
    fixed catastrophic-shortlist backfill in turn predates
    ``destructive-wiper-nationstate``'s addition to that shortlist). Without
    rev1's insert-OR-CORRECT fix, rev1's already-present branch would just
    ``continue`` and skip ``destructive-wiper-nationstate`` entirely, leaving
    it stuck at the ``loss_shape`` column's server_default of ``'capped'``
    instead of ``'catastrophic'`` -- silently capping its catastrophic tail
    at instantiation (``services/wizard_finalize.py:390``). This is the
    failure mode every fresh-volume deployment hits (``docker-entrypoint.sh``
    runs migrations on every boot: ephemeral instances, e2e harnesses,
    DR rebuilds) -- the existing test above only covers the (unaffected)
    prod-upgrade path.

    This test FAILS without the rev1 insert-OR-CORRECT fix (asserts
    ``loss_shape == 'capped'`` was observed pre-fix, so the current
    assertion below regresses it) and PASSES with it.
    """
    alembic_runner.migrate_up_to(_HEAD)

    with alembic_engine.connect() as conn:
        row = conn.execute(
            sa.text(
                "SELECT loss_shape FROM scenario_library_entries WHERE slug = :slug AND version = 1"
            ),
            {"slug": "destructive-wiper-nationstate"},
        ).first()
    assert row is not None, "destructive-wiper-nationstate not present after fresh-boot upgrade"
    assert row[0] == "catastrophic", (
        "FRESH-BOOT regression: destructive-wiper-nationstate landed "
        f"loss_shape={row[0]!r} instead of 'catastrophic' -- rev1's "
        "already-present branch is not correcting loss_shape on the "
        "fresh-boot skip-path (Task-3 review [Important])"
    )


def test_migration_new_slugs_tuple_matches_pinned_literal() -> None:
    """[Spec-compliance Minor, Task-3 review] -- the module docstring in
    ``63cfe62ef5a7`` claims a test "enforces" that its pinned ``_NEW_SLUGS``
    tuple and the extension JSON "stay in sync". The other assertions in
    this file only verify the sub direction indirectly (every name in
    ``_NEW_SLUGS`` ends up in the DB, which requires it to have existed in
    the JSON). This test closes the loop the same way the C-iii-b precedent
    does (``test_ciiib_expansion_seed.py::test_migration_new_slugs_tuple_matches_json``):
    live-import the migration module's ``_NEW_SLUGS`` and assert it equals
    this test file's independently-maintained copy (same 9 slugs, duplicated
    at module scope above) AND that every one of those 9 slugs is actually
    present in the extension JSON.

    **Documented limitation:** this cannot detect a hypothetical 10th
    attack-coverage-flavored slug that gets appended to the extension JSON
    without ALSO being added to ``_NEW_SLUGS`` in both the migration and this
    test file -- the JSON carries no per-entry epic tag, so "the
    attack-coverage slugs" cannot be identified structurally from the JSON
    alone. A 10th slug added this way would simply never be looked at by
    either _NEW_SLUGS copy and would slip through silently (it would still
    get inserted by ``0897a0ff350e`` on a fresh DB, just never
    insert-OR-CORRECTed by this migration). Catching that class of drift
    would require either a per-entry epic tag in the JSON schema or a
    pinned total-entry-count literal maintained by every future epic that
    touches this file -- both out of scope for this fix.
    """
    mig_path = next(_versions_dir().glob("63cfe62ef5a7_*.py"))
    spec = importlib.util.spec_from_file_location("_attack_coverage_mig", mig_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]

    pinned = frozenset(mod._NEW_SLUGS)
    assert pinned == set(_NEW_SLUGS), (
        f"Migration _NEW_SLUGS mismatch vs this test's pinned copy.\n"
        f"  In migration but not in test: {pinned - set(_NEW_SLUGS)}\n"
        f"  In test but not in migration: {set(_NEW_SLUGS) - pinned}"
    )

    ext_slugs = {e["slug"] for e in _extension()}
    missing_from_json = set(_NEW_SLUGS) - ext_slugs
    assert not missing_from_json, (
        f"_NEW_SLUGS entries absent from the extension JSON: {missing_from_json}"
    )
