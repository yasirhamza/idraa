"""revert telecom-ddos-core-network asset_class to business_process_revenue

Domain-expert correction post-WS3a: the Telecom Core-Network DDoS entry's
dominant loss is the CARRIER'S OWN revenue (SLA penalties on its own service
commitments to subscribers / retail customers), not a third party's.

"Carrier revenue cannot be third-party revenue."

Migration WS3a (``380ccba92ebd``) incorrectly reclassified this entry to
``business_process_third_party_revenue`` alongside the logistics entry.  The
logistics entry (slug='logistics-disruption') correctly stays at
``business_process_third_party_revenue`` — its loss driver IS downstream
shipper SLA penalties, which are third-party revenue.

This migration reverts ONLY the telecom entry.  The logistics entry and the
three WS3b energy/manufacturing entries (tolling, pipeline, energy-settlement)
are NOT touched.

**Idempotency:** the UPDATE is guarded by a WHERE clause requiring both
``slug = 'telecom-ddos-core-network'`` AND
``asset_class = 'business_process_third_party_revenue'``.  Re-running on a DB
that has already been upgraded produces a zero-row no-op.

**Downgrade:** reverts the row back to ``business_process_third_party_revenue``
using the same idempotent WHERE pattern (guards on the old value).

Revision ID: c9f1d3a7b2e0
Revises: 4b7f9e2a1c83
Create Date: 2026-06-14 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9f1d3a7b2e0"
down_revision: str = "4b7f9e2a1c83"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Revert telecom-ddos-core-network to business_process_revenue.

    The carrier's own revenue from SLA commitments to its subscribers is NOT
    a third-party's revenue.  Only the telecom row is updated; the logistics
    entry and the three WS3b entries are intentionally left unchanged.

    Idempotent: WHERE clause requires asset_class = 'business_process_third_party_revenue',
    so a second application on an already-upgraded DB is a zero-row no-op.
    """
    bind = op.get_bind()

    bind.execute(
        sa.text(
            "UPDATE scenario_library_entries "
            "SET asset_class = 'business_process_revenue' "
            "WHERE slug = 'telecom-ddos-core-network' "
            "  AND asset_class = 'business_process_third_party_revenue' "
            "  AND version = 1"
        )
    )


def downgrade() -> None:
    """Re-apply the WS3a reclassification for telecom-ddos-core-network.

    Reverts the row back to 'business_process_third_party_revenue', matching
    the state after migration 380ccba92ebd was applied.

    Idempotent: WHERE clause requires asset_class = 'business_process_revenue',
    so a second downgrade application is a zero-row no-op.
    """
    bind = op.get_bind()

    bind.execute(
        sa.text(
            "UPDATE scenario_library_entries "
            "SET asset_class = 'business_process_third_party_revenue' "
            "WHERE slug = 'telecom-ddos-core-network' "
            "  AND asset_class = 'business_process_revenue' "
            "  AND version = 1"
        )
    )
