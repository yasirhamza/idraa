"""Migration tests for domain-expert correction: revert telecom-ddos-core-network.

Migration revision: c9f1d3a7b2e0
Down-revision:     4b7f9e2a1c83

Three cases:
1. test_upgrade_reverts_telecom_to_business_process_revenue
   After upgrading to c9f1d3a7b2e0, the telecom row carries
   'business_process_revenue'; logistics stays 'business_process_third_party_revenue'.

2. test_upgrade_is_idempotent
   Re-running the migration's UPDATE SQL produces a zero-row change (no error,
   same value) — because the WHERE clause requires the *old* asset_class value.

3. test_downgrade_reverts_telecom_to_third_party_revenue
   After downgrading back to 4b7f9e2a1c83, the telecom row reverts to
   'business_process_third_party_revenue' (the WS3a state).
   The logistics row is not tested in downgrade since this migration never touched it.
"""

from __future__ import annotations

import sqlalchemy as sa
from pytest_alembic import MigrationContext

_PREV_REV = "4b7f9e2a1c83"  # immediate down_revision of this migration
_THIS_REV = "c9f1d3a7b2e0"  # the telecom revert migration


def _asset_class(engine: sa.Engine, slug: str) -> str | None:
    """Return asset_class for the version=1 row of *slug*, or None if not found."""
    with engine.connect() as conn:
        row = conn.execute(
            sa.text(
                "SELECT asset_class FROM scenario_library_entries "
                "WHERE slug = :slug AND version = 1"
            ),
            {"slug": slug},
        ).fetchone()
    return row[0] if row else None


def test_upgrade_reverts_telecom_to_business_process_revenue(
    alembic_runner: MigrationContext,
    alembic_engine: sa.Engine,
) -> None:
    """After upgrading to c9f1d3a7b2e0 telecom has business_process_revenue
    and logistics is unchanged at business_process_third_party_revenue."""
    alembic_runner.migrate_up_to(_THIS_REV)

    telecom = _asset_class(alembic_engine, "telecom-ddos-core-network")
    logistics = _asset_class(alembic_engine, "logistics-disruption")

    assert telecom == "business_process_revenue", (
        f"telecom-ddos-core-network asset_class after upgrade: expected "
        f"'business_process_revenue' (carrier's own revenue), got {telecom!r}"
    )
    assert logistics == "business_process_third_party_revenue", (
        f"logistics-disruption asset_class must remain 'business_process_third_party_revenue' "
        f"(this migration must NOT touch the logistics row), got {logistics!r}"
    )


def test_upgrade_is_idempotent(
    alembic_runner: MigrationContext,
    alembic_engine: sa.Engine,
) -> None:
    """Re-running the migration's UPDATE SQL produces a zero-row no-op.

    Simulates a DB where the migration has already been applied — the WHERE
    clause guard (asset_class = 'business_process_third_party_revenue') means
    re-execution touches no rows because the value has already changed.
    """
    alembic_runner.migrate_up_to(_THIS_REV)

    # Re-apply the same UPDATE statement directly.
    with alembic_engine.begin() as conn:
        result = conn.execute(
            sa.text(
                "UPDATE scenario_library_entries "
                "SET asset_class = 'business_process_revenue' "
                "WHERE slug = 'telecom-ddos-core-network' "
                "  AND asset_class = 'business_process_third_party_revenue' "
                "  AND version = 1"
            )
        )

    assert result.rowcount == 0, (
        f"Idempotency guard: telecom UPDATE should have touched 0 rows on re-run, "
        f"got {result.rowcount}"
    )

    # Value is unchanged after the no-op re-run.
    assert _asset_class(alembic_engine, "telecom-ddos-core-network") == ("business_process_revenue")


def test_downgrade_reverts_telecom_to_third_party_revenue(
    alembic_runner: MigrationContext,
    alembic_engine: sa.Engine,
) -> None:
    """Downgrading to 4b7f9e2a1c83 reverts the telecom row back to
    'business_process_third_party_revenue' (the WS3a state)."""
    alembic_runner.migrate_up_to(_THIS_REV)

    # Confirm the upgrade is applied.
    assert _asset_class(alembic_engine, "telecom-ddos-core-network") == ("business_process_revenue")

    alembic_runner.migrate_down_to(_PREV_REV)

    telecom = _asset_class(alembic_engine, "telecom-ddos-core-network")

    assert telecom == "business_process_third_party_revenue", (
        f"telecom-ddos-core-network asset_class after downgrade: expected "
        f"'business_process_third_party_revenue' (WS3a state), got {telecom!r}"
    )
