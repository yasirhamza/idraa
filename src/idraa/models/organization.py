"""Organization ORM — single row in phase 1; rich profile drives calibration + context.

Phase 1 ships one org hardcoded at the application layer (see
``CLAUDE.md`` — ``organization_id`` on every business table defers
multi-tenancy without a future schema rewrite). The column shape here is
deliberately richer than the phase-1 UI needs so later phases can surface
industry / size / maturity context to the calibration layer without an
additive migration per field.

Cross-dialect notes:
- ``Enum(..., native_enum=False)`` emits ``VARCHAR`` rather than a Postgres
  ENUM type so enum additions don't need ALTER TYPE migrations.
- ``JSON`` columns use ``default=list`` / ``default=dict`` to keep the
  server-side shape stable across SQLite (text blob) and Postgres (JSONB).
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Enum, Float, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from idraa.db import Base

if TYPE_CHECKING:
    from idraa.models.scenario import Scenario
from idraa.models.enums import (
    IndustrySubSector,
    IndustryType,
    OrganizationSize,
    RiskAppetite,
    SecurityMaturity,
)
from idraa.models.mixins import IdMixin, TimestampMixin
from idraa.services.calibration import (
    org_industry_slug,
    revenue_tier_from_annual_revenue,
)


class Organization(IdMixin, TimestampMixin, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    industry_type: Mapped[IndustryType] = mapped_column(
        Enum(IndustryType, native_enum=False, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    industry_sub_sector: Mapped[IndustrySubSector | None] = mapped_column(
        Enum(IndustrySubSector, native_enum=False, values_callable=lambda x: [e.value for e in x]),
        nullable=True,
    )
    naics_code: Mapped[str | None] = mapped_column(String(12), nullable=True)
    organization_size: Mapped[OrganizationSize] = mapped_column(
        Enum(OrganizationSize, native_enum=False, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    employee_count: Mapped[int | None] = mapped_column(nullable=True)
    annual_revenue: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    geographic_regions: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    headquarters_location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    security_maturity: Mapped[SecurityMaturity] = mapped_column(
        Enum(SecurityMaturity, native_enum=False, values_callable=lambda x: [e.value for e in x]),
        default=SecurityMaturity.BASIC,
        nullable=False,
    )
    annual_security_budget: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    has_cyber_insurance: Mapped[bool] = mapped_column(default=False, nullable=False)
    cyber_insurance_limit: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    cyber_insurance_deductible: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    risk_appetite: Mapped[RiskAppetite] = mapped_column(
        Enum(RiskAppetite, native_enum=False, values_callable=lambda x: [e.value for e in x]),
        default=RiskAppetite.MODERATE,
        nullable=False,
    )
    loss_tolerance_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    loss_tolerance_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    compliance_requirements: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    regulatory_environment: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    technology_stack: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    cloud_usage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    digital_maturity: Mapped[str | None] = mapped_column(String(32), nullable=True)
    preferred_currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    preferred_language: Mapped[str] = mapped_column(String(8), default="en", nullable=False)

    # Reverse side of Scenario.organization (many-to-one). lazy="raise"
    # guards against accidentally unbounded reverse loads (e.g., a template
    # doing org.scenarios triggering load of all scenarios in the org).
    # Use selectinload(Organization.scenarios) explicitly when needed.
    scenarios: Mapped[list[Scenario]] = relationship(
        "Scenario",
        back_populates="organization",
        lazy="raise",
    )

    @property
    def revenue_tier(self) -> str:
        """IRIS revenue-tier slug derived from annual_revenue.

        Live-derived, never snapshot. Issue #88 moved this off the Scenario
        row so wizard pre-fill always reflects current org state.
        """
        return revenue_tier_from_annual_revenue(self.annual_revenue)

    @property
    def industry_slug(self) -> str:
        """fair_cam industry slug derived from industry_type.

        Live-derived, never snapshot. Issue #88 moved this off the Scenario
        row so wizard pre-fill always reflects current org state.
        """
        return org_industry_slug(self.industry_type)
