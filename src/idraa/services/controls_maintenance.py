"""Maintenance summary for the post-import alert UX (issue #87).

A control "needs maintenance" if either:
- One or more ControlFunctionAssignment rows have confirmed_by_user_at IS NULL.
- Its ``annual_cost`` column equals ``Decimal('0')`` — the importer + wizard
  default for "placeholder cost not yet set".

Both signals indicate the importer's placeholder defaults have not yet been
operator-reviewed. The summary is consumed by:

- `MaintenanceBadgeCountMiddleware` (count-only, per-request, light path).
- `/controls/maintenance` route (full materialization, render the page).
- Post-import flash (count + deep-link).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from idraa.models.control import Control
from idraa.models.control_function_assignment import ControlFunctionAssignment


@dataclass(frozen=True)
class MaintenanceSummary:
    """Aggregate of org-wide maintenance state for /controls/maintenance UI."""

    unconfirmed_assignments_count: int
    zero_cost_controls_count: int
    zero_cost_controls: list[Control] = field(default_factory=list)
    unconfirmed_assignments: list[ControlFunctionAssignment] = field(default_factory=list)

    # NOTE: This is a @property, not a dataclass field — it will NOT appear
    # in dataclasses.asdict(summary) output. If a future caller needs to
    # serialize the summary across a layer boundary, prefer this property's
    # return value or convert to a real field with an __post_init__ computation.
    #
    # Counts DISTINCT controls needing attention (a control with both zero
    # cost AND ≥1 unconfirmed assignment is counted once, not twice). The
    # two `_count` fields above mix units (assignment rows vs control rows)
    # and would double-count such a control if summed — issue #108.
    @property
    def total_needs_attention(self) -> int:
        zero_cost_ids = {c.id for c in self.zero_cost_controls}
        unconfirmed_ids = {a.control_id for a in self.unconfirmed_assignments}
        return len(zero_cost_ids | unconfirmed_ids)


def _is_zero_cost(c: Control) -> bool:
    """True if the control's annual_cost is Decimal('0').

    The maintenance alert surfaces controls with annual_cost == 0 so
    admins can confirm "$0 is correct" or set a real cost. Importer-
    created controls + wizard-created controls that leave the field
    blank both default to 0.
    """
    return c.annual_cost == Decimal("0")


async def maintenance_summary(
    db: AsyncSession,
    org_id: uuid.UUID,
) -> MaintenanceSummary:
    """Materialize controls + assignments needing maintenance.

    Two queries:
    1. All controls for the org → Python filter → zero-cost subset.
    2. ControlFunctionAssignment rows where confirmed_by_user_at IS NULL,
       with eager-loaded .control (for downstream per-domain grouping).
    """
    all_controls_stmt = (
        select(Control)
        .where(Control.organization_id == org_id)
        # Sort by name only (issue #90): Control.domain column is removed in
        # Task 2 of the issue-90 PR. Pre-issue-90 the order was (domain, name);
        # the secondary grouping by domain was arbitrary and post-90 a control
        # belongs to a SET of domains, not a single one, so no trivial scalar
        # ordering on domain exists. Grouped-by-domain display, if desired,
        # belongs in the view-model layer.
        .order_by(Control.name)
    )
    all_controls_result = await db.execute(all_controls_stmt)
    zero_cost_controls = [c for c in all_controls_result.scalars().all() if _is_zero_cost(c)]

    # Issue #157: chain the eager-load to Control.assignments so the
    # route's `if a.control and a.control.assignments:` gate does not
    # trigger an implicit lazy-load under AsyncSession. While
    # Control.assignments is declared lazy="selectin" at the mapper level,
    # selectin propagation from a sub-load (back-ref Control loaded via
    # selectinload of ControlFunctionAssignment.control) is not guaranteed
    # under all session/identity-map states; explicit chaining makes the
    # loader deterministic and prevents sqlalchemy.exc.MissingGreenlet.
    unconfirmed_stmt = (
        select(ControlFunctionAssignment)
        .options(selectinload(ControlFunctionAssignment.control).selectinload(Control.assignments))
        .where(
            ControlFunctionAssignment.organization_id == org_id,
            ControlFunctionAssignment.confirmed_by_user_at.is_(None),
        )
        .order_by(ControlFunctionAssignment.sub_function)
    )
    unconfirmed_result = await db.execute(unconfirmed_stmt)
    unconfirmed_assignments = list(unconfirmed_result.scalars().all())

    return MaintenanceSummary(
        unconfirmed_assignments_count=len(unconfirmed_assignments),
        zero_cost_controls_count=len(zero_cost_controls),
        zero_cost_controls=zero_cost_controls,
        unconfirmed_assignments=unconfirmed_assignments,
    )


async def maintenance_badge_count(
    db: AsyncSession,
    org_id: uuid.UUID,
) -> int:
    """Lightweight count for the per-request middleware.

    Same shape as maintenance_summary but discards rows after counting.
    Acceptable cost for phase-1 single-org volume.
    """
    # Phase-1 perf trade (issue #109): materializes full rows then discards.
    # Replace with a dialect-portable COUNT(...) FILTER(...) only if profiling
    # surfaces this in the per-request hot path.
    summary = await maintenance_summary(db, org_id)
    return summary.total_needs_attention
