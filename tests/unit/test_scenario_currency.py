from __future__ import annotations

import math
from decimal import Decimal

import pytest
from fair_cam.quantile_pooling import lognormal_from_quantiles

from idraa.services.scenario_currency import convert_loss_inputs_to_usd


def test_pert_loss_values_divided_by_rate() -> None:
    raw = {
        "pl_low": "3750000",
        "pl_mode": "7500000",
        "pl_high": "15000000",
        "tef_low": "0.1",
        "tef_high": "0.5",
        "vuln_low": "0.2",
        "vuln_mode": "0.5",
        "vuln_high": "0.8",
    }
    out = convert_loss_inputs_to_usd(raw, "SAR", Decimal("3.75"))
    assert out["pl_low"] == "1000000"
    assert out["pl_mode"] == "2000000"
    assert out["pl_high"] == "4000000"
    assert out["tef_low"] == "0.1" and out["tef_high"] == "0.5"
    assert out["vuln_low"] == "0.2" and out["vuln_high"] == "0.8"


def test_secondary_loss_converted_when_present_blank_left_blank() -> None:
    raw = {"pl_low": "3.75", "pl_high": "3.75", "sl_low": "", "sl_mode": "", "sl_high": ""}
    out = convert_loss_inputs_to_usd(raw, "SAR", Decimal("3.75"))
    assert out["pl_low"] == "1"
    assert out["sl_low"] == "" and out["sl_high"] == ""


def test_usd_is_identity() -> None:
    raw = {"pl_low": "100", "pl_mode": "200", "pl_high": "300"}
    assert convert_loss_inputs_to_usd(raw, "USD", Decimal("1")) == raw


def test_convert_non_numeric_loss_raises_value_error() -> None:
    """convert_loss_inputs_to_usd raises ValueError on non-numeric loss (Fix B)."""
    with pytest.raises(ValueError, match="invalid loss amount"):
        convert_loss_inputs_to_usd({"pl_low": "abc"}, "SAR", Decimal("3.75"))


def test_convert_comma_formatted_loss_raises_value_error() -> None:
    """convert_loss_inputs_to_usd raises ValueError on comma-formatted loss (Fix B)."""
    with pytest.raises(ValueError, match="invalid loss amount"):
        convert_loss_inputs_to_usd({"pl_low": "1,000"}, "SAR", Decimal("3.75"))


def test_convert_nan_loss_raises_value_error() -> None:
    """convert_loss_inputs_to_usd raises ValueError on NaN loss value (Fix B)."""
    with pytest.raises(ValueError, match="invalid loss amount"):
        convert_loss_inputs_to_usd({"pl_low": "NaN"}, "SAR", Decimal("3.75"))


# ── PR2 D17 (Task 4c): pl_max / sl_max are loss-magnitude siblings ────────


def test_pl_max_and_sl_max_divided_by_rate() -> None:
    """A TYPED capacity cap is a real-space dollar magnitude exactly like
    low/mode/high — it must convert identically, or it becomes the ONLY
    unconverted loss-magnitude sibling inside this currency-scoped block."""
    raw = {
        "pl_low": "3750000",
        "pl_high": "15000000",
        "pl_max": "37500000",
        "sl_low": "375000",
        "sl_high": "1500000",
        "sl_max": "3750000",
    }
    out = convert_loss_inputs_to_usd(raw, "SAR", Decimal("3.75"))
    assert out["pl_max"] == "10000000"
    assert out["sl_max"] == "1000000"


def test_blank_max_left_blank_by_conversion() -> None:
    """The blank+mint path needs no conversion (minted from USD
    annual_revenue server-side) — a blank pl_max/sl_max passes through."""
    raw = {"pl_low": "3750000", "pl_high": "15000000", "pl_max": "", "sl_max": ""}
    out = convert_loss_inputs_to_usd(raw, "SAR", Decimal("3.75"))
    assert out["pl_max"] == "" and out["sl_max"] == ""


def test_lognormal_transform_sigma_invariant_mean_shifts() -> None:
    # Methodology pin: authoring (low/rate, high/rate) == sigma unchanged,
    # mean shifted by exactly -ln(rate). rate = code-per-USD.
    rate = Decimal("3.75")
    sar_low, sar_high = 3_750_000.0, 18_750_000.0
    usd = convert_loss_inputs_to_usd(
        {"pl_low": str(sar_low), "pl_high": str(sar_high)}, "SAR", rate
    )
    authored = lognormal_from_quantiles(float(usd["pl_low"]), float(usd["pl_high"]))
    sar_authored = lognormal_from_quantiles(sar_low, sar_high)
    assert authored["sigma"] == pytest.approx(sar_authored["sigma"])
    assert authored["mean"] == pytest.approx(sar_authored["mean"] - math.log(float(rate)))
