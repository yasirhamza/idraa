"""seed library extension entries

Revision ID: 0897a0ff350e
Revises: 7e29245a1930
Create Date: 2026-06-03 14:38:51.681279

Additive, insert-if-absent seed of the 13 scenario-library extension entries
(7 OT + 6 IT) from ``data/seed_library_entries_extension.json`` — the SEPARATE
extension file, NOT ``data/seed_library_entries.json`` (the original 31 entries
loaded by ancestor migration ``c1d2e3f4a5b6``).

Ordering: ``down_revision`` is the CHECK-widening migration ``7e29245a1930`` so
the 12→13 ``threatcategory`` CHECK widening runs BEFORE these inserts — three of
the OT entries use the new ``ot_integrity`` effect and would be rejected by the
12-value CHECK on a migrated/production DB otherwise.

Idempotency: each entry is INSERTed only if no row with that ``slug`` already
exists at ``version = 1``. Re-running the upgrade, or running it on a DB that
already holds some of the slugs, is a no-op for those slugs. Fresh installs and
existing UAT DBs both converge to 31 + 13 = 44 published entries.

Foot-gun vs c1d2e3f4a5b6: the INSERT column list INCLUDES ``calibration_anchor``.
That column was nullable when c1d2e3f4a5b6 ran (so its INSERT omits it) but was
flipped NOT NULL by PR γ-4 (#115). Omitting it here would violate the NOT NULL
constraint, so every extension entry carries an explicit anchor (validated by
``LibraryEntrySeed`` before the SQL runs).
"""

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0897a0ff350e"
down_revision = "7e29245a1930"
branch_labels = None
depends_on = None


# The 13 extension slugs (7 OT + 6 IT). Used by downgrade() to delete exactly
# the rows this migration owns; upgrade() reads the slugs from the JSON itself.
_NEW_SLUGS = (
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
)


def _ext_path() -> Path:
    # Paranoid-review (Major-finding F25 path): resolve the seed JSON via an
    # explicit project-root anchor (parent-of-package, then up to repo root),
    # mirroring c1d2e3f4a5b6, rather than a fragile Path(__file__) depth count.
    import idraa

    project_root = Path(idraa.__file__).resolve().parent.parent.parent
    seed_path = project_root / "data" / "seed_library_entries_extension.json"
    if not seed_path.exists():
        # Fallback for non-standard layouts (CI artefacts, packaged distros).
        seed_path = (
            Path(__file__).resolve().parent.parent.parent
            / "data"
            / "seed_library_entries_extension.json"
        )
    return seed_path


def upgrade() -> None:
    # Every entry runs through LibraryEntrySeed.model_validate before insert so
    # seed-load failures surface at migration time, not at first browse query.
    from idraa.services.seed_library_loader import LibraryEntrySeed

    bind = op.get_bind()
    existing = {
        r[0]
        for r in bind.execute(
            sa.text("SELECT slug FROM scenario_library_entries WHERE version = 1")
        ).fetchall()
    }
    entries = json.loads(_ext_path().read_text(encoding="utf-8"))
    now = datetime.now(UTC).isoformat()
    for raw in entries:
        if raw["slug"] in existing:
            # insert-if-absent (idempotent): skip slugs already present.
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
               calibration_anchor, row_version, created_at, updated_at)
            VALUES
              (:id, 1, :slug, :name, :status, :threat_event_type,
               :threat_actor_type, :asset_class, :attack_vector, :tags,
               :description, :example_incidents, :source_citations,
               :canonical_fair_gap, :applicable_industries,
               :applicable_sub_sectors, :applicable_org_sizes,
               :threat_event_frequency, :vulnerability, :primary_loss,
               :secondary_loss, :suggested_control_ids,
               :standards_references, :calibration_anchor, 1, :now, :now)
        """
            ),
            {
                # No-hyphen hex (matches the canonical id format that
                # e7d0c3a91f2b normalised the base seed to). The column's
                # UuidType(as_uuid=True) adapter binds id params as 32-char
                # no-hyphen hex, so a hyphenated str(uuid4()) would 404 every
                # id-based ORM query (detail page, wizard step-1→2 advance).
                "id": uuid.uuid4().hex,
                **{
                    k: json.dumps(v) if isinstance(v, (list, dict)) else v
                    for k, v in entry.items()
                },
                "now": now,
            },
        )


def downgrade() -> None:
    # Delete exactly the 13 extension rows BY SLUG. This runs BEFORE the
    # widening migration's downgrade (reverse order), so the ot_integrity rows
    # are gone before that migration narrows the CHECK back to 12 values.
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "DELETE FROM scenario_library_entries WHERE slug IN :slugs AND version = 1"
        ).bindparams(sa.bindparam("slugs", _NEW_SLUGS, expanding=True))
    )
