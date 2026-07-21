"""Geometry for the first-party SVG charts (epic #547 P1).

Hand-math anchors: with VIEW_W=820, MARGIN left=62/right=20 the plot width is
738. On a log-x domain [1e3, 1e7] (4 decades), x(1e5) sits exactly halfway:
62 + 738/2 = 431.0. Linear-y with plot height 380-16-46=318: y(0.5) = 16 +
0.5*318 = 175.0 measured from top with y inverted (probability 1.0 at top).
"""

import re

import pytest

from idraa.services.chart_svg import (
    CI_MARGIN,
    CI_VIEW_H,
    CI_VIEW_W,
    MARGIN,
    VIEW_H,
    VIEW_W,
    ci_band,
    comparison_bars,
    dual_curve,
    effectiveness_bars,
    epc_curve,
    single_epc_curve,
    single_lec_curve,
    slider_pos,
)

_PAYLOAD = {
    "without_controls": [
        {"loss": 1_000.0, "probability": 1.0},
        {"loss": 100_000.0, "probability": 0.5},
        {"loss": 10_000_000.0, "probability": 0.0},
    ],
    "with_controls": [
        {"loss": 1_000.0, "probability": 0.8},
        {"loss": 100_000.0, "probability": 0.2},
        {"loss": 10_000_000.0, "probability": 0.0},
    ],
}
_TOL = {"amount": 100_000.0, "probability": 0.5}


def test_log_x_midpoint_hand_math():
    g = dual_curve(_PAYLOAD, None, x_label="Loss")
    # 3 points -> path "M x0 y0Lx1 y1Lx2 y2"; middle point of series 0:
    d = g["series"][0]["path_d"]
    coords = [c.split() for c in d.replace("M", "").split("L")]
    x_mid = float(coords[1][0])
    assert x_mid == pytest.approx(62 + 738 / 2, abs=0.11)  # log midpoint of 4 decades


def test_linear_y_inverted_hand_math():
    g = dual_curve(_PAYLOAD, None, x_label="Loss")
    d = g["series"][0]["path_d"]
    coords = [c.split() for c in d.replace("M", "").split("L")]
    y_first = float(coords[0][1])  # probability 1.0 -> top of plot
    y_mid = float(coords[1][1])  # probability 0.5 -> vertical middle
    assert y_first == pytest.approx(MARGIN["top"], abs=0.11)
    assert y_mid == pytest.approx(16 + 0.5 * 318, abs=0.11)


def test_tolerance_marker_coordinates():
    g = dual_curve(_PAYLOAD, _TOL, x_label="Loss")
    m = g["tolerance_marker"]
    assert m is not None
    assert m["x"] == pytest.approx(62 + 738 / 2, abs=0.11)  # $100k on [1e3,1e7]
    assert m["y"] == pytest.approx(16 + 0.5 * 318, abs=0.11)  # p=0.5 linear
    # _fmt_money(100000,"$") -> "$100K"; _fmt_pct(0.5) -> "50%"
    assert m["label"] == "$100K @ 50%"


def test_log_y_scale_floor_and_ticks():
    g = dual_curve(_PAYLOAD, None, x_label="Loss", y_scale="log")
    labels = [t["label"] for t in g["y_ticks"]]
    assert "100%" in labels  # p=1 decade tick
    assert len(g["y_ticks"]) == 7  # decade ticks 1 .. 1e-6 exactly


def test_x_ticks_are_decades_with_currency():
    g = dual_curve(_PAYLOAD, None, x_label="Loss", currency_symbol="€")
    labels = [t["label"] for t in g["x_ticks"]]
    assert labels[0].startswith("€")
    assert len(labels) == 5  # 1e3..1e7 inclusive


def test_none_on_missing_or_empty_series():
    assert dual_curve(None, None, x_label="Loss") is None
    assert dual_curve({"without_controls": [], "with_controls": []}, None, x_label="Loss") is None


