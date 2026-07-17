"""F13: viewport_block_authoring — phone-only authoring-route block (UX, not auth)."""

from __future__ import annotations

from idraa.app import templates


def test_viewport_block_renders_reason_and_is_hidden_on_md_up() -> None:
    src = (
        "{% from 'macros/viewport_block_authoring.html' import viewport_block_authoring %}"
        "{{ viewport_block_authoring(reason='Use a tablet or desktop to create scenarios.') }}"
    )
    html = templates.env.from_string(src).render()
    assert "Use a tablet or desktop" in html
    assert "md:hidden" in html


def test_viewport_block_wraps_authoring_form_with_md_only_guard() -> None:
    src = """
    {% from 'macros/viewport_block_authoring.html' import viewport_block_authoring, only_on_md %}
    {{ viewport_block_authoring(reason='blocked') }}
    {% call only_on_md() %}<form>real form</form>{% endcall %}
    """
    html = templates.env.from_string(src).render()
    assert "md:hidden" in html
    assert "hidden md:block" in html
    assert "real form" in html
