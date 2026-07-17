"""Issue #129 T8 — NULL-backfill migration script tests.

Mirrors the test pattern at
``tests/integration/test_issue_131_reclassification_migration.py``:

  * In-process invocation of ``scripts.issue_129_audit_log_migration.main()``
    sharing the test session via the ``wire_executor_to_test_db`` fixture.
  * Idempotency via ``(organization_id, entity_id, action)`` triple.
  * Per-row audit payload preserves pre-mutation ``capability_value`` so
    auditors can reconstruct the original input post-NULL-backfill.

Scope-discriminator semantics (Sec-I5):

  * Sub-functions IN scope (4 unit-aware): ``LEC_DET_MONITORING``,
    ``LEC_RESP_EVENT_TERMINATION``, ``VMC_CORR_IMPLEMENTATION``,
    ``LEC_RESP_LOSS_REDUCTION`` — for each, ``capability_value < 1.0``
    is nulled (conservative threshold: ``cap >= 1.0`` is preserved as
    a legitimate ≥ 1-day / ≥ $1 value).
  * Sub-functions OUT of scope (PROBABILITY domain or non-unit-aware):
    left untouched regardless of ``capability_value``.

Audit payload shape (Meth-2-I3 + Meth-3-I2):
    {sub_function, previous_capability_value, interpreted_as_pre_mu1}
where the third field captures the pre-PR-μ.1 calculator semantic
("effectiveness_multiplier") for audit-replay context.
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


def _make_control(org_id: uuid.UUID, name: str) -> Control:
    return Control(
        id=uuid.uuid4(),
        organization_id=org_id,
        name=name,
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


@pytest.mark.asyncio
async def test_migration_nulls_only_in_scope_sub_functions(
    db_session: AsyncSession,
    seed_organization: Organization,
    wire_executor_to_test_db: None,
) -> None:
    """Only the 4 unit-aware sub-functions with cap < 1.0 get nulled.

    PROBABILITY-domain assignments are untouched. cap >= 1.0 on in-scope
    sub-functions is preserved (Sec-I5: conservative threshold guards
    legitimate 1-day / $1 values).
    """
    org = seed_organization
    ctrl = _make_control(org.id, "Mixed assignments — scope-discriminator test")
    db_session.add(ctrl)
    await db_session.flush()

    # In-scope, cap < 1.0 — must be nulled.
    a_in_scope_low = ControlFunctionAssignment(
        id=uuid.uuid4(),
        control_id=ctrl.id,
        organization_id=org.id,
        sub_function=FairCamSubFunction.LEC_DET_MONITORING,
        capability_value=0.5,
        coverage=0.8,
        reliability=0.85,
    )
    # In-scope, cap == 1.0 — preserved per Sec-I5 boundary.
    a_in_scope_boundary = ControlFunctionAssignment(
        id=uuid.uuid4(),
        control_id=ctrl.id,
        organization_id=org.id,
        sub_function=FairCamSubFunction.LEC_RESP_EVENT_TERMINATION,
        capability_value=1.0,
        coverage=0.8,
        reliability=0.85,
    )
    # In-scope, cap > 1.0 — preserved (legitimate day/$ value).
    a_in_scope_high = ControlFunctionAssignment(
        id=uuid.uuid4(),
        control_id=ctrl.id,
        organization_id=org.id,
        sub_function=FairCamSubFunction.VMC_CORR_IMPLEMENTATION,
        capability_value=3.0,
        coverage=0.8,
        reliability=0.85,
    )
    # Out-of-scope (PROBABILITY) with cap < 1.0 — must be left untouched.
    a_out_of_scope = ControlFunctionAssignment(
        id=uuid.uuid4(),
        control_id=ctrl.id,
        organization_id=org.id,
        sub_function=FairCamSubFunction.LEC_RESP_RESILIENCE,
        capability_value=0.4,
        coverage=0.8,
        reliability=0.85,
    )

    db_session.add_all([a_in_scope_low, a_in_scope_boundary, a_in_scope_high, a_out_of_scope])
    await db_session.commit()

    from scripts.issue_129_audit_log_migration import main as migration_main

    await migration_main()

    async def _refresh_cap(asgn_id: uuid.UUID) -> float | None:
        row = (
            await db_session.execute(
                select(ControlFunctionAssignment).where(ControlFunctionAssignment.id == asgn_id)
            )
        ).scalar_one()
        await db_session.refresh(row)
        return row.capability_value

    assert await _refresh_cap(a_in_scope_low.id) is None, "In-scope cap<1.0 must be nulled"
    assert await _refresh_cap(a_in_scope_boundary.id) == 1.0, (
        "In-scope cap==1.0 must be preserved per Sec-I5"
    )
    assert await _refresh_cap(a_in_scope_high.id) == 3.0, (
        "In-scope cap>1.0 must be preserved (legitimate day-count)"
    )
    assert await _refresh_cap(a_out_of_scope.id) == 0.4, (
        "Out-of-scope (PROBABILITY) sub-function must NOT be mutated"
    )


@pytest.mark.asyncio
async def test_migration_idempotent(
    db_session: AsyncSession,
    seed_organization: Organization,
    wire_executor_to_test_db: None,
) -> None:
    """Second run: no new audit_log rows, no further mutations."""
    org = seed_organization
    ctrl = _make_control(org.id, "Idempotency test")
    db_session.add(ctrl)
    await db_session.flush()

    asgn = ControlFunctionAssignment(
        id=uuid.uuid4(),
        control_id=ctrl.id,
        organization_id=org.id,
        sub_function=FairCamSubFunction.LEC_RESP_LOSS_REDUCTION,
        capability_value=0.3,
        coverage=0.8,
        reliability=0.85,
    )
    db_session.add(asgn)
    await db_session.commit()

    from scripts.issue_129_audit_log_migration import main as migration_main

    await migration_main()

    rows_after_first = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.entity_type == "control",
                    AuditLog.entity_id == ctrl.id,
                    AuditLog.action == "null_fallback_issue_129",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows_after_first) == 1, (
        f"Expected 1 audit_log row after first run; got {len(rows_after_first)}"
    )

    # Capture pre-2nd-run state.
    refreshed = (
        await db_session.execute(
            select(ControlFunctionAssignment).where(ControlFunctionAssignment.id == asgn.id)
        )
    ).scalar_one()
    await db_session.refresh(refreshed)
    assert refreshed.capability_value is None

    await migration_main()

    rows_after_second = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.entity_type == "control",
                    AuditLog.entity_id == ctrl.id,
                    AuditLog.action == "null_fallback_issue_129",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows_after_second) == 1, (
        f"Idempotency violation: 2nd run added rows. Total now: {len(rows_after_second)}"
    )

    refreshed_again = (
        await db_session.execute(
            select(ControlFunctionAssignment).where(ControlFunctionAssignment.id == asgn.id)
        )
    ).scalar_one()
    await db_session.refresh(refreshed_again)
    assert refreshed_again.capability_value is None


@pytest.mark.asyncio
async def test_migration_emits_audit_log_with_null_fallback_action(
    db_session: AsyncSession,
    seed_organization: Organization,
    wire_executor_to_test_db: None,
) -> None:
    """Audit row has action='null_fallback_issue_129' and 3-field payload.

    Spec-5-I1: the third field ``interpreted_as_pre_mu1`` captures the
    pre-PR-μ.1 calculator semantic, NOT the FAIR-node name.
    """
    org = seed_organization
    ctrl = _make_control(org.id, "Audit-payload shape test")
    db_session.add(ctrl)
    await db_session.flush()

    asgn = ControlFunctionAssignment(
        id=uuid.uuid4(),
        control_id=ctrl.id,
        organization_id=org.id,
        sub_function=FairCamSubFunction.LEC_DET_MONITORING,
        capability_value=0.5,
        coverage=0.8,
        reliability=0.85,
    )
    db_session.add(asgn)
    await db_session.commit()

    from scripts.issue_129_audit_log_migration import main as migration_main

    await migration_main()

    rows = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.action == "null_fallback_issue_129",
                    AuditLog.entity_id == ctrl.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1, f"Expected 1 row; got {len(rows)}"
    audit = rows[0]
    assert audit.entity_type == "control"
    assert audit.user_id is None
    assert audit.organization_id == org.id, "AuditLog.organization_id is REQUIRED (Sec3-B1)"

    payload: dict[str, Any] = dict(audit.changes)
    assert "nulled_assignments" in payload
    nulled = payload["nulled_assignments"]
    assert isinstance(nulled, list) and len(nulled) == 1, (
        f"Expected exactly 1 nulled entry; got {nulled}"
    )
    entry = nulled[0]
    # 3-field payload contract.
    assert entry["sub_function"] == "lec_det_monitoring"
    assert entry["previous_capability_value"] == 0.5
    # Spec-5-I1 / Meth-3-I2: third field is the pre-μ.1 calculator semantic.
    assert entry["interpreted_as_pre_mu1"] == "effectiveness_multiplier"


def test_migration_action_fits_string_64() -> None:
    """audit_log.action column is String(64) post-T6 widening."""
    action = "null_fallback_issue_129"
    assert len(action) <= 64, (
        f"action verb '{action}' exceeds AuditLog.action String(64) width: {len(action)}"
    )


@pytest.mark.asyncio
async def test_migration_isolates_per_org_audit_rows(
    db_session: AsyncSession,
    seed_organization_factory: Callable[..., Awaitable[Organization]],
    wire_executor_to_test_db: None,
) -> None:
    """Multi-org tenant-isolation regression — mirrors the #131 multi-org test.

    Per-org commit pattern (Sec-B3/Arch-I4) must produce one audit row per
    Org with matching ``organization_id``, no cross-org bleed.
    """
    org_a = await seed_organization_factory(name="Org A — issue 129 multi-org")
    org_b = await seed_organization_factory(name="Org B — issue 129 multi-org")

    ctrl_a = _make_control(org_a.id, "Org A control")
    ctrl_b = _make_control(org_b.id, "Org B control")
    db_session.add_all([ctrl_a, ctrl_b])
    await db_session.flush()

    asgn_a = ControlFunctionAssignment(
        id=uuid.uuid4(),
        control_id=ctrl_a.id,
        organization_id=org_a.id,
        sub_function=FairCamSubFunction.LEC_DET_MONITORING,
        capability_value=0.4,
        coverage=0.8,
        reliability=0.85,
    )
    asgn_b = ControlFunctionAssignment(
        id=uuid.uuid4(),
        control_id=ctrl_b.id,
        organization_id=org_b.id,
        sub_function=FairCamSubFunction.VMC_CORR_IMPLEMENTATION,
        capability_value=0.6,
        coverage=0.8,
        reliability=0.85,
    )
    db_session.add_all([asgn_a, asgn_b])
    await db_session.commit()

    from scripts.issue_129_audit_log_migration import main as migration_main

    await migration_main()

    rows = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.action == "null_fallback_issue_129",
                    AuditLog.entity_id.in_([ctrl_a.id, ctrl_b.id]),
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2, f"Expected exactly 2 audit_log rows; got {len(rows)}"

    by_entity: dict[uuid.UUID, AuditLog] = {row.entity_id: row for row in rows}
    assert set(by_entity) == {ctrl_a.id, ctrl_b.id}

    row_a = by_entity[ctrl_a.id]
    row_b = by_entity[ctrl_b.id]
    assert row_a.organization_id == org_a.id, "Cross-org leak on Org A row"
    assert row_b.organization_id == org_b.id, "Cross-org leak on Org B row"
    assert row_a.organization_id != row_b.organization_id
