"""Epic D-iii-b: insert-if-absent seed of the 8 new attested vertical entries

Revision ID: f4a1c2b3d4e5
Revises: d3f1a7c9e5b2
Create Date: 2026-07-06

Additive, insert-if-absent seed of the 8 new D-iii-b scenario library entries
(#497) appended to ``data/seed_library_entries_extension.json`` (which now
contains 62 entries total: the 54 present at ``d3f1a7c9e5b2`` plus the 8 new
attested vertical archetypes authored in Task 1).

**Ordering / convergence guarantee:**
``down_revision`` is ``d3f1a7c9e5b2`` (the D-iii-a envelope×share recalibration,
currently the only head). Fresh DB: the chain runs
  c1d2e3f4a5b6 -> ... -> 0897a0ff350e -> ... -> d3f1a7c9e5b2 -> this migration,
landing on 31 + 62 = 93 published entries (0897a0ff350e reads the live
extension JSON at migration time and already inserts the 8 new slugs on a
fresh DB, so this migration is then a no-op there).
Existing UAT/prod DB at ``d3f1a7c9e5b2``: the 8 new slugs are NOT yet present
(0897a0ff350e ran against the extension JSON before Task 1 appended them), so
this migration's insert-if-absent guard inserts exactly the 8 -> same
93-entry terminal state.

**Column-omission trap (0897a0ff350e precedent):** this INSERT explicitly sets
``loss_tier``, ``loss_form_profile``, and ``source_citations`` from the seed
row -- these columns did not exist (loss_tier) or were not yet populated
(loss_form_profile, added by ``e1f2a3b4c5d6``) when ``0897a0ff350e`` and
``60ff242180f6`` ran. On a prod DB this migration is the ONLY inserter of the
8 new slugs, so omitting these columns would ship ``loss_tier='anecdotal'``
(server_default) / ``loss_form_profile=[]`` (server_default) -- silently
regressing these entries out of the D-iii envelope×share model they were
authored under.

**No ``organization_id`` column:** ``scenario_library_entries`` is the
canonical (non-org-scoped) layer -- only ``scenario_library_overrides``
(OrgMixin) carries ``organization_id``. Neither ``0897a0ff350e`` nor
``60ff242180f6`` set it; this migration follows the same precedent.

**UUID format:** every inserted ``id`` uses ``uuid.uuid4().hex`` -- a 32-char
no-hyphen hex string. The ``UuidType(as_uuid=True)`` adapter binds hex params
without hyphens; a hyphenated UUID string would cause 404 on every
id-based ORM query.

**Downgrade mirrors ``60ff242180f6``:** a real scoped DELETE + source guard.
  Step 1: DELETE scenario_library_overrides referencing the 8 entries
          (SQLite FK enforcement is OFF during Alembic migrations; PGA-ARCH-I1
          precedent -- without this pre-delete, override rows would be
          silently orphaned).
  Step 2: DELETE WHERE slug IN (<8 pinned slugs>) AND version = 1
          AND source = 'seed'

**Idempotency:** each entry is INSERTed only if no row with that ``slug``
already exists at ``version = 1``. Re-running upgrade, or running it on a DB
that already holds some of the 8 slugs, is a no-op for those slugs.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "f4a1c2b3d4e5"
down_revision = "d3f1a7c9e5b2"
branch_labels = None
depends_on = None


# The 8 new D-iii-b slugs, pinned as a literal tuple so the downgrade() never
# depends on the mutable JSON file. The insert-if-absent guard in upgrade()
# reads the actual JSON, so the two sources must stay in sync -- a
# tests/migrations/test_insert_d_iii_b.py assertion enforces this.
_NEW_SLUGS = (
    "physician-practice-clearinghouse-revenue-disruption",
    "law-enforcement-records-extortion-breach",
    "casino-ransomware-operational-disruption",
    "telecom-lawful-intercept-nationstate-compromise",
    "law-firm-privileged-data-ransomware-extortion",
    "k12-edtech-vendor-breach",
    "higher-ed-insider-ddos",
    "judiciary-court-system-ransomware",
)


def _ext_path() -> Path:
    """Resolve the extension seed JSON via the project-root anchor.

    Mirrors the path-resolution pattern from ``0897a0ff350e`` / ``60ff242180f6``:
    parent-of-idraa-package, then up to the repo root. Fallback to a
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
    """Insert the 8 new D-iii-b extension entries, skipping any slug already
    present at version = 1. Each payload is validated through
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
    new_slugs_set = set(_NEW_SLUGS)
    now = datetime.now(UTC).isoformat()
    for raw in entries:
        if raw["slug"] not in new_slugs_set:
            # Skip everything except the 8 new D-iii-b slugs; the other 54
            # extension entries are already seeded by earlier migrations.
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
               calibration_anchor, loss_tier, loss_form_profile,
               row_version, created_at, updated_at)
            VALUES
              (:id, 1, :slug, :name, :status, :threat_event_type,
               :threat_actor_type, :asset_class, :attack_vector, :tags,
               :description, :example_incidents, :source_citations,
               :canonical_fair_gap, :applicable_industries,
               :applicable_sub_sectors, :applicable_org_sizes,
               :threat_event_frequency, :vulnerability, :primary_loss,
               :secondary_loss, :suggested_control_ids,
               :standards_references, :calibration_anchor,
               :loss_tier, :loss_form_profile, 1, :now, :now)
        """
            ),
            {
                # No-hyphen hex UUID (0897a0ff350e / 60ff242180f6 precedent) --
                # matches the UuidType(as_uuid=True) adapter that binds
                # 32-char no-hyphen hex; a hyphenated UUID string would 404
                # every id-based ORM query.
                "id": uuid.uuid4().hex,
                **{
                    k: json.dumps(v) if isinstance(v, (list, dict)) else v
                    for k, v in entry.items()
                },
                "now": now,
            },
        )


