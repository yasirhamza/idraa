"""Attack-coverage gap-fill epic (#529): insert-OR-CORRECT seed of the 9 new
cross-cutting-vector entries

Revision ID: 63cfe62ef5a7
Revises: b6f2d8c4a1e9
Create Date: 2026-07-10

Additive, insert-if-absent seed of the 9 new attack-coverage scenario library
entries (#529 Task 1) appended to ``data/seed_library_entries_extension.json``
(which now contains 71 entries total: the 62 present at ``d9e5a3c7f2b4`` plus
the 9 new edge-appliance / transient-device / client-exploitation /
OT-wireless / removable-media / destructive-wiper entries authored via
``scripts/build_attack_coverage_entries.py``).

**Ordering / convergence guarantee:**
``down_revision`` is ``b6f2d8c4a1e9``. It was originally authored against
``d9e5a3c7f2b4`` (the then-single head), but between authoring and merge
origin/main advanced with ``e7b1c9d4a2f8`` (crosswalk-ext-provenance, #533)
and ``b6f2d8c4a1e9`` (CODB gap entries, #535), both descendants of
``d9e5a3c7f2b4``. Re-pointing onto the new single head ``b6f2d8c4a1e9`` (done
at the PR-gate reconciliation) keeps a LINEAR chain and avoids forking the DAG
into two heads (which would break ``alembic upgrade head`` at boot). Both new
main migrations touch only ``framework_crosswalk`` / control-library seeds --
neither touches ``scenario_library_entries`` -- and ``b6f2d8c4a1e9`` is a
descendant of ``d9e5a3c7f2b4`` so the ``loss_shape`` column (added by
``b8c4f2e6a1d3``) still exists when this migration runs. Fresh DB: the chain
runs c1d2e3f4a5b6 -> ... -> 0897a0ff350e -> ... -> d9e5a3c7f2b4 ->
e7b1c9d4a2f8 -> b6f2d8c4a1e9 -> this migration, landing on 62 + 9 = 71
published entries (0897a0ff350e reads the live extension JSON at migration
time and already inserts the 9 new slugs on a fresh DB -- rows for those 9
already exist by the time this migration runs).
Existing UAT/prod DB at ``b6f2d8c4a1e9``: the 9 new slugs are NOT yet present
(0897a0ff350e ran against the extension JSON before Task 1 appended them), so
this migration's insert-if-absent guard inserts exactly the 9 -> same
71-entry terminal state.

**Fresh-boot loss_shape reconciliation (Task-3 review [Important],
methodology + architect):** on a fresh DB the 9 rows inserted by
``0897a0ff350e`` predate the ``loss_shape`` column (added later by
``b8c4f2e6a1d3``, whose fixed catastrophic-shortlist backfill in turn
predates this migration and predates ``destructive-wiper-nationstate``'s
addition to that shortlist) -- so on the fresh-DB path this migration is NOT
a pure no-op: its already-present branch is insert-OR-CORRECT, re-asserting
each of the 9 rows' ``loss_shape`` from the seed JSON (``loss_shape`` ONLY,
never the loss nodes -- see the "Idempotency" section below). Without this,
``destructive-wiper-nationstate`` would silently land ``loss_shape='capped'``
on every fresh-volume deployment (``docker-entrypoint.sh`` runs migrations on
each boot: per-tester Fly instances, e2e harnesses, DR rebuilds), capping its
catastrophic tail at instantiation
(``services/wizard_finalize.py:390``). The prod-upgrade path is unaffected --
there the 9 slugs are absent, so the INSERT branch runs and already sets
``loss_shape`` correctly.

**Column-omission trap (0897a0ff350e / f4a1c2b3d4e5 precedent):** this INSERT
explicitly sets EVERY seed column, including ``loss_shape`` -- the column
added by ``b8c4f2e6a1d3`` AFTER ``f4a1c2b3d4e5`` (the D-iii-b precedent) ran,
so that precedent's column list does not include it and cannot be copied
blindly. ``loss_shape`` server_defaults to ``'capped'``; 8 of the 9 new
entries are ``capped`` (PERT) but ``destructive-wiper-nationstate`` is
``catastrophic`` (raw lognormal, owner-approved shortlist addition per
``tests/_loss_shape_helpers.py``) -- omitting the column would silently ship
it as ``capped``, corrupting its distribution shape.

**No ``organization_id`` column:** ``scenario_library_entries`` is the
canonical (non-org-scoped) layer -- only ``scenario_library_overrides``
(OrgMixin) carries ``organization_id``. This migration follows the same
precedent as ``f4a1c2b3d4e5`` / ``0897a0ff350e`` / ``60ff242180f6``.

**UUID format:** every inserted ``id`` uses ``uuid.uuid4().hex`` -- a 32-char
no-hyphen hex string. The ``UuidType(as_uuid=True)`` adapter binds hex params
without hyphens; a hyphenated UUID string would cause 404 on every
id-based ORM query.

**Downgrade mirrors ``f4a1c2b3d4e5``:** a real scoped DELETE + source guard.
  Step 1: DELETE scenario_library_overrides referencing the 9 entries
          (SQLite FK enforcement is OFF during Alembic migrations; PGA-ARCH-I1
          precedent -- without this pre-delete, override rows would be
          silently orphaned).
  Step 2: DELETE WHERE slug IN (<9 pinned slugs>) AND version = 1
          AND source = 'seed'
Slug-scoped delete-all is SAFE here (the 9 are brand-new) -- unlike rev2's
mapping downgrade (see ``e7d8e05ede6b``), which MUST be technique-scoped
because 3 of its rows attach to pre-existing entries.

**Idempotency:** each entry is INSERTed only if no row with that ``slug``
already exists at ``version = 1``; if a row already exists, only its
``loss_shape`` column is UPDATEd (to the seed entry's own value) --
``primary_loss``/``secondary_loss``/every other column are left untouched so
this can never clobber ``d9e5a3c7f2b4``'s PERT conversion. Re-running
upgrade, or running it on a DB that already holds some of the 9 slugs, is a
no-op for those slugs' ``loss_shape`` (the UPDATE sets the same value again).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "63cfe62ef5a7"
down_revision = "b6f2d8c4a1e9"
branch_labels = None
depends_on = None


# The 9 new attack-coverage slugs, pinned as a literal tuple so the
# downgrade() never depends on the mutable JSON file. The insert-if-absent
# guard in upgrade() reads the actual JSON, so the two sources must stay in
# sync -- tests/migrations/test_insert_attack_coverage.py::
# test_migration_new_slugs_tuple_matches_pinned_literal live-imports this
# tuple and asserts it equals both the test file's independently-maintained
# copy AND that every slug is present in the extension JSON (the sub
# direction; it cannot detect an unrelated 10th slug added to the JSON
# without also being added here -- see that test's docstring for the
# documented limitation).
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


def _ext_path() -> Path:
    """Resolve the extension seed JSON via the project-root anchor.

    Mirrors the path-resolution pattern from ``0897a0ff350e`` / ``f4a1c2b3d4e5``:
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
    """Insert the 9 new attack-coverage extension entries, insert-OR-CORRECT
    on any slug already present at version = 1 (see the module docstring's
    "Fresh-boot loss_shape reconciliation" section -- the already-present
    branch UPDATEs ``loss_shape`` only, from the seed entry's own value).
    Each payload is validated through ``LibraryEntrySeed.model_validate``
    before insert/correction so seed-data corruption surfaces at
    ``alembic upgrade`` rather than silently at first browse query.
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
            # Skip everything except the 9 new attack-coverage slugs; the
            # other 62 extension entries are already seeded by earlier
            # migrations.
            continue
        entry = LibraryEntrySeed.model_validate(raw).model_dump()
        if raw["slug"] in existing:
            # insert-OR-CORRECT (Task-3 review [Important], methodology +
            # architect): on a FRESH `alembic upgrade head` from empty,
            # ancestor 0897a0ff350e inserts these 9 slugs before the
            # loss_shape column exists, and b8c4f2e6a1d3's fixed
            # catastrophic-shortlist backfill predates this migration (and
            # predates W1's addition to that shortlist) -- so a bare
            # insert-if-absent skip would leave
            # destructive-wiper-nationstate stuck at the server_default
            # 'capped', silently capping its catastrophic tail at
            # instantiation (services/wizard_finalize.py:390). Correct
            # loss_shape ONLY (never primary_loss/secondary_loss -- that
            # would risk clobbering d9e5a3c7f2b4's PERT conversion) from the
            # seed entry's own (validated) value. No-op on the prod-upgrade
            # path (rows absent -> the INSERT below already sets it
            # correctly) and idempotent (re-running sets the same value).
            bind.execute(
                sa.text(
                    "UPDATE scenario_library_entries SET loss_shape = :shape "
                    "WHERE slug = :slug AND version = 1 AND source = 'seed'"
                ),
                {"shape": entry["loss_shape"], "slug": raw["slug"]},
            )
            continue
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
               calibration_anchor, loss_tier, loss_shape, loss_form_profile,
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
               :loss_tier, :loss_shape, :loss_form_profile, 1, :now, :now)
        """
            ),
            {
                # No-hyphen hex UUID (0897a0ff350e / f4a1c2b3d4e5 precedent) --
                # matches the UuidType(as_uuid=True) adapter that binds
                # 32-char no-hyphen hex; a hyphenated UUID string would 404
                # every id-based ORM query.
                "id": uuid.uuid4().hex,
                **{
                    k: json.dumps(v) if isinstance(v, (list, dict)) else v for k, v in entry.items()
                },
                "now": now,
            },
        )


