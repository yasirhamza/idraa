"""Chart series palette — single Python source for server-rendered chart SVG.

Validated 2026-07-03 (dataviz six-checks validator, light surface #FFFFFF /
dark surface #18181B): lightness band, chroma floor, CVD separation, contrast
all PASS in both modes. Values are duplicated BY DESIGN into:

  - src/idraa/static/css/app.css   (--chart-inherent / --chart-residual vars,
    used by legends, verdict-strip accents, dumbbell fallbacks, and the SVG
    <path> stroke colors themselves — the browser recomputes these on the
    data-theme attribute flip, no client-side JS restyle needed)

tests/unit/test_chart_tokens.py pins these copies to the same hexes.
"""

CHART_SERIES: dict[str, dict[str, str]] = {
    # "Without controls" — inherent risk series.
    "inherent": {"light": "#C2410C", "dark": "#EA580C"},
    # "With controls" — residual risk series.
    "residual": {"light": "#1E6BB0", "dark": "#3B82F6"},
}

# Trace-identity tags historically consumed by the retired client-side
# theme-restyle script; kept as named constants since chart.html's SVG
# macros still key data-series="without"/"with" attributes on them.
TRACE_META_INHERENT: str = "chart-inherent"
TRACE_META_RESIDUAL: str = "chart-residual"

# Appetite marker color (v3 SVG-only). Amber per the owner mockup's appetite
# marker; dataviz-validated on both surfaces alongside the CHART_SERIES
# palette.
CHART_APPETITE: dict[str, str] = {"light": "#B07A10", "dark": "#D99A2B"}

# Risk-reduction / value-of-controls bar color (epic #547 P2 milestone-gate
# finding 1) — SVG-only, same mechanism as CHART_APPETITE (pinned across
# chart_palette.py + app.css only: comparison_bars is a server-SVG-only
# macro). Reuses the design system's
# existing --color-status-success green (app.css) rather than inventing a
# 4th chart hue, so risk-reduction reads as "good/positive" consistent with
# the rest of the UI's success semantics — and, critically, is visually
# distinct from --chart-appetite (amber), which the LEC/EPC tolerance
# markers render on the SAME results panel.
CHART_REDUCTION: dict[str, str] = {"light": "#15803D", "dark": "#22C55E"}
