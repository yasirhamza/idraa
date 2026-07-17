"""Issue #129 T3 — unit_aware_inputs.html macro rendering.

Renders the macro in isolation against mock assignments per unit type.
Verifies widget shape (input attrs) and display format.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from jinja2 import Environment, FileSystemLoader

from idraa.models.enums import SUB_FUNCTION_UNITS, FairCamSubFunction


@dataclass
class _MockAssignment:
    sub_function_value: str
    capability_value: float | None
    coverage: float = 0.8
    reliability: float = 0.8

    @property
    def sub_function(self):
        return FairCamSubFunction(self.sub_function_value)


@pytest.fixture
def env():
    e = Environment(
        loader=FileSystemLoader("src/idraa/templates"),
        autoescape=True,
    )
    e.globals["sub_function_units_map"] = {
        sf: unit.value for sf, unit in SUB_FUNCTION_UNITS.items()
    }
    return e


def test_unit_display_probability_passes_through(env):
    """PROBABILITY → '{value:.3f}' to match current behavior."""
    tmpl = env.from_string(
        '{% from "macros/unit_aware_inputs.html" import unit_display %}{{ unit_display(a) }}'
    )
    html = tmpl.render(a=_MockAssignment("lec_prev_avoidance", 0.7))
    assert "0.700" in html
    assert "days" not in html
    assert "$" not in html


def test_unit_display_elapsed_time_shows_days(env):
    tmpl = env.from_string(
        '{% from "macros/unit_aware_inputs.html" import unit_display %}{{ unit_display(a) }}'
    )
    html = tmpl.render(a=_MockAssignment("lec_det_monitoring", 14.0))
    assert "14.0 days" in html


def test_unit_display_currency_shows_dollars(env):
    tmpl = env.from_string(
        '{% from "macros/unit_aware_inputs.html" import unit_display %}{{ unit_display(a) }}'
    )
    html = tmpl.render(a=_MockAssignment("lec_resp_loss_reduction", 5000))
    assert "$5,000 per event" in html


def test_unit_display_null_capability_renders_em_dash(env):
    """NULL capability_value renders '—' regardless of unit type."""
    tmpl = env.from_string(
        '{% from "macros/unit_aware_inputs.html" import unit_display %}{{ unit_display(a) }}'
    )
    html = tmpl.render(a=_MockAssignment("lec_det_monitoring", None))
    assert "—" in html


def test_unit_input_probability_has_bounds(env):
    tmpl = env.from_string(
        '{% from "macros/unit_aware_inputs.html" import unit_input %}{{ unit_input(a, 0) }}'
    )
    html = tmpl.render(a=_MockAssignment("lec_prev_avoidance", 0.7))
    assert 'min="0"' in html
    assert 'max="1"' in html
    assert 'step="0.01"' in html


def test_unit_input_elapsed_time_no_max(env):
    tmpl = env.from_string(
        '{% from "macros/unit_aware_inputs.html" import unit_input %}{{ unit_input(a, 0) }}'
    )
    html = tmpl.render(a=_MockAssignment("lec_det_monitoring", 14.0))
    assert 'min="0"' in html
    assert 'max="' not in html
    assert 'step="0.5"' in html
    assert "days" in html


def test_unit_input_currency_overlay_prefix_pattern(env):
    """UAT 2026-05-21 — proper currency widget per UXP / USWDS / Tailwind UI
    guidance:

    - type="text" + inputmode="numeric" (NOT type="number" — spinner arrows
      ate the 26px-wide input under the prior join-based layout, and
      type=number rejects locale separators / has inconsistent decimals)
    - Absolute-positioned $ glyph overlay (does not consume input width)
    - text-right alignment per financial convention
    - aria-hidden="true" on the visual $ so the screen reader gets unit
      context from the label, not duplicated by the glyph
    - The "per event" / "$" semantics move to the label via
      unit_label_suffix — NO inline suffix span in the input row
    - Pre-existing values render with thousands separators for readability;
      the field_validator strips them server-side
    """
    tmpl = env.from_string(
        '{% from "macros/unit_aware_inputs.html" import unit_input %}{{ unit_input(a, 0) }}'
    )
    html = tmpl.render(a=_MockAssignment("lec_resp_loss_reduction", 5000))
    assert 'type="text"' in html
    assert 'inputmode="numeric"' in html
    assert 'type="number"' not in html
    assert "step=" not in html  # no spinner-arrow stepper for currency
    assert 'aria-hidden="true"' in html
    assert "text-right" in html
    assert "absolute" in html  # overlay positioning
    # Pre-existing value rendered with thousands separators.
    assert 'value="5,000"' in html
    # Unit-context strings DO NOT appear inline in the input row —
    # they're in the label via unit_label_suffix.
    assert "per event" not in html
    assert "/ event" not in html


def test_unit_input_currency_empty_value_renders_blank_value(env):
    tmpl = env.from_string(
        '{% from "macros/unit_aware_inputs.html" import unit_input %}{{ unit_input(a, 0) }}'
    )
    html = tmpl.render(a=_MockAssignment("lec_resp_loss_reduction", None))
    assert 'value=""' in html
    assert 'placeholder="0"' in html


def test_unit_label_suffix_currency(env):
    """UAT 2026-05-21 — suffix is the compact "($/event)" rather than
    "($ per event)" because the Capability column is ~134px wide and the
    longer form wrapped onto a second line. Same semantics, no wrap."""
    tmpl = env.from_string(
        '{% from "macros/unit_aware_inputs.html" import unit_label_suffix %}'
        "Capability{{ unit_label_suffix(a) }}"
    )
    out = tmpl.render(a=_MockAssignment("lec_resp_loss_reduction", 5000))
    assert out.strip() == "Capability ($/event)"


def test_unit_label_suffix_elapsed_time(env):
    tmpl = env.from_string(
        '{% from "macros/unit_aware_inputs.html" import unit_label_suffix %}'
        "Capability{{ unit_label_suffix(a) }}"
    )
    out = tmpl.render(a=_MockAssignment("lec_det_monitoring", 14.0))
    assert out.strip() == "Capability (days)"


def test_unit_label_suffix_probability_is_empty(env):
    tmpl = env.from_string(
        '{% from "macros/unit_aware_inputs.html" import unit_label_suffix %}'
        "Capability{{ unit_label_suffix(a) }}"
    )
    out = tmpl.render(a=_MockAssignment("lec_prev_avoidance", 0.7))
    assert out.strip() == "Capability"


def test_unit_label_suffix_none_assignment_is_empty(env):
    """Blank-row (no sub-function picked yet) path renders no suffix."""
    tmpl = env.from_string(
        '{% from "macros/unit_aware_inputs.html" import unit_label_suffix %}'
        "Capability{{ unit_label_suffix(a) }}"
    )
    out = tmpl.render(a=None)
    assert out.strip() == "Capability"