def test_single_series_payload_still_renders():
    p = {"without_controls": _PAYLOAD["without_controls"], "with_controls": []}
    g = dual_curve(p, None, x_label="Loss")
    assert g is not None and len(g["series"]) == 1


def test_view_box_constants():
    assert (VIEW_W, VIEW_H) == (820, 380)


def test_slider_pos_round_trip():
    # (log10(.05)+4)/4*100 = 67.47 -> round -> 67
    assert slider_pos(0.05) == 67
    assert slider_pos(1.0) == 100 and slider_pos(0.0001) == 0


# percentile increases -> loss increases (physical). exceedance prob = 1-percentile,
# so percentile 0.0 -> exc 1.0 -> right edge; percentile 1.0 -> exc 0.0 -> left edge.
_EPC_PAYLOAD = {
    "without_controls": [
        {"percentile": 0.0, "loss": 1_000.0},  # exc 1.0 -> x=800; $1K -> bottom
        {"percentile": 0.5, "loss": 100_000.0},  # exc 0.5 -> x=431; $100K -> mid
        {"percentile": 1.0, "loss": 10_000_000.0},  # exc 0.0 -> x=62; $10M -> top
    ],
    "with_controls": [
        {"percentile": 0.0, "loss": 1_000.0},
        {"percentile": 0.5, "loss": 10_000.0},
        {"percentile": 1.0, "loss": 1_000_000.0},
    ],
}
_EPC_TOL = {"amount": 100_000.0, "probability": 0.5}


def test_epc_x_is_exceedance_probability_linear():
    g = epc_curve(_EPC_PAYLOAD, None, y_label="Annual loss")
    d = g["series"][0]["path_d"]
    coords = [c.split() for c in d.replace("M", "").split("L")]
    assert float(coords[0][0]) == pytest.approx(800.0, abs=0.11)  # exc 1.0 -> 62+738
    assert float(coords[1][0]) == pytest.approx(62 + 738 * 0.5, abs=0.11)  # exc 0.5 -> 431
    assert float(coords[2][0]) == pytest.approx(62.0, abs=0.11)  # exc 0.0 -> left


def test_epc_log_y_loss_decades():
    g = epc_curve(_EPC_PAYLOAD, None, y_label="Annual loss")
    d = g["series"][0]["path_d"]
    coords = [c.split() for c in d.replace("M", "").split("L")]
    assert float(coords[0][1]) == pytest.approx(334.0, abs=0.11)  # $1K -> bottom (380-46)
    assert float(coords[1][1]) == pytest.approx(16 + 0.5 * 318, abs=0.11)  # $100K -> mid
    assert float(coords[2][1]) == pytest.approx(16.0, abs=0.11)  # $10M -> top


def test_epc_tolerance_marker_swapped_axes():
    g = epc_curve(_EPC_PAYLOAD, _EPC_TOL, y_label="Annual loss")
    m = g["tolerance_marker"]
    assert m is not None
    assert m["x"] == pytest.approx(62 + 738 * 0.5, abs=0.11)  # exceedance prob 0.5 on x
    assert m["y"] == pytest.approx(16 + 0.5 * 318, abs=0.11)  # $100K on log y
    assert m["label"] == "$100K @ 50%"


def test_epc_x_ticks_are_probability_quartiles():
    g = epc_curve(_EPC_PAYLOAD, None, y_label="Annual loss")
    labels = [t["label"] for t in g["x_ticks"]]
    assert labels == ["0%", "25%", "50%", "75%", "100%"]


def test_epc_y_ticks_are_loss_decades_with_currency():
    g = epc_curve(_EPC_PAYLOAD, None, y_label="Annual loss", currency_symbol="€")
    labels = [t["label"] for t in g["y_ticks"]]
    assert labels[0].startswith("€") and len(labels) == 5  # 1e3..1e7


def test_epc_kind_and_log_scale():
    g = epc_curve(_EPC_PAYLOAD, None, y_label="Annual loss")
    assert g["kind"] == "epc" and g["y_scale"] == "log"
    assert g["x_label"] == "Exceedance probability"


