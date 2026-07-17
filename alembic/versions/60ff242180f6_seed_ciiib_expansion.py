"""seed C-iii-b expansion — 38 new scenario library archetypes

Revision ID: 60ff242180f6
Revises: 3d7b9e357d52
Create Date: 2026-06-11

Additive, insert-if-absent seed of the 38 new C-iii-b scenario library entries
appended to ``data/seed_library_entries_extension.json`` (which now contains
51 entries total: the original 13 seeded by ``0897a0ff350e`` plus the 38 new
archetypes authored in Tasks 3–5).

**Ordering / convergence guarantee:**
``down_revision`` is ``3d7b9e357d52`` (the C-iii-a re-curation migration,
currently the only head).  Fresh DB: the chain runs
  c1d2e3f4a5b6 → … → 0897a0ff350e → … → 3d7b9e357d52 → this migration,
landing on 31 + 13 + 38 = 82 published entries.
Existing UAT DB at ``3d7b9e357d52``: the 13 original extension slugs are
already present; the insert-if-absent guard skips them, inserting only the 38
new slugs → same 82-entry terminal state.

**T2 rebalance audit (ciiib-rebalance-decisions.md) ruled 0 trims/merges;
no deprecation UPDATEs in this migration.**  All 44 existing entries retain
``status = 'published'``.

**UUID format:** every inserted ``id`` uses ``uuid.uuid4().hex`` — a 32-char
no-hyphen hex string matching the canonical id format normalised in the
``e7d0c3a91f2b`` migration.  The ``UuidType(as_uuid=True)`` adapter binds hex
params without hyphens; a hyphenated ``str(uuid4())`` would cause 404 on every
id-based ORM query (detail page, wizard step-1→2 advance).  Use ONLY
``uuid4().hex`` — never ``str(uuid4())``.  The ``0897a0ff350e`` migration is
the authoritative precedent; ``c1d2e3f4a5b6`` predates the foot-gun fix and
must NOT be mirrored.

**Downgrade is a real scoped DELETE + source guard (SB-2c hardening):**
  DELETE WHERE slug IN (<38 pinned slugs>) AND version = 1 AND source = 'seed'
The ``source`` column exists (added by ``c5a2f17b9e34``; server_default='seed')
and the ``AND source = 'seed'`` guard prevents accidentally deleting a
user-imported entry that happened to collide with one of the 38 slugs.

**Reversibility asymmetry vs 3d7b9e357d52:**
The C-iii-a migration (``3d7b9e357d52``) has a deliberate no-op downgrade —
restoring 44 PERT payloads inline would dual-source them and invert the
"JSON is the single source of truth" guarantee.  This migration's downgrade
IS reversible because the operation is additive INSERT: the 38 rows can be
cleanly deleted without disturbing any other state.  The asymmetry is by
design: UPDATE-based migrations (in-place re-curation) are one-way;
INSERT-based migrations (additive seeding) are reversible.

**Idempotency:** each entry is INSERTed only if no row with that ``slug``
already exists at ``version = 1``.  Re-running upgrade, or running it on a DB
that already holds some of the 38 slugs (e.g., an interrupted run), is a
no-op for those slugs.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "60ff242180f6"
down_revision = "3d7b9e357d52"
branch_labels = None
depends_on = None


# The 38 new C-iii-b slugs, pinned as a literal tuple so the downgrade()
# never depends on the mutable JSON file.  The insert-if-absent guard in
# upgrade() reads the actual JSON, so the two sources must stay in sync — a
# tests/migrations/test_ciiib_expansion_seed.py assertion enforces this.
_NEW_SLUGS = (
    "telecom-subscriber-data-breach",
    "hospitality-pos-card-skimming",
    "hospitality-loyalty-account-takeover",
    "hospitality-guest-data-insider",
    "education-student-records-insider",
    "gov-citizen-portal-ddos",
    "gov-records-tampering",
    "gov-employee-insider-leak",
    "ip-theft-by-competitor",
    "manufacturing-billing-fraud",
    "healthcare-staff-credential-phish",
    "professional-payroll-bec",
    "energy-billing-system-tamper",
    "telecom-ddos-core-network",
    "telecom-sim-swap-fraud",
    "telecom-bgp-route-hijack",
    "telecom-field-cabinet-tamper",
    "food-cold-chain-ransomware",
    "food-recall-data-tampering",
    "agri-equipment-physical-tamper",
    "agri-coop-bec-fraud",
    "crop-science-ip-exfiltration",
    "hospitality-booking-ddos-peak-season",
    "education-research-ip-exfiltration",
    "logistics-tms-data-tampering",
    "logistics-warehouse-physical-intrusion",
    "competitor-trade-secret-recruit",
    "datacenter-physical-breach",
    "branch-atm-physical-tamper",
    "financial-transaction-tampering",
    "healthcare-record-alteration",
    "retail-ecommerce-checkout-ddos",
    "saas-revenue-outage-sabotage",
    "professional-office-physical-theft",
    "retail-store-employee-fraud",
    "manufacturing-facility-sabotage",
    "financial-call-center-social-eng",
    "education-campus-facility-tamper",
)


def _ext_path() -> Path:
    """Resolve the extension seed JSON via the project-root anchor.

    Mirrors the path-resolution pattern from ``0897a0ff350e``:
    parent-of-idraa-package, then up to the repo root.  Fallback to a
    ``__file__``-depth count for non-standard layouts (CI artefacts, packaged
    distributions).
    """
    import idraa

    project_root = Path(idraa.__file__).resolve().parent.parent.parent
    seed_path = project_root / "data" / "seed_library_entries_extension.json"
    if not seed_path.exists():
        seed_path = (
            Path(__file__).resolve().parent.parent.parent
            / "data"
            / "seed_library_entries_extension.json"
        )
    return seed_path


def upgrade() -> None:
    """Insert the 38 new C-iii-b extension entries, skipping any slug already
    present at version = 1.  Each payload is validated through
    ``LibraryEntrySeed.model_validate`` before insert so seed-data corruption
    surfaces at ``alembic upgrade`` rather than silently at first browse query.
    """
    from idraa.services.seed_library_loader import LibraryEntrySeed

    bind = op.get_bind()
    existing = {
        r[0]
        for r in bind.execute(
            sa.text("SELECT slug FROM scenario_library_entries WHERE version = 1")
        ).fetchall()
    }
    entries = json.loads(_ext_path().read_text(encoding="utf-8"))
    # Only consider the 38 new slugs; the original 13 are naturally in 'existing'
    # on an upgraded DB and will be skipped by the insert-if-absent guard below.
    new_slugs_set = set(_NEW_SLUGS)
    now = datetime.now(UTC).isoformat()
    for raw in entries:
        if raw["slug"] not in new_slugs_set:
            # Skip the 13 original extension entries (already seeded by 0897a0ff350e).
            continue
        if raw["slug"] in existing:
            # insert-if-absent: skip slugs already present at version = 1.
            continue
        entry = LibraryEntrySeed.model_validate(raw).model_dump()
        bind.execute(
            sa.text(
                """
            INSERT INTO scenario_library_entries
              (id, version, slug, name, status, threat_event_type,
               threat_actor_type, asset_class, attack_vector, tags,
               description, example_incidents, source_citations,
               canonical_fair_gap, applicable_industries,
               applicable_sub_sectors, applicable_org_sizes,
               threat_event_frequency, vulnerability, primary_loss,
               secondary_loss, suggested_control_ids, standards_references,
               calibration_anchor, loss_tier, row_version, created_at, updated_at)
            VALUES
              (:id, 1, :slug, :name, :status, :threat_event_type,
               :threat_actor_type, :asset_class, :attack_vector, :tags,
               :description, :example_incidents, :source_citations,
               :canonical_fair_gap, :applicable_industries,
               :applicable_sub_sectors, :applicable_org_sizes,
               :threat_event_frequency, :vulnerability, :primary_loss,
               :secondary_loss, :suggested_control_ids,
               :standards_references, :calibration_anchor,
               :loss_tier, 1, :now, :now)
        """
            ),
            {
                # No-hyphen hex UUID (0897a0ff350e precedent ONLY — matches the
                # UuidType(as_uuid=True) adapter that binds 32-char no-hyphen hex;
                # a hyphenated str(uuid4()) would 404 every id-based ORM query).
                "id": uuid.uuid4().hex,
                **{
                    k: json.dumps(v) if isinstance(v, (list, dict)) else v
                    for k, v in entry.items()
                },
                "now": now,
            },
        )


def downgrade() -> None:
    """Delete exactly the 38 C-iii-b rows inserted by this migration.

    Scoped by:
      - slug IN (_NEW_SLUGS) — the literal pinned tuple; never reads the JSON
      - version = 1          — never touches version > 1 entries
      - source = 'seed'      — SB-2c hardening: avoids deleting a user-imported
                               entry that collided with one of the 38 slugs
                               (source = 'imported' for user-uploaded entries;
                               server_default = 'seed' for migration-seeded rows)

    Reversibility: INSERT-based additive migrations are cleanly reversible by
    DELETE.  This contrasts with the C-iii-a migration (3d7b9e357d52) whose
    in-place UPDATE downgrade is a deliberate no-op (restoring 44 PERT payloads
    inline would dual-source them; rollback there requires a git checkout).

    Override pre-delete (PGA-ARCH-I1): SQLite runs with FK enforcement OFF
    during Alembic migrations (PRAGMA foreign_keys is session-scoped; Alembic's
    connection does not re-enable it).  The composite FK on
    scenario_library_overrides(library_entry_id, library_entry_version) →
    scenario_library_entries(id, version) would therefore NOT cascade on the
    library-entry DELETE — orphaned override rows would silently remain.
    We PREPEND an explicit override DELETE scoped to the same 38 slugs so that
    downgrade leaves no orphans regardless of FK-enforcement state.
    """
    bind = op.get_bind()

    # Step 1: delete any scenario_library_overrides rows referencing the 38
    # library entries we are about to remove.  Must run BEFORE the library-entry
    # DELETE below because SQLite FK enforcement is OFF in Alembic migrations
    # (session-scoped PRAGMA; not re-enabled by Alembic) — without this guard
    # the library-entry DELETE would silently orphan override rows.
    bind.execute(
        sa.text(
            "DELETE FROM scenario_library_overrides "
            "WHERE library_entry_id IN ("
            "  SELECT id FROM scenario_library_entries "
            "  WHERE slug IN :slugs AND version = 1 AND source = 'seed'"
            ")"
        ).bindparams(sa.bindparam("slugs", _NEW_SLUGS, expanding=True))
    )

    # Step 2: delete the 38 C-iii-b library entries.
    bind.execute(
        sa.text(
            "DELETE FROM scenario_library_entries "
            "WHERE slug IN :slugs AND version = 1 AND source = 'seed'"
        ).bindparams(sa.bindparam("slugs", _NEW_SLUGS, expanding=True))
    )
