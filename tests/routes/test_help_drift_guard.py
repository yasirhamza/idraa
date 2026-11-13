"""Drift guard for the help articles: UI labels, stated limits, product paths.

Three mechanical kinds of drift fail here instead of reaching a reader:

1. UI labels. A ``<strong data-ui="<source path>">Label</strong>`` in an article
   names a product control. The source path (relative to ``src/idraa/``) must
   exist, must not be help content, and must contain the label text.
2. Limits. Numeric limits the articles state as literals are pinned to the code
   defaults, so changing a default without updating the article fails.
3. Product paths. Every internal path an article links to or names in
   ``<code>`` must match a registered GET route.

Pure and synchronous: files are read from disk, and the route table is read
from the app object without starting a server.
"""

from __future__ import annotations

import html
import io
import re
import tokenize
from collections.abc import Iterable, Iterator
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
from fastapi.routing import APIRoute

from idraa.app import app
from idraa.config import Settings
from idraa.services.scenario_import_parsers import MAX_ROWS

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "src" / "idraa"
_ARTICLES_DIR = _SRC / "templates" / "help" / "articles"

# Floor for the number of ``data-ui`` labels across all articles. Raise it when
# labels are added; the test exists so the marker cannot silently disappear.
_MIN_MARKED_LABELS = 62

# Labels that must stay marked somewhere (the reader-facing download buttons).
_REQUIRED_LABELS = (
    "Export CSV",
    "Export JSON",
    "Download report (PDF)",
    "Download verification workbook (Excel)",
    "Download raw samples (CSV)",
    "Upload & preview",
    "Use in wizard",
)


_VOID_TAGS = frozenset(
    [
        "br",
        "img",
        "hr",
        "input",
        "wbr",
        "meta",
        "link",
        "area",
        "base",
        "col",
        "embed",
        "source",
        "track",
        "param",
    ]
)
_TEMPLATE_COMMENT_RE = re.compile(r"\{#.*?#\}|<!--.*?-->", re.DOTALL)


def _matchable_text(text: str, suffix: str) -> str:
    """Return only the text a reader-facing label can legitimately come from.

    Templates: everything outside Jinja ``{# #}`` (including ``{#-``/``-#}``) and
    HTML ``<!-- -->`` comments.
    Python: string literals only (``STRING`` tokens, plus ``FSTRING_MIDDLE`` on
    3.12+), excluding docstrings. Identifiers, keywords, operators and comments
    are never matchable. Raises if the source cannot be tokenized.
    """
    if suffix != ".py":
        return _TEMPLATE_COMMENT_RE.sub("", text)
    tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    fstring_middle = getattr(tokenize, "FSTRING_MIDDLE", None)
    out: list[str] = []
    prev_sig: tokenize.TokenInfo | None = None
    for i, tok in enumerate(tokens):
        if tok.type == tokenize.STRING:
            # A docstring: a string that starts a statement and ends it.
            starts = prev_sig is None or prev_sig.type in (
                tokenize.NEWLINE,
                tokenize.INDENT,
                tokenize.DEDENT,
            )
            nxt = next((t for t in tokens[i + 1 :] if t.type != tokenize.COMMENT), None)
            ends = nxt is None or nxt.type in (tokenize.NEWLINE, tokenize.ENDMARKER)
            if not (starts and ends):
                out.append(tok.string)
                out.append(" ")
        elif fstring_middle is not None and tok.type == fstring_middle:
            out.append(tok.string)
            out.append(" ")
        if tok.type not in (tokenize.NL, tokenize.COMMENT):
            prev_sig = tok
    return "".join(out)


def _collapse(text: str) -> str:
    """Unescape entities, treat non-breaking spaces as spaces, collapse runs."""
    return re.sub(r"\s+", " ", html.unescape(text).replace("\xa0", " ")).strip()