def test_epc_none_guards():
    assert epc_curve(None, None, y_label="Annual loss") is None
    assert (
        epc_curve({"without_controls": [], "with_controls": []}, None, y_label="Annual loss")
        is None
    )


def _area_tail_coords(area_d: str, path_d: str) -> list[tuple[str, str]]:
    """Extract the coordinate pairs appended after ``path_d`` in an area_d
    string (the two baseline-closing points before Z)."""
    assert area_d.startswith(path_d)
    tail = area_d[len(path_d) :]
    return re.findall(r"L ([\-0-9.]+),([\-0-9.]+)", tail)


def test_area_d_closes_to_baseline():
    """Design-language P2 (#59) chart style layer: every series gains an
    area_d = path_d extended with two baseline-closing points + Z, so the
    macro can fill the area under the curve with a gradient. baseline =
    round(VIEW_H - MARGIN["bottom"], 1) — the shared plot-bottom constant,
    computed the SAME way in both dual_curve (probability y) and epc_curve
    (log-loss y, axis-swapped) — EPC's plot-bottom closure is the
    verified-correct "under-curve" semantics even though its y-axis has no
    zero (plan-gate)."""
    baseline = round(VIEW_H - MARGIN["bottom"], 1)
    assert baseline == pytest.approx(334.0, abs=0.01)  # 380 - 46

    dual = dual_curve(_PAYLOAD, None, x_label="Loss")
    for s in dual["series"]:
        assert s["area_d"] is not None
        assert s["area_d"].endswith(" Z")
        coords = _area_tail_coords(s["area_d"], s["path_d"])
        assert len(coords) == 2
        for _x, y in coords:
            assert float(y) == pytest.approx(baseline, abs=0.01)

    epc = epc_curve(_EPC_PAYLOAD, None, y_label="Annual loss")
    for s in epc["series"]:
        assert s["area_d"] is not None
        assert s["area_d"].endswith(" Z")
        coords = _area_tail_coords(s["area_d"], s["path_d"])
        assert len(coords) == 2
        for _x, y in coords:
            assert float(y) == pytest.approx(baseline, abs=0.01)


# ===========================================================================
# epic #547 P2: single_lec_curve / single_epc_curve / ci_band /
# effectiveness_bars / comparison_bars
# ===========================================================================

_SINGLE_LEC_POINTS = _PAYLOAD["without_controls"]  # reuse the P1 3-point series
_SINGLE_EPC_POINTS = _EPC_PAYLOAD["without_controls"]


def test_single_lec_curve_matches_dual_curve_wrapped_payload():
    """single_lec_curve is a thin wrapper: identical geometry to calling
    dual_curve directly with the points folded into without_controls and an
    empty with_controls (P2 "single-curve figure reuse"). x_label matches
    dual_lec_curve's "Annual loss" (milestone-gate finding: single/dual LEC
    cards must use the same axis label)."""
    wrapped = dual_curve(
        {"without_controls": _SINGLE_LEC_POINTS, "with_controls": []},
        _TOL,
        x_label="Annual loss",
        y_scale="linear",
    )
    g = single_lec_curve(_SINGLE_LEC_POINTS, _TOL)
    assert g == wrapped
    assert g["x_label"] == "Annual loss"
    assert len(g["series"]) == 1


def test_single_lec_curve_none_on_empty():
    assert single_lec_curve([], None) is None
    assert single_lec_curve(None, None) is None


def test_single_lec_curve_tolerance_marker_hand_math():
    # Same domain as test_tolerance_marker_coordinates ($1k..$10M, tol $100k@50%).
    g = single_lec_curve(_SINGLE_LEC_POINTS, _TOL)
    m = g["tolerance_marker"]
    assert m["x"] == pytest.approx(62 + 738 / 2, abs=0.11)
    assert m["label"] == "$100K @ 50%"


