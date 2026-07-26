"""PR2 Task 8b: the "Max" (capacity cap) row in the ``pert_distribution_chart``
macro's lognormal / lognormal_mixture branches (templates/macros/chart.html).

Renders the macro through the REAL app template environment (same pattern as
tests/unit/test_macros_forms.py) so the ``lognormal_display_rows`` /
``lognormal_mixture_display_rows`` globals and the ``format_dist_value``
filter resolve exactly as they do in production — no isolated/stubbed env.
"""

from __future__ import annotations

import math

from idraa.app import templates


def _render_pert_chart(distribution: dict, fmt: str = "money") -> str:
    src = (
        "{% from 'macros/chart.html' import pert_distribution_chart %}"
        "{{ pert_distribution_chart('t', distribution, fmt=fmt) }}"
    )
    return templates.env.from_string(src).render(distribution=distribution, fmt=fmt)


def test_lognormal_chart_no_max_row_when_uncapped() -> None:
    html = _render_pert_chart({"distribution": "lognormal", "mean": 12.0, "sigma": 1.5})
    assert "Max" not in html


def test_lognormal_chart_shows_max_row_when_capped() -> None:
    mu, sigma, cap = math.log(1_000_000.0), 1.7, 500_000.0
    html = _render_pert_chart({"distribution": "lognormal", "mean": mu, "sigma": sigma, "max": cap})
    assert "Max" in html
    # cap=500,000 formatted via format_money_input — sanity-check the digits appear.
    assert "500,000" in html or "500000" in html


def test_lognormal_mixture_chart_no_max_row_when_uncapped() -> None:
    html = _render_pert_chart(
        {
            "distribution": "lognormal_mixture",
            "components": [
                {"mean": 8.06, "sigma": 0.70, "weight": 0.5},
                {"mean": 15.77, "sigma": 1.19, "weight": 0.5},
            ],
        }
    )
    assert "Max" not in html


def test_lognormal_mixture_chart_shows_max_row_when_capped() -> None:
    html = _render_pert_chart(
        {
            "distribution": "lognormal_mixture",
            "components": [
                {"mean": math.log(1_000.0), "sigma": 0.5, "weight": 0.4},
                {"mean": math.log(1_000_000_000.0), "sigma": 0.8, "weight": 0.6},
            ],
            "max": 1_500.0,
        }
    )
    assert "Max" in html
