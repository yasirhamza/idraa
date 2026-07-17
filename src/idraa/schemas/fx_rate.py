"""Pydantic schema for the ADMIN FX rate-admin form (Task 5, P2)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from idraa.currency import is_supported_code


class FxRateForm(BaseModel):
    code: str = Field(min_length=3, max_length=3)
    usd_rate: Decimal = Field(gt=Decimal("0"))
    as_of_date: dt.date
    source: str = Field(min_length=1, max_length=255)

    @field_validator("code")
    @classmethod
    def _supported(cls, v: str) -> str:
        if v == "USD":
            raise ValueError("USD is the base currency; it has no stored rate")
        if not is_supported_code(v):
            raise ValueError(f"{v} is not a supported currency")
        return v
