"""seed_starter_overlays — populate STARTER_OVERLAYS for every organization.

Revision ID: d8e4e0b19bac
Revises: 28a33a04a6a8
Create Date: 2026-04-25 16:55:42.155012

Idempotent: skips an (org, tag) row that already exists. Safe to re-run on
upgrade-from-empty as well as upgrade-on-existing-deployment.

Per plan §C3 + B14: refuses to seed if STARTER_OVERLAY_PROVENANCE is missing
methodology for any tag — fail loud rather than silent-skip dangling
forward-references. Mirrors the async callable in
``idraa.services.overlays.seed_starter_overlays_for_org`` so prod and tests
produce identical seed rows.
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d8e4e0b19bac"
down_revision: str | None = "28a33a04a6a8"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # Inline import so this migration doesn't break if STARTER_OVERLAYS
    # changes shape in a future release; we read whatever is current at
    # deployment time. PR pi F12 relocated STARTER_OVERLAYS into the v3
    # services package — fair_cam is now math-only and does not own
    # v3-specific reference data.
    from idraa.services._starter_overlays_seed_data import (
        STARTER_OVERLAY_PROVENANCE,
        STARTER_OVERLAYS,
    )

    bind = op.get_bind()

    organizations = sa.table(
        "organizations",
        sa.column("id", sa.Uuid(as_uuid=True)),
    )

    overlay_definitions = sa.table(
        "overlay_definitions",
        sa.column("id", sa.Uuid(as_uuid=True)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
        sa.column("organization_id", sa.Uuid(as_uuid=True)),
        sa.column("tag", sa.String),
        sa.column("display_name", sa.String),
        sa.column("frequency_multiplier", sa.Float),
        sa.column("magnitude_multiplier", sa.Float),
        sa.column("sources", sa.JSON),
        sa.column("methodology", sa.Text),
        sa.column("version", sa.Integer),
        sa.column("is_active", sa.Boolean),
    )

    overlay_revisions = sa.table(
        "overlay_definition_revisions",
        sa.column("id", sa.Uuid(as_uuid=True)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
        sa.column("overlay_definition_id", sa.Uuid(as_uuid=True)),
        sa.column("version", sa.Integer),
        sa.column("tag", sa.String),
        sa.column("display_name", sa.String),
        sa.column("frequency_multiplier", sa.Float),
        sa.column("magnitude_multiplier", sa.Float),
        sa.column("sources", sa.JSON),
        sa.column("methodology", sa.Text),
        sa.column("methodology_change_reason", sa.Text),
        sa.column("created_by_user_id", sa.Uuid(as_uuid=True)),
    )

    org_ids = [row[0] for row in bind.execute(sa.select(organizations.c.id))]

    # DB-side timestamps; the async callable uses _now_utc() via TimestampMixin.
    # Only business columns (tag/multipliers/methodology/version/...) are
    # byte-identical between paths; row-level created_at/updated_at can differ.
    now = sa.func.now()

    for org_id in org_ids:
        existing_tags = {
            row[0]
            for row in bind.execute(
                sa.select(overlay_definitions.c.tag).where(
                    overlay_definitions.c.organization_id == org_id
                )
            )
        }
        for overlay in STARTER_OVERLAYS:
            if overlay.tag in existing_tags:
                continue
            provenance = STARTER_OVERLAY_PROVENANCE.get(overlay.tag, {})
            sources = list(provenance.get("sources", ()))
            methodology = str(provenance.get("methodology", ""))
            if not methodology:
                # Fail loud rather than silent-skip — provenance must be filled
                # in fair_cam before the seed is meaningful, and a silent skip
                # would leave dangling forward-references with no signal.
                # Mirrors the parallel RuntimeError in the async seed callable.
                raise RuntimeError(
                    f"STARTER_OVERLAY_PROVENANCE missing methodology for tag "
                    f"{overlay.tag!r}; refusing to seed dangling forward-references"
                )

            overlay_id = uuid.uuid4()
            bind.execute(
                overlay_definitions.insert().values(
                    id=overlay_id,
                    created_at=now,
                    updated_at=now,
                    organization_id=org_id,
                    tag=overlay.tag,
                    display_name=overlay.display_name,
                    frequency_multiplier=overlay.frequency_multiplier,
                    magnitude_multiplier=overlay.magnitude_multiplier,
                    sources=sources,
                    methodology=methodology,
                    version=1,
                    is_active=True,
                )
            )
            bind.execute(
                overlay_revisions.insert().values(
                    id=uuid.uuid4(),
                    created_at=now,
                    updated_at=now,
                    overlay_definition_id=overlay_id,
                    version=1,
                    tag=overlay.tag,
                    display_name=overlay.display_name,
                    frequency_multiplier=overlay.frequency_multiplier,
                    magnitude_multiplier=overlay.magnitude_multiplier,
                    sources=sources,
                    methodology=methodology,
                    methodology_change_reason="initial seed from STARTER_OVERLAYS",
                    created_by_user_id=None,
                )
            )


def downgrade() -> None:
    """Idempotent removal: delete only seeded rows.

    Triple-check filter (per plan preamble line 60): a row is treated as a
    seed-row only if ALL THREE conditions hold:
      - ``version == 1`` (first revision; user edits bump version)
      - ``methodology_change_reason LIKE 'initial seed%'`` (defends against a
        future-renamed seed reason while still excluding user reasons)
      - ``created_by_user_id IS NULL`` (user-edits always set the FK)
    This protects user-authored revisions whose reason text happens to match.
    """
    bind = op.get_bind()

    overlay_revisions = sa.table(
        "overlay_definition_revisions",
        sa.column("methodology_change_reason", sa.Text),
        sa.column("overlay_definition_id", sa.Uuid(as_uuid=True)),
        sa.column("version", sa.Integer),
        sa.column("created_by_user_id", sa.Uuid(as_uuid=True)),
    )
    overlay_definitions = sa.table(
        "overlay_definitions",
        sa.column("id", sa.Uuid(as_uuid=True)),
    )

    seeded_ids = [
        row[0]
        for row in bind.execute(
            sa.select(overlay_revisions.c.overlay_definition_id).where(
                overlay_revisions.c.version == 1,
                overlay_revisions.c.methodology_change_reason.like("initial seed%"),
                overlay_revisions.c.created_by_user_id.is_(None),
            )
        )
    ]
    if seeded_ids:
        bind.execute(
            sa.delete(overlay_revisions).where(
                overlay_revisions.c.overlay_definition_id.in_(seeded_ids)
            )
        )
        bind.execute(
            sa.delete(overlay_definitions).where(
                overlay_definitions.c.id.in_(seeded_ids)
            )
        )
