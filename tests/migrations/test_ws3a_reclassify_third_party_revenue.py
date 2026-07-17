"""Migration tests for WS3a: reclassify 2 entries to business_process_third_party_revenue.

Migration revision: a1b2c3d4e5f6
Down-revision:     3011adc6a115

Three cases:
1. test_upgrade_sets_correct_asset_class
   After upgrading to a1b2c3d4e5f6, both rows carry
   'business_process_third_party_revenue'.

2. test_upgrade_is_idempotent
   Re-running the migration's UPDATE SQL produces zero-row changes (no error,
   same values).

3. test_downgrade_reverts_asset_class
   After downgrading back to 3011adc6a115 (the previous head), both rows
   revert to their original values ('business_process_revenue' and 'systems').
"""

from __future__ import annotations

import sqlalchemy as sa
from pytest_alembic import MigrationContext

_PREV_REV = "3011adc6a115"  # immediate down_revision of this migration
_THIS_REV = "380ccba92ebd"  # the reclassification migration


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


def test_upgrade_sets_correct_asset_class(
    alembic_runner: MigrationContext,
    alembic_engine: sa.Engine,
) -> None:
    """After upgrading to a1b2c3d4e5f6 both rows carry the third-party-revenue class."""
    alembic_runner.migrate_up_to(_THIS_REV)

    telecom = _asset_class(alembic_engine, "telecom-ddos-core-network")
    logistics = _asset_class(alembic_engine, "logistics-disruption")

    assert telecom == "business_process_third_party_revenue", (
        f"telecom-ddos-core-network asset_class after upgrade: expected "
        f"'business_process_third_party_revenue', got {telecom!r}"
    )
    assert logistics == "business_process_third_party_revenue", (
        f"logistics-disruption asset_class after upgrade: expected "
        f"'business_process_third_party_revenue', got {logistics!r}"
    )


def test_upgrade_is_idempotent(
    alembic_runner: MigrationContext,
    alembic_engine: sa.Engine,
) -> None:
    """Re-running the migration's UPDATE SQL produces a zero-row no-op.

    Simulates a DB where the migration has already been applied — the WHERE
    clause guards (asset_class = '<old value>') mean re-execution touches no
    rows.
    """
    alembic_runner.migrate_up_to(_THIS_REV)

    # Re-apply the same UPDATE statements directly.
    with alembic_engine.begin() as conn:
        result1 = conn.execute(
            sa.text(
                "UPDATE scenario_library_entries "
                "SET asset_class = 'business_process_third_party_revenue' "
                "WHERE slug = 'telecom-ddos-core-network' "
                "  AND asset_class = 'business_process_revenue' "
                "  AND version = 1"
            )
        )
        result2 = conn.execute(
            sa.text(
                "UPDATE scenario_library_entries "
                "SET asset_class = 'business_process_third_party_revenue' "
                "WHERE slug = 'logistics-disruption' "
                "  AND asset_class = 'systems' "
                "  AND version = 1"
            )
        )

    assert result1.rowcount == 0, (
        f"Idempotency guard: telecom UPDATE should have touched 0 rows on re-run, "
        f"got {result1.rowcount}"
    )
    assert result2.rowcount == 0, (
        f"Idempotency guard: logistics UPDATE should have touched 0 rows on re-run, "
        f"got {result2.rowcount}"
    )

    # Values are unchanged after the no-op re-run.
    assert _asset_class(alembic_engine, "telecom-ddos-core-network") == (
        "business_process_third_party_revenue"
    )
    assert _asset_class(alembic_engine, "logistics-disruption") == (
        "business_process_third_party_revenue"
    )


def test_downgrade_reverts_asset_class(
    alembic_runner: MigrationContext,
    alembic_engine: sa.Engine,
) -> None:
    """Downgrading to 3011adc6a115 reverts both rows to their original values."""
    alembic_runner.migrate_up_to(_THIS_REV)

    # Confirm the upgrade is applied.
    assert _asset_class(alembic_engine, "telecom-ddos-core-network") == (
        "business_process_third_party_revenue"
    )
    assert _asset_class(alembic_engine, "logistics-disruption") == (
        "business_process_third_party_revenue"
    )

    alembic_runner.migrate_down_to(_PREV_REV)

    telecom = _asset_class(alembic_engine, "telecom-ddos-core-network")
    logistics = _asset_class(alembic_engine, "logistics-disruption")

    assert telecom == "business_process_revenue", (
        f"telecom-ddos-core-network asset_class after downgrade: expected "
        f"'business_process_revenue', got {telecom!r}"
    )
    assert logistics == "systems", (
        f"logistics-disruption asset_class after downgrade: expected 'systems', got {logistics!r}"
    )
