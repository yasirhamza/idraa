"""FX rate persistence + conversion. Rate direction: usd_rate = code per USD.

Conversions return full-precision Decimal; rounding to per-currency minor units
happens only at format time (money_format / Babel), per the design's
"convert in full precision, round only at display" rule.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.currency import is_supported_code
from idraa.models.fx_rate import FX_RATE_MAX, FX_RATE_MIN, FxRate
from idraa.services.audit import AuditWriter


class RateNotFoundError(LookupError):
    """No active fx_rate for a non-USD code."""


class InvalidRateError(ValueError):
    """A rate outside the sane bounds [FX_RATE_MIN, FX_RATE_MAX], or USD."""


def _decimal_str(d: Decimal) -> str:
    """Normalize trailing zeros so audit pairs are human-readable.

    Numeric(20, 8) stores e.g. 3.75 as 3.75000000; str() would give
    "3.75000000" which breaks audit-pair equality checks against the
    value as the caller wrote it. normalize() strips trailing zeros:
    Decimal("3.75000000").normalize() == Decimal("3.75") → str "3.75".
    """
    return format(d.normalize(), "f")


class FxRateService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def active_rate(self, org_id: uuid.UUID, code: str) -> FxRate | None:
        if code == "USD":
            return None
        result = await self._db.execute(
            select(FxRate).where(
                FxRate.organization_id == org_id,
                FxRate.code == code,
                FxRate.is_active.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def _rate_value(self, org_id: uuid.UUID, code: str) -> Decimal:
        row = await self.active_rate(org_id, code)
        if row is None:
            raise RateNotFoundError(code)
        return row.usd_rate

    async def to_usd(self, amount: Decimal, code: str, org_id: uuid.UUID) -> Decimal:
        """Entry currency → USD: amount / usd_rate."""
        if code == "USD":
            return amount
        return amount / await self._rate_value(org_id, code)

    async def from_usd(self, usd: Decimal, code: str, org_id: uuid.UUID) -> Decimal:
        """USD → reporting currency: usd * usd_rate."""
        if code == "USD":
            return usd
        return usd * await self._rate_value(org_id, code)

    async def upsert_rate(
        self,
        org_id: uuid.UUID,
        code: str,
        usd_rate: Decimal,
        as_of_date: dt.date,
        source: str,
        *,
        user_id: uuid.UUID | None,
        ip_address: str | None = None,
    ) -> FxRate:
        """Insert a new active rate row; deactivate the prior active row; bump
        version. Audit-logged. USD is never stored.

        ``user_id=None`` is reserved for the seed/migration system path only; the
        future ADMIN route MUST thread the real ``user.id`` (security I-Sec-3)."""
        if code == "USD":
            raise InvalidRateError("USD is the base currency; it has no stored rate")
        if not (FX_RATE_MIN <= usd_rate <= FX_RATE_MAX):
            raise InvalidRateError(f"usd_rate {usd_rate} outside [{FX_RATE_MIN}, {FX_RATE_MAX}]")
        prior = await self.active_rate(org_id, code)
        next_version = (prior.version + 1) if prior is not None else 1
        if prior is not None:
            await self._db.execute(
                update(FxRate).where(FxRate.id == prior.id).values(is_active=False)
            )
        row = FxRate(
            organization_id=org_id,
            code=code,
            usd_rate=usd_rate,
            as_of_date=as_of_date,
            source=source,
            is_active=True,
            version=next_version,
        )
        self._db.add(row)
        await self._db.flush()
        prior_rate = _decimal_str(prior.usd_rate) if prior is not None else None
        await AuditWriter(self._db).log(
            organization_id=org_id,
            entity_type="fx_rate",
            entity_id=row.id,
            action="update" if prior is not None else "create",
            changes={
                "code": [None, code],
                "usd_rate": [prior_rate, _decimal_str(usd_rate)],
                "as_of_date": [None, str(as_of_date)],
            },
            user_id=user_id,
            ip_address=ip_address,
        )
        return row


async def is_selectable_currency(db: AsyncSession, org_id: uuid.UUID, code: str) -> bool:
    """True iff ``code`` may be chosen as a reporting/entry currency: USD, or an
    offered code that also has an active rate. The whitelist that keeps the DB
    from holding an unrenderable/unrated code (design §Predictability rule 7)."""
    if not is_supported_code(code):
        return False
    if code == "USD":
        return True
    return await FxRateService(db).active_rate(org_id, code) is not None
