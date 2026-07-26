"""Convert real-space loss-magnitude form inputs (entry currency → USD) BEFORE
FAIR authoring. Methodology contract (design §Core semantics 3): only real-space
loss values (pl_*, sl_*) are scaled; TEF (frequency) and vulnerability (proportion
∈[0,1]) carry no currency dimension and are NEVER converted. The rate is Decimal
end-to-end (never Decimal(float)). Values are re-emitted as plain fixed-point
strings the downstream float parser accepts.

PR2 D17 (Task 4c): ``pl_max``/``sl_max`` (the expert form's authorable capacity
cap, a real-space dollar magnitude exactly like low/mode/high) are in
``_LOSS_KEYS`` too. Without them, a typed cap would be the ONLY loss-magnitude
sibling left unconverted inside this currency-scoped block — stored as if USD
while the paired mean/sigma WERE divided by the rate. Direction/magnitude: an
unconverted cap typed in a code with rate > 1 (e.g. SAR, 3.75) would be stored
that many times too large — for a rate near the schema's permitted ceiling,
effectively uncapped, silently defeating the one surface D17 makes visible and
overridable. The blank+mint path needs no conversion (minted server-side from
USD ``annual_revenue`` — see ``routes/scenario_form_helpers.py::
_resolve_capacity_max``); this only matters for a TYPED cap on the CREATE path.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

_LOSS_KEYS = (
    "pl_low",
    "pl_mode",
    "pl_high",
    "pl_max",
    "sl_low",
    "sl_mode",
    "sl_high",
    "sl_max",
)


def _dec_str(d: Decimal) -> str:
    return format(d.normalize(), "f")  # plain fixed-point, no sci-notation


def convert_loss_inputs_to_usd(
    raw: dict[str, str], entry_currency: str, usd_rate: Decimal
) -> dict[str, str]:
    """Return a copy of ``raw`` with loss-magnitude values converted from
    ``entry_currency`` to USD (value ÷ usd_rate). Non-loss keys untouched; blank
    loss values pass through; USD is the identity."""
    if entry_currency == "USD":
        return raw
    out = dict(raw)
    for key in _LOSS_KEYS:
        val = raw.get(key, "")
        if val is None or str(val).strip() == "":
            continue
        try:
            parsed = Decimal(str(val))
        except (InvalidOperation, ArithmeticError) as exc:
            raise ValueError(f"invalid loss amount: {val!r}") from exc
        if not parsed.is_finite():
            raise ValueError(f"invalid loss amount: {val!r}")
        out[key] = _dec_str(parsed / usd_rate)
    return out