def test_single_epc_curve_matches_epc_curve_wrapped_payload():
    wrapped = epc_curve(
        {"without_controls": _SINGLE_EPC_POINTS, "with_controls": []},
        _EPC_TOL,
        y_label="Loss",
    )
    g = single_epc_curve(_SINGLE_EPC_POINTS, _EPC_TOL)
    assert g == wrapped
    assert g["kind"] == "epc" and g["y_scale"] == "log"


def test_single_epc_curve_none_on_empty():
    assert single_epc_curve([], None) is None
    assert single_epc_curve(None, None) is None


# --- ci_band ---------------------------------------------------------------
#
# Hand math: CI_VIEW_W=820, CI_MARGIN left=40/right=40 -> plot_w=740.
# lo=800_000, hi=1_200_000, value=1_000_000 -> dom=[800k,1.2M], pad=10%*400k=40k
# -> x_min=760_000, x_max=1_240_000, span=480_000.
# sx(lo)   = 40 + (800_000-760_000)/480_000*740  = 40 + 61.667 = 101.7
# sx(hi)   = 40 + (1_200_000-760_000)/480_000*740 = 40 + 678.333 = 718.3
# sx(value)= 40 + (1_000_000-760_000)/480_000*740 = 40 + 370.0 = 410.0
_HEADLINE = {"value": 1_000_000.0, "lo": 800_000.0, "hi": 1_200_000.0, "has_ci_band": True}


def test_ci_band_hand_math():
    g = ci_band(_HEADLINE)
    assert g["band"]["x0"] == pytest.approx(101.7, abs=0.05)
    assert g["band"]["x1"] == pytest.approx(718.3, abs=0.05)
    assert g["marker"]["x"] == pytest.approx(410.0, abs=0.05)
    assert g["band"]["y"] == g["marker"]["y"]  # same horizontal row


def test_ci_band_view_box_constants():
    assert (CI_VIEW_W, CI_VIEW_H) == (820, 90)
    assert CI_MARGIN == {"top": 28, "right": 40, "bottom": 22, "left": 40}


def test_ci_band_labels_use_compact_money():
    g = ci_band(_HEADLINE)
    assert g["lo_label"] == "$800K"
    assert g["hi_label"] == "$1.2M"
    assert g["value_label"] == "$1M"


def test_ci_band_none_when_no_band():
    assert ci_band({"value": 1.0, "lo": 0.0, "hi": 0.0, "has_ci_band": False}) is None
    assert ci_band(None) is None


def test_ci_band_degenerate_lo_equals_hi_does_not_divide_by_zero():
    g = ci_band({"value": 5.0, "lo": 5.0, "hi": 5.0, "has_ci_band": True})
    assert g is not None
    assert g["band"]["x0"] == g["band"]["x1"] == g["marker"]["x"]


# --- effectiveness_bars ------------------------------------------------
#
# Hand math: VIEW_W=820, margin left=190/right=60 -> plot_w=570.
# Row 0 eff=0.85: x1 = 190 + 0.85*570 = 190 + 484.5 = 674.5
# Row 0 y_center = top(10) + 0*32 + 32/2 = 26.0; row 1 y_center = 10+32+16 = 58.0
# x_ticks: sx(0)=190, sx(1)=190+570=760
_EB_ROWS = [
    {"control_id": "c1", "name": "MFA", "effectiveness": 0.85},
    {"control_id": "c2", "name": "EDR", "effectiveness": 0.5},
]


def test_effectiveness_bars_hand_math():
    g = effectiveness_bars(_EB_ROWS)
    assert g["bars"][0]["x1"] == pytest.approx(674.5, abs=0.05)
    assert g["bars"][0]["y_center"] == pytest.approx(26.0, abs=0.05)
    assert g["bars"][1]["y_center"] == pytest.approx(58.0, abs=0.05)
    assert g["x_ticks"] == [{"x": 190.0, "label": "0"}, {"x": 760.0, "label": "1"}]


def test_effectiveness_bars_value_labels_two_decimal():
    g = effectiveness_bars(_EB_ROWS)
    assert g["bars"][0]["value_label"] == "0.85"
    assert g["bars"][1]["value_label"] == "0.50"


