"""Server-side SVG chart geometry (epic #547 P1).

Pure functions: payload dicts in, plain dicts of pixel coordinates / SVG path
strings out. No I/O, no Jinja. Registered (with epc_curve/slider_pos) as the
Jinja global ``chart_svg`` SimpleNamespace in app.py; macros in
``macros/chart.html`` turn these dicts into <svg> markup.

Conventions:
- viewBox is fixed (VIEW_W x VIEW_H); the <svg> scales via width:100%.
- dual_curve: x is log10(loss); y is exceedance probability, linear [0, 1] or
  log down to Y_LOG_MIN (1e-6), inverted (p=1 at the top).
- Coordinates are rounded to 0.1px so path strings are stable for tests and
  small in HTML.
- Point series pass through 1:1 — NO decimation here (spec: any thinning must
  be a labeled view-model step).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

VIEW_W = 820
VIEW_H = 380
MARGIN = {"top": 16, "right": 20, "bottom": 46, "left": 62}
Y_LOG_MIN = 1e-6


def _fmt_money(v: float, sym: str) -> str:
    if v >= 1e9:
        return f"{sym}{v / 1e9:.1f}B".replace(".0B", "B")
    if v >= 1e6:
        return f"{sym}{v / 1e6:.1f}M".replace(".0M", "M")
    if v >= 1e3:
        return f"{sym}{v / 1e3:.0f}K"
    return f"{sym}{v:.0f}"


def _fmt_pct(p: float) -> str:
    if p >= 0.01:
        s = f"{p * 100:.1f}" if p < 0.1 else f"{p * 100:.0f}"
        return f"{s}%"
    return f"{p * 100:.4g}%"


def _x_scale(x_min: float, x_max: float) -> Callable[[float], float]:
    lo, hi = math.log10(x_min), math.log10(x_max)
    span = (hi - lo) or 1.0
    plot_w = VIEW_W - MARGIN["left"] - MARGIN["right"]

    def sx(v: float) -> float:
        return round(MARGIN["left"] + (math.log10(max(v, 1e-9)) - lo) / span * plot_w, 1)

    return sx


def _y_scale(y_scale: str) -> Callable[[float], float]:
    plot_h = VIEW_H - MARGIN["top"] - MARGIN["bottom"]
    if y_scale == "log":
        lo = math.log10(Y_LOG_MIN)

        def sy(p: float) -> float:
            pp = max(p, Y_LOG_MIN)
            frac = (math.log10(pp) - lo) / (0.0 - lo)
            return round(MARGIN["top"] + (1.0 - frac) * plot_h, 1)

    else:

        def sy(p: float) -> float:
            return round(MARGIN["top"] + (1.0 - p) * plot_h, 1)

    return sy


def _path_d(
    points: list[dict[str, Any]],
    sx: Callable[[float], float],
    sy: Callable[[float], float],
) -> str:
    parts: list[str] = []
    for i, pt in enumerate(points):
        cmd = "M" if i == 0 else "L"
        parts.append(f"{cmd}{sx(pt['loss'])} {sy(pt['probability'])}")
    return "".join(parts)


def _area_d(path_d: str, x_first: float, x_last: float, baseline: float) -> str | None:
    """Close a stroked curve's ``path_d`` into a fillable area shape (design-
    language P2 #59): extend to the plot-bottom baseline under the LAST
    point, across to under the FIRST point, then close. Pure geometry, no
    color/fill decision here (that's the macro's job).

    None-safe: an empty ``path_d`` (should not happen — callers only invoke
    this for non-empty series) yields None rather than a malformed path.
    """
    if not path_d:
        return None
    return f"{path_d} L {x_last},{baseline} L {x_first},{baseline} Z"


def dual_curve(
    payload: dict[str, Any] | None,
    tolerance: dict[str, Any] | None,
    *,
    x_label: str,
    y_scale: str = "linear",
    currency_symbol: str = "$",
) -> dict[str, Any] | None:
    """Geometry bundle for the dual with/without-controls LEC chart.

    Returns None when the payload is absent or BOTH series are empty (macro
    renders its existing no-data note). A single non-empty series renders
    alone (legacy runs). Series order: without first, then with.
    """
    if not payload:
        return None
    series_in = [
        ("without", "Without controls", payload.get("without_controls") or []),
        ("with", "With controls", payload.get("with_controls") or []),
    ]
    series_in = [(k, lbl, pts) for k, lbl, pts in series_in if pts]
    if not series_in:
        return None

    all_pts = [pt for _, _, pts in series_in for pt in pts]
    x_min = min(pt["loss"] for pt in all_pts)
    x_max = max(pt["loss"] for pt in all_pts)
    if tolerance and tolerance.get("amount") is not None:
        x_min = min(x_min, float(tolerance["amount"]))
        x_max = max(x_max, float(tolerance["amount"]))
    x_min = max(x_min, 1.0)
    x_max = max(x_max, x_min * 10.0)

    sx, sy = _x_scale(x_min, x_max), _y_scale(y_scale)
    # Shared plot-bottom baseline for the under-curve area fill (design-
    # language P2 #59) — computed here, not via _y_scale(0), since a log
    # y-axis has no zero to feed it; this constant IS the pixel row _y_scale
    # asymptotes to at its floor (see module docstring / plan-gate note).
    baseline = round(VIEW_H - MARGIN["bottom"], 1)

    series = []
    for key, label, pts in series_in:
        # Series identity is carried by a legend row (macro-side) + line dash,
        # not by on-curve endpoint labels (those clip/overlap where the dual
        # curves converge at the bottom-right). Geometry emits key/label only.
        path_d = _path_d(pts, sx, sy)
        x_first, x_last = sx(pts[0]["loss"]), sx(pts[-1]["loss"])
        series.append(
            {
                "key": key,
                "label": label,
                "path_d": path_d,
                "area_d": _area_d(path_d, x_first, x_last, baseline),
            }
        )

    # x ticks: decade values inside [x_min, x_max]
    x_ticks = []
    for e in range(math.ceil(math.log10(x_min)), math.floor(math.log10(x_max)) + 1):
        v = 10.0**e
        x_ticks.append({"x": sx(v), "label": _fmt_money(v, currency_symbol)})

    # y ticks
    if y_scale == "log":
        tick_ps = [10.0**-i for i in range(0, 7)]  # 1 .. 1e-6
    else:
        tick_ps = [0.0, 0.25, 0.5, 0.75, 1.0]
    y_ticks = [
        {"y": sy(p), "label": _fmt_pct(max(p, Y_LOG_MIN if y_scale == "log" else p))}
        for p in tick_ps
    ]
    if y_scale != "log":
        y_ticks[0]["label"] = "0%"

    marker = None
    if (
        tolerance
        and tolerance.get("amount") is not None
        and tolerance.get("probability") is not None
    ):
        amt, prob = float(tolerance["amount"]), float(tolerance["probability"])
        marker = {
            "x": sx(amt),
            "y": sy(prob),
            "label": f"{_fmt_money(amt, currency_symbol)} @ {_fmt_pct(prob)}",
        }

    return {
        "view_w": VIEW_W,
        "view_h": VIEW_H,
        "margin": MARGIN,
        "x_ticks": x_ticks,
        "y_ticks": y_ticks,
        "series": series,
        "tolerance_marker": marker,
        "y_scale": y_scale,
        "x_label": x_label,
        "currency_symbol": currency_symbol,
    }


def epc_curve(
    payload: dict[str, Any] | None,
    tolerance: dict[str, Any] | None,
    *,
    y_label: str,
    currency_symbol: str = "$",
) -> dict[str, Any] | None:
    """Geometry for the exceedance-PROBABILITY curve card (epic #547 P1).

    Axis-swapped vs ``dual_curve``, preserving the EXISTING retired-chart-vendor
    ``dual_epc_curve`` orientation (macros/chart.html): x = exceedance
    probability = (1 - percentile), LINEAR over [0, 1] with 0 at the LEFT and
    1.0 at the RIGHT (not reversed); y = loss, LOG, high
    loss at the top. Points arrive as ``{"percentile": 0..1, "loss": $}`` from
    ``_build_dual_epc`` (aggregate_run_view_model.py), which clamps loss to
    >= $1 so the log y-axis renders every point. A high-percentile (large-loss)
    point therefore maps to LOW x (left) + HIGH y (top); a low-percentile
    (small-loss) point maps to HIGH x (right) + LOW y (bottom), so the curve
    descends from upper-left to lower-right.

    Returns the same key set as ``dual_curve`` plus ``y_scale`` fixed to "log",
    ``x_label`` fixed to "Exceedance probability", the loss-axis ``y_label``,
    and ``kind`` = "epc" so the macro/JS brand the hover-only hydration mode.
    Returns None when the payload is absent or both series are empty (macro
    renders its existing no-data note).
    """
    if not payload:
        return None
    series_in = [
        ("without", "Without controls", payload.get("without_controls") or []),
        ("with", "With controls", payload.get("with_controls") or []),
    ]
    series_in = [(k, lbl, pts) for k, lbl, pts in series_in if pts]
    if not series_in:
        return None

    all_pts = [pt for _, _, pts in series_in for pt in pts]
    y_min = min(pt["loss"] for pt in all_pts)
    y_max = max(pt["loss"] for pt in all_pts)
    if tolerance and tolerance.get("amount") is not None:
        y_min = min(y_min, float(tolerance["amount"]))
        y_max = max(y_max, float(tolerance["amount"]))
    y_min = max(y_min, 1.0)
    y_max = max(y_max, y_min * 10.0)

    plot_w = VIEW_W - MARGIN["left"] - MARGIN["right"]
    plot_h = VIEW_H - MARGIN["top"] - MARGIN["bottom"]
    lo, hi = math.log10(y_min), math.log10(y_max)
    span = (hi - lo) or 1.0

    def sx(p_exc: float) -> float:  # exceedance probability -> x (linear [0,1])
        return round(MARGIN["left"] + min(max(p_exc, 0.0), 1.0) * plot_w, 1)

    def sy(loss: float) -> float:  # loss -> y (log, inverted: high loss on top)
        frac = (math.log10(max(loss, 1e-9)) - lo) / span
        return round(MARGIN["top"] + (1.0 - frac) * plot_h, 1)

    def _path(pts: list[dict[str, Any]]) -> str:
        parts: list[str] = []
        for i, pt in enumerate(pts):
            cmd = "M" if i == 0 else "L"
            parts.append(f"{cmd}{sx(1.0 - pt['percentile'])} {sy(pt['loss'])}")
        return "".join(parts)

    # Shared plot-bottom baseline for the under-curve area fill (design-
    # language P2 #59). EPC's y-axis is log-loss (never zero — there is no
    # "0 loss" to close against), so the baseline is the SAME raw
    # plot-bottom pixel row dual_curve uses, computed directly here (EPC
    # never routes through _y_scale) — the verified-correct "under-curve"
    # semantics per plan-gate.
    baseline = round(VIEW_H - MARGIN["bottom"], 1)

    series = []
    for key, label, pts in series_in:
        # Identity via the legend row (macro-side) + line dash — no on-curve
        # endpoint labels (see dual_curve).
        path_d = _path(pts)
        x_first = sx(1.0 - pts[0]["percentile"])
        x_last = sx(1.0 - pts[-1]["percentile"])
        series.append(
            {
                "key": key,
                "label": label,
                "path_d": path_d,
                "area_d": _area_d(path_d, x_first, x_last, baseline),
            }
        )

    # x ticks: exceedance-probability quartiles 0..100%
    x_ticks = [
        {"x": round(MARGIN["left"] + f * plot_w, 1), "label": (_fmt_pct(f) if f > 0 else "0%")}
        for f in (0.0, 0.25, 0.5, 0.75, 1.0)
    ]
    # y ticks: loss decades inside [y_min, y_max]
    y_ticks = []
    for e in range(math.ceil(math.log10(y_min)), math.floor(math.log10(y_max)) + 1):
        v = 10.0**e
        y_ticks.append({"y": sy(v), "label": _fmt_money(v, currency_symbol)})

    marker = None
    if (
        tolerance
        and tolerance.get("amount") is not None
        and tolerance.get("probability") is not None
    ):
        amt, prob = float(tolerance["amount"]), float(tolerance["probability"])
        marker = {
            "x": sx(prob),
            "y": sy(amt),
            "label": f"{_fmt_money(amt, currency_symbol)} @ {_fmt_pct(prob)}",
        }

    return {
        "view_w": VIEW_W,
        "view_h": VIEW_H,
        "margin": MARGIN,
        "x_ticks": x_ticks,
        "y_ticks": y_ticks,
        "series": series,
        "tolerance_marker": marker,
        "y_scale": "log",
        "kind": "epc",
        "x_label": "Exceedance probability",
        "y_label": y_label,
        "currency_symbol": currency_symbol,
    }


def slider_pos(probability: float) -> int:
    """Slider position (0-100) for an exceedance probability on the log scale
    p = 10^(-4 + s/100 * 4); inverse mapping, clamped. Same mapping the JS uses
    so the server-rendered slider INITIALIZES at tol_prob."""
    p = min(max(probability, 1e-4), 1.0)
    return round((math.log10(p) + 4.0) / 4.0 * 100)


# ---------------------------------------------------------------------------
# epic #547 P2: single-curve (SINGLE-run) LEC/EPC, static CI band, and bars.
# ---------------------------------------------------------------------------


def single_lec_curve(
    points: list[dict[str, Any]] | None,
    tolerance: dict[str, Any] | None,
    *,
    currency_symbol: str = "$",
) -> dict[str, Any] | None:
    """LEC geometry for SINGLE-run result panels (epic #547 P2).

    Thin wrapper over ``dual_curve`` with an EMPTY ``with_controls`` series —
    the "P2 single-curve figure reuse" note designed for in P1: identical
    log-x / linear-y scale, tick, and tolerance-marker math as the dual card,
    just one series. Callers pass a bare points list (the SINGLE-run payload
    shape), not a dual with/without dict. Returns None when points is
    empty/absent (dual_curve's own no-data convention).
    """
    return dual_curve(
        {"without_controls": points or [], "with_controls": []},
        tolerance,
        x_label="Annual loss",
        y_scale="linear",
        currency_symbol=currency_symbol,
    )


def single_epc_curve(
    points: list[dict[str, Any]] | None,
    tolerance: dict[str, Any] | None,
    *,
    currency_symbol: str = "$",
) -> dict[str, Any] | None:
    """EPC geometry for SINGLE-run result panels (epic #547 P2).

    Thin wrapper over ``epc_curve`` — see ``single_lec_curve`` docstring for
    the P2 single-curve figure-reuse rationale.
    """
    return epc_curve(
        {"without_controls": points or [], "with_controls": []},
        tolerance,
        y_label="Loss",
        currency_symbol=currency_symbol,
    )


CI_VIEW_W = 820
CI_VIEW_H = 90
CI_MARGIN = {"top": 28, "right": 40, "bottom": 22, "left": 40}


def ci_band(
    headline: dict[str, Any] | None,
    *,
    currency_symbol: str = "$",
) -> dict[str, Any] | None:
    """Geometry for the residual-ALE central-95% band (epic #547 P2).

    ``headline`` = {value, lo, hi, has_ci_band}. Returns None when
    has_ci_band is falsy (legacy runs without a persisted band) — the macro
    renders its existing "not available" text in that branch, same
    None-on-no-data convention as dual_curve/epc_curve.

    Band + marker sit on a LINEAR x-scale over [lo, hi] (padded 10% each
    side so the marker circle/labels never clip the viewBox edges) — the
    retired chart vendor's version used an untyped (linear, autoranged)
    x-axis, so linear is the faithful port, not log.
    """
    if not headline or not headline.get("has_ci_band"):
        return None
    lo, hi, value = float(headline["lo"]), float(headline["hi"]), float(headline["value"])
    dom_lo, dom_hi = min(lo, value), max(hi, value)
    if dom_hi <= dom_lo:
        dom_hi = dom_lo + 1.0
    pad = (dom_hi - dom_lo) * 0.1
    x_min, x_max = dom_lo - pad, dom_hi + pad
    plot_w = CI_VIEW_W - CI_MARGIN["left"] - CI_MARGIN["right"]

    def sx(v: float) -> float:
        return round(CI_MARGIN["left"] + (v - x_min) / (x_max - x_min) * plot_w, 1)

    y_mid = CI_MARGIN["top"] + (CI_VIEW_H - CI_MARGIN["top"] - CI_MARGIN["bottom"]) / 2
    return {
        "view_w": CI_VIEW_W,
        "view_h": CI_VIEW_H,
        "margin": CI_MARGIN,
        "band": {"x0": sx(lo), "x1": sx(hi), "y": y_mid},
        "marker": {"x": sx(value), "y": y_mid},
        "lo_label": _fmt_money(lo, currency_symbol),
        "hi_label": _fmt_money(hi, currency_symbol),
        "value_label": _fmt_money(value, currency_symbol),
    }


_EB_ROW_H = 32.0
_EB_BAR_H = 18.0
_EB_MARGIN = {"top": 10.0, "right": 60.0, "bottom": 34.0, "left": 190.0}


def effectiveness_bars(rows: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """Geometry for the per-control effectiveness horizontal bars (epic #547
    P2). ``rows`` = list[{control_id, name, effectiveness}], pre-sorted desc
    by the view-model builder — pure renderer here, same contract as the
    retired chart-vendor macro.

    x is linear [0, 1] (effectiveness is a 0-1 score); one row per control at
    a fixed row height. Returns None on an empty/absent rows list — the
    macro renders its existing "No controls applied to this run" alert.
    """
    if not rows:
        return None
    plot_w = VIEW_W - _EB_MARGIN["left"] - _EB_MARGIN["right"]
    view_h = _EB_MARGIN["top"] + _EB_ROW_H * len(rows) + _EB_MARGIN["bottom"]

    def sx(v: float) -> float:
        return round(_EB_MARGIN["left"] + min(max(v, 0.0), 1.0) * plot_w, 1)

    x_ticks = [{"x": sx(0.0), "label": "0"}, {"x": sx(1.0), "label": "1"}]
    bars = []
    for i, row in enumerate(rows):
        eff = float(row["effectiveness"])
        eff_clamped = min(max(eff, 0.0), 1.0)
        y_center = round(_EB_MARGIN["top"] + i * _EB_ROW_H + _EB_ROW_H / 2, 1)
        bars.append(
            {
                "name": row["name"],
                "control_id": row.get("control_id"),
                "y_center": y_center,
                "bar_y0": round(y_center - _EB_BAR_H / 2, 1),
                "bar_h": _EB_BAR_H,
                "x0": sx(0.0),
                "x1": sx(eff),
                # Clamped to match the bar's own x1 — an out-of-range score
                # (corrupt/legacy data) must not print a label the bar itself
                # disagrees with (milestone-gate finding: bar and label must
                # agree).
                "value_label": f"{eff_clamped:.2f}",
            }
        )
    return {
        "view_w": VIEW_W,
        "view_h": round(view_h, 1),
        "margin": _EB_MARGIN,
        "x_ticks": x_ticks,
        "bars": bars,
    }


_CB_ROW_H = 60.0
_CB_BAR_H = 28.0
_CB_MARGIN = {"top": 10.0, "right": 80.0, "bottom": 20.0, "left": 140.0}
_CB_KEYS = ("base", "residual", "reduction")
# base/residual reuse the existing --chart-inherent / --chart-residual
# series tokens. reduction gets its OWN --chart-reduction token (epic #547
# P2 milestone-gate finding 1) rather than reusing --chart-appetite: this
# bar renders on the SAME results panel as the LEC/EPC tolerance markers,
# which stroke --chart-appetite, so sharing the token would encode two
# unrelated quantities (risk-reduction vs loss-tolerance) in one amber hue.
_CB_TOKENS = {"base": "inherent", "residual": "residual", "reduction": "reduction"}


def comparison_bars(comparison: dict[str, Any] | None) -> dict[str, Any] | None:
    """Geometry for the 3-bar Base/Residual/Reduction risk comparison (epic
    #547 P2). ``comparison`` = {base, residual, reduction, reduction_pct}.

    x is linear over [min(0, values), max(values)] so a bar baseline always
    sits at zero (a theoretically-possible negative reduction bar still
    renders correctly, extending left of the baseline instead of clipping).

    Bar TEXT labels (money-filter formatted, the reduction bar's "(pct%)"
    vs "(&mdash;)" branch) stay in the Jinja macro on purpose — that
    formatting is currency-code/locale-aware (Babel's ``money`` filter), not
    something this currency-symbol-agnostic geometry module should own.
    ``token`` selects the CSS var per bar. Returns None when comparison is
    absent.
    """
    if not comparison:
        return None
    values = [float(comparison[k]) for k in _CB_KEYS]
    x_min, x_max = min(0.0, *values), max(values)
    if x_max <= x_min:
        x_max = x_min + 1.0
    plot_w = VIEW_W - _CB_MARGIN["left"] - _CB_MARGIN["right"]

    def sx(v: float) -> float:
        return round(_CB_MARGIN["left"] + (v - x_min) / (x_max - x_min) * plot_w, 1)

    zero_x = sx(0.0)
    bars = []
    for i, key in enumerate(_CB_KEYS):
        v = float(comparison[key])
        vx = sx(v)
        y_center = round(_CB_MARGIN["top"] + i * _CB_ROW_H + _CB_ROW_H / 2, 1)
        bars.append(
            {
                "key": key,
                "token": _CB_TOKENS[key],
                "value": v,
                "y_center": y_center,
                "bar_y0": round(y_center - _CB_BAR_H / 2, 1),
                "bar_h": _CB_BAR_H,
                "x0": min(zero_x, vx),
                "x1": max(zero_x, vx),
            }
        )
    view_h = _CB_MARGIN["top"] + _CB_ROW_H * len(_CB_KEYS) + _CB_MARGIN["bottom"]
    return {
        "view_w": VIEW_W,
        "view_h": round(view_h, 1),
        "margin": _CB_MARGIN,
        "bars": bars,
    }
