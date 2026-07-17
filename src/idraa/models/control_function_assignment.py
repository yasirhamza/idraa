"""ControlFunctionAssignment ORM — per-sub-function effectiveness assignment.

Each Control row may have at most one assignment per (control_id, sub_function) pair
enforced by the DB UNIQUE constraint uq_cfa_control_sub_function (spec §4.4, OQ3).
Multiple assignments per Control are permitted from PR kappa onwards; the Pydantic
ControlForm model_validator catches duplicate sub_functions before they reach the DB.

# Phase 2: VF/VD columns (variance_freq_per_year, variance_duration_days) deferred
#           -- see audit §10.3 and spec §4.7.
# Phase 2: derived_from_assignment_id index -- add when column is populated.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from idraa.db import Base
from idraa.models.enums import FairCamSubFunction
from idraa.models.mixins import IdMixin, OrgMixin, TimestampMixin


class ControlFunctionAssignment(IdMixin, TimestampMixin, OrgMixin, Base):
    """One sub-function effectiveness triple per Control row.

    Columns:
      sub_function      -- FairCamSubFunction slug (frozen after PR iota)
      capability_value  -- NULLABLE: NULL for TIME/CURRENCY-unit backfill rows (OQ1)
      coverage          -- Deployment breadth [0,1] (Standard §2.4.2 pages 6-7)
      reliability       -- Probability of consistent performance [0,1] (§2.4.3 page 7)
      confirmed_by_user_at -- NULL = backfilled/unconfirmed; set via confirm endpoint (OQ4)
      derived_from_assignment_id -- Reserved for computed-virtual DSC_CORR_MISALIGNED rows;
                                    all NULL in PR iota (spec §4.9)
      measured_at       -- When effectiveness was last assessed (set via confirm endpoint)
      measured_by       -- Who last assessed (FK -> users.id; set via confirm endpoint)
    """

    __tablename__ = "control_function_assignments"

    __table_args__ = (
        UniqueConstraint("control_id", "sub_function", name="uq_cfa_control_sub_function"),
        CheckConstraint(
            "capability_value IS NULL OR capability_value >= 0.0",
            name="ck_cfa_capability_nonneg",
        ),
        CheckConstraint(
            "coverage >= 0.0 AND coverage <= 1.0",
            name="ck_cfa_coverage_range",
        ),
        CheckConstraint(
            "reliability >= 0.0 AND reliability <= 1.0",
            name="ck_cfa_reliability_range",
        ),
        CheckConstraint(
            "sub_function != 'dsc_corr_misaligned' OR derived_from_assignment_id IS NOT NULL",
            name="ck_cfa_virtual_requires_derived",
        ),
        # Mirror: alembic 1297897c44f5_pr_mu_1_capability_value_upper_bound.
        # Drift guard is informal — extend snapshot_orm_shape to capture CHECK
        # constraint sqltext (see follow-up issue) for a real automated check.
        CheckConstraint(
            "capability_value IS NULL OR capability_value <= 1e10",
            name="ck_cfa_capability_upper_bound",
        ),
    )

    control_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("controls.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Project convention: store StrEnum columns via
    # ``Enum(<StrEnum>, native_enum=False, values_callable=lambda x: [e.value for e in x])``
    # (mirrors models/scenario.py).  The ``values_callable`` is REQUIRED — without
    # it SA serializes by enum NAME and the Alembic CHECK constraint (which
    # enforces VALUES) rejects every insert.  This way SA hydrates rows back
    # into the FairCamSubFunction enum member on read, so ``a.sub_function.value``
    # works at every callsite (services, templates, snapshot writer, audit).
    sub_function: Mapped[FairCamSubFunction] = mapped_column(
        Enum(FairCamSubFunction, native_enum=False, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    capability_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    coverage: Mapped[float] = mapped_column(Float, nullable=False)
    reliability: Mapped[float] = mapped_column(Float, nullable=False)
    confirmed_by_user_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Phase 2: reserved for computed-virtual assignments (spec §4.9).
    # Service layer asserts this is NULL during PR iota write paths.
    derived_from_assignment_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("control_function_assignments.id", ondelete="SET NULL"),
        nullable=True,
    )
    measured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    measured_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Relationships
    control: Mapped[Control] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Control", back_populates="assignments"
    )
