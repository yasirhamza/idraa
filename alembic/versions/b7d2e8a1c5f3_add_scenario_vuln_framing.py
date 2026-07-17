"""add scenarios.vuln_framing elicitation-provenance column (audit F2)

PR #339 redefined the vulnerability field's semantics from residual ("how
likely is the attacker to get through your current controls?") to INHERENT
(control-naive, before-your-controls). Pre-#339 scenarios were elicited under
the old residual framing — their stored vulnerability already embeds the
analyst's mental control discount, so the FAIR-CAM control layer double-counts
on top of it, and the post-#339 UI/help mislabel the value as inherent.

This column stamps the elicitation framing so the UI can banner legacy
scenarios for review (spec
``docs/superpowers/specs/2026-06-10-audit-remediation-f1-f2-design.md``).

Values (app-enforced, NO CHECK constraint — plan-gate SC-I2, mirroring the
d6b8e2f0a719 / #303 CHECK-widening-foot-gun precedent):
``'legacy_residual' | 'inherent'``.

Backfill cutoff evidence (plan-gate SC-I1): the #339 cutover is Fly release
v101 — uvicorn "Application startup complete" at 2026-06-10T09:28:15Z in the
deploy logs (next scenario-affecting deploy 10:52Z). Prod observation at audit
time: 15 of 16 scenarios were created before 09:30Z, consistent with the
cutoff. created_at is server-set (TimestampMixin), not user-suppliable, so
the cutoff cannot be gamed.

Downgrade: drop_column — data loss intentional (backfill is directional).

Revision ID: b7d2e8a1c5f3
Down revision: e3a1c4f7b2d9
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b7d2e8a1c5f3"
down_revision = "e3a1c4f7b2d9"
branch_labels = None
depends_on = None

# Fly release v101 startup-complete 2026-06-10T09:28:15Z (#339 cutover).
_CUTOFF = "2026-06-10 09:30:00"


def upgrade() -> None:
    op.add_column(
        "scenarios",
        sa.Column(
            "vuln_framing",
            sa.String(length=32),
            server_default="inherent",
            nullable=False,
        ),
    )
    op.get_bind().execute(
        sa.text(
            "UPDATE scenarios SET vuln_framing = 'legacy_residual' "
            "WHERE created_at < :cutoff"
        ),
        {"cutoff": _CUTOFF},
    )


def downgrade() -> None:
    op.drop_column("scenarios", "vuln_framing")
