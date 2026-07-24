"""Structural guards for the help hub: trigger/anchor liveness across ALL
templates (render-time validation only covers pages some test renders),
route-map drift, and the tojson-attribute quoting convention."""

from __future__ import annotations

import pathlib
import re

from idraa.help_content import HELP_BY_SLUG, HELP_DERIVED, HELP_ROUTE_MAP

TEMPLATES = pathlib.Path("src/idraa/templates")
# Sec2-N2: accept both quote styles so a double-quoted call site can't
# silently drop out of the inventory.
_TRIGGER = re.compile(
    r"help_trigger\(\s*['\"](?P<slug>[^'\"]+)['\"]"
    r"(?:[^)]*?anchor\s*=\s*['\"](?P<anchor>[^'\"]+)['\"])?",
)


_JINJA_COMMENT = re.compile(r"{#.*?#}", re.DOTALL)


def _template_sources() -> list[tuple[pathlib.Path, str]]:
    # Jinja comments stripped (Sec3-I2/SC3-B1: base.html:160's drawer doc
    # comment contains the literal `help_trigger(` — comments are not call
    # sites, and stripping keeps the count and the regex symmetric).
    return [
        (p, _JINJA_COMMENT.sub(" ", p.read_text(encoding="utf-8")))
        for p in TEMPLATES.rglob("*.html")
    ]


def test_every_template_trigger_resolves_to_live_slug_and_anchor() -> None:
    found = 0
    raw_calls = 0
    for path, src in _template_sources():
        if path.name == "help_trigger.html":
            continue  # the macro definition, not call sites
        raw_calls += src.count("help_trigger(")
        for m in _TRIGGER.finditer(src):
            found += 1
            slug, anchor = m.group("slug"), m.group("anchor")
            assert slug in HELP_BY_SLUG, f"{path}: dangling help slug {slug!r}"
            if anchor:
                ids = {i for i, _ in HELP_DERIVED[slug].toc}
                assert anchor in ids, f"{path}: dangling anchor {anchor!r} for {slug!r}"
    assert found >= 9, "trigger inventory shrank unexpectedly — grep regex drift?"
    assert found == raw_calls, (
        f"regex matched {found} of {raw_calls} call sites — quote-style drift?"
    )


def test_no_first_party_chrome_link_targets_a_redirect_source() -> None:
    # Arch2-I4: the redirect map serves bookmarks and stale drawers, never
    # live first-party CHROME references — those must point at live slugs.
    # help/articles/** is EXCLUDED (Arch3-B1/Sec3-B1/SC3-B2): article-body
    # cross-links intentionally ride the 301s until the P2 content pass —
    # P2 removes this exclusion (spec P2 watch-item e).
    # Boundary lookahead (Sec3-I1): a plain substring for "reports" would
    # match the LIVE slug reports-and-workbook and false-fire forever.
    from idraa.help_content import HELP_REDIRECTS

    articles = TEMPLATES / "help" / "articles"
    offenders = [
        f"{path}:{old}"
        for path, src in _template_sources()
        if not path.is_relative_to(articles)
        for old in HELP_REDIRECTS
        if re.search(rf"/help/{re.escape(old)}(?![a-z0-9-])", src)
    ]
    assert not offenders, f"first-party chrome links ride a 301: {offenders}"


def test_route_map_prefixes_match_registered_routes() -> None:
    # Arch-N2: prefixes are string-coupled to mounted routes; guard drift.
    # SC2-I2: on FastAPI 0.139 include_router wraps routers in
    # _IncludedRouter — app.routes exposes only a handful of path-bearing
    # entries, so traverse nested routers via original_router (100+ paths).
    from idraa.app import app

    route_paths: list[str] = []
    stack = list(app.routes)
    while stack:
        r = stack.pop()
        inner = getattr(r, "original_router", None)
        if inner is not None:
            stack.extend(inner.routes)
            continue
        p = getattr(r, "path", None)
        if p:
            route_paths.append(p)
    for prefix, _ in HELP_ROUTE_MAP:
        assert any(p == prefix or p.startswith(prefix + "/") for p in route_paths), (
            f"route-map prefix {prefix!r} matches no registered route"
        )


# Sec2-I1: scan the WHOLE source, not per-line — [^"]* crosses newlines, and
# 21 double-quoted attributes in this codebase already wrap across lines.
# Sec3-N1: \s* before the quote — `attr = "…"` formatting would evade a bare
# `="` anchor (verified 0 false positives repo-wide).
_TOJSON_IN_DQUOTE = re.compile(r'=\s*"[^"]*\|\s*tojson')
# Sec2-I1 second form: an UNQUOTED attribute value taking tojson output is
# the same breakout class (the JSON string's \" is not an HTML escape).
_TOJSON_UNQUOTED = re.compile(r"=\s*\{\{[^}]*\|\s*tojson")


def test_no_tojson_in_unsafe_attribute_position() -> None:
    # Sec-B1: markupsafe's tojson escapes ' but NOT " — inside a
    # double-quoted (or unquoted) attribute it terminates/escapes the
    # attribute. House convention is single-quoted attributes for tojson
    # payloads; make it structural.
    offenders = []
    for path, src in _template_sources():
        for pattern in (_TOJSON_IN_DQUOTE, _TOJSON_UNQUOTED):
            for m in pattern.finditer(src):
                line = src.count("\n", 0, m.start()) + 1
                offenders.append(f"{path}:{line}")
    assert not offenders, f"|tojson in unsafe attribute position: {offenders}"
