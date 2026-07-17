"""The run-detail chart macros must use the tokenized palette, never a
hardcoded/legacy color.

Renders each macro standalone through the app's real Jinja environment
(templates.env is module-level in idraa.app; env.from_string gets request-
free rendering via the ContextVar fallback) and inspects the emitted SVG.

History: originally inspected the emitted JSON of the prior chart vendor
(meta tags + literal `line.color`/`marker.color` hexes). Epic #547 ported
every chart macro in this file to first-party SVG (P1: dual_lec_curve/
dual_epc_curve; P2: the remaining 5, plus per_scenario_ale_bar deleted as
dead code) — palette coverage moved to the CSS-var contract every
series/bar element carries directly (``var(--chart-inherent)`` /
``var(--chart-residual)`` / ``var(--chart-appetite)`` /
``var(--chart-reduction)``), never a raw hex. There is no chart-vendor-
emitting macro left in chart.html to test the old way.
"""

import re

from idraa.app import templates

LEC_PAYLOAD = {
    "with_controls": [
        {"loss": 10_000, "probability": 0.9},
        {"loss": 1_000_000, "probability": 0.3},
    ],
    "without_controls": [
        {"loss": 10_000, "probability": 0.95},
        {"loss": 1_000_000, "probability": 0.6},
    ],
}
# dual_epc_curve reads pt.percentile / pt.loss (NOT loss/probability like
# dual_lec_curve — confirmed by reading chart.html's dual_epc_curve body,
# lines ~483-492). Reusing LEC_PAYLOAD there raises jinja2.UndefinedError
# ('dict object' has no attribute 'percentile') since plain dicts don't
# fall back on missing attribute access the way the macro's `.percentile`
# getattr-style access expects a present key. Task-2 fix, not in the brief.
EPC_PAYLOAD = {
    "with_controls": [{"percentile": 0.5, "loss": 10_000}, {"percentile": 0.99, "loss": 1_000_000}],
    "without_controls": [
        {"percentile": 0.5, "loss": 10_000},
        {"percentile": 0.99, "loss": 1_000_000},
    ],
}
_LEC_POINTS = [{"loss": 10_000, "probability": 0.9}, {"loss": 1_000_000, "probability": 0.3}]
_EPC_POINTS = [{"percentile": 0.5, "loss": 10_000}, {"percentile": 0.99, "loss": 1_000_000}]
_HEADLINE_WITH_BAND = {
    "value": 1_000_000.0,
    "lo": 800_000.0,
    "hi": 1_200_000.0,
    "has_ci_band": True,
}
_EFFECTIVENESS_ROWS = [{"control_id": "c1", "name": "MFA", "effectiveness": 0.85}]
_COMPARISON = {
    "base": 2_000_000.0,
    "residual": 500_000.0,
    "reduction": 1_500_000.0,
    "reduction_pct": 75.0,
}


def _render(macro_call: str, **ctx) -> str:
    tmpl = templates.env.from_string("{% import 'macros/chart.html' as chart %}" + macro_call)
    return tmpl.render(**ctx)


def test_dual_lec_curve_uses_tokenized_palette_svg():
    """epic #547 P1 Task 3: dual_lec_curve is first-party SVG now (no chart
    vendor, no meta tags) — its palette coverage moves to the CSS-var contract each
    series <path> carries directly: 'without' uses --chart-inherent, 'with'
    uses --chart-residual, dash-differentiated (never color-alone, plan-gate
    Spec-I5)."""
    html = _render("{{ chart.dual_lec_curve(payload) }}", payload=LEC_PAYLOAD)
    without_path = re.search(r'<path[^>]*data-series="without"[^>]*/>', html)
    with_path = re.search(r'<path[^>]*data-series="with"[^>]*/>', html)
    assert without_path and "var(--chart-inherent)" in without_path.group()
    assert with_path and "var(--chart-residual)" in with_path.group()
    assert "stroke-dasharray" in with_path.group()  # dashed identity, not color-alone