def _contains_label(haystack: str, label: str) -> bool:
    """Whole-label match: no word char before, no word char or hyphen after."""
    pattern = rf"(?<!\w){re.escape(_collapse(label))}(?![\w-])"
    return re.search(pattern, _collapse(haystack)) is not None


class _ArticleScan(HTMLParser):
    """Collects data-ui labels, hrefs, <code> texts and plain text of one article."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.labels: list[tuple[str, str]] = []  # (source path, label text)
        self.hrefs: list[str] = []
        self.codes: list[str] = []
        self.text: list[str] = []
        self._strong_src: str | None = None
        self._strong_depth = 0
        self._strong_buf: list[str] = []
        self._in_code = False
        self._code_buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        if tag == "a" and attr.get("href"):
            self.hrefs.append(attr["href"] or "")
        if tag == "code":
            self._in_code = True
            self._code_buf = []
        if tag in _VOID_TAGS:
            return
        if self._strong_src is not None:
            self._strong_depth += 1
        elif tag == "strong" and attr.get("data-ui") is not None:
            self._strong_src = attr["data-ui"] or ""
            self._strong_depth = 1
            self._strong_buf = []

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID_TAGS:
            return
        if tag == "code" and self._in_code:
            self._in_code = False
            self.codes.append(_collapse("".join(self._code_buf)))
        if self._strong_src is not None:
            self._strong_depth -= 1
            if self._strong_depth == 0:
                self.labels.append((self._strong_src, _collapse("".join(self._strong_buf))))
                self._strong_src = None

    def handle_data(self, data: str) -> None:
        self.text.append(data)
        if self._in_code:
            self._code_buf.append(data)
        if self._strong_src is not None:
            self._strong_buf.append(data)


def _scan(path: Path) -> _ArticleScan:
    scan = _ArticleScan()
    scan.feed(path.read_text(encoding="utf-8"))
    scan.close()
    return scan


def _articles() -> dict[str, _ArticleScan]:
    return {p.stem: _scan(p) for p in sorted(_ARTICLES_DIR.glob("*.html"))}


_ARTICLES = _articles()


def test_articles_were_found() -> None:
    assert len(_ARTICLES) == 14, sorted(_ARTICLES)


# --------------------------------------------------------------------------
# Guard 1: UI labels
# --------------------------------------------------------------------------

_LABELS: list[tuple[str, str, str]] = [
    (slug, label, src) for slug, scan in _ARTICLES.items() for src, label in scan.labels
]


@pytest.mark.parametrize(
    ("slug", "label", "src"),
    _LABELS,
    ids=[f"{slug}:{label}" for slug, label, _ in _LABELS],
)
def test_marked_ui_label_exists_in_its_source(slug: str, label: str, src: str) -> None:
    where = f"article {slug!r}: label {label!r} names source {src!r}"
    source = _SRC / src
    assert source.is_file(), f"{where}, but that file does not exist under src/idraa/"
    rel = source.resolve().relative_to(_SRC.resolve()).as_posix()
    assert not rel.startswith("templates/help/"), f"{where}, but help content is not a source"
    try:
        haystack = _matchable_text(source.read_text(encoding="utf-8"), source.suffix)
    except (tokenize.TokenError, IndentationError, SyntaxError) as exc:
        pytest.fail(f"{where}, but {src} could not be tokenized: {exc!r}")
    assert _contains_label(haystack, label), f"{where}, but the file does not contain that text"


def test_label_in_jinja_comment_only_is_not_found() -> None:
    src = "{#- org-wide badge -#}\n{# multi\nline org-wide #}\n<b>x</b>"
    assert "org-wide" not in _matchable_text(src, ".html")


def test_label_in_html_comment_only_is_not_found() -> None:
    assert "Export" not in _matchable_text("<!-- Export\nCSV --><p>y</p>", ".html")


def test_label_in_rendered_template_text_is_found() -> None:
    assert "org-wide" in _matchable_text("{# c #}<span>org-wide</span>", ".html")


def test_label_in_python_comment_or_docstring_only_is_not_found() -> None:
    src = '"""Module says Export CSV."""\n\n\ndef f() -> None:\n    """Doc Export CSV."""\n    x = 1  # Export CSV\n'
    assert "Export CSV" not in _matchable_text(src, ".py")


def test_label_in_python_string_literal_is_found() -> None:
    src = '"""doc"""\nPara("Export CSV", style)\nx = "Contents"\n'
    out = _matchable_text(src, ".py")
    assert "Export CSV" in out
    assert "Contents" in out
    assert "doc" not in out


def test_identifier_is_not_a_python_label_match() -> None:
    src = "from x import TableOfContents\ntoc = TableOfContents()\n"
    assert not _contains_label(_matchable_text(src, ".py"), "Contents")
    assert "Contents" not in _matchable_text(src, ".py")


def test_python_string_literal_label_is_matched() -> None:
    assert _contains_label(_matchable_text('Paragraph("Contents", style)\n', ".py"), "Contents")


def test_fstring_literal_part_is_found() -> None:
    src = 'x = f"Reduction {sym}"\n'
    assert "Reduction" in _matchable_text(src, ".py")


def test_label_is_not_satisfied_by_longer_hyphenated_text() -> None:
    hay = _matchable_text('x = "Ranking-stability check skipped"\n', ".py")
    assert not _contains_label(hay, "Ranking")
    assert _contains_label(_matchable_text('x = "Ranking"\n', ".py"), "Ranking")


def test_label_is_not_satisfied_by_longer_word() -> None:
    assert not _contains_label("<p>TableOfContents</p>", "Contents")


@pytest.mark.parametrize("tail", ['"', "<", ""])
def test_label_ending_in_non_word_char_matches(tail: str) -> None:
    assert _contains_label(f"<b>Download report (PDF){tail}", "Download report (PDF)")


def test_unparseable_python_source_raises() -> None:
    with pytest.raises((tokenize.TokenError, IndentationError, SyntaxError)):
        _matchable_text('x = """unterminated\n', ".py")


