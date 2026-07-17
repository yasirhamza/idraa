"""Epic D-iii-a: in-place recalibration of all 85 seed library entries to the
envelope x share loss model (design Amendment A1).

Reads data/seed_library_entries*.json (single source of truth — Epic C
3d7b9e357d52 convergence pattern) and UPDATEs each slug's loss nodes:
primary_loss / secondary_loss (envelope x Sum(shares), or a beyond-envelope IC3
own-lognormal for BEC), loss_form_profile (the per-form shares), loss_tier
(paginated for envelope, vendor for BEC), and source_citations (IRIS envelope /
IC3). TEF + vulnerability are NOT touched here (de-templating is a separate
follow-on). Parameterized binds only — no operator-supplied SQL.

Revision ID: d3f1a7c9e5b2
Down revision: e1f2a3b4c5d6
"""

from __future__ import annotations

import json
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "d3f1a7c9e5b2"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def _seed_paths() -> tuple[Path, Path]:
    import idraa

    try:
        root = Path(idraa.__file__).resolve().parent.parent.parent
        base = root / "data" / "seed_library_entries.json"
        ext = root / "data" / "seed_library_entries_extension.json"
        if base.exists() and ext.exists():
            return base, ext
    except Exception:  # pragma: no cover - fallback
        pass
    here = Path(__file__).resolve().parent.parent.parent
    return (
        here / "data" / "seed_library_entries.json",
        here / "data" / "seed_library_entries_extension.json",
    )


_UPDATE = sa.text(
    "UPDATE scenario_library_entries "
    "SET primary_loss = :primary_loss, "
    "    secondary_loss = :secondary_loss, "
    "    loss_form_profile = :loss_form_profile, "
    "    loss_tier = :loss_tier, "
    "    source_citations = :source_citations "
    "WHERE slug = :slug AND version = 1"
)


def upgrade() -> None:
    base_path, ext_path = _seed_paths()
    entries = json.loads(base_path.read_text(encoding="utf-8")) + json.loads(
        ext_path.read_text(encoding="utf-8")
    )
    bind = op.get_bind()
    for entry in entries:
        bind.execute(
            _UPDATE,
            {
                "primary_loss": json.dumps(entry["primary_loss"]),
                "secondary_loss": json.dumps(entry.get("secondary_loss")),
                "loss_form_profile": json.dumps(entry.get("loss_form_profile", [])),
                "loss_tier": entry.get("loss_tier", "anecdotal"),
                "source_citations": json.dumps(entry.get("source_citations", [])),
                "slug": entry["slug"],
            },
        )


def downgrade() -> None:
    """No-op — one-way content migration (Epic C 3d7b9e357d52 policy, ruling R6).

    The pre-D-iii-a per-entry loss payloads are deliberately superseded and are
    recoverable from git history only; restoring them inline would dual-source 85
    payloads and break the "JSON is single source of truth" convergence guarantee.
    Rollback requires a git checkout of the prior seed JSON + a re-run.
    """
    pass
