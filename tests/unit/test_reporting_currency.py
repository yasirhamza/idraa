from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import ClassVar

from idraa.services.reporting_currency import resolve_reporting_currency


class _Org:
    preferred_currency = "EUR"


class _Run:
    presentation_fx_snapshot: ClassVar[dict] = {
        "code": "EUR",
        "usd_rate": "0.92",
        "as_of_date": "2026-06-14",
        "source": "ECB",
    }


def test_usd_org_is_identity() -> None:
    class _U:  # USD org
        preferred_currency = "USD"

    rc = resolve_reporting_currency(_Run(), _U(), active_rate_row=None)
    assert rc.code == "USD"
    assert rc.convert(2_610_000.0) == 2_610_000.0
    assert rc.is_pinned is True  # USD needs no rate; treat as canonical
    assert rc.provenance is None


def test_pinned_snapshot_used_over_live() -> None:
    rc = resolve_reporting_currency(_Run(), _Org(), active_rate_row=None)
    assert rc.code == "EUR"
    assert rc.rate == Decimal("0.92")
    assert rc.convert(1_000_000.0) == 920_000.0  # usd * rate
    assert rc.is_pinned is True
    assert "0.92" in rc.provenance and "ECB" in rc.provenance


def test_legacy_run_falls_back_to_live_rate_labeled() -> None:
    class _LegacyRun:
        presentation_fx_snapshot = None

    class _ActiveRate:
        code = "EUR"
        usd_rate = Decimal("0.93")
        as_of_date = dt.date(2026, 6, 15)
        source = "ECB"

    rc = resolve_reporting_currency(_LegacyRun(), _Org(), active_rate_row=_ActiveRate())
    assert rc.code == "EUR" and rc.rate == Decimal("0.93")
    assert rc.is_pinned is False
    assert "not pinned" in rc.provenance.lower()


def test_no_rate_available_fails_soft_to_usd() -> None:
    # Non-USD org, no snapshot, no active rate → fail-soft to USD identity + note.
    class _LegacyRun:
        presentation_fx_snapshot = None

    rc = resolve_reporting_currency(_LegacyRun(), _Org(), active_rate_row=None)
    assert rc.code == "USD"
    assert rc.convert(2_610_000.0) == 2_610_000.0
    assert rc.provenance is not None and "unavailable" in rc.provenance.lower()


def test_convert_passes_through_none_and_nonfinite() -> None:
    import math

    rc = resolve_reporting_currency(_Run(), _Org(), active_rate_row=None)
    assert rc.convert(None) is None
    assert math.isinf(rc.convert(math.inf))  # inf*rate would be inf anyway; not 0/NaN
