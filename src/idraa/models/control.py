"""Control ORM — security control definition."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, Enum, ForeignKey, Numeric, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from idraa.db import Base
from idraa.models.enums import (
    ControlDomain,
    ControlImplementationStage,
    ControlSource,
    ControlType,
    EntityStatus,
)
from idraa.models.mixins import IdMixin, OrgMixin, TimestampMixin


class Control(IdMixin, TimestampMixin, OrgMixin, Base):
    __tablename__ = "controls"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # NOTE: 'domain' column REMOVED (issue #90). Domains derive from
    # assignments at query time via the Control.domains property below.
    # FAIR-CAM Standard §2.2 (p5): domain is a property of sub-functions,
    # not controls — a single control can span multiple domains.
    # NOTE: 'function' column REMOVED (spec §4.1 Decision 1 — classical taxonomy dropped).
    # NOTE: 'control_strength', 'control_reliability', 'control_coverage' REMOVED (spec §6.4).
    #       Effectiveness is now per-assignment via ControlFunctionAssignment.
    type: Mapped[ControlType] = mapped_column(
        Enum(ControlType, native_enum=False, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )

    annual_cost: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        default=Decimal("0"),
        server_default="0",
        doc="Annual OPEX cost. $0 means 'no cost set' (maintenance alert triggers).",
    )
    nist_csf_functions: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    iso_27001_domains: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    compliance_mappings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    skill_requirements: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    technology_dependencies: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    applicable_industries: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    applicable_org_sizes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    status: Mapped[EntityStatus] = mapped_column(
        Enum(EntityStatus, native_enum=False, values_callable=lambda x: [e.value for e in x]),
        default=EntityStatus.ACTIVE,
        nullable=False,
    )
    # Implementation maturity — SEPARATE axis from `status` (issue #395).
    # `status` is publish/soft-delete lifecycle; this is how far along the
    # org is in actually deploying the control. ONLY `active` contributes to
    # the FAIR-CAM composition (gate applied in services/runs.py via
    # ControlImplementationStage.contributes_to_composition).
    implementation_stage: Mapped[ControlImplementationStage] = mapped_column(
        Enum(
            ControlImplementationStage,
            native_enum=False,
            values_callable=lambda x: [e.value for e in x],
        ),
        default=ControlImplementationStage.ACTIVE,
        server_default=ControlImplementationStage.ACTIVE.value,
        nullable=False,
    )
    version: Mapped[str] = mapped_column(String(32), default="1.0", nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # P2b — library reference; NULL for custom controls.
    library_pin: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # #438 — verbatim copy of the library-entry values cloned at adopt time
    # (fields + assignments), NEVER touched by user edits. Enables a clean
    # 3-way re-sync diff (library change vs analyst edit); NULL for custom
    # controls and for adoptions that predate the column (coarse diff only).
    adopted_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # P2b — provenance. CUSTOM (default) vs LIBRARY_DERIVED (adopted from catalog).
    source: Mapped[ControlSource] = mapped_column(
        Enum(ControlSource, native_enum=False, values_callable=lambda x: [e.value for e in x]),
        default=ControlSource.CUSTOM,
        server_default=ControlSource.CUSTOM.value,
        nullable=False,
    )

    # Per-sub-function effectiveness assignments.
    # selectin loading: adapter + snapshot writer both need assignments without
    # a second query. Hard-cap at 1 per control during PR iota -> PR kappa window
    # (Pydantic + service layer enforce cap; DB enforces uniqueness per §4.4).
    #
    # Deterministic ordering (plan-gate fix Arch-I1, issue #90): `assignments[0]`
    # in adapter code (run_executor._v3_to_fair_cam_control representative-domain
    # pick) must be stable across SQLA reloads so the FAIR-CAM bridge choice is
    # reproducible across run executions.
    #
    # `id` tiebreaker: `created_at` is DateTime(timezone=True) with microsecond
    # precision and is NOT unique — bulk-import loops that create multiple
    # assignments within one function call routinely collide. Adding the UUID
    # primary key as a secondary sort key guarantees a total ordering so
    # `assignments[0]` is truly deterministic across reloads.
    assignments: Mapped[list[ControlFunctionAssignment]] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "ControlFunctionAssignment",
        back_populates="control",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ControlFunctionAssignment.created_at, ControlFunctionAssignment.id",
    )

    @property
    def domains(self) -> frozenset[ControlDomain]:
        """Distinct FAIR-CAM domains spanned by this control's assignments.

        FAIR-CAM Standard §2.2 (p5): domain is a property of sub-functions,
        not controls. A control with assignments across multiple domains
        legitimately spans all of them (issue #90).

        Returns an empty frozenset when the control has no assignments
        yet (transient state during creation, or post-row-create before
        flush).
        """
        from idraa.models.enums import subfunction_to_domain

        return frozenset(subfunction_to_domain(a.sub_function) for a in (self.assignments or []))
