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


# PRSec2-1: forward quoted-span scan. A backward walk cannot distinguish a
# tag-close '>' from a value-internal '>' (Alpine `count > 5`), so it had
# false-negative holes. Consuming the full quoted span makes quote state
# structural. The `\{\{.*?\}\}` alternative treats a whole Jinja expression
# as one atomic unit before considering it character-by-character —
# otherwise a same-type quote embedded INSIDE the expression (e.g.
# `(anchor or "")` under a double-quoted outer attribute) would be
# mistaken for the outer delimiter and truncate the match before the real
# `|tojson` is ever reached. Unquoted `= {{ ... | tojson }}` is covered by
# _TOJSON_UNQUOTED.
_QUOTED_ATTR = re.compile(
    r"=\s*(?P<delim>[\"'])(?P<val>(?:\{\{.*?\}\}|(?!(?P=delim)).)*)(?P=delim)",
    re.DOTALL,
)
_TOJSON_IN_VAL = re.compile(r"\|\s*tojson")

# The fully-unquoted case (`attr={{ ... | tojson ... }}`, no delimiter at
# all) never reaches _QUOTED_ATTR — there's no quote to anchor on — but
# it's unsafe on its own terms: an unquoted HTML attribute value terminates
# at the first whitespace/`>`, and tojson's rendered JSON (brackets,
# commas, spaces) breaks the attribute long before quoting rules matter.
_TOJSON_UNQUOTED = re.compile(r"=\s*\{\{(?:(?!\}\}).)*?\|\s*tojson(?:(?!\}\}).)*?\}\}", re.DOTALL)


def _tojson_attr_offenders(src: str) -> list[int]:
    offenders = [
        src.count("\n", 0, m.start()) + 1
        for m in _QUOTED_ATTR.finditer(src)
        if m.group("delim") == '"' and _TOJSON_IN_VAL.search(m.group("val"))
    ]
    offenders += [src.count("\n", 0, m.start()) + 1 for m in _TOJSON_UNQUOTED.finditer(src)]
    return offenders


def test_no_tojson_in_unsafe_attribute_position() -> None:
    # Sec-B1: markupsafe's tojson escapes ' but NOT " — inside a
    # double-quoted (or unquoted) attribute it terminates/escapes the
    # attribute. House convention is single-quoted attributes for tojson
    # payloads; make it structural.
    offenders = []
    for path, src in _template_sources():
        for offset in _tojson_attr_offenders(src):
            line = src.count("\n", 0, offset) + 1
            offenders.append(f"{path}:{line}")
    assert not offenders, f"|tojson in unsafe attribute position: {offenders}"


# PRSec-1 negative fixture: the committed macros/help_trigger.html line
# verbatim, and that SAME expression with its outer quotes flipped to
# double — the exact shape a naive `="[^"]*|tojson` regex misses because
# `(anchor or "")` embeds a `"` before the real tojson usage ever appears.
_COMMITTED_SINGLE_QUOTED = """@click='$store.helpDrawer.show({{ (anchor or "") | tojson }})'"""
_FLIPPED_DOUBLE_QUOTED = '''@click="$store.helpDrawer.show({{ (anchor or "") | tojson }})"'''


def test_tojson_scanner_flags_true_positives_and_passes_safe_forms() -> None:
    # (a) the committed macros/help_trigger.html line, verbatim, passes.
    assert _tojson_attr_offenders(_COMMITTED_SINGLE_QUOTED) == []
    # (b) that SAME expression with its outer quotes flipped to double is
    # flagged — the exact shape a naive `="[^"]*|tojson` regex misses
    # because `(anchor or "")` embeds a `"` before the real tojson usage.
    assert _tojson_attr_offenders(_FLIPPED_DOUBLE_QUOTED) != []
    # (c) PRSec2-1: each of these defeated the prior backward-walking
    # scanner — a value-internal `>` (Alpine comparison / ternary /
    # data-cmp) or a `}}{{` run inside a double-quoted attribute was
    # mistaken for a tag-close or a prior-sibling boundary, so the walk
    # never reached the attribute's own `=`. The forward scanner has no
    # such blind spot: it consumes the WHOLE quoted span before ever
    # looking for `|tojson` inside it, so quote state is structural.
    defeated_the_old_scanner = [
        '<div x-data="{ big: count > 5, items: {{ items | tojson }} }">',
        '<div x-show="a >= 3 && {{ v | tojson }}">',
        '<div x-bind="n > 0 ? {{ v | tojson }} : 0">',
        '<div data-cmp="x > {{ v | tojson }}">',
        '<div x-data="{ a:{ b:1 }}{{ v | tojson }}">',
    ]
    for probe in defeated_the_old_scanner:
        assert _tojson_attr_offenders(probe) != [], f"probe not flagged: {probe!r}"
    # (d) a single-quoted attribute with a double-quoted inner default (the
    # house convention for tojson payloads) still passes — quote state is
    # structural, so the nested opposite-type quote can't defeat it.
    safe_nested_quote = """@click='$store.show({{ (anchor or "") | tojson }})'"""
    assert _tojson_attr_offenders(safe_nested_quote) == []
