"""Migration tests for the C-iii-b expansion seed (60ff242180f6).

Mirrors the test_library_extension_seed.py scaffolding pattern.

Assertions:
  - insert round-trip: upgrade → 38 new rows present with correct loss_tier /
    distributions; sample includes ≥1 entry from each of batches A, B, C
  - downgrade → 38 gone; the 44 pre-existing rows untouched
  - re-upgrade → back to 82
  - idempotent re-run: pre-seeding one of the 38 slugs before upgrade yields 82
    total, no duplicate
  - no-hyphen UUID guard: every inserted id contains no '-'
  - LibraryEntrySeed validation pass over every inserted payload
  - _NEW_SLUGS tuple in migration file matches the 38 slugs in the seed JSON
"""

from __future__ import annotations

import json
from pathlib import Path

import sqlalchemy as sa
from pytest_alembic import MigrationContext
from sqlalchemy.engine import Engine

import idraa
from idraa.services.seed_library_loader import LibraryEntrySeed

# Revision IDs.
_C_IIIA_REV = "3d7b9e357d52"  # down_revision of this migration (C-iii-a re-curation)
_SEED_REV = "60ff242180f6"  # this migration (C-iii-b expansion)


def _root() -> Path:
    return Path(idraa.__file__).resolve().parent.parent.parent


def _versions_dir() -> Path:
    return _root() / "alembic" / "versions"


def _extension() -> list[dict]:
    return json.loads((_root() / "data" / "seed_library_entries_extension.json").read_text())


def _base() -> list[dict]:
    return json.loads((_root() / "data" / "seed_library_entries.json").read_text())


# The 13 original extension slugs seeded by 0897a0ff350e.
_ORIG_13 = frozenset(
    {
        "ransomware-on-control-layer",
        "process-view-manipulation",
        "field-instrument-spoofing",
        "oem-remote-maintenance-abuse",
        "grid-protective-relay-manipulation",
        "pipeline-scada-integrity",
        "chemical-process-safety-attack",
        "accidental-insider-exposure",
        "web-app-exploitation",
        "third-party-processor-breach",
        "retail-pos-card-skimming",
        "public-sector-targeted-intrusion",
        "logistics-disruption",
    }
)

# The 3 WS3b energy/manufacturing third-party-revenue slugs seeded by 4b7f9e2a1c83.
# These are NOT C-iii-b slugs: exclude them from _NEW_38_SLUGS so the
# C-iii-b migration's pinned _NEW_SLUGS tuple (38 entries) stays consistent.
_WS3B_SLUGS = frozenset(
    {
        "tolling-plant-ransomware-customer-liability",
        "pipeline-nomination-scada-curtailment-shipper-penalty",
        "energy-settlement-platform-tampering-offtaker-liability",
    }
)

# The 38 new C-iii-b slugs, PINNED as a literal frozenset (Epic D-iii-b,
# #497). Previously computed as ``_extension() - _ORIG_13 - _WS3B_SLUGS``,
# but that derivation would silently grow to 46 once D-iii-b appended its 8
# new slugs to the extension JSON, breaking the ``pinned == _NEW_38_SLUGS``
# assert in test_migration_new_slugs_tuple_matches_json below (which compares
# against the migration file's own literal tuple). Captured from the JSON
# BEFORE the D-iii-b entries were appended -- do NOT re-derive from the JSON.
_NEW_38_SLUGS = frozenset(
    {
        "agri-coop-bec-fraud",
        "agri-equipment-physical-tamper",
        "branch-atm-physical-tamper",
        "competitor-trade-secret-recruit",
        "crop-science-ip-exfiltration",
        "datacenter-physical-breach",
        "education-campus-facility-tamper",
        "education-research-ip-exfiltration",
        "education-student-records-insider",
        "energy-billing-system-tamper",
        "financial-call-center-social-eng",
        "financial-transaction-tampering",
        "food-cold-chain-ransomware",
        "food-recall-data-tampering",
        "gov-citizen-portal-ddos",
        "gov-employee-insider-leak",
        "gov-records-tampering",
        "healthcare-record-alteration",
        "healthcare-staff-credential-phish",
        "hospitality-booking-ddos-peak-season",
        "hospitality-guest-data-insider",
        "hospitality-loyalty-account-takeover",
        "hospitality-pos-card-skimming",
        "ip-theft-by-competitor",
        "logistics-tms-data-tampering",
        "logistics-warehouse-physical-intrusion",
        "manufacturing-billing-fraud",
        "manufacturing-facility-sabotage",
        "professional-office-physical-theft",
        "professional-payroll-bec",
        "retail-ecommerce-checkout-ddos",
        "retail-store-employee-fraud",
        "saas-revenue-outage-sabotage",
        "telecom-bgp-route-hijack",
        "telecom-ddos-core-network",
        "telecom-field-cabinet-tamper",
        "telecom-sim-swap-fraud",
        "telecom-subscriber-data-breach",
    }
)

