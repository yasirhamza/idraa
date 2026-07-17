"""seed scenario suggested controls

Revision ID: de2080181a9c
Revises: d4f6a2b9c8e1
Create Date: 2026-06-02 17:33:14.044402

In-place metadata UPDATE (no version bump): back-fills the curated
``suggested_control_ids`` (catalog slugs) onto the seeded ``version = 1``
scenario library entries, reading the SAME ``data/seed_library_entries.json``
as the fresh-install seed path so both install paths converge by construction.
Advisory recommendation metadata only; does not touch FAIR distributions.
"""
import json
import uuid  # noqa: F401  (kept for parity if needed)
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "de2080181a9c"
down_revision = "d4f6a2b9c8e1"
branch_labels = None
depends_on = None


def _seed_path() -> Path:
    import idraa
    root = Path(idraa.__file__).resolve().parent.parent.parent
    p = root / "data" / "seed_library_entries.json"
    if not p.exists():
        p = Path(__file__).resolve().parent.parent.parent / "data" / "seed_library_entries.json"
    return p


def upgrade() -> None:
    bind = op.get_bind()
    entries = json.loads(_seed_path().read_text(encoding="utf-8"))
    for entry in entries:
        bind.execute(
            sa.text(
                "UPDATE scenario_library_entries SET suggested_control_ids = :json "
                "WHERE slug = :slug AND version = 1"
            ),
            {"json": json.dumps(entry.get("suggested_control_ids", [])), "slug": entry["slug"]},
        )


def downgrade() -> None:
    bind = op.get_bind()
    # Restore empty arrays on the seeded v1 rows (canonical seed data; no runtime CRUD).
    bind.execute(
        sa.text(
            "UPDATE scenario_library_entries SET suggested_control_ids = :empty WHERE version = 1"
        ),
        {"empty": json.dumps([])},
    )