def downgrade() -> None:
    """Delete exactly the 9 attack-coverage rows inserted by this migration.

    Scoped by:
      - slug IN (_NEW_SLUGS) -- the literal pinned tuple; never reads the JSON
      - version = 1          -- never touches version > 1 entries
      - source = 'seed'      -- avoids deleting a user-imported entry that
                                happened to collide with one of the 9 slugs

    Override pre-delete (PGA-ARCH-I1 precedent, mirrors f4a1c2b3d4e5): SQLite
    runs with FK enforcement OFF during Alembic migrations (PRAGMA
    foreign_keys is session-scoped; Alembic's connection does not re-enable
    it). The composite FK on scenario_library_overrides(library_entry_id,
    library_entry_version) -> scenario_library_entries(id, version) would
    therefore NOT cascade on the library-entry DELETE -- orphaned override
    rows would silently remain. We PREPEND an explicit override DELETE scoped
    to the same 9 slugs so that downgrade leaves no orphans regardless of
    FK-enforcement state.
    """
    bind = op.get_bind()

    # Step 1: delete any scenario_library_overrides rows referencing the 9
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

    # Step 2: delete the 9 attack-coverage library entries.
    bind.execute(
        sa.text(
            "DELETE FROM scenario_library_entries "
            "WHERE slug IN :slugs AND version = 1 AND source = 'seed'"
        ).bindparams(sa.bindparam("slugs", _NEW_SLUGS, expanding=True))
    )
