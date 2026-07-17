"""seed three energy/manufacturing third-party-revenue library scenarios

Revision ID: 4b7f9e2a1c83
Revises: 380ccba92ebd
Create Date: 2026-06-13 00:00:00.000000

Additive insert-if-absent seed of 3 new scenario-library entries targeting
the ``business_process_third_party_revenue`` asset class, authored for the
energy / process-manufacturing user industry (WS3b):

  1. slug='tolling-plant-ransomware-customer-liability'
     Ransomware on a contract-manufacturer / toller halts production of
     brand-owner product → contractual make-whole / SLA penalties owed to
     the brand-owner customer (third-party revenue loss driver).

  2. slug='pipeline-nomination-scada-curtailment-shipper-penalty'
     OT-availability attack (nation-state) disrupts the nomination/SCADA
     scheduling system of a pipeline operator → contracted shippers cannot
     move product → firm-transportation SLA penalties + make-whole owed to
     shippers (third-party revenue loss driver).

  3. slug='energy-settlement-platform-tampering-offtaker-liability'
     Data-tampering / availability attack on ETRM or ISO/RTO settlement
     platform corrupts energy-contract settlement → offtaker / PPA
     counterparty loses confirmed energy revenue → dispute liability /
     make-whole owed to counterparty (third-party revenue loss driver).

Idempotency: each entry is INSERTed only if no row with that ``slug`` exists
at ``version = 1``.  Re-running the upgrade on a DB that already holds any
of these slugs is a no-op for those slugs.

Downgrade: DELETE the 3 rows by slug (version = 1 guard).

UUID foot-gun: uses ``uuid.uuid4().hex`` (no-hyphen 32-char hex) consistent
with the raw-text seed UUID convention documented in project CLAUDE.md and
enforced by the per-table no-hyphen guard test.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4b7f9e2a1c83"
down_revision: str = "380ccba92ebd"
branch_labels = None
depends_on = None

_NEW_SLUGS = (
    "tolling-plant-ransomware-customer-liability",
    "pipeline-nomination-scada-curtailment-shipper-penalty",
    "energy-settlement-platform-tampering-offtaker-liability",
)


def _ext_path() -> Path:
    """Resolve ``data/seed_library_entries_extension.json`` via project-root anchor."""
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
    """Insert the 3 new third-party-revenue entries (insert-if-absent, idempotent)."""
    from idraa.services.seed_library_loader import LibraryEntrySeed

    bind = op.get_bind()
    existing = {
        r[0]
        for r in bind.execute(
            sa.text("SELECT slug FROM scenario_library_entries WHERE version = 1")
        ).fetchall()
    }

    entries = json.loads(_ext_path().read_text(encoding="utf-8"))
    # Filter to only the 3 new slugs this migration owns.
    new_entries = [e for e in entries if e["slug"] in _NEW_SLUGS]

    now = datetime.now(UTC).isoformat()
    for raw in new_entries:
        if raw["slug"] in existing:
            continue  # insert-if-absent (idempotent)
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
                # No-hyphen hex UUID (raw-text seed UUID convention; see project
                # CLAUDE.md and the per-table no-hyphen guard test).
                "id": uuid.uuid4().hex,
                **{
                    k: json.dumps(v) if isinstance(v, (list, dict)) else v
                    for k, v in entry.items()
                },
                "now": now,
            },
        )


def downgrade() -> None:
    """Delete exactly the 3 rows this migration owns, by slug + version = 1."""
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "DELETE FROM scenario_library_entries WHERE slug IN :slugs AND version = 1"
        ).bindparams(sa.bindparam("slugs", _NEW_SLUGS, expanding=True))
    )
