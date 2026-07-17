"""Help security invariants: autoescape on; article bodies carry no client script."""

from __future__ import annotations

import re
from pathlib import Path

from idraa.app import templates

_ARTICLES = (
    Path(__file__).resolve().parents[2] / "src" / "idraa" / "templates" / "help" / "articles"
)


def test_help_env_autoescapes_html():
    # Starlette's Jinja2Templates enables autoescape for .html; assert a value
    # with HTML metacharacters renders escaped.
    tpl = templates.env.from_string("{{ value }}")
    assert tpl.render(value="<b>&x</b>") == "&lt;b&gt;&amp;x&lt;/b&gt;"


def test_article_bodies_have_no_inline_script_or_alpine():
    # Directive-class regex (Sec-PG-1): the design promises "no x-data/@/x-on";
    # enforce the whole class, not just a few directives. Catches <script>,
    # inline DOM event handlers (onclick=, onerror=, ...), javascript: URLs, and
    # ALL Alpine directives: x-* (incl. x-html/x-init/x-effect), @event, and
    # :bind shorthand.
    bad = re.compile(
        r"<script"
        r"|javascript:"
        r"|\son\w+\s*="  # onclick=, onerror=, onload=, ...
        r"|(?<![\w-])x-[\w:.-]+"  # x-data, x-html, x-init, x-effect, ...
        r"|(?<![\w-])@[\w:.-]+\s*="  # @click=, @keydown=, @change=, ...
        r"|(?<![\w-]):[\w-]+\s*="  # :class=, :href= Alpine bind shorthand
    )
    for p in _ARTICLES.glob("*.html"):
        text = p.read_text(encoding="utf-8")
        m = bad.search(text)
        assert m is None, f"{p.name} contains client-script construct: {m.group(0)!r}"


def test_article_bodies_have_no_unsafe_or_dangerous_hrefs():
    # Sec-PG-2 / Sec-I3: hrefs must be hardcoded https (or routed through
    # linkify_https); never a bare |safe, never http:/javascript: anchors.
    bad = re.compile(r"\|\s*safe|href=\"http:|href=\"javascript:")
    for p in _ARTICLES.glob("*.html"):
        text = p.read_text(encoding="utf-8")
        m = bad.search(text)
        assert m is None, f"{p.name} contains an unsafe href/filter: {m.group(0)!r}"
