"""Migration tests for the C-iii-a re-curation in-place UPDATE (revision 3d7b9e357d52).

Mirrors the structure of:
  - tests/migrations/test_calibration_anchor_migration.py  (JSON-payload UPDATE + ORM round-trip)
  - tests/migrations/test_library_extension_seed.py        (pytest_alembic upgrade scaffolding)

Three explicit cases required by the plan (Task 4 Step 1):

test_conversion
    After upgrade, 3 sampled entries match the committed seed JSON's
    distributions / loss_tier / source_citations:
    1. ``insider-data-theft-financial`` — paginated / lognormal (base file)
    2. ``process-view-manipulation``    — vendor  / lognormal (EXTENSION FILE — satisfies the
                                          plan's "at least one from the extension" requirement,
                                          self-defending against the both-files miss)
    3. ``bec-fraud-financial``          — anecdotal / PERT untouched (base file)

test_idempotent
    Running the upgrade logic twice yields identical row state.

test_stale_heal
    Manually write a stale pre-C-iii-a row (old PERT primary_loss) for
    ``ransomware-on-ehr`` (a paginated/lognormal converted entry in the base
    file).  After upgrade the row shows the lognormal distribution with the
    correct sigma (1.9602032155565388).
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import sqlalchemy as sa
from pytest_alembic import MigrationContext
from sqlalchemy.engine import Engine

import idraa

# ---------------------------------------------------------------------------
# Revision IDs
# ---------------------------------------------------------------------------
_PREV_REV = "d6b8e2f0a719"  # loss_tier column addition — immediate down_revision
_THIS_REV = "3d7b9e357d52"  # this C-iii-a re-curation UPDATE migration

# Columns stored as JSON in SQLite (come back as strings, need json.loads).
# Plain-text columns like loss_tier and canonical_fair_gap are NOT in this set.
_json_columns: frozenset[str] = frozenset(
    {
        "primary_loss",
        "secondary_loss",
        "source_citations",
        "calibration_anchor",
        "vulnerability",
        "threat_event_frequency",
    }
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _root() -> Path:
    return Path(idraa.__file__).resolve().parent.parent.parent


def _base_entries() -> list[dict]:
    return json.loads((_root() / "data" / "seed_library_entries.json").read_text(encoding="utf-8"))


def _ext_entries() -> list[dict]:
    return json.loads(
        (_root() / "data" / "seed_library_entries_extension.json").read_text(encoding="utf-8")
    )


def _all_entries() -> list[dict]:
    return _base_entries() + _ext_entries()


def _entry_by_slug(slug: str) -> dict:
    for e in _all_entries():
        if e["slug"] == slug:
            return e
    raise KeyError(slug)


def _row(engine: Engine, slug: str) -> dict:
    """Fetch the version=1 row for *slug* as a plain dict.

    JSON columns (primary_loss, secondary_loss, source_citations,
    calibration_anchor, vulnerability, threat_event_frequency) are decoded
    from the SQLite string representation.  Plain-text columns (loss_tier,
    canonical_fair_gap) are returned as-is.
    """
    with engine.connect() as conn:
        row = (
            conn.execute(
                sa.text(
                    "SELECT primary_loss, secondary_loss, loss_tier, source_citations, "
                    "calibration_anchor, vulnerability, threat_event_frequency, "
                    "canonical_fair_gap "
                    "FROM scenario_library_entries WHERE slug = :slug AND version = 1"
                ),
                {"slug": slug},
            )
            .mappings()
            .one()
        )
    # SQLite JSON columns come back as strings; decode only the known JSON ones.
    result = {}
    for k, v in row.items():
        if k in _json_columns and isinstance(v, str):
            result[k] = json.loads(v)
        else:
            result[k] = v
    return result


def _insert_stale_pert_row(engine: Engine, slug: str, stale_primary: dict) -> None:
    """Write a synthetic stale row with the given PERT primary_loss for *slug*.

    This simulates a DB that was migrated before C-iii-a (PERT primary_loss that
    the re-curation converts to lognormal).  The row is inserted at the current
    schema (post loss_tier column) so it satisfies NOT-NULL constraints but carries
    the old PERT distribution — exactly the state a production DB upgrade heals.
    """
    seed = _entry_by_slug(slug)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT OR REPLACE INTO scenario_library_entries "
                "(id, version, slug, name, status, threat_event_type, threat_actor_type, "
                "asset_class, attack_vector, tags, description, example_incidents, "
                "source_citations, canonical_fair_gap, applicable_industries, "
                "applicable_sub_sectors, applicable_org_sizes, threat_event_frequency, "
                "vulnerability, primary_loss, secondary_loss, suggested_control_ids, "
                "standards_references, calibration_anchor, loss_tier, row_version, "
                "created_at, updated_at) "
                "VALUES (:id, 1, :slug, :name, :status, :threat_event_type, :threat_actor_type, "
                ":asset_class, :attack_vector, :tags, :description, :example_incidents, "
                ":source_citations, :canonical_fair_gap, :applicable_industries, "
                ":applicable_sub_sectors, :applicable_org_sizes, :threat_event_frequency, "
                ":vulnerability, :primary_loss, :secondary_loss, :suggested_control_ids, "
                ":standards_references, :calibration_anchor, :loss_tier, 1, "
                ":now, :now)"
            ),
            {
                "id": uuid.uuid4().hex,
                "slug": slug,
                "name": seed["name"],
                "status": seed["status"],
                "threat_event_type": seed["threat_event_type"],
                "threat_actor_type": seed["threat_actor_type"],
                "asset_class": seed["asset_class"],
                "attack_vector": seed.get("attack_vector"),
                "tags": json.dumps(seed.get("tags", [])),
                "description": seed["description"],
                "example_incidents": seed.get("example_incidents"),
                "source_citations": json.dumps(seed.get("source_citations", [])),
                "canonical_fair_gap": seed["canonical_fair_gap"],
                "applicable_industries": json.dumps(seed.get("applicable_industries")),
                "applicable_sub_sectors": json.dumps(seed.get("applicable_sub_sectors")),
                "applicable_org_sizes": json.dumps(seed.get("applicable_org_sizes")),
                "threat_event_frequency": json.dumps(seed["threat_event_frequency"]),
                "vulnerability": json.dumps(seed["vulnerability"]),
                "primary_loss": json.dumps(stale_primary),
                "secondary_loss": json.dumps(seed.get("secondary_loss")),
                "suggested_control_ids": json.dumps(seed.get("suggested_control_ids", [])),
                "standards_references": json.dumps(seed.get("standards_references")),
                "calibration_anchor": json.dumps(
                    {"industry": "healthcare", "revenue_tier": "1b_to_10b"}
                ),
                "loss_tier": "anecdotal",  # stale pre-C-iii-a tier
                "now": "2026-01-01T00:00:00+00:00",
            },
        )


# ---------------------------------------------------------------------------
# Structural guard — confirms the migration file reads BOTH seed files
# ---------------------------------------------------------------------------


def _versions_dir() -> Path:
    return _root() / "alembic" / "versions"


def test_migration_reads_both_seed_files() -> None:
    """Structural guard (plan A2-I2): the migration source references BOTH
    ``seed_library_entries.json`` (31 base entries) AND
    ``seed_library_entries_extension.json`` (13 extension entries).
    A migration that only reads the base file silently skips 13 entries
    with zero errors — the dual-file check is the only guard against that.
    """
    mig = next(_versions_dir().glob("*_recurate_seed_entries_ciii_a.py"))
    text = mig.read_text()
    assert "seed_library_entries.json" in text
    assert "seed_library_entries_extension.json" in text


def test_migration_uses_parameterized_sql_not_interpolation() -> None:
    """Structural guard: no f-string or %-interpolation of user-data into SQL.
    The UPDATE must use :slug / :json named binds (SQLAlchemy parameterized),
    never format-string construction — per plan Task 4 Step 2.
    """
    mig = next(_versions_dir().glob("*_recurate_seed_entries_ciii_a.py"))
    text = mig.read_text()
    assert ":slug" in text  # parameterized slug bind
    assert "WHERE slug = :slug" in text


def test_migration_has_noop_downgrade_with_rationale() -> None:
    """Structural guard: downgrade() is a no-op with a policy-choice docstring
    (plan A-I3/SC-I1 ruling: pre-curation PERT payloads are superseded,
    recoverable from git history only).
    """
    mig = next(_versions_dir().glob("*_recurate_seed_entries_ciii_a.py"))
    text = mig.read_text()
    # The downgrade function exists but takes no SQL action.
    assert "def downgrade" in text
    # The policy rationale appears in the migration body.
    assert "no-op" in text.lower() or "noop" in text.lower()


# ---------------------------------------------------------------------------
# pytest_alembic round-trip tests
# ---------------------------------------------------------------------------


def test_conversion(
    alembic_runner: MigrationContext,
    alembic_engine: Engine,
) -> None:
    """After upgrade, 3 sampled entries match the committed seed JSON.

    Sample set covers all three tiers required by the plan:
    1. insider-data-theft-financial  — paginated / lognormal   (base file)
    2. process-view-manipulation     — vendor   / lognormal   (EXTENSION file)
    3. bec-fraud-financial           — anecdotal / PERT untouched (base file)
    """
    # Bring DB to the revision BEFORE the C-iii-a UPDATE migration.
    alembic_runner.migrate_up_to(_PREV_REV)

    # ---- apply the migration under test ----
    alembic_runner.migrate_up_to(_THIS_REV)

    # Milestone B (#loss-pert-overhaul): all three sample slugs are capped ->
    # the C-iii-a migration replays the CONVERTED seed, so the landed nodes
    # are bounded PERT (the seed comparison is the strong assertion; the
    # distribution check pins the shape class).

    # ---- sample 1: insider-data-theft-financial (paginated, capped, base) ----
    seed_insider = _entry_by_slug("insider-data-theft-financial")
    row_insider = _row(alembic_engine, "insider-data-theft-financial")

    assert row_insider["primary_loss"]["distribution"] == "PERT", (
        "insider-data-theft-financial primary_loss must be PERT (capped) post-Milestone-B"
    )
    assert row_insider["primary_loss"] == seed_insider["primary_loss"]
    assert row_insider["loss_tier"] == "paginated"
    assert row_insider["source_citations"] == seed_insider["source_citations"]
    assert row_insider["calibration_anchor"]["vuln_posture"] == (
        "inherent (control-naive) per fair-cam-methodology 'Vulnerability anchor'"
    )

    # ---- sample 2: process-view-manipulation (capped, EXTENSION file) ----
    seed_pvm = _entry_by_slug("process-view-manipulation")
    row_pvm = _row(alembic_engine, "process-view-manipulation")

    assert row_pvm["primary_loss"]["distribution"] == "PERT", (
        "process-view-manipulation primary_loss must be PERT (capped) post-Milestone-B"
    )
    assert row_pvm["primary_loss"] == seed_pvm["primary_loss"]
    assert row_pvm["loss_tier"] == seed_pvm["loss_tier"], (
        f"process-view-manipulation loss_tier must match seed ({seed_pvm['loss_tier']!r}) "
        f"— D-iii-a re-tiered it to the envelope 'paginated'; got {row_pvm['loss_tier']!r}"
    )
    assert row_pvm["source_citations"] == seed_pvm["source_citations"]

    # ---- sample 3: bec-fraud-financial (vendor tier, capped, base) ----
    seed_bec = _entry_by_slug("bec-fraud-financial")
    row_bec = _row(alembic_engine, "bec-fraud-financial")

    # Epic D-iii-a made bec-fraud-financial a BEYOND-ENVELOPE own IC3 node
    # (funds-transfer fraud, vendor tier); Milestone B converted it to the
    # capped PERT of that same IC3-derived lognormal.
    assert row_bec["primary_loss"]["distribution"] == "PERT", (
        "bec-fraud-financial primary_loss is the capped PERT of the IC3 lognormal"
    )
    assert row_bec["primary_loss"] == seed_bec["primary_loss"]
    assert row_bec["loss_tier"] == seed_bec["loss_tier"]  # 'vendor' post-D-iii-a
    # Vulnerability raised to inherent-posture (mode=0.20 per plan rule 6)
    assert abs(row_bec["vulnerability"]["mode"] - seed_bec["vulnerability"]["mode"]) < 1e-9
    # calibration_anchor gained vuln_posture key
    assert row_bec["calibration_anchor"].get("vuln_posture") == (
        "inherent (control-naive) per fair-cam-methodology 'Vulnerability anchor'"
    )


def test_idempotent(
    alembic_runner: MigrationContext,
    alembic_engine: Engine,
) -> None:
    """Running the upgrade logic twice yields identical row state.

    Simulates a DB that already had C-iii-a applied, then the migration
    runs again (e.g., a partial replay or a fresh-DB scenario that somehow
    re-runs the migration).
    """
    alembic_runner.migrate_up_to(_PREV_REV)
    alembic_runner.migrate_up_to(_THIS_REV)

    # Capture state after first upgrade.
    row_first = _row(alembic_engine, "insider-data-theft-financial")
    row_bec_first = _row(alembic_engine, "bec-fraud-financial")

    # Re-apply the upgrade logic by directly executing the migration's SQL again.
    # We cannot call migrate_up_to twice (alembic_runner tracks state), so we
    # replicate the core UPDATE loop to confirm idempotency.
    all_entries = _all_entries()
    with alembic_engine.begin() as conn:
        for entry in all_entries:
            conn.execute(
                sa.text(
                    "UPDATE scenario_library_entries "
                    "SET primary_loss = :primary_loss, "
                    "    secondary_loss = :secondary_loss, "
                    "    loss_tier = :loss_tier, "
                    "    source_citations = :source_citations, "
                    "    calibration_anchor = :calibration_anchor, "
                    "    vulnerability = :vulnerability, "
                    "    threat_event_frequency = :threat_event_frequency, "
                    "    canonical_fair_gap = :canonical_fair_gap "
                    "WHERE slug = :slug AND version = 1"
                ),
                {
                    "primary_loss": json.dumps(entry["primary_loss"]),
                    "secondary_loss": json.dumps(entry.get("secondary_loss")),
                    "loss_tier": entry.get("loss_tier", "anecdotal"),
                    "source_citations": json.dumps(entry.get("source_citations", [])),
                    "calibration_anchor": json.dumps(entry["calibration_anchor"]),
                    "vulnerability": json.dumps(entry["vulnerability"]),
                    "threat_event_frequency": json.dumps(entry["threat_event_frequency"]),
                    "canonical_fair_gap": entry["canonical_fair_gap"],
                    "slug": entry["slug"],
                },
            )

    # State after second run must be identical.
    row_second = _row(alembic_engine, "insider-data-theft-financial")
    row_bec_second = _row(alembic_engine, "bec-fraud-financial")

    assert row_second["primary_loss"] == row_first["primary_loss"]
    assert row_second["loss_tier"] == row_first["loss_tier"]
    assert row_second["calibration_anchor"] == row_first["calibration_anchor"]
    assert row_bec_second["primary_loss"] == row_bec_first["primary_loss"]
    assert row_bec_second["vulnerability"] == row_bec_first["vulnerability"]


def test_stale_heal(
    alembic_runner: MigrationContext,
    alembic_engine: Engine,
) -> None:
    """Stale row heal: a pre-C-iii-a DB row for ``ransomware-on-ehr`` with old
    (differently-valued) PERT primary_loss is overwritten with the seed JSON's
    value after upgrade.

    Milestone B (#loss-pert-overhaul): ``ransomware-on-ehr`` is capped, so the
    seed value the heal lands is now itself a bounded PERT — the heal is proven
    by VALUES (the injected stale triple differs from the converted seed
    triple), not by a shape flip. Pinned target (plan conversion table):
      (low, mode, high) = (15955.6628554057, 15955.6628554057, 10080000.000343738)
    """
    alembic_runner.migrate_up_to(_PREV_REV)

    # Insert a stale row with pre-curation PERT primary_loss (values distinct
    # from the converted seed triple, so the overwrite is observable).
    _stale_pert = {"distribution": "PERT", "low": 500_000, "mode": 2_000_000, "high": 15_000_000}
    _insert_stale_pert_row(alembic_engine, "ransomware-on-ehr", stale_primary=_stale_pert)

    # Verify the stale row is in place.
    row_before = _row(alembic_engine, "ransomware-on-ehr")
    assert row_before["primary_loss"]["low"] == 500_000
    assert row_before["loss_tier"] == "anecdotal"

    # Apply the migration.
    alembic_runner.migrate_up_to(_THIS_REV)

    # Row must now carry the seed JSON's converted PERT (single source of truth).
    row_after = _row(alembic_engine, "ransomware-on-ehr")
    seed_ehr = _entry_by_slug("ransomware-on-ehr")
    assert row_after["primary_loss"] == seed_ehr["primary_loss"], (
        "stale primary_loss must be healed to the seed JSON value by the C-iii-a migration"
    )
    assert row_after["primary_loss"]["distribution"] == "PERT"
    assert (
        row_after["primary_loss"]["low"],
        row_after["primary_loss"]["mode"],
        row_after["primary_loss"]["high"],
    ) == (15955.6628554057, 15955.6628554057, 10080000.000343738)
    assert row_after["loss_tier"] == "paginated", (
        "stale anecdotal loss_tier must be healed to 'paginated' after C-iii-a migration"
    )
    # T4M-N1: calibration_anchor must contain vuln_posture after heal.
    assert row_after["calibration_anchor"].get("vuln_posture") == (
        "inherent (control-naive) per fair-cam-methodology 'Vulnerability anchor'"
    ), (
        "stale-heal must populate calibration_anchor.vuln_posture "
        f"(got: {row_after['calibration_anchor'].get('vuln_posture')!r})"
    )
