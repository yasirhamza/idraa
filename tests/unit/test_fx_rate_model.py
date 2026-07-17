from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from idraa.models.fx_rate import FxRate


def test_fx_rate_constructs_with_defaults() -> None:
    r = FxRate(
        organization_id=uuid.uuid4(),
        code="SAR",
        usd_rate=Decimal("3.75"),
        as_of_date=dt.date(2026, 6, 14),
        source="SAMA",
    )
    assert r.id is not None
    assert r.is_active is True
    assert r.version == 1
    assert r.usd_rate == Decimal("3.75")