def test_self_closing_anchor_href_is_recorded() -> None:
    scan = _ArticleScan()
    scan.feed('<p><a href="/scenarios" /> <a href="/runs"></a></p>')
    scan.close()
    assert scan.hrefs == ["/scenarios", "/runs"]


def test_void_tag_inside_marked_label_still_closes() -> None:
    scan = _ArticleScan()
    scan.feed(
        '<p><strong data-ui="a.html">First<br>line</strong> and '
        '<strong data-ui="b.html">Second<img src="x"><hr/></strong></p>'
    )
    scan.close()
    assert scan.labels == [("a.html", "Firstline"), ("b.html", "Second")]


def test_marked_label_count_does_not_drop() -> None:
    assert len(_LABELS) >= _MIN_MARKED_LABELS, (
        f"only {len(_LABELS)} data-ui labels across the articles; the floor is "
        f"{_MIN_MARKED_LABELS}. Restore the markers (or lower the floor on purpose)."
    )


@pytest.mark.parametrize("label", _REQUIRED_LABELS)
def test_required_label_is_marked(label: str) -> None:
    marked = {text for _, text, _ in _LABELS}
    assert label in marked, f"no article marks {label!r} with data-ui"


# --------------------------------------------------------------------------
# Guard 2: limits
# --------------------------------------------------------------------------


def _default(name: str) -> int:
    value = Settings.model_fields[name].default
    assert isinstance(value, int), name
    return value


def _plain(slug: str) -> str:
    return _collapse("".join(_ARTICLES[slug].text))