# The 8 new D-iii-b attested vertical entries (#497), seeded by
# f4a1c2b3d4e5. Added to ``_slugs_to_remove`` (below) so the simulated
# pre-C-iii-b DB state stays faithful now that the extension JSON holds 62
# entries (54 present when this file's assertions were pinned + 8 new).
_D_IIIB_SLUGS = frozenset(
    {
        "physician-practice-clearinghouse-revenue-disruption",
        "law-enforcement-records-extortion-breach",
        "casino-ransomware-operational-disruption",
        "telecom-lawful-intercept-nationstate-compromise",
        "law-firm-privileged-data-ransomware-extortion",
        "k12-edtech-vendor-breach",
        "higher-ed-insider-ddos",
        "judiciary-court-system-ransomware",
    }
)

# The 9 new attack-coverage gap-fill entries (#529), seeded by an
# insert-if-absent migration in the same style as f4a1c2b3d4e5. Added to
# ``_slugs_to_remove`` (below) so the simulated pre-C-iii-b DB state stays
# faithful now that the extension JSON holds 71 entries (62 present when
# this file's assertions were pinned + 9 new).
_ATTACK_COVERAGE_SLUGS = frozenset(
    {
        "edge-ransomware-perimeter-gateway",
        "edge-espionage-nationstate",
        "edge-device-orb-foothold",
        "transient-cyber-asset-ot-intrusion",
        "browser-zeroday-driveby",
        "email-client-zeroclick-espionage",
        "removable-media-airgap-ot",
        "ot-wireless-field-network-compromise",
        "destructive-wiper-nationstate",
    }
)

# One representative slug from each batch (A=paginated, B=anecdotal telecom/food/hosp,
# C=remaining anecdotal) to verify round-trip coverage across all three batches.
_BATCH_A_SAMPLE = "telecom-subscriber-data-breach"  # paginated
_BATCH_B_SAMPLE = "telecom-ddos-core-network"  # anecdotal, telecom (batch B)
_BATCH_C_SAMPLE = "competitor-trade-secret-recruit"  # anecdotal, technology_saas (batch C)


# ---------------------------------------------------------------------------
# Structural tests (no alembic runner — fast)
# ---------------------------------------------------------------------------


