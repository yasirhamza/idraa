"""Appetite verdict strip derivations (epic #547 P1 / #545 scope A).

Methodology anchors:
- verdicts route through _verdict_from_probability (single decision rule) —
  the run page can NEVER disagree with the dashboard on the same curve.
- times_over = p_without / tol_probability, only when verdict_without ==
  "exceeds" (a "0.4x over" badge is nonsense — None below threshold). v3
  view-model derivation, not FAIR-grounded.
- loss_at_tol_prob inverts the with-controls curve at tol_probability using
  the EXACT inverse of _interpolate_exceedance_probability (linear in
  probability per bracketing segment, endpoint-clamped).
- headroom = tol_amount - loss_at_tol_prob (negative allowed). v3 view-model
  derivation, not FAIR-grounded.
- BASIS: dual_lec + tolerance are BOTH already reporting-currency-converted by
  the route; the strip therefore reads one currency space (no sample/curve mix).
"""

import pytest

from idraa.services.dashboard_view_model import (
    appetite_strip,
    interpolate_loss_at_probability,
)

_CURVE_WITHOUT = [
    {"loss": 1_000.0, "probability": 1.0},
    {"loss": 8_000_000.0, "probability": 0.40},
    {"loss": 100_000_000.0, "probability": 0.0},
]
_CURVE_WITH = [
    {"loss": 1_000.0, "probability": 1.0},
    {"loss": 2_000_000.0, "probability": 0.05},
    {"loss": 8_000_000.0, "probability": 0.01},
    {"loss": 100_000_000.0, "probability": 0.0},
]
_TOL = {"amount": 8_000_000.0, "probability": 0.05}
_DUAL = {"without_controls": _CURVE_WITHOUT, "with_controls": _CURVE_WITH}


def test_hand_math_verdicts_and_times_over():
    s = appetite_strip(_DUAL, _TOL)
    # p_without at $8M is a curve point: exactly 0.40
    assert s["p_without"] == pytest.approx(0.40)
    assert s["verdict_without"] == "exceeds"
    assert s["times_over"] == pytest.approx(0.40 / 0.05)  # 8x
    # p_with at $8M is a curve point: exactly 0.01 -> within
    assert s["p_with"] == pytest.approx(0.01)
    assert s["verdict_with"] == "within"


def test_loss_at_probability_exact_inverse():
    # p=0.05 is a with-curve point at $2M
    assert interpolate_loss_at_probability(_CURVE_WITH, 0.05) == pytest.approx(2_000_000.0)
    # halfway in p between (2M, .05) and (8M, .01): p=0.03 -> loss = 2M + 0.5*(8M-2M)
    assert interpolate_loss_at_probability(_CURVE_WITH, 0.03) == pytest.approx(5_000_000.0)


def test_headroom_sign():
    s = appetite_strip(_DUAL, _TOL)
    assert s["loss_at_tol_prob"] == pytest.approx(2_000_000.0)
    assert s["headroom"] == pytest.approx(6_000_000.0)


def test_times_over_none_when_within():
    within = {"without_controls": _CURVE_WITH, "with_controls": _CURVE_WITH}
    s = appetite_strip(within, _TOL)
    assert s["verdict_without"] == "within" and s["times_over"] is None


def test_none_without_tolerance_or_curve():
    assert appetite_strip(_DUAL, None) is None
    assert appetite_strip(None, _TOL) is None
    assert appetite_strip({"without_controls": [], "with_controls": []}, _TOL) is None


def test_prob_zero_degenerate_footer_keys_on_verdict():
    """loss_tolerance_probability == 0.0 is schema-reachable (ge=0.0): any
    positive exceedance probability then "exceeds" while times_over is None
    (division guard). Card 1's footer must key on the VERDICT, not times_over
    truthiness — the bug rendered a red "bad" accent with a "Within appetite"
    footer. The "N× over" multiplier is an optional suffix, never the decision.
    """
    from idraa.app import templates

    tol0 = {"amount": 8_000_000.0, "probability": 0.0}
    s = appetite_strip(_DUAL, tol0)
    assert s["verdict_without"] == "exceeds"
    assert s["times_over"] is None

    dr = {
        "appetite_strip": s,
        "dual_lec": None,
        "dual_epc": None,
        "loss_tolerance": tol0,
        "currency": {"code": "USD", "symbol": "$"},
    }
    tpl = templates.env.from_string(
        "{% from 'runs/components/exceedance_chart.html' import exceedance_toggle %}"
        "{{ exceedance_toggle(dr, none) }}"
    )
    html = tpl.render(dr=dr)
    # Scope to Card 1 (the "without controls" card) — everything between the
    # strip container and Card 2's label.
    card1 = html.split('data-testid="appetite-strip"', 1)[1].split("— with controls", 1)[0]
    assert "Exceeds appetite" in card1
    assert "Within appetite" not in card1  # the bug: red accent + within-footer
    assert "× over" not in card1  # no multiplier suffix when times_over is None
    assert "border-l-[var(--color-status-critical)]" in card1  # accent still "bad"


def test_strip_basis_is_the_converted_curve():
    # dual_lec + tolerance BOTH in reporting currency (converted by the same rc).
    # Scaling losses AND the tolerance amount by the same factor scales the money
    # outputs by that factor and leaves probabilities unchanged — proving p/loss
    # share ONE currency basis (no mixed sample/curve source).
    factor = 1.5

    def conv(pts):
        return [{"loss": p["loss"] * factor, "probability": p["probability"]} for p in pts]

    dual_c = {"without_controls": conv(_CURVE_WITHOUT), "with_controls": conv(_CURVE_WITH)}
    tol_c = {"amount": _TOL["amount"] * factor, "probability": _TOL["probability"]}
    base = appetite_strip(_DUAL, _TOL)
    conv_strip = appetite_strip(dual_c, tol_c)
    assert conv_strip["p_with"] == pytest.approx(base["p_with"])  # prob unchanged
    assert conv_strip["loss_at_tol_prob"] == pytest.approx(base["loss_at_tol_prob"] * factor)
    assert conv_strip["headroom"] == pytest.approx(base["headroom"] * factor)
