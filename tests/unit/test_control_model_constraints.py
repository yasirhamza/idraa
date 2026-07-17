"""Issue #66: column-level constraint regression on Control.annual_cost.

Two contracts:
  1. Flush-time default — Control constructed without an explicit annual_cost
     reads Decimal('0') after the session flushes (the column's Python-side
     default fires at flush, not at construction). This is the practical
     contract: no production code path reads pre-flush Controls.
  2. Type contract — explicitly-set annual_cost stays a Decimal at
     construction time (pure-Python; no DB round-trip).
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.control import Control
from idraa.models.control_function_assignment import ControlFunctionAssignment
from idraa.models.enums import (
    ControlDomain,
    ControlType,
    FairCamSubFunction,
    IndustryType,
    OrganizationSize,
)
from idraa.models.organization import Organization


async def test_control_annual_cost_default_after_flush_is_zero_decimal(
    db_session: AsyncSession,
) -> None:
    """Flush-time default contract.

    SQLAlchemy's ``default=Decimal('0')`` is a Python-side callable that fires
    AT FLUSH, not at ``Control(...)`` construction. This test verifies the
    contract that matters in practice: a Control built without an explicit
    annual_cost reads ``Decimal('0')`` after the session flushes. (Pre-flush,
    the attribute is None — but no production code path reads pre-flush
    Controls.)
    """
    org = Organization(
        name="Default-Cost-Org",
        industry_type=IndustryType.MANUFACTURING,
        organization_size=OrganizationSize.MEDIUM,
    )
    db_session.add(org)
    await db_session.flush()

    c = Control(
        organization_id=org.id,
        name="Default Cost Test",
        description="",
        type=ControlType.ADMINISTRATIVE,
    )
    db_session.add(c)
    await db_session.flush()
    assert c.annual_cost == Decimal("0")
    assert isinstance(c.annual_cost, Decimal)


def test_control_annual_cost_is_decimal_type() -> None:
    """Type contract: explicitly-set annual_cost stays a Decimal at construction time."""
    c = Control(
        organization_id=uuid4(),
        name="Decimal Type Test",
        description="",
        type=ControlType.ADMINISTRATIVE,
        annual_cost=Decimal("12000.50"),
    )
    assert c.annual_cost == Decimal("12000.50")
    assert isinstance(c.annual_cost, Decimal)


async def test_control_domains_derives_from_assignments(
    db_session: AsyncSession,
) -> None:
    """Issue #90: Control.domains is a derived frozenset over assignments.

    With two assignments — one LEC sub-function, one DSC sub-function — the
    derived ``domains`` property must return the union of their domains. The
    property has no setter and no backing column; it reads strictly from
    ``self.assignments``.
    """
    org = Organization(
        name="Domains-Derived-Org",
        industry_type=IndustryType.INFORMATION,
        organization_size=OrganizationSize.SMALL,
    )
    db_session.add(org)
    await db_session.flush()

    c = Control(
        organization_id=org.id,
        name="Multi",
        description="",
        type=ControlType.ADMINISTRATIVE,
        annual_cost=Decimal("0"),
    )
    db_session.add(c)
    await db_session.flush()

    db_session.add_all(
        [
            ControlFunctionAssignment(
                control_id=c.id,
                organization_id=org.id,
                sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
                capability_value=0.5,
                coverage=1.0,
                reliability=1.0,
            ),
            ControlFunctionAssignment(
                control_id=c.id,
                organization_id=org.id,
                sub_function=FairCamSubFunction.DSC_PREV_DEFINED_EXPECTATIONS,
                capability_value=0.4,
                coverage=1.0,
                reliability=1.0,
            ),
        ]
    )
    # Stash the id BEFORE commit: commit expires attributes and a sync
    # access to c.id afterwards triggers an _load_expired re-query in the
    # wrong greenlet context (MissingGreenlet). This is the same pattern as
    # the tiebreaker test in tests/unit/test_control_model.py.
    control_id = c.id
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

    assert fetched.domains == frozenset({ControlDomain.LOSS_EVENT, ControlDomain.DECISION_SUPPORT})


def test_control_domains_empty_assignments_returns_empty_frozenset() -> None:
    """Issue #90: a Control with no assignments yields an empty frozenset.

    Property must always return a frozenset (never None / never a set / never
    raise), even when no assignments have been attached. Callers rely on the
    .domains attribute being safe to membership-test without a None guard.
    """
    c = Control(
        organization_id=uuid4(),
        name="Empty",
        description="",
        type=ControlType.ADMINISTRATIVE,
        annual_cost=Decimal("0"),
    )
    assert c.domains == frozenset()
    assert isinstance(c.domains, frozenset)