def downgrade() -> None:
    """Delete exactly the 8 D-iii-b rows inserted by this migration.

    Scoped by:
      - slug IN (_NEW_SLUGS) -- the literal pinned tuple; never reads the JSON
      - version = 1          -- never touches version > 1 entries
      - source = 'seed'      -- avoids deleting a user-imported entry that
                                happened to collide with one of the 8 slugs

    Override pre-delete (PGA-ARCH-I1 precedent, mirrors 60ff242180f6): SQLite
    runs with FK enforcement OFF during Alembic migrations (PRAGMA
    foreign_keys is session-scoped; Alembic's connection does not re-enable
    it). The composite FK on scenario_library_overrides(library_entry_id,
    library_entry_version) -> scenario_library_entries(id, version) would
    therefore NOT cascade on the library-entry DELETE -- orphaned override
    rows would silently remain. We PREPEND an explicit override DELETE scoped
    to the same 8 slugs so that downgrade leaves no orphans regardless of
    FK-enforcement state.
    """
    bind = op.get_bind()

    # Step 1: delete any scenario_library_overrides rows referencing the 8
    # library entries we are about to remove. Must run BEFORE the
    # library-entry DELETE below.
    bind.execute(
        sa.text(
            "DELETE FROM scenario_library_overrides "
            "WHERE library_entry_id IN ("
            "  SELECT id FROM scenario_library_entries "
            "  WHERE slug IN :slugs AND version = 1 AND source = 'seed'"
            ")"
        ).bindparams(sa.bindparam("slugs", _NEW_SLUGS, expanding=True))
    )

    # Step 2: delete the 8 D-iii-b library entries.
    bind.execute(
        sa.text(
            "DELETE FROM scenario_library_entries "
            "WHERE slug IN :slugs AND version = 1 AND source = 'seed'"
        ).bindparams(sa.bindparam("slugs", _NEW_SLUGS, expanding=True))
    )
