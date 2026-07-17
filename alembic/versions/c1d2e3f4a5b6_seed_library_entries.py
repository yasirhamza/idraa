"""seed_library_entries

Revision ID: c1d2e3f4a5b6
Revises: b8e0334b7f43
Create Date: 2026-04-29 00:00:00.000000

"""

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c1d2e3f4a5b6"
down_revision = "b8e0334b7f43"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # Paranoid-review (Major-finding F25 path): resolve the seed JSON via
    # an explicit project-root anchor instead of Path(__file__).parent.parent.parent
    # (the depth assumption is fragile; alembic versions can be reorganised).
    # Use the same anchor pattern as src/idraa/app.py:PACKAGE_ROOT (parent
    # of the package), then walk up to the repo root.
    import idraa

    project_root = Path(idraa.__file__).resolve().parent.parent.parent
    seed_path = project_root / "data" / "seed_library_entries.json"
    if not seed_path.exists():
        # Fallback for non-standard layouts (CI artefacts, packaged distros);
        # commit body must document if this triggers.
        seed_path = (
            Path(__file__).resolve().parent.parent.parent
            / "data"
            / "seed_library_entries.json"
        )
    entries = json.loads(seed_path.read_text(encoding="utf-8"))
    # Paranoid-review (Major-finding F25 validator wiring): every entry runs
    # through LibraryEntrySeed.model_validate before insert so seed-load
    # failures surface at migration time, not at first browse query.
    from idraa.services.seed_library_loader import LibraryEntrySeed

    validated = [LibraryEntrySeed.model_validate(e).model_dump() for e in entries]
    now = datetime.now(UTC).isoformat()
    for entry in validated:
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
               row_version, created_at, updated_at)
            VALUES
              (:id, 1, :slug, :name, :status, :threat_event_type,
               :threat_actor_type, :asset_class, :attack_vector, :tags,
               :description, :example_incidents, :source_citations,
               :canonical_fair_gap, :applicable_industries,
               :applicable_sub_sectors, :applicable_org_sizes,
               :threat_event_frequency, :vulnerability, :primary_loss,
               :secondary_loss, :suggested_control_ids,
               :standards_references, 1, :now, :now)
        """
            ),
            {
                "id": str(uuid.uuid4()),
                **{
                    k: json.dumps(v) if isinstance(v, (list, dict)) else v
                    for k, v in entry.items()
                },
                "now": now,
            },
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text("DELETE FROM scenario_library_entries WHERE version = 1")
    )
