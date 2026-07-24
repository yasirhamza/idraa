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


# PRSec-1: a single regex anchored on `="[^"]*|tojson` (the prior approach)
# is defeated by a nested string literal INSIDE the Jinja expression itself
# — e.g. `(anchor or "")`, the exact shape macros/help_trigger.html uses:
# the embedded `""` terminates a naive `[^"]*` character class before it
# ever reaches the real `|tojson`, so a double-quoted attribute wrapping
# THAT EXACT expression sails through undetected. A delimiter-walking
# scanner sidesteps this: it never tries to match the whole span between
# the attribute's `=` and `|tojson` in one shot — it walks back from the
# tojson usage to the ENCLOSING `{{` first, then keeps walking back past
# that to find the attribute's own `=`, so a stray quote nested inside the
# expression is never mistaken for the attribute delimiter.
def _tojson_attr_offenders(src: str) -> list[int]:
    """For each `| tojson` occurrence in ``src``, walk back to the
    enclosing `{{`, then further back for the nearest `=` whose delimiter
    (the first non-whitespace char after it) is a quote — or, for the
    fully-unquoted case, whose delimiter position IS the `{{` itself.

    Two things are deliberately skipped rather than examined character by
    character, because their innards can contain an `=`/quote that is NOT
    the enclosing attribute's own delimiter:

    - A PRIOR sibling `{{ ... }}` expression (e.g. two `|tojson` calls in
      the same `x-data=` object literal): its own kwargs — `map(attribute=
      "id")` is a real, committed example (analyses/new.html) — must never
      be mistaken for the CURRENT expression's attribute delimiter. Hitting
      its closing `}}` jumps straight back to before its opening `{{`,
      skipping the content entirely.
    - A `==`/`!=`/`<=`/`>=` comparison (e.g. `state.loss_shape == "x"`, a
      real committed example in scenarios/wizard/step_4_impact.html): the
      whitespace-then-quote skip meant for `attr = "value"` formatting
      would otherwise land on the comparison's quoted RHS and mistake it
      for a delimiter. `=` signs that are neither part of such an operator
      nor a genuine delimiter (e.g. JS `m=i` inside an already-open
      attribute value, house convention in scenarios/form.html's
      `hx-vals`) are skipped one at a time instead — their "delimiter"
      char is neither a quote nor `{{`, so the walk keeps going.

    A bare `>` crossed before any qualifying `=` means the `{{` sits in
    plain text or a `<script>` body, not an attribute value at all — not
    this lint's concern (an `=>` arrow function's `>` doesn't count as a
    tag close).

    Returns the offset of each unsafe `| tojson` occurrence — one whose
    enclosing attribute delimiter is not a bare `'`.
    """
    offenders: list[int] = []
    for m in re.finditer(r"\|\s*tojson", src):
        start = m.start()
        open_brace = src.rfind("{{", 0, start)
        if open_brace == -1:
            continue  # no enclosing Jinja expression -- not this lint's concern

        i = open_brace - 1
        delim: str | None = None
        while i >= 0:
            c = src[i]
            if c == "}" and i > 0 and src[i - 1] == "}":
                # tail of a PRIOR sibling {{ ... }} -- skip its whole body.
                prior_open = src.rfind("{{", 0, i - 1)
                if prior_open == -1:
                    break
                i = prior_open - 1
                continue
            if c == ">" and not (i > 0 and src[i - 1] == "="):
                break  # crossed a real tag close -- not inside an attribute
            if c == "=":
                prev_c = src[i - 1] if i > 0 else ""
                next_c = src[i + 1] if i + 1 < len(src) else ""
                if next_c == "=" or prev_c in "=!<>":
                    i -= 1
                    continue  # part of ==/!=/<=/>=/=>, not an assignment
                j = i + 1
                while j < len(src) and src[j] in " \t\r\n":
                    j += 1
                ch = src[j] if j < len(src) else ""
                if ch in ("'", '"') or j == open_brace:
                    delim = ch
                    break
                # not a genuine attribute delimiter (e.g. JS `m=i` inside an
                # already-open value) -- keep walking back for the true one.
            i -= 1
        if delim is not None and delim != "'":
            offenders.append(start)
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


def test_tojson_scanner_flags_flipped_quotes_and_passes_committed_form() -> None:
    assert _tojson_attr_offenders(_COMMITTED_SINGLE_QUOTED) == []
    assert _tojson_attr_offenders(_FLIPPED_DOUBLE_QUOTED) != []