def test_migration_reads_extension_file_with_loss_tier():
    """Structural guard: the migration reads the extension file, includes
    loss_tier in the INSERT column list, uses uuid4().hex (no str(uuid4())),
    and has the correct down_revision and _NEW_SLUGS pin."""
    mig = next(_versions_dir().glob("*_seed_ciiib_expansion.py"))
    text = mig.read_text()
    # Reads the extension file.
    assert "seed_library_entries_extension.json" in text
    # NOT the base file (must only read the extension).
    assert '"data" / "seed_library_entries.json"' not in text
    # loss_tier column is explicitly included in the INSERT.
    assert "loss_tier" in text
    # No-hyphen UUID: uses uuid4().hex.
    # Check for the positive pattern: uuid4().hex must appear.
    assert "uuid.uuid4().hex" in text
    # Check the INSERT value binding uses .hex not str(): look for the assignment
    # pattern '": uuid.uuid4().hex' (what the code actually does) and that the
    # INVERSE pattern '"id": str(uuid' or '"id": str(uuid4' does not appear.
    assert '"id": uuid.uuid4().hex' in text, "Migration must assign id as uuid.uuid4().hex"
    # Correct down_revision.
    assert f'down_revision = "{_C_IIIA_REV}"' in text
    # insert-if-absent guard.
    assert "WHERE version = 1" in text
    # _NEW_SLUGS pin: downgrade must NOT read the JSON.
    assert "_NEW_SLUGS" in text
    # source = 'seed' guard in downgrade.
    assert "source = 'seed'" in text


