"""Organization form DTO.

Wraps the raw HTML form POST into a validated Pydantic model that the
route handler can copy onto the ORM row. Three non-trivial coercions:

1. Comma-separated string → ``list[str]`` for the four CSV-backed JSON
   columns (geographic_regions, compliance_requirements,
   regulatory_environment, technology_stack). Done via a ``mode="before"``
   validator so the plain form-field value is the input, not the post-
   cast list (which Pydantic would reject as str).
2. ``str`` → ``Decimal`` for the four money columns — Pydantic's built-in
   coercion handles this; no custom validator needed.
3. ``bool`` for ``has_cyber_insurance`` — HTML sends ``"on"`` when the
   checkbox is checked and omits the field entirely when unchecked.
   Pydantic's bool coercion would FAIL on ``"on"``, so the route handler
   constructs the bool via presence-check (``"has_cyber_insurance" in
   form_data``) before handing the dict to this schema.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from idraa.models.enums import (
    IndustrySubSector,
    IndustryType,
    OrganizationSize,
    RiskAppetite,
    SecurityMaturity,
)
from idraa.schemas._csv import split_csv

# Matches Numeric(18, 2) on the corresponding DB columns — 16 digits left
# of the decimal, 2 right. Reject beyond this at the 400-path rather than
# surfacing a Postgres NumericValueOutOfRange as a 500.
_MONEY_MAX = Decimal("9999999999999999.99")


class OrganizationForm(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    industry_type: IndustryType
    industry_sub_sector: IndustrySubSector | None = Field(default=None)
    naics_code: str | None = Field(default=None, max_length=12)
    organization_size: OrganizationSize
    employee_count: int | None = None
    annual_revenue: Decimal | None = Field(
        default=None, ge=Decimal("0"), le=_MONEY_MAX, max_digits=18, decimal_places=2
    )
    geographic_regions: list[str] = []
    headquarters_location: str | None = Field(default=None, max_length=255)
    security_maturity: SecurityMaturity = SecurityMaturity.BASIC
    annual_security_budget: Decimal | None = Field(
        default=None, ge=Decimal("0"), le=_MONEY_MAX, max_digits=18, decimal_places=2
    )
    has_cyber_insurance: bool = False
    cyber_insurance_limit: Decimal | None = Field(
        default=None, ge=Decimal("0"), le=_MONEY_MAX, max_digits=18, decimal_places=2
    )
    cyber_insurance_deductible: Decimal | None = Field(
        default=None, ge=Decimal("0"), le=_MONEY_MAX, max_digits=18, decimal_places=2
    )
    risk_appetite: RiskAppetite = RiskAppetite.MODERATE
    loss_tolerance_amount: Decimal | None = Field(
        default=None, ge=Decimal("0"), le=_MONEY_MAX, max_digits=18, decimal_places=2
    )
    loss_tolerance_probability: float | None = Field(default=None, ge=0.0, le=1.0)
    compliance_requirements: list[str] = []
    regulatory_environment: list[str] = []
    technology_stack: list[str] = []
    cloud_usage: str | None = Field(default=None, max_length=32)
    digital_maturity: str | None = Field(default=None, max_length=32)
    preferred_currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    preferred_language: str = Field(default="en", min_length=2, max_length=8)

    @field_validator(
        "geographic_regions",
        "compliance_requirements",
        "regulatory_environment",
        "technology_stack",
        mode="before",
    )
    @classmethod
    def _csv_to_list(cls, v: object) -> list[str]:
        return split_csv(v)  # type: ignore[arg-type]

    @field_validator(
        "employee_count",
        "annual_revenue",
        "annual_security_budget",
        "cyber_insurance_limit",
        "cyber_insurance_deductible",
        "loss_tolerance_amount",
        "loss_tolerance_probability",
        mode="before",
    )
    @classmethod
    def _blank_to_none(cls, v: object) -> object:
        """HTML number inputs send ``""`` when the user blanks the field.

        Pydantic's ``int | None`` and ``Decimal | None`` reject empty
        strings (only ``None`` is accepted), so without this validator
        clearing an optional money/count field 400s the whole form. The
        coercion accepts empty + whitespace-only strings; real
        validation errors (negative values, non-numeric input, etc.)
        still surface as ``ValidationError``.

        Hotfix for first-real-user 400 on POST /organization.
        """
        if isinstance(v, str) and v.strip() == "":
            return None
        return v
