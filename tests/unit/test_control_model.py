"""Control model round-trip — phase 1.2.1.

Verifies the ``Control`` ORM persists through SQLite and that its
typed + JSON-backed columns (``annual_cost``, ``nist_csf_functions``)
survive the round-trip intact. Schema parity with the 1.1 tables (IdMixin /
TimestampMixin / OrgMixin) is covered by the existing mixin tests, so
this file only exercises the Control-specific shape.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.control import Control
from idraa.models.enums import (
    ControlType,
    EntityStatus,
    FairCamSubFunction,
    IndustryType,
    OrganizationSize,
)
from idraa.models.organization import Organization


async def test_control_roundtrip(db_session: AsyncSession) -> None:
    from datetime import UTC, datetime

    from idraa.models.control_function_assignment import ControlFunctionAssignment

    org = Organization(
        name="A",
        industry_type=IndustryType.INFORMATION,
        organization_size=OrganizationSize.SMALL,
    )
    db_session.add(org)
    await db_session.flush()
    c = Control(
        organization_id=org.id,
        name="MFA",
        description="Enforce MFA",
        type=ControlType.TECHNICAL,
        annual_cost=Decimal("2000"),
        nist_csf_functions=["PR.AC"],
        iso_27001_domains=["A.9"],
        status=EntityStatus.ACTIVE,
        version="1.0",
    )
    db_session.add(c)
    await db_session.flush()  # populate c.id

    asgn = ControlFunctionAssignment(
        control_id=c.id,
        organization_id=org.id,
        sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
        capability_value=0.9,
        coverage=0.8,
        reliability=0.95,
        confirmed_by_user_at=datetime.now(UTC),
    )
    db_session.add(asgn)
    await db_session.commit()

    fetched = (await db_session.execute(select(Control))).scalar_one()
    assert fetched.name == "MFA"
    assert fetched.annual_cost == Decimal("2000")
    assert fetched.nist_csf_functions == ["PR.AC"]
    # Effectiveness is now per-assignment (PR iota spec §6.4).
    await db_session.refresh(fetched, attribute_names=["assignments"])
    assert fetched.assignments[0].capability_value == 0.9


def test_control_function_assignment_model_is_importable() -> None:
    """ControlFunctionAssignment ORM model must be importable from models package."""
    from idraa.models.control_function_assignment import ControlFunctionAssignment  # noqa: F401


def test_control_has_assignments_relationship() -> None:
    """Control ORM must expose an 'assignments' attribute (relationship)."""
    from idraa.models.control import Control

    assert hasattr(Control, "assignments"), (
        "Control.assignments relationship is missing. "
        "Spec §6.1 requires selectin eager-load relationship to ControlFunctionAssignment."
    )


def test_control_lacks_dropped_fields() -> None:
    """Control ORM must NOT have the four dropped columns.

    These columns were removed in spec §6.4. Their presence would indicate
    the model was not updated after the migration landed.
    """
    from idraa.models.control import Control

    for dropped in ("function", "control_strength", "control_reliability", "control_coverage"):
        assert not hasattr(Control, dropped), (
            f"Control.{dropped} still exists — should have been removed in PR iota F2. Spec §6.4."
        )


async def test_control_assignments_order_tiebreaks_on_id_when_created_at_collides(
    db_session: AsyncSession,
) -> None:
    """Tiebreaker regression (issue #90 plan-gate fix Arch-I1, full mitigation).

    ``Control.assignments`` is ordered by ``(created_at, id)``. ``created_at``
    has microsecond precision and is NOT unique — bulk-import paths that build
    multiple assignments in one function call routinely collide on
    microsecond. Without the ``id`` tiebreaker, ``assignments[0]`` would be
    non-deterministic across SQLA reloads, breaking the adapter's
    representative-domain pick in ``run_executor._v3_to_fair_cam_control``.

    This test forces a created_at collision across three assignments and
    verifies the order after expire/refresh is the id-sorted order — i.e.
    deterministic.
    """
    from datetime import UTC, datetime

    from idraa.models.control_function_assignment import ControlFunctionAssignment

    org = Organization(
        name="Tiebreak-Org",
        industry_type=IndustryType.INFORMATION,
        organization_size=OrganizationSize.SMALL,
    )
    db_session.add(org)
    await db_session.flush()

    c = Control(
        organization_id=org.id,
        name="Tiebreak Control",
        description="",
        type=ControlType.TECHNICAL,
        annual_cost=Decimal("0"),
    )
    db_session.add(c)
    await db_session.flush()

    # Force the three assignments to share an identical created_at so the
    # secondary id sort is what actually orders them.
    shared_ts = datetime.now(UTC)
    a1 = ControlFunctionAssignment(
        control_id=c.id,
        organization_id=org.id,
        sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
        capability_value=0.9,
        coverage=0.8,
        reliability=0.95,
        created_at=shared_ts,
        updated_at=shared_ts,
    )
    a2 = ControlFunctionAssignment(
        control_id=c.id,
        organization_id=org.id,
        sub_function=FairCamSubFunction.LEC_DET_VISIBILITY,
        capability_value=0.85,
        coverage=0.7,
        reliability=0.9,
        created_at=shared_ts,
        updated_at=shared_ts,
    )
    a3 = ControlFunctionAssignment(
        control_id=c.id,
        organization_id=org.id,
        sub_function=FairCamSubFunction.LEC_DET_RECOGNITION,
        capability_value=0.8,
        coverage=0.75,
        reliability=0.85,
        created_at=shared_ts,
        updated_at=shared_ts,
    )
    db_session.add_all([a1, a2, a3])
    # Capture ids/ordering BEFORE commit: commit expires all attributes, after
    # which a sync access to a.id triggers IO outside a greenlet.
    control_id = c.id
    expected_ids_in_order = sorted([a1.id, a2.id, a3.id])
    await db_session.commit()

    db_session.expire_all()
    from sqlalchemy.orm import selectinload

    fetched = (
        await db_session.execute(
            select(Control)
            .where(Control.id == control_id)
            .options(selectinload(Control.assignments))
        )
    ).scalar_one()

    assert len(fetched.assignments) == 3
    actual_ids = [a.id for a in fetched.assignments]
    assert actual_ids == expected_ids_in_order, (
        "Control.assignments must order by (created_at, id) — without the id "
        "tiebreaker, ordering with colliding created_at timestamps is "
        "non-deterministic, breaking assignments[0] reproducibility in "
        "run_executor._v3_to_fair_cam_control (plan-gate fix Arch-I1)."
    )