def test_migration_new_slugs_tuple_matches_json():
    """_NEW_SLUGS pinned in the migration file must equal the 38 new slugs
    actually present in the extension JSON (i.e. extension slugs minus the 13
    original ones).  A mismatch means the downgrade would delete wrong rows."""
    mig_path = next(_versions_dir().glob("*_seed_ciiib_expansion.py"))
    # Import the migration module to access _NEW_SLUGS.
    import importlib.util

    spec = importlib.util.spec_from_file_location("_ciiib_mig", mig_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    pinned = frozenset(mod._NEW_SLUGS)
    assert pinned == _NEW_38_SLUGS, (
        f"Migration _NEW_SLUGS mismatch.\n"
        f"  Pinned but not in JSON: {pinned - _NEW_38_SLUGS}\n"
        f"  In JSON but not pinned: {_NEW_38_SLUGS - pinned}"
    )


def test_all_new_entries_validate_through_library_entry_seed():
    """LibraryEntrySeed.model_validate must pass for every one of the 38 new
    extension entries — validates the payload shape before the migration even
    runs."""
    ext = _extension()
    for raw in ext:
        if raw["slug"] not in _NEW_38_SLUGS:
            continue
        LibraryEntrySeed.model_validate(raw)  # raises ValidationError on failure


def test_all_new_entries_have_expected_distribution_shapes():
    """Epic D-iii-a (envelope×share), re-scoped for Milestone B
    (#loss-pert-overhaul): every entry's loss shape follows its loss_shape
    class -- capped entries carry bounded PERT, the catastrophic shortlist
    keeps native lognormal. loss_tier ∈ {paginated (envelope), vendor
    (beyond-envelope IC3)}. secondary_loss follows the same class OR is None
    (an entry with no active SECONDARY forms has Σsecondary=0)."""
    from tests._loss_shape_helpers import CATASTROPHIC_SLUGS

    ext = _extension()
    for raw in ext:
        if raw["slug"] not in _NEW_38_SLUGS:
            continue
        loss_tier = raw["loss_tier"]
        assert loss_tier in ("paginated", "vendor"), (
            f"{raw['slug']}: post-D-iii-a loss_tier must be paginated|vendor, got {loss_tier!r}"
        )
        expected = "lognormal" if raw["slug"] in CATASTROPHIC_SLUGS else "PERT"
        assert raw["primary_loss"]["distribution"] == expected, (
            f"{raw['slug']}: primary_loss must be {expected}, "
            f"got {raw['primary_loss']['distribution']!r}"
        )
        sec = raw.get("secondary_loss")
        assert sec is None or sec["distribution"] == expected, (
            f"{raw['slug']}: secondary_loss must be {expected} or None, got {sec!r}"
        )


# ---------------------------------------------------------------------------
# Round-trip migration tests (require alembic_runner + alembic_engine)
# ---------------------------------------------------------------------------


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


def test_ciiib_expansion_round_trip(
    alembic_runner: MigrationContext,
    alembic_engine: Engine,
) -> None:
    """Full round-trip:
      - migrate to C-iii-a head; simulate the UAT DB state (44 entries) by
        deleting the 38 new slugs that 0897a0ff350e inserted from the live JSON
      - upgrade → 82 entries, all 38 new slugs present with correct loss_tier
      - downgrade → 44 entries, 38 gone, originals untouched
      - re-upgrade → 82 entries (idempotent recovery)

    Background: 0897a0ff350e reads the LIVE extension JSON at migration time.
    In the test environment the JSON already has 71 entries (38 new + 13
    original + 3 WS3b + 8 D-iii-b + 9 attack-coverage), so a fresh migration
    chain lands at 102 entries by the time it reaches 3d7b9e357d52 — the 38
    new slugs are inserted by 0897a0ff350e rather than 60ff242180f6.
    This test simulates the production scenario: a UAT DB that ran 0897a0ff350e
    when the extension JSON had only 13 entries (the 44-entry state), by deleting
    the 38 new slugs after migrating to 3d7b9e357d52.  Our new migration is then
    responsible for inserting them, which is its purpose on production DBs.

    Sampled coverage: verifies ≥1 entry from each of batch A, B, C and checks
    loss_tier + distribution shape on those samples.
    """
    # Migrate up to the C-iii-a head (will land at 102 entries on fresh DB
    # because 0897a0ff350e reads the live JSON with 71 entries: 13 orig + 38
    # C-iii-b + 3 WS3b + 8 D-iii-b + 9 attack-coverage).
    alembic_runner.migrate_up_to(_C_IIIA_REV)
    total_at_ciiia = _count_entries(alembic_engine)
    # Simulate the UAT DB state (44 entries): delete the 38 C-iii-b slugs AND
    # the 3 WS3b slugs AND the 8 D-iii-b slugs AND the 9 attack-coverage slugs
    # that were inserted by 0897a0ff350e from the updated extension JSON.
    # This recreates the state of a production DB that ran 0897a0ff350e
    # before the C-iii-b, WS3b, D-iii-b, and attack-coverage entries were
    # appended.
    _slugs_to_remove = tuple(_NEW_38_SLUGS | _WS3B_SLUGS | _D_IIIB_SLUGS | _ATTACK_COVERAGE_SLUGS)
    with alembic_engine.begin() as conn:
        conn.execute(
            sa.text(
                "DELETE FROM scenario_library_entries WHERE slug IN :slugs AND version = 1"
            ).bindparams(sa.bindparam("slugs", _slugs_to_remove, expanding=True))
        )
    pre_count = _count_entries(alembic_engine)
    assert pre_count == 44, (
        f"Expected 44 entries after removing C-iii-b + WS3b + D-iii-b + "
        f"attack-coverage slugs, got {pre_count} "
        f"(was {total_at_ciiia} after migrate_up_to {_C_IIIA_REV})"
    )
    assert _NEW_38_SLUGS.isdisjoint(_slugs(alembic_engine)), (
        "Some new C-iii-b slugs still present after simulating 44-entry UAT state"
    )

    # Upgrade → 82 entries.
    alembic_runner.migrate_up_to(_SEED_REV)
    post_count = _count_entries(alembic_engine)
    assert post_count == 82, f"Expected 82 entries after upgrade, got {post_count}"
    assert _slugs(alembic_engine) >= _NEW_38_SLUGS, "Not all 38 new slugs present after upgrade"

    # Verify batch A sample: paginated entry with lognormal distributions.
    with alembic_engine.connect() as conn:
        row = conn.execute(
            sa.text(
                "SELECT loss_tier, primary_loss, secondary_loss FROM scenario_library_entries "
                "WHERE slug = :slug AND version = 1"
            ),
            {"slug": _BATCH_A_SAMPLE},
        ).fetchone()
    assert row is not None, f"Batch A sample {_BATCH_A_SAMPLE!r} not found after upgrade"
    a_loss_tier = row[0]
    a_primary = json.loads(row[1]) if isinstance(row[1], str) else row[1]
    assert a_loss_tier == "paginated", (
        f"{_BATCH_A_SAMPLE}: expected loss_tier='paginated', got {a_loss_tier!r}"
    )
    # Milestone B (#loss-pert-overhaul): this capped slug is bounded PERT.
    assert a_primary["distribution"] == "PERT", (
        f"{_BATCH_A_SAMPLE}: expected PERT primary (capped), got {a_primary['distribution']!r}"
    )
    # Compare against seed JSON (not literals) to avoid double-maintenance.
    seed_a = next(e for e in _extension() if e["slug"] == _BATCH_A_SAMPLE)
    assert a_primary == seed_a["primary_loss"], (
        f"{_BATCH_A_SAMPLE}: stored primary_loss differs from seed JSON"
    )

    # Verify batch B sample: post-D-iii-a every entry is a lognormal envelope×share
    # loss node (paginated), superseding the pre-A1 anecdotal/PERT state.
    with alembic_engine.connect() as conn:
        row = conn.execute(
            sa.text(
                "SELECT loss_tier, primary_loss FROM scenario_library_entries "
                "WHERE slug = :slug AND version = 1"
            ),
            {"slug": _BATCH_B_SAMPLE},
        ).fetchone()
    assert row is not None, f"Batch B sample {_BATCH_B_SAMPLE!r} not found after upgrade"
    b_loss_tier = row[0]
    b_primary = json.loads(row[1]) if isinstance(row[1], str) else row[1]
    assert b_loss_tier == "paginated", (
        f"{_BATCH_B_SAMPLE}: expected loss_tier='paginated' (D-iii-a), got {b_loss_tier!r}"
    )
    # Milestone B (#loss-pert-overhaul): this capped slug is bounded PERT.
    assert b_primary["distribution"] == "PERT", (
        f"{_BATCH_B_SAMPLE}: expected PERT primary (capped), got {b_primary['distribution']!r}"
    )
    seed_b = next(e for e in _extension() if e["slug"] == _BATCH_B_SAMPLE)
    assert b_primary == seed_b["primary_loss"], (
        f"{_BATCH_B_SAMPLE}: stored primary_loss differs from seed JSON"
    )

    # Verify batch C sample: post-D-iii-a loss_tier ∈ {paginated, vendor}.
    with alembic_engine.connect() as conn:
        row = conn.execute(
            sa.text(
                "SELECT loss_tier FROM scenario_library_entries WHERE slug = :slug AND version = 1"
            ),
            {"slug": _BATCH_C_SAMPLE},
        ).fetchone()
    assert row is not None, f"Batch C sample {_BATCH_C_SAMPLE!r} not found after upgrade"
    assert row[0] in ("paginated", "vendor"), (
        f"{_BATCH_C_SAMPLE}: expected loss_tier paginated|vendor (D-iii-a), got {row[0]!r}"
    )

    # No-hyphen UUID guard: every id inserted by this migration must be hyphen-free.
    with alembic_engine.connect() as conn:
        ids = [
            r[0]
            for r in conn.execute(
                sa.text(
                    "SELECT id FROM scenario_library_entries WHERE slug IN :slugs AND version = 1"
                ).bindparams(sa.bindparam("slugs", tuple(_NEW_38_SLUGS), expanding=True))
            ).fetchall()
        ]
    assert len(ids) == 38, f"Expected 38 id rows, got {len(ids)}"
    for eid in ids:
        eid_str = str(eid)
        assert "-" not in eid_str, (
            f"Hyphen found in id {eid_str!r} — use uuid4().hex, not str(uuid4())"
        )

    # Downgrade → 44 entries, all 38 new slugs gone, 44 originals untouched.
    alembic_runner.migrate_down_one()
    down_count = _count_entries(alembic_engine)
    assert down_count == 44, f"Expected 44 entries after downgrade, got {down_count}"
    assert _NEW_38_SLUGS.isdisjoint(_slugs(alembic_engine)), (
        "Some C-iii-b slugs still present after downgrade"
    )
    # Verify all 44 pre-existing slugs survived.
    remaining = _slugs(alembic_engine)
    expected_orig = {e["slug"] for e in _base()} | _ORIG_13
    assert remaining == expected_orig, (
        f"Unexpected slug changes after downgrade.\n"
        f"  Missing: {expected_orig - remaining}\n"
        f"  Extra: {remaining - expected_orig}"
    )

    # Re-upgrade → 82 entries (idempotent recovery).
    alembic_runner.migrate_up_to(_SEED_REV)
    assert _count_entries(alembic_engine) == 82, (
        f"Expected 82 entries after re-upgrade, got {_count_entries(alembic_engine)}"
    )
    assert _slugs(alembic_engine) >= _NEW_38_SLUGS


def test_ciiib_expansion_idempotent_when_slug_already_present(
    alembic_runner: MigrationContext,
    alembic_engine: Engine,
) -> None:
    """Insert-if-absent: if one of the 38 new slugs is already present when the
    migration runs, it must NOT cause a duplicate or an error — the migration
    skips slugs already present and inserts only the missing ones.

    This covers the case where the migration is re-run (e.g., after a partial
    failure) or where a slug was pre-populated by another path."""
    import uuid as _uuid

    # Migrate to C-iii-a head and simulate UAT state (44 entries — see
    # test_ciiib_expansion_round_trip for the simulation rationale).
    # On a fresh DB, 0897a0ff350e inserts all 71 extension entries (102 total).
    # Delete C-iii-b slugs (38) AND WS3b slugs (3) AND D-iii-b slugs (8) AND
    # attack-coverage slugs (9) to reach the 44-entry UAT state.
    alembic_runner.migrate_up_to(_C_IIIA_REV)
    _slugs_to_remove = tuple(_NEW_38_SLUGS | _WS3B_SLUGS | _D_IIIB_SLUGS | _ATTACK_COVERAGE_SLUGS)
    with alembic_engine.begin() as conn:
        conn.execute(
            sa.text(
                "DELETE FROM scenario_library_entries WHERE slug IN :slugs AND version = 1"
            ).bindparams(sa.bindparam("slugs", _slugs_to_remove, expanding=True))
        )
    assert _count_entries(alembic_engine) == 44

    # Pre-seed one of the 38 new slugs (batch C sample — an anecdotal entry
    # so its asset_class / threat_event_type values are already CHECK-permitted).
    raw = next(e for e in _extension() if e["slug"] == _BATCH_C_SAMPLE)
    v = LibraryEntrySeed.model_validate(raw).model_dump()
    with alembic_engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO scenario_library_entries "
                "(id, version, slug, name, status, threat_event_type, threat_actor_type, "
                "asset_class, attack_vector, tags, description, example_incidents, "
                "source_citations, canonical_fair_gap, applicable_industries, "
                "applicable_sub_sectors, applicable_org_sizes, threat_event_frequency, "
                "vulnerability, primary_loss, secondary_loss, suggested_control_ids, "
                "standards_references, calibration_anchor, loss_tier, "
                "row_version, created_at, updated_at) "
                "VALUES (:id, 1, :slug, :name, :status, :threat_event_type, :threat_actor_type, "
                ":asset_class, :attack_vector, :tags, :description, :example_incidents, "
                ":source_citations, :canonical_fair_gap, :applicable_industries, "
                ":applicable_sub_sectors, :applicable_org_sizes, :threat_event_frequency, "
                ":vulnerability, :primary_loss, :secondary_loss, :suggested_control_ids, "
                ":standards_references, :calibration_anchor, :loss_tier, "
                "1, :now, :now)"
            ),
            {
                "id": _uuid.uuid4().hex,
                **{
                    k: json.dumps(val) if isinstance(val, (list, dict)) else val
                    for k, val in v.items()
                },
                "now": "2026-06-11T00:00:00+00:00",
            },
        )
    assert _count_entries(alembic_engine) == 45

    # Upgrade — inserts the remaining 37 C-iii-b slugs (skips pre-seeded slug) → 82.
    # WS3b slugs were deleted above; C-iii-b migration only inserts its pinned 38
    # C-iii-b slugs, so total = 44 + 1 pre-seeded + 37 inserted = 82.
    alembic_runner.migrate_up_to(_SEED_REV)
    assert _count_entries(alembic_engine) == 82, (
        f"Expected 82 after idempotent upgrade, got {_count_entries(alembic_engine)}"
    )
    with alembic_engine.connect() as conn:
        dup = conn.execute(
            sa.text(
                "SELECT COUNT(*) FROM scenario_library_entries WHERE slug = :slug AND version = 1"
            ),
            {"slug": _BATCH_C_SAMPLE},
        ).scalar_one()
    assert dup == 1, f"Expected no duplicate for {_BATCH_C_SAMPLE!r}, got {dup} rows"


