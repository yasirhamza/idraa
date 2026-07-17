"""help_trigger(slug) macro renders a drawer button and rejects bad slugs."""

from __future__ import annotations

import pytest

from idraa.app import templates

_ENV = templates.env


def _render(slug: str) -> str:
    tpl = _ENV.from_string(
        '{% from "macros/help_trigger.html" import help_trigger %}{{ help_trigger(slug) }}'
    )
    return tpl.render(slug=slug)


def _render_labeled(slug: str, label: str) -> str:
    tpl = _ENV.from_string(
        '{% from "macros/help_trigger.html" import help_trigger %}'
        "{{ help_trigger(slug, label=label) }}"
    )
    return tpl.render(slug=slug, label=label)


def test_renders_drawer_button_for_valid_slug():
    html = _render("build-a-scenario")
    assert 'hx-get="/help/build-a-scenario"' in html
    assert 'hx-target="#help-drawer-body"' in html
    assert "$store.helpDrawer.show()" in html


def test_invalid_slug_raises_at_render():
    with pytest.raises(KeyError):  # help_url raises KeyError for unknown slugs
        _render("totally-bogus")


def test_default_icon_label_uses_circle_button():
    # The bare "?" affordance is a fixed-size circular icon button.
    html = _render("build-a-scenario")
    assert "btn-circle" in html


def test_text_label_is_not_forced_into_a_circle():
    # A multi-word label must size to its content. btn-circle pins the button
    # to a one-glyph square, so a phrase like "Sub-function help" wrapped and
    # overlapped the neighbouring text on the control form header. The text
    # variant must drop btn-circle and size to its content.
    html = _render_labeled("control-sub-functions", "Sub-function help")
    assert "Sub-function help" in html
    assert "btn-circle" not in html