def test_dual_epc_curve_uses_tokenized_palette_svg():
    """epic #547 P1 Task 4: dual_epc_curve is first-party SVG now (no chart
    vendor, no meta tags) — same CSS-var palette contract as dual_lec_curve above:
    'without' uses --chart-inherent, 'with' uses --chart-residual,
    dash-differentiated (never color-alone, plan-gate Spec-I5)."""
    html = _render("{{ chart.dual_epc_curve(payload) }}", payload=EPC_PAYLOAD)
    without_path = re.search(r'<path[^>]*data-series="without"[^>]*/>', html)
    with_path = re.search(r'<path[^>]*data-series="with"[^>]*/>', html)
    assert without_path and "var(--chart-inherent)" in without_path.group()
    assert with_path and "var(--chart-residual)" in with_path.group()
    assert "stroke-dasharray" in with_path.group()  # dashed identity, not color-alone


def test_single_lec_and_epc_curves_use_tokenized_palette_svg():
    """epic #547 P2: the single-series LEC/EPC curves (run-results panel) are
    first-party SVG too — one series, so --chart-residual (matching the
    'with controls' dual-card token and the PDF), never a raw hex."""
    for call, ctx in (
        ("{{ chart.loss_exceedance_curve(points) }}", {"points": _LEC_POINTS}),
        ("{{ chart.exceedance_probability_curve(points) }}", {"points": _EPC_POINTS}),
    ):
        html = _render(call, **ctx)
        path = re.search(r'<path[^>]*data-series="without"[^>]*/>', html)
        assert path and "var(--chart-residual)" in path.group()


def test_ci_band_uses_tokenized_palette_svg():
    """epic #547 P2: headline_ale_with_ci_band's band + marker are
    --chart-residual (matching the residual-ALE series everywhere else)."""
    html = _render("{{ chart.headline_ale_with_ci_band(headline) }}", headline=_HEADLINE_WITH_BAND)
    assert "var(--chart-residual)" in html


def test_control_effectiveness_bar_uses_tokenized_palette_svg():
    """epic #547 P2: control_effectiveness_bar's <rect> bars are
    --chart-residual (single series, same convention as the single-run
    curves above)."""
    html = _render("{{ chart.control_effectiveness_bar(rows) }}", rows=_EFFECTIVENESS_ROWS)
    bar = re.search(r'<rect[^>]*data-role="bar"[^>]*>', html)
    assert bar and "var(--chart-residual)" in bar.group()


def test_risk_comparison_bar_uses_all_three_tokens_svg():
    """epic #547 P2: risk_comparison_bar's 3 bars use tokenized colors —
    base->inherent, residual->residual reuse the existing series tokens, and
    reduction->reduction uses its OWN dedicated --chart-reduction token
    (milestone-gate finding 1: NOT --chart-appetite, which the LEC/EPC
    tolerance markers on the SAME results panel already own)."""
    html = _render("{{ chart.risk_comparison_bar(comparison) }}", comparison=_COMPARISON)
    assert "var(--chart-inherent)" in html
    assert "var(--chart-residual)" in html
    assert "var(--chart-reduction)" in html
    # The reduction bar must NOT reuse the appetite (tolerance-marker) token.
    assert "var(--chart-appetite)" not in html


def test_no_legacy_hardcoded_series_hexes_in_retouched_macros():
    with open("src/idraa/templates/macros/chart.html", encoding="utf-8") as f:
        src = f.read()
    # #ef4444/#1f77b4/#10b981 must no longer appear in the retouched macros.
    # Cheap proxy: the strings may survive elsewhere in the file, so slice
    # out each retouched macro body.
    # Note (task-2-brief Step 1 footnote): chart.html's macro-open delimiter
    # is "{% macro" (no leading dash) even though the close is "{%- endmacro".
    for name in (
        "dual_lec_curve",
        "dual_epc_curve",
        "loss_exceedance_curve",
        "exceedance_probability_curve",
        "headline_ale_with_ci_band",
        "control_effectiveness_bar",
        "risk_comparison_bar",
    ):
        start = src.index("{% macro " + name)
        end = src.index("{%- endmacro", start)
        body = src[start:end]
        for legacy in ("#ef4444", "#1f77b4", "#10b981", "rgb(37, 99, 235)"):
            assert legacy not in body, f"{legacy} still in {name}"
