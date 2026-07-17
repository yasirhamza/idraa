"""Resolve the reporting currency + conversion for a run.

Both the web view-model and the PDF data builder call this so the two surfaces
render identical figures (design §render-parity). Conversion is usd * rate
(float scalar; the rate is Decimal). USD is the identity. A run pins the rate at
calc time in ``presentation_fx_snapshot``; legacy runs (no snapshot) fall back to
the org's current active rate, labeled "not pinned".
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class ReportingCurrency:
    code: str
    rate: Decimal  # USD -> reporting (1 for USD)
    is_pinned: bool
    provenance: str | None  # None for USD

    def convert(self, usd_value: float | None) -> float | None:
        # Non-finite / None pass through unchanged so the formatter's "—"
        # collapse fires (never inf*rate); mirrors the #306 finite discipline.
        if usd_value is None or not math.isfinite(usd_value):
            return usd_value
        if self.code == "USD":
            return usd_value
        return usd_value * float(self.rate)


def resolve_reporting_currency(run: Any, org: Any, active_rate_row: Any) -> ReportingCurrency:
    from idraa.currency import is_supported_code

    code = getattr(org, "preferred_currency", "USD") or "USD"
    if code == "USD" or not is_supported_code(code):
        # USD identity, or a code no longer offered (defense-in-depth): render USD.
        return ReportingCurrency("USD", Decimal("1"), is_pinned=True, provenance=None)
    snap = getattr(run, "presentation_fx_snapshot", None)
    if snap and snap.get("code") == code and is_supported_code(snap.get("code", "")):
        rate = Decimal(str(snap["usd_rate"]))
        prov = (
            f"Converted from USD at 1 USD = {rate} {code}, as-of "
            f"{snap.get('as_of_date')}, source {snap.get('source')}"
        )
        return ReportingCurrency(code, rate, is_pinned=True, provenance=prov)
    # Legacy run (no/old snapshot): current active rate, labeled.
    if active_rate_row is not None and active_rate_row.code == code:
        rate = active_rate_row.usd_rate
        prov = (
            f"Converted from USD at 1 USD = {rate} {code} (current rate — not "
            f"pinned for this run), as-of {active_rate_row.as_of_date}, "
            f"source {active_rate_row.source}"
        )
        return ReportingCurrency(code, rate, is_pinned=False, provenance=prov)
    # No rate available at all → render USD (identity) with a note.
    return ReportingCurrency(
        "USD", Decimal("1"), is_pinned=False, provenance=f"{code} rate unavailable — showing USD"
    )
