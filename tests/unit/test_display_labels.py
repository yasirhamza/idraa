"""Web display-label map over the canonical tail ladder (spec §Label map).

Methodology rule pinned here: every plain-language label must faithfully render
the statistic — return-period phrasing only on VaR quantiles, "typical case"
only on the median, "average" only on the mean.
"""

from idraa.services._view_model_helpers import (
    TAIL_LADDER_DISPLAY_LABELS,
    TAIL_LADDER_LABELS,
)


def test_every_canonical_label_has_a_display_label():
    assert set(TAIL_LADDER_DISPLAY_LABELS.keys()) == set(TAIL_LADDER_LABELS.values())


def test_display_labels_exact_strings():
    assert TAIL_LADDER_DISPLAY_LABELS == {
        "Mean": "Mean (average)",
        "Median": "Typical case (median)",
        "Std dev": "Std deviation",
        "VaR 90%": "1-in-10 year (VaR 90%)",
        "VaR 95%": "1-in-20 year (VaR 95%)",
        "VaR 99%": "1-in-100 year (VaR 99%)",
        "VaR 99.9%": "1-in-1000 year (VaR 99.9%)",
        "ES 95%": "Expected shortfall (95%)",
        "ES 99%": "Expected shortfall (99%)",
        "ES 99.9%": "Expected shortfall (99.9%)",
    }
