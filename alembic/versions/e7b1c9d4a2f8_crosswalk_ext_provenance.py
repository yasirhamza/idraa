"""Link-level provenance for RiskFlow crosswalk-seed extensions (#449).

Revision ID: e7b1c9d4a2f8
Revises: d9e5a3c7f2b4
Create Date: 2026-07-09

Adds ``framework_control_faircam.is_extension`` (NOT NULL, server_default 0) and
backfills it to 1 for the RiskFlow-added methodology links, so a report/UI can
distinguish "RiskFlow methodology decision" from "FAIR-Institute canon" at the
DB row level (#449 item 2 — previously the ``riskflow_extension`` marker lived
only in the parent ``FrameworkControl.citation`` JSON).

The backfill's source of truth is ``data/seed_framework_crosswalk.json``'s
``riskflow_extension_functions`` overlay arrays (structurally separated from the
canonical ``fair_cam_functions`` layer in the same #449 change), so a future
extension entry is covered by re-running this pattern, not by editing this
migration. Partial-DB safe: safeguards absent from the DB are skipped silently
(same convention as the T1/T2 ext-link migrations f1a2b3c4d5e6 / c7e2a9b4f1d6).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7b1c9d4a2f8"
down_revision: str | Sequence[str] | None = "d9e5a3c7f2b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _seed_entries() -> list[dict]:
    import idraa

    project_root = Path(idraa.__file__).resolve().parent.parent.parent
    seed_path = project_root / "data" / "seed_framework_crosswalk.json"
    if not seed_path.exists():
        # Fallback for non-standard layouts (same anchor pattern as 3fc33f8e7ddc).
        seed_path = (
            Path(__file__).resolve().parent.parent.parent
            / "data"
            / "seed_framework_crosswalk.json"
        )
    return json.loads(seed_path.read_text(encoding="utf-8"))["entries"]


def upgrade() -> None:
    op.add_column(
        "framework_control_faircam",
        sa.Column(
            "is_extension",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    bind = op.get_bind()
    for entry in _seed_entries():
        ext_fns = entry.get("riskflow_extension_functions") or []
        if not ext_fns:
            continue
        control_id = bind.execute(
            sa.text(
                "SELECT id FROM framework_controls "
                "WHERE framework = :fw AND framework_version = :ver AND code = :code"
            ),
            {
                "fw": entry["framework"],
                "ver": entry["framework_version"],
                "code": entry["code"],
            },
        ).scalar()
        if control_id is None:
            # Safeguard absent (partial seed) — skip silently.
            continue
        for fn in ext_fns:
            bind.execute(
                sa.text(
                    "UPDATE framework_control_faircam SET is_extension = 1 "
                    "WHERE framework_control_id = :cid AND fair_cam_function = :fn"
                ),
                {"cid": control_id, "fn": fn},
            )


def downgrade() -> None:
    op.drop_column("framework_control_faircam", "is_extension")
