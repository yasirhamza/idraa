"""Assert Jinja2 autoescape is wired for HTML — primary XSS defense.

Regression guard: if someone disables autoescape globally or swaps the
``Jinja2Templates`` instance for a bare ``jinja2.Environment`` without
autoescape, this test fails. Cheap belt-and-suspenders against the class
of bugs covered by OWASP A03:2021 (Injection, incl. XSS).
"""

from __future__ import annotations

from idraa.app import templates


def test_autoescape_enabled() -> None:
    """Autoescape selector must be configured and active for .html templates."""
    auto = templates.env.autoescape
    assert auto, "Jinja2 autoescape must not be disabled"
    # FastAPI defaults to select_autoescape(['html', 'htm', 'xml']) — confirm
    # it actually returns True for our real file type.
    assert callable(auto), "autoescape should be a selector function, not a bare bool"
    assert auto("dashboard/index.html") is True


def test_autoescape_escapes_user_input() -> None:
    """End-to-end check: a variable containing HTML is escaped when rendered."""
    tmpl = templates.env.from_string("{{ payload }}")
    rendered = tmpl.render(payload="<script>alert(1)</script>")
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