def test_effectiveness_bars_view_h_scales_with_row_count():
    g2 = effectiveness_bars(_EB_ROWS)
    g3 = effectiveness_bars([*_EB_ROWS, {"control_id": "c3", "name": "SIEM", "effectiveness": 0.3}])
    assert g3["view_h"] > g2["view_h"]
    assert len(g3["bars"]) == 3


def test_effectiveness_bars_none_on_empty():
    assert effectiveness_bars([]) is None
    assert effectiveness_bars(None) is None


def test_effectiveness_bars_clamps_out_of_range_scores():
    # Defensive clamp: a corrupt/legacy score outside [0,1] must not push the
    # bar off the plot area.
    g = effectiveness_bars([{"control_id": "c1", "name": "X", "effectiveness": 1.5}])
    assert g["bars"][0]["x1"] == g["x_ticks"][1]["x"]  # clamped to sx(1.0)
    # milestone-gate finding: the printed label must agree with the clamped
    # bar, not the raw unclamped score.
    assert g["bars"][0]["value_label"] == "1.00"


# --- comparison_bars ---------------------------------------------------
#
# Hand math: VIEW_W=820, margin left=140/right=80 -> plot_w=600.
# values base=2_000_000, residual=500_000, reduction=1_500_000 -> x_min=0, x_max=2_000_000.
# zero_x = sx(0) = 140.
# base:      sx(2_000_000) = 140 + 600         = 740.0
# residual:  sx(500_000)   = 140 + 0.25*600    = 290.0
# reduction: sx(1_500_000) = 140 + 0.75*600    = 590.0
_COMPARISON = {
    "base": 2_000_000.0,
    "residual": 500_000.0,
    "reduction": 1_500_000.0,
    "reduction_pct": 75.0,
}


def test_comparison_bars_hand_math():
    g = comparison_bars(_COMPARISON)
    by_key = {b["key"]: b for b in g["bars"]}
    assert by_key["base"]["x1"] == pytest.approx(740.0, abs=0.05)
    assert by_key["residual"]["x1"] == pytest.approx(290.0, abs=0.05)
    assert by_key["reduction"]["x1"] == pytest.approx(590.0, abs=0.05)
    for b in g["bars"]:
        assert b["x0"] == pytest.approx(140.0, abs=0.05)  # zero baseline


def test_comparison_bars_token_mapping_reduction_gets_dedicated_token():
    # epic #547 P2 milestone-gate finding 1: base/residual reuse the existing
    # series tokens, but reduction gets its OWN --chart-reduction token (NOT
    # --chart-appetite) — this bar shares a results panel with the LEC/EPC
    # tolerance markers, which stroke --chart-appetite, so reusing it would
    # make one amber hue encode two unrelated quantities.
    g = comparison_bars(_COMPARISON)
    by_key = {b["key"]: b["token"] for b in g["bars"]}
    assert by_key == {"base": "inherent", "residual": "residual", "reduction": "reduction"}


def test_comparison_bars_negative_reduction_extends_left_of_baseline():
    # Pathological case: residual > base -> reduction < 0. Bar must extend
    # LEFT of the zero baseline, not clip/invert.
    g = comparison_bars(
        {"base": 100.0, "residual": 150.0, "reduction": -50.0, "reduction_pct": -50.0}
    )
    by_key = {b["key"]: b for b in g["bars"]}
    red = by_key["reduction"]
    zero_x = by_key["base"]["x0"]
    assert red["x0"] < zero_x  # left of the baseline
    assert red["x1"] == pytest.approx(zero_x, abs=0.11)


def test_comparison_bars_none_when_absent():
    assert comparison_bars(None) is None
    assert comparison_bars({}) is None


def test_comparison_bars_view_box_width_matches_curve_charts():
    g = comparison_bars(_COMPARISON)
    assert g["view_w"] == VIEW_W  # shared viewBox width convention