def _insert_org(conn: sa.Connection) -> str:
    """Insert a minimal Organization row; return its UUID hex string.

    Mirrors the pattern from tests/migrations/test_pr_iota_control_reshape.py.
    All NOT NULL columns without server_default are supplied.
    """
    import uuid as _uuid

    org_id = _uuid.uuid4().hex
    conn.execute(
        sa.text(
            "INSERT INTO organizations "
            "(id, name, organization_size, industry_type, security_maturity, "
            "has_cyber_insurance, risk_appetite, compliance_requirements, "
            "regulatory_environment, technology_stack, geographic_regions, "
            "preferred_currency, preferred_language, "
            "created_at, updated_at) "
            "VALUES (:id, 'TestOrg', 'MEDIUM', 'manufacturing', 'BASIC', "
            "0, 'MODERATE', '[]', '[]', '[]', '[]', "
            "'USD', 'en', "
            "(CURRENT_TIMESTAMP), (CURRENT_TIMESTAMP))"
        ),
        {"id": org_id},
    )
    return org_id


def test_downgrade_pre_deletes_overrides_for_38_entries(
    alembic_runner: MigrationContext,
    alembic_engine: Engine,
) -> None:
    """PGA-ARCH-I1 regression: downgrade() must DELETE scenario_library_overrides
    rows referencing the 38 new entries BEFORE deleting those entries.

    SQLite FK enforcement is OFF during Alembic migrations (session-scoped
    PRAGMA; Alembic does not re-enable it), so without the explicit pre-delete
    in downgrade() the override rows would silently be orphaned.

    Test design:
      - Migrate to C-iii-b head (82 entries).
      - INSERT one override referencing a new entry (the experimental row) and
        one override referencing a pre-existing entry (the control row).
      - Downgrade back to C-iii-a.
      - Assert the experimental override is GONE (pre-delete fired).
      - Assert the control override is STILL PRESENT (pre-delete is scoped to
        the 38 new slugs only; pre-existing entries must not be touched).
    """
    import uuid as _uuid

    # Migrate all the way to the C-iii-b head.
    # On a fresh DB, 0897a0ff350e inserts all 71 extension entries (13 orig + 38
    # C-iii-b + 3 WS3b + 8 D-iii-b + 9 attack-coverage) → 31 + 71 = 102.  The
    # C-iii-b migration (60ff242180f6) then finds all its 38 slugs already
    # present → no-op → still 102.
    alembic_runner.migrate_up_to(_SEED_REV)
    assert _count_entries(alembic_engine) == 102

    # We need an organization row to satisfy the NOT NULL FK on
    # scenario_library_overrides.organization_id.
    with alembic_engine.begin() as conn:
        org_id = _insert_org(conn)

    # Look up the id of one of the 38 new entries (batch A sample) and one of
    # the 44 pre-existing entries to create two overrides: experimental + control.
    new_entry_slug = _BATCH_A_SAMPLE  # one of the 38 C-iii-b entries
    old_entry_slug = next(iter(_ORIG_13))  # one of the 13 original extension slugs

    with alembic_engine.connect() as conn:
        new_entry_id = conn.execute(
            sa.text("SELECT id FROM scenario_library_entries WHERE slug = :slug AND version = 1"),
            {"slug": new_entry_slug},
        ).scalar_one()
        old_entry_id = conn.execute(
            sa.text("SELECT id FROM scenario_library_entries WHERE slug = :slug AND version = 1"),
            {"slug": old_entry_slug},
        ).scalar_one()

    # Insert the experimental override (references a new C-iii-b entry → should
    # be deleted by downgrade's pre-delete step).
    exp_override_id = _uuid.uuid4().hex
    with alembic_engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO scenario_library_overrides "
                "(id, organization_id, library_entry_id, library_entry_version, "
                "reason, created_at, updated_at) "
                "VALUES (:id, :org_id, :entry_id, 1, "
                "'PGA-ARCH-I1 test — experimental override for a new C-iii-b entry', "
                "(CURRENT_TIMESTAMP), (CURRENT_TIMESTAMP))"
            ),
            {"id": exp_override_id, "org_id": org_id, "entry_id": new_entry_id},
        )

    # Insert the control override (references a pre-existing entry → must survive
    # downgrade; the pre-delete is scoped only to the 38 new slugs).
    # We use a DIFFERENT org_id to avoid hitting the UNIQUE constraint on
    # (organization_id, library_entry_id) — each org can have at most one override
    # per entry; the two overrides target different entries so they can share the
    # same org, but using a fresh org makes the intent clearer.
    with alembic_engine.begin() as conn:
        ctrl_org_id = _insert_org(conn)
    ctrl_override_id = _uuid.uuid4().hex
    with alembic_engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO scenario_library_overrides "
                "(id, organization_id, library_entry_id, library_entry_version, "
                "reason, created_at, updated_at) "
                "VALUES (:id, :org_id, :entry_id, 1, "
                "'PGA-ARCH-I1 test — control override for a pre-existing entry', "
                "(CURRENT_TIMESTAMP), (CURRENT_TIMESTAMP))"
            ),
            {"id": ctrl_override_id, "org_id": ctrl_org_id, "entry_id": old_entry_id},
        )

    # Verify both overrides exist before downgrade.
    with alembic_engine.connect() as conn:
        pre_count = conn.execute(
            sa.text("SELECT COUNT(*) FROM scenario_library_overrides")
        ).scalar_one()
    assert pre_count == 2, f"Expected 2 override rows before downgrade, got {pre_count}"

    # Downgrade → C-iii-a state (C-iii-b's 38 entries deleted; WS3b 3,
    # D-iii-b 8, and attack-coverage 9 remain).
    # On a fresh DB, 0897a0ff350e inserted all 71 extension entries (102 total).
    # C-iii-b downgrade deletes only its 38 pinned slugs → 102 - 38 = 64.
    alembic_runner.migrate_down_one()
    assert _count_entries(alembic_engine) == 64

    # Experimental override (referenced a new C-iii-b entry) must be GONE.
    with alembic_engine.connect() as conn:
        exp_remaining = conn.execute(
            sa.text("SELECT COUNT(*) FROM scenario_library_overrides WHERE id = :id"),
            {"id": exp_override_id},
        ).scalar_one()
    assert exp_remaining == 0, (
        "PGA-ARCH-I1 regression: experimental override for a C-iii-b entry "
        f"was NOT deleted by downgrade pre-delete (id={exp_override_id!r})"
    )

    # Control override (referenced a pre-existing entry) must SURVIVE.
    with alembic_engine.connect() as conn:
        ctrl_remaining = conn.execute(
            sa.text("SELECT COUNT(*) FROM scenario_library_overrides WHERE id = :id"),
            {"id": ctrl_override_id},
        ).scalar_one()
    assert ctrl_remaining == 1, (
        "PGA-ARCH-I1 regression: control override for a pre-existing entry "
        f"was unexpectedly deleted by downgrade pre-delete (id={ctrl_override_id!r})"
    )
