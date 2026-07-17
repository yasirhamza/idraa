"""Tests for services.controls_maintenance — MaintenanceSummary + count helper."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models._types import now_utc
from idraa.models.control import Control
from idraa.models.control_function_assignment import ControlFunctionAssignment
from idraa.models.enums import (
    ControlType,
    EntityStatus,
    FairCamSubFunction,
    IndustryType,
    OrganizationSize,
)
from idraa.models.organization import Organization
from idraa.services.controls_maintenance import (
    maintenance_badge_count,
    maintenance_summary,
)

pytestmark = pytest.mark.asyncio


async def _make_org(db_session: AsyncSession) -> Organization:
    org = Organization(
        name="Acme",
        industry_type=IndustryType.MANUFACTURING,
        organization_size=OrganizationSize.MEDIUM,
        annual_revenue=Decimal("1000000000"),
    )
    db_session.add(org)
    await db_session.flush()
    return org


def _make_control(
    org_id: uuid.UUID,
    *,
    name: str,
    annual_cost: Decimal,
) -> Control:
    return Control(
        organization_id=org_id,
        name=name,
        description=f"{name} description",
        type=ControlType.ADMINISTRATIVE,
        annual_cost=annual_cost,
        status=EntityStatus.ACTIVE,
        version="1.0",
    )


async def _make_assignment(
    db_session: AsyncSession,
    control: Control,
    *,
    sub_function: FairCamSubFunction = FairCamSubFunction.LEC_DET_MONITORING,
    confirmed: bool,
) -> ControlFunctionAssignment:
    a = ControlFunctionAssignment(
        control_id=control.id,
        organization_id=control.organization_id,
        sub_function=sub_function,
        capability_value=None if sub_function == FairCamSubFunction.LEC_DET_MONITORING else 0.7,
        coverage=0.8,
        reliability=0.8,
        confirmed_by_user_at=now_utc() if confirmed else None,
    )
    db_session.add(a)
    await db_session.flush()
    return a


class TestMaintenanceSummaryClean:
    async def test_zero_when_org_has_no_controls(self, db_session: AsyncSession) -> None:
        org = await _make_org(db_session)
        summary = await maintenance_summary(db_session, org.id)
        assert summary.unconfirmed_assignments_count == 0
        assert summary.zero_cost_controls_count == 0
        assert summary.total_needs_attention == 0
        assert summary.zero_cost_controls == []
        assert summary.unconfirmed_assignments == []


class TestMaintenanceSummaryUnconfirmedOnly:
    async def test_counts_unconfirmed_assignments(self, db_session: AsyncSession) -> None:
        org = await _make_org(db_session)
        priced = _make_control(org.id, name="Priced", annual_cost=Decimal("1000"))
        db_session.add(priced)
        await db_session.flush()
        await _make_assignment(db_session, priced, confirmed=False)
        summary = await maintenance_summary(db_session, org.id)
        assert summary.unconfirmed_assignments_count == 1
        assert summary.zero_cost_controls_count == 0
        assert summary.total_needs_attention == 1
        assert len(summary.unconfirmed_assignments) == 1


class TestMaintenanceSummaryZeroCostOnly:
    async def test_counts_zero_cost_controls(self, db_session: AsyncSession) -> None:
        org = await _make_org(db_session)
        unpriced = _make_control(org.id, name="Unpriced", annual_cost=Decimal("0"))
        db_session.add(unpriced)
        await db_session.flush()
        await _make_assignment(db_session, unpriced, confirmed=True)
        summary = await maintenance_summary(db_session, org.id)
        assert summary.unconfirmed_assignments_count == 0
        assert summary.zero_cost_controls_count == 1
        assert summary.total_needs_attention == 1
        assert len(summary.zero_cost_controls) == 1
        assert summary.zero_cost_controls[0].name == "Unpriced"


class TestMaintenanceSummaryBoth:
    async def test_counts_mixed_state(self, db_session: AsyncSession) -> None:
        org = await _make_org(db_session)
        priced_unconfirmed = _make_control(org.id, name="P", annual_cost=Decimal("500"))
        unpriced_confirmed = _make_control(org.id, name="U", annual_cost=Decimal("0"))
        db_session.add_all([priced_unconfirmed, unpriced_confirmed])
        await db_session.flush()
        await _make_assignment(db_session, priced_unconfirmed, confirmed=False)
        await _make_assignment(db_session, unpriced_confirmed, confirmed=True)
        summary = await maintenance_summary(db_session, org.id)
        assert summary.unconfirmed_assignments_count == 1
        assert summary.zero_cost_controls_count == 1
        # Two distinct controls each in exactly one bucket → union = 2.
        assert summary.total_needs_attention == 2

    async def test_single_control_in_both_buckets_counted_once(
        self, db_session: AsyncSession
    ) -> None:
        """Issue #108: total_needs_attention is a count of DISTINCT controls.

        Pre-fix `total_needs_attention` summed the two `_count` fields directly,
        which mixed units (assignment rows + control rows). A single control
        with both zero annual_cost AND ≥1 unconfirmed assignment incremented
        both terms and was double-counted.
        """
        org = await _make_org(db_session)
        both_buckets = _make_control(org.id, name="Both", annual_cost=Decimal("0"))
        db_session.add(both_buckets)
        await db_session.flush()
        await _make_assignment(db_session, both_buckets, confirmed=False)
        await _make_assignment(
            db_session,
            both_buckets,
            sub_function=FairCamSubFunction.LEC_PREV_RESISTANCE,
            confirmed=False,
        )
        summary = await maintenance_summary(db_session, org.id)
        assert summary.unconfirmed_assignments_count == 2
        assert summary.zero_cost_controls_count == 1
        # One distinct control in both buckets → union = 1, NOT 2 + 1 = 3.
        assert summary.total_needs_attention == 1


class TestMaintenanceSummaryOrgScoped:
    async def test_other_org_data_excluded(self, db_session: AsyncSession) -> None:
        org_a = await _make_org(db_session)
        org_b = await _make_org(db_session)
        a_ctrl = _make_control(org_a.id, name="A", annual_cost=Decimal("0"))
        b_ctrl = _make_control(org_b.id, name="B", annual_cost=Decimal("0"))
        db_session.add_all([a_ctrl, b_ctrl])
        await db_session.flush()
        summary_a = await maintenance_summary(db_session, org_a.id)
        assert summary_a.zero_cost_controls_count == 1
        assert summary_a.zero_cost_controls[0].name == "A"


class TestMaintenanceSummaryEagerLoadsControl:
    async def test_unconfirmed_assignment_control_accessible(
        self, db_session: AsyncSession
    ) -> None:
        """maintenance_summary must eagerload .control so the maintenance page
        can render `a.control.domain` without lazy-load errors in async."""
        org = await _make_org(db_session)
        ctrl = _make_control(org.id, name="Eager", annual_cost=Decimal("500"))
        db_session.add(ctrl)
        await db_session.flush()
        await _make_assignment(db_session, ctrl, confirmed=False)
        summary = await maintenance_summary(db_session, org.id)
        assert len(summary.unconfirmed_assignments) == 1
        # This access must NOT trigger lazy-load (would fail in async)
        loaded_control = summary.unconfirmed_assignments[0].control
        assert loaded_control is not None
        assert loaded_control.name == "Eager"


class TestMaintenanceSummaryDefaultAnnualCost:
    async def test_control_with_default_annual_cost_counted_as_zero_cost(
        self, db_session: AsyncSession
    ) -> None:
        """A Control constructed without annual_cost defaults to Decimal('0')
        at flush time and counts as needing maintenance.

        Issue #66 replaced cost_model JSON with annual_cost Decimal (NOT NULL,
        default Decimal('0')); the maintenance check is now `annual_cost == 0`.
        """
        org = await _make_org(db_session)
        c = Control(
            organization_id=org.id,
            name="DefaultCost",
            description="x",
            type=ControlType.ADMINISTRATIVE,
            status=EntityStatus.ACTIVE,
            version="1.0",
        )
        db_session.add(c)
        await db_session.flush()
        assert c.annual_cost == Decimal("0")
        summary = await maintenance_summary(db_session, org.id)
        assert summary.zero_cost_controls_count == 1


class TestMaintenanceSortOrderByNameOnly:
    async def test_zero_cost_controls_sorted_by_name_only(self, db_session: AsyncSession) -> None:
        """Issue #90: the maintenance sort ORDER BY clause must reference only
        Control.name (NOT Control.domain).

        Pre-issue-90 the query sorted by (domain, name). After the column drop
        in Task 2 that reference would crash with AttributeError on the SQL
        compile step. Task 1 removes the column reference from the ORDER BY
        ahead of Task 2 to keep intermediate commits clean.

        Regression coverage: seed three zero-cost controls with names whose
        alphabetic order spans every domain bucket, and assert the returned
        order is alphabetic regardless of domain. Two of the rows alphabetize
        ahead of the LOSS_EVENT row that pre-issue-90 would have appeared
        first — a (domain, name) sort would put the LOSS_EVENT row first.
        """
        org = await _make_org(db_session)
        # Pre-issue-90 the maintenance_summary query sorted by (domain, name),
        # so a (LOSS_EVENT, "M") row would rank ahead of a (VARIANCE_MANAGEMENT,
        # "A") row. Post-90 the column is gone and the sort is name-only —
        # "A" < "M" < "Z" regardless of which domain each control's
        # assignments (which we don't even seed here) would have derived to.
        a_var = _make_control(
            org.id,
            name="A first",
            annual_cost=Decimal("0"),
        )
        m_loss = _make_control(
            org.id,
            name="M middle",
            annual_cost=Decimal("0"),
        )
        z_dsc = _make_control(
            org.id,
            name="Z last",
            annual_cost=Decimal("0"),
        )
        db_session.add_all([a_var, m_loss, z_dsc])
        await db_session.flush()

        summary = await maintenance_summary(db_session, org.id)
        names = [c.name for c in summary.zero_cost_controls]
        assert names == ["A first", "M middle", "Z last"], (
            f"maintenance_summary must sort zero_cost_controls by name only — got {names!r}"
        )


class TestMaintenanceBadgeCount:
    async def test_count_matches_summary_total(self, db_session: AsyncSession) -> None:
        org = await _make_org(db_session)
        priced_unconfirmed = _make_control(org.id, name="P", annual_cost=Decimal("500"))
        db_session.add(priced_unconfirmed)
        await db_session.flush()
        await _make_assignment(db_session, priced_unconfirmed, confirmed=False)
        unpriced = _make_control(org.id, name="U", annual_cost=Decimal("0"))
        db_session.add(unpriced)
        await db_session.flush()
        count = await maintenance_badge_count(db_session, org.id)
        summary = await maintenance_summary(db_session, org.id)
        assert count == summary.total_needs_attention
        assert count == 2

    async def test_count_zero_when_clean(self, db_session: AsyncSession) -> None:
        org = await _make_org(db_session)
        count = await maintenance_badge_count(db_session, org.id)
        assert count == 0
