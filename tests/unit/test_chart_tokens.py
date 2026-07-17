"""Pins the chart-palette single-source-of-truth pairing (spec §Tokens & charts).

The palette lives in two coordinated places (CSS vars for DOM/SVG, Python
constant for server-rendered chart geometry — see services/chart_svg.py).
These tests pin both to the validator-approved hex values so drift in either
place fails loudly.

History: a third place, static/js/chart_theme.js (a client-side restyle
script for the retired prior chart vendor), was removed in epic #547 P3 —
the browser now recomputes SVG stroke colors from the CSS custom properties
directly on the data-theme attribute flip, with no JS restyle needed. Its
token-pin tests were removed with it (test-migration, not a rewrite).
"""

from pathlib import Path

from idraa.services.chart_palette import CHART_SERIES

APP_CSS = Path("src/idraa/static/css/app.css").read_text(encoding="utf-8")

EXPECTED = {
    ("inherent", "light"): "#C2410C",
    ("inherent", "dark"): "#EA580C",
    ("residual", "light"): "#1E6BB0",
    ("residual", "dark"): "#3B82F6",
}


def test_chart_series_python_constant_matches_validated_palette():
    for (series, mode), hexval in EXPECTED.items():
        assert CHART_SERIES[series][mode] == hexval


def test_app_css_defines_chart_vars_in_both_theme_blocks():
    # Light values in :root, dark values in the [data-theme="dark"] block.
    # Whitespace-tolerant (plan-gate Arch-N3): pin var->hex, not alignment.
    import re

    for var, hexval in (
        ("--chart-inherent", "#C2410C"),
        ("--chart-residual", "#1E6BB0"),
        ("--chart-inherent", "#EA580C"),
        ("--chart-residual", "#3B82F6"),
    ):
        assert re.search(rf"{var}:\s*{hexval};", APP_CSS), f"{var} -> {hexval} missing"


def test_btn_primary_navy_override_present():
    assert ".btn-primary" in APP_CSS
    assert "var(--color-brand)" in APP_CSS.split(".btn-primary", 1)[1][:400]


def test_appetite_token_python_and_css_only():
    import re

    from idraa.services.chart_palette import CHART_APPETITE

    assert CHART_APPETITE == {"light": "#B07A10", "dark": "#D99A2B"}
    for hexval in ("#B07A10", "#D99A2B"):
        assert re.search(rf"--chart-appetite:\s*{hexval};", APP_CSS), (
            f"--chart-appetite {hexval} missing"
        )


def test_reduction_token_python_and_css_only():
    # epic #547 P2 milestone-gate finding 1: --chart-reduction is a dedicated
    # SVG-only token for the comparison_bars Reduction bar, distinct from
    # --chart-appetite (which is reserved for the tolerance marker that
    # renders on the SAME results panel). Same single-source mechanism as
    # the --chart-appetite pin above.
    import re

    from idraa.services.chart_palette import CHART_REDUCTION

    assert CHART_REDUCTION == {"light": "#15803D", "dark": "#22C55E"}
    for hexval in ("#15803D", "#22C55E"):
        assert re.search(rf"--chart-reduction:\s*{hexval};", APP_CSS), (
            f"--chart-reduction {hexval} missing"
        )
