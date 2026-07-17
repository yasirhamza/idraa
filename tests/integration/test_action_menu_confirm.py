"""Sec-B1 regression: action_menu confirm-string quoting.

The macro renders ``onsubmit='return window.confirm({{ item.confirm | tojson }});'``
(``window.``-qualified since T8's e2e finding -- see action_menu.html's inline
comment; a bare ``confirm`` identifier is shadowed by the destructive items'
own ``<input name="confirm">`` hidden field).
`tojson` escapes `'` but NOT `"`, so the attribute MUST be single-quoted:
a double-quoted attribute would truncate at the first `"` inside the confirm
string (fail-open on the only human gate) AND inject the trailing tokens as
new attributes — an attribute-injection XSS at call sites that interpolate
user-controlled strings (scenario name, email).

This test would FAIL on the pre-fix double-quoted macro and PASS after.
"""

from __future__ import annotations

from html.parser import HTMLParser
from types import SimpleNamespace

from idraa.app import templates

# A confirm string carrying every char class that breaks the double-quoted form:
# a `"` (attribute terminator), spaces, and `=` (bare-attribute injector).
_CONFIRM = 'Delete "x" permanently y=z?'


class _FormAttrCollector(HTMLParser):
    """Collect the attribute list of every <form> start tag."""

    def __init__(self) -> None:
        super().__init__()
        self.form_attrs: list[list[tuple[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "form":
            self.form_attrs.append(attrs)


def _render() -> str:
    src = (
        "{% from 'macros/action_menu.html' import action_menu %}"
        "{{ action_menu([{'label':'Delete','method':'post',"
        "'action':'/x/delete','danger':True,'confirm':confirm}]) }}"
    )
    fake_request = SimpleNamespace(state=SimpleNamespace(csrf_token="test-csrf"))
    return templates.env.from_string(src).render(request=fake_request, confirm=_CONFIRM)


def test_action_menu_confirm_is_single_attribute_no_injection() -> None:
    html = _render()
    parser = _FormAttrCollector()
    parser.feed(html)

    assert len(parser.form_attrs) == 1, "expected exactly one <form> tag"
    attrs = parser.form_attrs[0]
    names = [name for name, _ in attrs]

    # (a) exactly one onsubmit attribute, carrying the WHOLE confirm string
    #     (unbroken through the `"` — proves no early truncation).
    onsubmit_vals = [v for n, v in attrs if n == "onsubmit"]
    assert len(onsubmit_vals) == 1, f"expected one onsubmit attr, got {onsubmit_vals}"
    onsubmit = onsubmit_vals[0] or ""
    # The tail token survives → the handler was not truncated at the inner `"`.
    assert "y=z?" in onsubmit
    assert "permanently" in onsubmit

    # Must call window.confirm, not bare confirm: a hidden name="confirm" input
    # in the same form shadows the global confirm() in the inline-handler scope
    # chain, so bare confirm() throws and the form submits UNCONFIRMED. Only the
    # e2e (excluded from the default suite) exercises a real browser, so this
    # unit assertion is the default-suite guard against a silent revert.
    assert "window.confirm(" in onsubmit, (
        "must call window.confirm, not bare confirm (scope-chain shadowing regression)"
    )

    # (b) no injected attributes leaked out of the confirm string onto the tag.
    for injected in ("y", "z?", "permanently", "x"):
        assert injected not in names, f"attribute injection: {injected!r} became a form attr"
