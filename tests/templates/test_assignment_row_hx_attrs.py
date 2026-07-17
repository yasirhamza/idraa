"""Regression test for issue #150: hx-params="none" wipes hx-vals expressions.

The sub-function `<select>` on `controls/_assignment_row.html` uses
``hx-vals='js:{sub_function: event.target.value}'`` to inject the
selected sub-function value into the HTMX GET request. HTMX 1.9.x
``filterValues`` short-circuits to ``{}`` when ``hx-params="none"`` is
set, which wipes hx-vals expressions in the same step. This was a real
production bug shipped in PR #148 (T5) that silently broke the
unit-aware widget swap — every user picking ELAPSED_TIME or CURRENCY
sub-functions saw the default blank widget instead of the unit-aware
one.

This test asserts the rendered template does NOT carry ``hx-params="none"``
on the sub-function select. It is the cheapest stable gate against the
class of bug; a deeper Playwright-based assertion is part of the
Phase 1.5b E2E infrastructure backlog.
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from idraa.models.enums import SUB_FUNCTION_UNITS, FairCamSubFunction

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "src" / "idraa" / "templates"


def _render_assignment_row() -> str:
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=True)
    env.globals["sub_function_units_map"] = {
        sf: unit.value for sf, unit in SUB_FUNCTION_UNITS.items()
    }
    # Combobox-race fix (2026-05-21): the x-data prop now inlines
    # `sub_function_groups_json` (a Jinja env global wired in `app.py`)
    # so hx-boost navigation can't race the trailing-script-tag setter.
    # The minimal test env doesn't load app.py's globals, so stub a
    # placeholder here — the value isn't exercised by these tests.
    env.globals["sub_function_groups_json"] = []
    template = env.get_template("controls/_assignment_row.html")
    return template.render(
        index=0,
        assignment=None,
        sub_function_choices=list(FairCamSubFunction),
    )


def test_sub_function_select_does_not_use_hx_params_none() -> None:
    """Issue #150 regression: hx-params="none" wipes hx-vals — must NOT be on the sub-function select."""
    html = _render_assignment_row()
    # The sub-function select block ends at </select> and starts with `<select`
    # containing `name="assignments[0][sub_function]"`. Slice it out.
    select_start = html.index('name="assignments[0][sub_function]"')
    # Walk back to find the opening <select tag
    select_open = html.rfind("<select", 0, select_start)
    select_close = html.index("</select>", select_start)
    select_block = html[select_open:select_close]

    assert 'hx-params="none"' not in select_block, (
        f'Sub-function <select> must NOT carry hx-params="none" — it wipes '
        f"the hx-vals JS expression at request-build time per HTMX 1.9.x "
        f"filterValues. Issue #150. Rendered block:\n{select_block}"
    )


def test_sub_function_select_has_hx_vals_expression() -> None:
    """Positive assertion: the hx-vals expression IS present and uses event.target.value.

    Pairs with the negative assertion above so a future change that removes
    hx-vals (rather than hx-params) is also caught.
    """
    html = _render_assignment_row()
    select_start = html.index('name="assignments[0][sub_function]"')
    select_open = html.rfind("<select", 0, select_start)
    select_close = html.index("</select>", select_start)
    select_block = html[select_open:select_close]

    assert "hx-vals=" in select_block, "hx-vals attribute missing from sub-function select"
    assert "sub_function" in select_block, (
        "hx-vals should inject the selected value under the dedicated "
        "'sub_function' query param so the route handler reads one source of truth"
    )
    assert "event.target.value" in select_block, (
        "hx-vals JS expression should evaluate event.target.value at request-build time"
    )