# (setting, display string built from the live default, article slugs)
_LIMITS: list[tuple[str, str, str]] = [
    ("mc_iterations_max", f"{_default('mc_iterations_max'):,}", "raw-samples-export"),
    ("mc_iterations_max", f"{_default('mc_iterations_max'):,}", "run-and-read-analyses"),
    (
        "mc_iterations_default",
        f"{_default('mc_iterations_default'):,} iterations",
        "run-and-read-analyses",
    ),
    (
        "high_fidelity_iterations_threshold",
        f"{_default('high_fidelity_iterations_threshold'):,}",
        "run-and-read-analyses",
    ),
    (
        "verification_workbook_max_n",
        f"{_default('verification_workbook_max_n'):,}",
        "reports-and-workbook",
    ),
    (
        "verification_workbook_aggregate_total_max",
        f"{_default('verification_workbook_aggregate_total_max'):,}",
        "reports-and-workbook",
    ),
    (
        "verification_workbook_max_scenarios",
        f"first {_default('verification_workbook_max_scenarios')} scenarios",
        "reports-and-workbook",
    ),
    ("scenario_import_parsers.MAX_ROWS", f"more than {MAX_ROWS} rows", "import-export"),
]


@pytest.mark.parametrize(
    ("setting", "expected", "slug"),
    _LIMITS,
    ids=[f"{setting}-{slug}" for setting, _, slug in _LIMITS],
)
def test_stated_limit_matches_code_default(setting: str, expected: str, slug: str) -> None:
    # Digit-boundary match: "50,000" must not be satisfied by "150,000".
    pattern = rf"(?<![\d,]){re.escape(expected)}(?!\d|,\d)"
    assert re.search(pattern, _plain(slug)), (
        f"article {slug!r} no longer states {expected!r}, the live default of {setting}. "
        f"Update the article (or the code default) so they agree."
    )


# --------------------------------------------------------------------------
# Guard 3: product paths
# --------------------------------------------------------------------------


def _flatten(routes: Iterable[Any]) -> Iterator[APIRoute]:
    # Same walk as tests/unit/test_export_role_gate.py.
    for r in routes:
        if isinstance(r, APIRoute):
            yield r
        orig = getattr(r, "original_router", None)
        if orig is not None and getattr(orig, "routes", None):
            yield from _flatten(orig.routes)
        sub = getattr(r, "routes", None)
        if sub:
            yield from _flatten(sub)


def _get_route_patterns() -> list[re.Pattern[str]]:
    patterns: list[re.Pattern[str]] = []
    for route in _flatten(app.routes):
        if "GET" not in (route.methods or set()):
            continue
        regex = re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(route.path))
        patterns.append(re.compile(f"^{regex}$"))
    return patterns


_GET_ROUTES = _get_route_patterns()


def _article_paths(scan: _ArticleScan) -> set[str]:
    raw = [h for h in scan.hrefs if h.startswith("/")] + [
        c for c in scan.codes if c.startswith("/") and " " not in c
    ]
    paths: set[str] = set()
    for item in raw:
        path = item.split("#", 1)[0].split("?", 1)[0]
        if not path or path.startswith(("//", "/static")):
            continue
        if path == "/help" or path.startswith("/help/"):
            continue  # covered by the help routing tests
        paths.add(path)
    return paths


_PATHS: list[tuple[str, str]] = [
    (slug, path) for slug, scan in _ARTICLES.items() for path in sorted(_article_paths(scan))
]


# Floor for collected product paths (22 when written). Raise it when paths are
# added; it stops a parser regression from emptying the parametrisation.
_MIN_PATHS = 22


def test_path_count_does_not_drop() -> None:
    assert len(_PATHS) >= _MIN_PATHS, (
        f"only {len(_PATHS)} product paths collected; the floor is {_MIN_PATHS}."
    )


def test_route_table_was_read() -> None:
    assert any(p.match("/scenarios") for p in _GET_ROUTES)
    assert any(p.match("/runs/abc") for p in _GET_ROUTES)


@pytest.mark.parametrize(("slug", "path"), _PATHS, ids=[f"{slug}:{path}" for slug, path in _PATHS])
def test_article_path_is_a_get_route(slug: str, path: str) -> None:
    assert any(p.match(path) for p in _GET_ROUTES), (
        f"article {slug!r} names the path {path!r}, which is not a registered GET route"
    )
