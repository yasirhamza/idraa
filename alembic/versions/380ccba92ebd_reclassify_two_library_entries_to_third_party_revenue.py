"""reclassify two library entries to business_process_third_party_revenue

Methodology audit found two seed library entries whose asset_class was
incorrect — their primary loss driver is a THIRD PARTY's revenue
(downstream partner / shipper SLA penalties), which maps to
``business_process_third_party_revenue``, not to their previously assigned
classes.

Entries corrected:
  1. slug='telecom-ddos-core-network' ("Telecom Core-Network DDoS — Service
     Disruption and SLA Penalties") — was ``business_process_revenue``
  2. slug='logistics-disruption' ("Transportation & Logistics Operational
     Disruption") — was ``systems``

``business_process_third_party_revenue`` is an already-allowed VARCHAR value:
it was added to the CHECK constraint in migration ``bf920a18ef0c``
("expand_assetclass_with_fair_feb_2025_canonical_values"), so no schema
change is required here — this is a pure data UPDATE.

**Idempotency:** each UPDATE is guarded by a WHERE clause that filters on
both the slug (stable key) and the *old* asset_class value.  Re-running the
migration (e.g., on a DB that has already been upgraded) produces zero-row
no-op UPDATEs, which are silent and safe.

**Downgrade:** reverts the two rows to their prior values using the same
idempotent WHERE pattern.

Revision ID: 380ccba92ebd
Revises: 3011adc6a115
Create Date: 2026-06-13 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "380ccba92ebd"
down_revision: str = "3011adc6a115"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Re-classify two seed library entries to business_process_third_party_revenue.

    Idempotent: WHERE clause requires the *old* asset_class value, so a
    second application is a zero-row no-op.
    """
    bind = op.get_bind()

    # Entry 1: Telecom Core-Network DDoS — was business_process_revenue
    bind.execute(
        sa.text(
            "UPDATE scenario_library_entries "
            "SET asset_class = 'business_process_third_party_revenue' "
            "WHERE slug = 'telecom-ddos-core-network' "
            "  AND asset_class = 'business_process_revenue' "
            "  AND version = 1"
        )
    )

    # Entry 2: Transportation & Logistics Operational Disruption — was systems
    bind.execute(
        sa.text(
            "UPDATE scenario_library_entries "
            "SET asset_class = 'business_process_third_party_revenue' "
            "WHERE slug = 'logistics-disruption' "
            "  AND asset_class = 'systems' "
            "  AND version = 1"
        )
    )


def downgrade() -> None:
    """Revert both rows to their prior asset_class values.

    Idempotent: WHERE clause requires asset_class =
    'business_process_third_party_revenue', so a second application is a
    zero-row no-op.
    """
    bind = op.get_bind()

    # Revert Entry 1: Telecom Core-Network DDoS → business_process_revenue
    bind.execute(
        sa.text(
            "UPDATE scenario_library_entries "
            "SET asset_class = 'business_process_revenue' "
            "WHERE slug = 'telecom-ddos-core-network' "
            "  AND asset_class = 'business_process_third_party_revenue' "
            "  AND version = 1"
        )
    )

    # Revert Entry 2: Transportation & Logistics Operational Disruption → systems
    bind.execute(
        sa.text(
            "UPDATE scenario_library_entries "
            "SET asset_class = 'systems' "
            "WHERE slug = 'logistics-disruption' "
            "  AND asset_class = 'business_process_third_party_revenue' "
            "  AND version = 1"
        )
    )
