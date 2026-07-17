"""Unit tests for the SVG curve macros in macros/chart.html (loss_exceedance_curve,
exceedance_probability_curve, dual_lec_curve, dual_epc_curve).

Loads the macro via the same Jinja Environment configuration the FastAPI
app uses, then invokes the macro with explicit point/payload dicts to verify
the rendered SVG contract.

History: this module originally covered ``per_scenario_ale_bar`` (omicron-1
F14). That macro was confirmed dead code (zero template callers — replaced by
the verdict strip / recent-activity redesign) and deleted in epic #547 P2;
its 3 tests were removed with it (test-migration, not a rewrite — there is
nothing left to port)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

# T1 (redesign) added the tokenized palette globals that chart.html macros now
# reference at parse/render time — register the REAL objects (same source app.py
# uses) so the isolated env resolves them instead of raising UndefinedError.
from idraa.services.chart_palette import (
    CHART_SERIES,
    TRACE_META_INHERENT,
    TRACE_META_RESIDUAL,
)

# epic #547 P1 Task 3 + P2: chart.html's curve/bar macros render first-party
# SVG via chart_svg.* + chart_uid() (same globals app.py registers) — register
# the REAL implementations so the isolated env resolves them at render time.
from idraa.services.chart_svg import (
    ci_band,
    comparison_bars,
    dual_curve,
    effectiveness_bars,
    epc_curve,
    single_epc_curve,
    single_lec_curve,
    slider_pos,
)

# Build a Jinja env that mirrors idraa.app.templates configuration.
# Anchor template root to the test file's location (CWD-independent —
# works regardless of where pytest is invoked from, e.g. project root,
# worktree, IDE runner).
_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "src" / "idraa" / "templates"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "jinja"]),
)

# chart.html defines several macros that depend on app-level filters and
# globals (`money`, `abbreviate_money`, `format_money`, etc.). Jinja parses
# the whole template module up-front, so even though a given test only
# invokes one macro, the OTHER macros' references must resolve at parse
# time. Register no-op stubs so the module loads in this isolated unit-test
# env without booting the full FastAPI app.
_env.filters["format_money"] = lambda v: "" if v is None else str(v)
_env.filters["abbreviate_money"] = lambda v: "" if v is None else str(v)
_env.filters["money"] = lambda v, code="USD", compact=True: "" if v is None else str(v)
_env.filters["format_datetime"] = lambda v: "" if v is None else str(v)
_env.filters["format_mult"] = lambda v: "" if v is None else str(v)
_env.globals["chart_series"] = CHART_SERIES
_env.globals["trace_meta_inherent"] = TRACE_META_INHERENT
_env.globals["trace_meta_residual"] = TRACE_META_RESIDUAL
_env.globals["chart_svg"] = SimpleNamespace(
    dual_curve=dual_curve,
    epc_curve=epc_curve,
    slider_pos=slider_pos,
    single_lec_curve=single_lec_curve,
    single_epc_curve=single_epc_curve,
    ci_band=ci_band,
    effectiveness_bars=effectiveness_bars,
    comparison_bars=comparison_bars,
)
# Deterministic (not uuid4) so isolated-macro-render assertions on the emitted
# markup stay stable across runs.
_env.globals["chart_uid"] = lambda: "unit0000"


_LEC_POINTS = [
    {"loss": 1_000_000.0, "probability": 0.95},
    {"loss": 10_000_000.0, "probability": 0.50},
    {"loss": 90_000_000.0, "probability": 0.05},
]
_DUAL_LEC_PAYLOAD = {
    "without_controls": _LEC_POINTS,
    "with_controls": [
        {"loss": p["loss"] / 3, "probability": p["probability"]} for p in _LEC_POINTS
    ],
}
_DUAL_EPC_PAYLOAD = {
    "without_controls": [
        {"percentile": 1.0 - p["probability"], "loss": p["loss"]} for p in _LEC_POINTS
    ],
    "with_controls": [
        {"percentile": 1.0 - p["probability"], "loss": p["loss"] / 3} for p in _LEC_POINTS
    ],
}
# The single EPC curve reads .percentile/.loss (x = exceedance prob, y = log-$).
_EPC_POINTS = [{"percentile": 1.0 - p["probability"], "loss": p["loss"]} for p in _LEC_POINTS]


def _render_macro(name: str, arg: Any) -> str:
    template = _env.get_template("macros/chart.html")
    macro = getattr(template.module, name)
    return str(macro(arg))


def test_single_lec_curve_is_svg_not_prior_vendor() -> None:
    """epic #547 P2: loss_exceedance_curve is first-party SVG now — this
    supersedes the old "every log-$ axis must be readable, non-overlapping"
    retired-chart-vendor dtick/nticks guard (history: this module used to pin
    ``dtick=1``/no-``nticks`` on this macro's log axis; that whole
    regression class is structurally impossible once the axis is Python-
    computed decade ticks, see test_chart_svg.py's dual_curve tick tests).
    Hover-only hydration: no crosshair/slider/toggle (those stay exclusive
    to the dual LEC card)."""
    rendered = _render_macro("loss_exceedance_curve", _LEC_POINTS)
    assert "<svg" in rendered
    assert 'data-chart-hydrate="curve"' in rendered
    assert "'type': 'log'" not in rendered  # retired chart vendor's axis JSON is gone
    assert "dtick" not in rendered
    assert 'data-role="p-slider"' not in rendered
    assert 'data-role="y-log"' not in rendered


def test_single_epc_curve_is_svg_not_prior_vendor() -> None:
    """epic #547 P2: exceedance_probability_curve is first-party SVG now —
    same supersession as test_single_lec_curve_is_svg_not_prior_vendor above
    (its log-$ y-axis is chart_svg.epc_curve's Python-computed decade ticks,
    see test_chart_svg.py's epc_curve tick tests)."""
    rendered = _render_macro("exceedance_probability_curve", _EPC_POINTS)
    assert "<svg" in rendered
    assert 'data-chart-hydrate="curve"' in rendered
    assert "'type': 'log'" not in rendered  # retired chart vendor's axis JSON is gone
    assert "dtick" not in rendered
    assert 'data-role="p-slider"' not in rendered


def test_dual_lec_curve_svg_log_axis_variant_present() -> None:
    """epic #547 P1 Task 3: dual_lec_curve is first-party SVG now — its "every
    log-$ axis must be readable, non-overlapping" INTENT (the history this
    module's docstring documents) is now satisfied by chart_svg.dual_curve
    emitting exactly one y-tick per decade (see test_chart_svg.py
    test_log_y_scale_floor_and_ticks), enforced in Python geometry instead of
    the retired chart vendor's dtick layout key. This pins the SVG
    replacement: both y-scale variants render, and the retired axis JSON is
    gone."""
    rendered = _render_macro("dual_lec_curve", _DUAL_LEC_PAYLOAD)
    assert 'data-y-scale="log"' in rendered
    assert 'data-y-scale="linear"' in rendered
    assert "'type': 'log'" not in rendered  # retired chart vendor's axis JSON is gone
    assert "dtick" not in rendered


def test_dual_epc_curve_svg_log_axis_variant_present() -> None:
    """epic #547 P1 Task 4: dual_epc_curve is first-party SVG now (axis-swapped,
    hover-only) — its "every log-$ axis must be readable, non-overlapping"
    INTENT (the history this module's docstring documents) is now satisfied by
    chart_svg.epc_curve emitting exactly one y-tick per decade (see
    test_chart_svg.py test_epc_y_ticks_are_loss_decades_with_currency),
    enforced in Python geometry instead of the retired chart vendor's dtick
    layout key. This pins the SVG replacement: a single log-y svg renders
    with NO slider/toggle controls (hover-only hydration mode), and the
    retired axis JSON is gone."""
    rendered = _render_macro("dual_epc_curve", _DUAL_EPC_PAYLOAD)
    assert 'data-y-scale="log"' in rendered
    assert 'data-role="p-slider"' not in rendered
    assert "'type': 'log'" not in rendered  # retired chart vendor's axis JSON is gone
    assert "dtick" not in rendered


def test_single_run_charts_use_residual_token_not_default_blue() -> None:
    """Design-system consistency (2026-07-04, updated epic #547 P2): the
    single-run LEC/EPC curves use the shared --chart-residual CSS var —
    matching the aggregate charts AND the PDF — NOT a hardcoded color.
    Originally pinned the raw CHART_SERIES hex (the retired chart vendor's
    ``line.color`` took a literal color); now that these macros are
    first-party SVG the series color is a CSS custom property
    (``var(--chart-residual)``, never a raw hex — theming is pure CSS per
    the epic's Architecture rule), so the contract to pin is the var()
    reference, mirroring test_chart_macro_palette.py's dual-card SVG
    palette tests."""
    for name, pts in (
        ("loss_exceedance_curve", _LEC_POINTS),
        ("exceedance_probability_curve", _EPC_POINTS),
    ):
        rendered = _render_macro(name, pts)
        assert "var(--chart-residual)" in rendered, f"{name}: expected the residual CSS var"
        assert CHART_SERIES["residual"]["light"] not in rendered, (
            f"{name}: raw residual hex must not appear — color must be a CSS var, not a literal"
        )
        assert "#1f77b4" not in rendered, (
            f"{name}: default chart-vendor blue #1f77b4 must not appear"
        )
