"""Integration test for ``scripts/issue_131_audit_log_migration.py`` (issue #131 T2 Step 8.5b).

Asserts:
  * Pre-existing stale legacy capability_value rows ([3.0, 5.0, 7.0]) on
    reclassified-to-PROBABILITY sub-functions are nulled by the migration.
  * One audit_log row is inserted per Control with at least one mutated
    assignment; the ``changes`` payload enumerates each affected slug
    paired with its ``previous_capability_value`` (Sec-I1).
  * Idempotency: a second run produces no further mutations and no
    additional audit_log rows.
  * Multi-org tenant isolation: per-org audit rows reference their own
    Control's ``organization_id`` and ``entity_id`` with no cross-org
    bleed (Sec-I2).

Calls the script's ``main()`` directly (in-process) rather than via a
subprocess so the test inherits the test DB session created by the
``db_session`` fixture. The fixture wires ``DATABASE_URL`` via the
``client`` fixture's monkeypatch in other tests; here we use the
``wire_executor_to_test_db`` fixture which sets DATABASE_URL + resets
the cached singletons so ``get_session()`` in the script picks up the
per-test SQLite DB.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.audit_log import AuditLog
from idraa.models.control import Control
from idraa.models.control_function_assignment import ControlFunctionAssignment
from idraa.models.enums import (
    ControlType,
    EntityStatus,
    FairCamSubFunction,
)
from idraa.models.organization import Organization


@pytest.mark.asyncio
async def test_migration_nulls_legacy_capability_values_and_writes_audit(
    db_session: AsyncSession,
    seed_organization: Organization,
    wire_executor_to_test_db: None,
) -> None:
    """Seed legacy rows on reclassified-PROBABILITY sub-functions with
    day-count capability_values (3.0, 5.0, 7.0). Running the migration
    must:
      1. NULL all three capability_value fields.
      2. Insert exactly one audit_log row for the affected Control.
      3. Idempotency: running again does NOT add another audit_log row
         and does NOT re-mutate any rows (they're already NULL).
    """
    org = seed_organization
    ctrl = Control(
        id=uuid.uuid4(),
        organization_id=org.id,
        name="Pre-#131 control with stale day-count caps",
        type=ControlType.TECHNICAL,
        annual_cost=Decimal("0"),
        nist_csf_functions=[],
        iso_27001_domains=[],
        compliance_mappings={},
        skill_requirements=[],
        technology_dependencies=[],
        applicable_industries=[],
        applicable_org_sizes=[],
        status=EntityStatus.ACTIVE,
        version="1.0",
        created_by=None,
    )
    db_session.add(ctrl)
    await db_session.flush()

    # Three legacy capability_values on reclassified-to-PROBABILITY sub-functions.
    legacy_specs: list[tuple[FairCamSubFunction, float]] = [
        (FairCamSubFunction.LEC_RESP_RESILIENCE, 3.0),
        (FairCamSubFunction.VMC_ID_THREAT_INTELLIGENCE, 5.0),
        (FairCamSubFunction.VMC_ID_CONTROL_MONITORING, 7.0),
    ]
    asgn_ids: list[uuid.UUID] = []
    for sf, cap in legacy_specs:
        asgn = ControlFunctionAssignment(
            id=uuid.uuid4(),
            control_id=ctrl.id,
            organization_id=org.id,
            sub_function=sf,
            capability_value=cap,
            coverage=0.8,
            reliability=0.85,
        )
        db_session.add(asgn)
        asgn_ids.append(asgn.id)
    await db_session.commit()

    # Run migration (in-process — shares DB via wire_executor_to_test_db).
    from scripts.issue_131_audit_log_migration import main as migration_main

    await migration_main()

    # Re-query under the test session — migration committed under its own
    # session; refresh assignments and confirm capability_value is NULL.
    for asgn_id in asgn_ids:
        row = (
            await db_session.execute(
                select(ControlFunctionAssignment).where(ControlFunctionAssignment.id == asgn_id)
            )
        ).scalar_one()
        await db_session.refresh(row)
        assert row.capability_value is None, (
            f"Migration failed to NULL capability_value for {row.sub_function.value}: "
            f"got {row.capability_value}"
        )

    # Exactly one audit_log row for this control.
    audit_rows = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.entity_type == "control",
                    AuditLog.entity_id == ctrl.id,
                    AuditLog.action == "reclassify_unit_type_issue_131",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(audit_rows) == 1, (
        f"Expected exactly 1 audit_log row for control {ctrl.id}; got {len(audit_rows)}"
    )
    audit = audit_rows[0]
    assert audit.organization_id == org.id, "AuditLog.organization_id is REQUIRED (Sec3-B1)"
    assert audit.user_id is None, "Migration is system-initiated; user_id must be None"
    # changes payload enumerates all three slugs paired with their previous
    # capability_value (Sec-I1). Order not asserted; build a slug → value
    # dict and compare set-equal.
    payload: dict[str, Any] = dict(audit.changes)
    nulled = payload["nulled_assignments"]
    assert isinstance(nulled, list) and len(nulled) == 3, (
        f"Expected 3 nulled_assignments entries; got {nulled}"
    )
    by_slug: dict[str, float] = {
        entry["sub_function"]: entry["previous_capability_value"] for entry in nulled
    }
    expected_by_slug = {sf.value: cap for sf, cap in legacy_specs}
    assert by_slug == expected_by_slug, (
        f"nulled_assignments payload mismatch: expected {expected_by_slug}, got {by_slug}"
    )
    # Each previous_capability_value is the pre-#131 day-count (plain float),
    # NOT NULL — auditor must be able to reconstruct the original input.
    for entry in nulled:
        assert isinstance(entry["previous_capability_value"], float)
        assert entry["previous_capability_value"] in {3.0, 5.0, 7.0}
    assert "ELAPSED_TIME" in payload["note"] and "PROBABILITY" in payload["note"]
    assert "previous_capability_value" in payload["note"], (
        "Migration note must reference the per-entry previous_capability_value field"
    )

    # ── Idempotency: second run is a no-op ────────────────────────────────
    await migration_main()

    audit_rows_after_2nd = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.entity_type == "control",
                    AuditLog.entity_id == ctrl.id,
                    AuditLog.action == "reclassify_unit_type_issue_131",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(audit_rows_after_2nd) == 1, (
        f"Idempotency violation: 2nd run added an audit_log row. "
        f"Total now: {len(audit_rows_after_2nd)}"
    )
    # capability_value still NULL on all three (no re-mutation).
    for asgn_id in asgn_ids:
        row = (
            await db_session.execute(
                select(ControlFunctionAssignment).where(ControlFunctionAssignment.id == asgn_id)
            )
        ).scalar_one()
        await db_session.refresh(row)
        assert row.capability_value is None


@pytest.mark.asyncio
async def test_migration_skips_already_valid_caps(
    db_session: AsyncSession,
    seed_organization: Organization,
    wire_executor_to_test_db: None,
) -> None:
    """Reclassified sub-functions with already-in-range PROBABILITY caps
    (e.g. 0.7) must NOT be mutated, and must NOT trigger an audit_log row.

    Boundary cases:
      - capability_value=0.5 (in range; > 0 but not > 1.0): leave intact.
      - capability_value=None: leave intact.
      - capability_value=1.0 (boundary; not > 1.0): leave intact.
    """
    org = seed_organization
    ctrl = Control(
        id=uuid.uuid4(),
        organization_id=org.id,
        name="Control with in-range PROBABILITY caps",
        type=ControlType.TECHNICAL,
        annual_cost=Decimal("0"),
        nist_csf_functions=[],
        iso_27001_domains=[],
        compliance_mappings={},
        skill_requirements=[],
        technology_dependencies=[],
        applicable_industries=[],
        applicable_org_sizes=[],
        status=EntityStatus.ACTIVE,
        version="1.0",
        created_by=None,
    )
    db_session.add(ctrl)
    await db_session.flush()

    in_range_specs: list[tuple[FairCamSubFunction, float | None]] = [
        (FairCamSubFunction.LEC_RESP_RESILIENCE, 0.5),
        (FairCamSubFunction.VMC_ID_THREAT_INTELLIGENCE, None),
        (FairCamSubFunction.VMC_ID_CONTROL_MONITORING, 1.0),
    ]
    asgn_ids: list[uuid.UUID] = []
    for sf, cap in in_range_specs:
        asgn = ControlFunctionAssignment(
            id=uuid.uuid4(),
            control_id=ctrl.id,
            organization_id=org.id,
            sub_function=sf,
            capability_value=cap,
            coverage=0.8,
            reliability=0.85,
        )
        db_session.add(asgn)
        asgn_ids.append(asgn.id)
    await db_session.commit()

    from scripts.issue_131_audit_log_migration import main as migration_main

    await migration_main()

    # capability_value preserved on all three.
    expected_caps: dict[uuid.UUID, float | None] = {
        asgn_ids[i]: in_range_specs[i][1] for i in range(len(in_range_specs))
    }
    for asgn_id, expected_cap in expected_caps.items():
        row = (
            await db_session.execute(
                select(ControlFunctionAssignment).where(ControlFunctionAssignment.id == asgn_id)
            )
        ).scalar_one()
        await db_session.refresh(row)
        assert row.capability_value == expected_cap, (
            f"In-range cap was mutated: id={asgn_id} expected={expected_cap} "
            f"got={row.capability_value}"
        )

    # Zero audit_log rows for this control (no mutations occurred).
    audit_rows = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.entity_type == "control",
                    AuditLog.entity_id == ctrl.id,
                    AuditLog.action == "reclassify_unit_type_issue_131",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(audit_rows) == 0, (
        f"Migration spuriously added audit_log row for unmutated control: got {len(audit_rows)}"
    )


@pytest.mark.asyncio
async def test_migration_isolates_per_org_audit_rows(
    db_session: AsyncSession,
    seed_organization_factory: Callable[..., Awaitable[Organization]],
    wire_executor_to_test_db: None,
) -> None:
    """Multi-org tenant-isolation regression (Sec-I2, plan-gate-4).

    Build a two-organization fixture; each Org has its own Control with
    a single reclassified-sub-function assignment carrying a stale
    ``> 1.0`` ``capability_value``. After running the migration:

      * Exactly TWO audit_log rows exist (one per Org).
      * Each row's ``organization_id`` matches its parent Control's
        ``organization_id``.
      * Each row's ``entity_id`` matches its own Control's id; NEITHER
        row references the OTHER org's Control id.

    Catches FK-misordered writes, accidental cross-org payload bleed,
    and any future regression that re-introduces the Sec3-B1 risk
    (org_id NULL on system-initiated AuditLog rows).
    """
    org_a = await seed_organization_factory(name="Org A — issue 131 multi-org")
    org_b = await seed_organization_factory(name="Org B — issue 131 multi-org")

    def _make_control(org_id: uuid.UUID, label: str) -> Control:
        return Control(
            id=uuid.uuid4(),
            organization_id=org_id,
            name=f"{label} — pre-#131 control with stale day-count cap",
            type=ControlType.TECHNICAL,
            annual_cost=Decimal("0"),
            nist_csf_functions=[],
            iso_27001_domains=[],
            compliance_mappings={},
            skill_requirements=[],
            technology_dependencies=[],
            applicable_industries=[],
            applicable_org_sizes=[],
            status=EntityStatus.ACTIVE,
            version="1.0",
            created_by=None,
        )

    ctrl_a = _make_control(org_a.id, "Org A")
    ctrl_b = _make_control(org_b.id, "Org B")
    db_session.add_all([ctrl_a, ctrl_b])
    await db_session.flush()

    # One stale legacy assignment per Org, distinct sub_functions to make
    # the per-org payload contents trivially distinguishable in asserts.
    asgn_a = ControlFunctionAssignment(
        id=uuid.uuid4(),
        control_id=ctrl_a.id,
        organization_id=org_a.id,
        sub_function=FairCamSubFunction.LEC_RESP_RESILIENCE,
        capability_value=4.0,
        coverage=0.8,
        reliability=0.85,
    )
    asgn_b = ControlFunctionAssignment(
        id=uuid.uuid4(),
        control_id=ctrl_b.id,
        organization_id=org_b.id,
        sub_function=FairCamSubFunction.VMC_ID_THREAT_INTELLIGENCE,
        capability_value=6.0,
        coverage=0.8,
        reliability=0.85,
    )
    db_session.add_all([asgn_a, asgn_b])
    await db_session.commit()

    from scripts.issue_131_audit_log_migration import main as migration_main

    await migration_main()

    # All audit_log rows from this migration, across both orgs.
    rows = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.entity_type == "control",
                    AuditLog.action == "reclassify_unit_type_issue_131",
                    AuditLog.entity_id.in_([ctrl_a.id, ctrl_b.id]),
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2, f"Expected exactly 2 audit_log rows (one per Org); got {len(rows)}"

    by_entity: dict[uuid.UUID, AuditLog] = {row.entity_id: row for row in rows}
    assert set(by_entity) == {ctrl_a.id, ctrl_b.id}, (
        f"audit_log entity_id set mismatch: expected {{ctrl_a.id, ctrl_b.id}}, got {set(by_entity)}"
    )

    # Org A row: organization_id == org_a.id; entity_id == ctrl_a.id; does
    # NOT reference org_b's control.
    row_a = by_entity[ctrl_a.id]
    assert row_a.organization_id == org_a.id, (
        f"Cross-org leak: Org A audit row has organization_id={row_a.organization_id}, "
        f"expected {org_a.id}"
    )
    assert row_a.entity_id != ctrl_b.id

    # Org B row: organization_id == org_b.id; entity_id == ctrl_b.id; does
    # NOT reference org_a's control.
    row_b = by_entity[ctrl_b.id]
    assert row_b.organization_id == org_b.id, (
        f"Cross-org leak: Org B audit row has organization_id={row_b.organization_id}, "
        f"expected {org_b.id}"
    )
    assert row_b.entity_id != ctrl_a.id

    # Cross-org sanity: Org A and Org B organization_ids are distinct.
    assert row_a.organization_id != row_b.organization_id

    # Payload sanity per Org: each row's nulled_assignments references the
    # sub_function that was seeded for that Org, and only that one.
    payload_a = dict(row_a.changes)
    nulled_a = payload_a["nulled_assignments"]
    assert {entry["sub_function"] for entry in nulled_a} == {
        FairCamSubFunction.LEC_RESP_RESILIENCE.value
    }
    assert nulled_a[0]["previous_capability_value"] == 4.0

    payload_b = dict(row_b.changes)
    nulled_b = payload_b["nulled_assignments"]
    assert {entry["sub_function"] for entry in nulled_b} == {
        FairCamSubFunction.VMC_ID_THREAT_INTELLIGENCE.value
    }
    assert nulled_b[0]["previous_capability_value"] == 6.0
