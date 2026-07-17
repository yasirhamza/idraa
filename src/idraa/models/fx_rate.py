"""Admin-managed FX rate rows. Single source for entry→USD and USD→reporting.

Rate DIRECTION (documented, methodology-checked): ``usd_rate`` = units of
``code`` per 1 USD (e.g. SAR 3.75). Therefore:
    usd          = entry_amount / usd_rate          (entry currency → USD)
    reporting    = usd          * usd_rate(target)  (USD → reporting currency)
USD is implicit (rate 1.0) and never stored. Editing a rate inserts a NEW row,
deactivates the prior active row, and bumps ``version`` — history is retained.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    Index,
    Integer,
    Numeric,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from idraa.db import Base
from idraa.models.mixins import IdMixin, OrgMixin, TimestampMixin


class FxRate(IdMixin, TimestampMixin, OrgMixin, Base):
    __tablename__ = "fx_rates"

    code: Mapped[str] = mapped_column(String(3), nullable=False)
    usd_rate: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    as_of_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    source: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    def __init__(self, **kwargs: Any) -> None:
        # Mirror Python-side defaults into __init__ (SQLAlchemy fires them only
        # at flush) — same pattern as OverlayDefinition.
        kwargs.setdefault("is_active", True)
        kwargs.setdefault("version", 1)
        super().__init__(**kwargs)

    # FX_RATE_MIN/MAX bound real ISO-4217 rates vs USD (KWD≈0.307; weak
    # currencies run thousands-per-USD). A near-zero rate would make
    # to_usd = amount / usd_rate divide-amplify astronomical values into stored
    # FAIR distributions (finite analogue of the #306 inf corruption); an
    # absurd rate zeroes a real loss. Bounds are generous but finite.
    __table_args__ = (
        CheckConstraint("usd_rate >= 0.000001 AND usd_rate <= 100000", name="ck_fx_rate_range"),
        CheckConstraint("length(code) = 3", name="ck_fx_rate_code_len"),
        # DB-enforce one active row per (org, code) — without this, two actives
        # make active_rate's scalar_one_or_none() raise MultipleResultsFound on
        # every render. Partial unique index per the sme.py precedent.
        Index(
            "ux_fx_rate_active_per_code",
            "organization_id",
            "code",
            unique=True,
            sqlite_where=text("is_active = 1"),
            postgresql_where=text("is_active = TRUE"),
        ),
    )


# Service-layer validation bounds (mirror the DB CheckConstraint so the future
# admin route returns a clean 4xx instead of an IntegrityError 500).
FX_RATE_MIN = Decimal("0.000001")
FX_RATE_MAX = Decimal("100000")
