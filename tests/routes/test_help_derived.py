"""Import-time derivation of help TOC / reading time / search text (spec §Registry)."""

from __future__ import annotations

import pytest

from idraa.help_content import (
    HELP_ARTICLES,
    HELP_DERIVED,
    parse_article_source,
)


def test_every_article_has_derived_entry_with_toc() -> None:
    for a in HELP_ARTICLES:
        d = HELP_DERIVED[a.slug]
        assert d.minutes >= 1
        assert len(d.toc) >= 1, f"{a.slug} has no h2 sections"
        ids = [i for i, _ in d.toc]
        assert len(set(ids)) == len(ids)
        assert d.search_text == d.search_text.lower()


def test_parse_extracts_toc_minutes_and_text() -> None:
    src = (
        '<h2 id="alpha" class="x">Alpha section</h2><p>' + ("word " * 400) + "</p>"
        '<h2 id="beta">Beta</h2><p>tail metrics</p>'
    )
    d = parse_article_source(src, slug="t")
    assert d.toc == (("alpha", "Alpha section"), ("beta", "Beta"))
    assert d.minutes == 2  # ~404 words / 200
    assert "tail metrics" in d.search_text


def test_parse_rejects_h2_without_id() -> None:
    with pytest.raises(ValueError, match="t: <h2> without id"):
        parse_article_source("<h2>No id</h2>", slug="t")


def test_parse_rejects_duplicate_ids() -> None:
    with pytest.raises(ValueError, match="duplicate h2 id"):
        parse_article_source('<h2 id="a">x</h2><h2 id="a">y</h2>', slug="t")


def test_parse_ignores_jinja_include_lines() -> None:
    d = parse_article_source(
        '{% include "help/figures/lec_anatomy.html" %}<h2 id="a">A</h2><p>body</p>',
        slug="t",
    )
    assert d.toc == (("a", "A"),)


def test_parse_rejects_bad_anchor_charset() -> None:
    with pytest.raises(ValueError, match="outside"):
        parse_article_source('<h2 id="Bad_Id!">x</h2>', slug="t")


def test_parse_rejects_script_tags() -> None:
    with pytest.raises(ValueError, match="not allowed in help content"):
        parse_article_source('<h2 id="a">x</h2><script>1</script>', slug="t")
    # Sec2-N1: the raw pre-check is differential-immune — comment-tokenizer
    # tricks and Jinja-stripped payloads still contain the literal bytes.
    with pytest.raises(ValueError, match="not allowed in help content"):
        parse_article_source("<!--><script>1</script>-->", slug="t")
    with pytest.raises(ValueError, match="not allowed in help content"):
        parse_article_source("{{ '<script>1</script>' | safe }}", slug="t")


def test_build_derived_rejects_script_in_figure(tmp_path) -> None:
    # Sec2-I3: figure sources are invisible to the article parser (includes
    # are Jinja-stripped) — _build_derived scans them directly.
    from idraa.help_content import HelpArticle, _build_derived

    articles_dir = tmp_path / "articles"
    figures_dir = tmp_path / "figures"
    articles_dir.mkdir()
    figures_dir.mkdir()
    (articles_dir / "a.html").write_text('<h2 id="s">S</h2><p>body</p>')
    (figures_dir / "evil.html").write_text("<svg></svg><script>1</script>")
    art = (HelpArticle("a", "A", "guide", 1, "s", ()),)
    with pytest.raises(ValueError, match="not allowed in help content"):
        _build_derived(
            articles=art,
            redirects={},
            route_map=(),
            articles_dir=articles_dir,
            figures_dir=figures_dir,
        )


def test_build_derived_rejects_quote_adjacent_event_handler_in_figure(tmp_path) -> None:
    # PRSec-2: `<rect x="1"onload=alert(1)>` has NO whitespace or slash
    # before `onload=` — just the closing quote of the PRECEDING attribute.
    # Browsers parse this identically to the whitespace-separated form; the
    # original `[\s/]on[a-z]+\s*=` pattern anchored only on whitespace/slash
    # and missed it, so the quote char itself must anchor the match too.
    from idraa.help_content import HelpArticle, _build_derived

    articles_dir = tmp_path / "articles"
    figures_dir = tmp_path / "figures"
    articles_dir.mkdir()
    figures_dir.mkdir()
    (articles_dir / "a.html").write_text('<h2 id="s">S</h2><p>body</p>')
    (figures_dir / "evil.html").write_text('<svg><rect x="1"onload=alert(1)></svg>')
    art = (HelpArticle("a", "A", "guide", 1, "s", ()),)
    with pytest.raises(ValueError, match="inline event-handler attribute not allowed"):
        _build_derived(
            articles=art,
            redirects={},
            route_map=(),
            articles_dir=articles_dir,
            figures_dir=figures_dir,
        )


def test_build_derived_scans_non_html_figure_files(tmp_path) -> None:
    # PRSec-3: figures_dir.rglob("*.html") silently skipped a sibling .svg
    # (invisible to the article parser too, since {% include %} is
    # Jinja-stripped) — rglob("*") + is_file() scans every figure file
    # regardless of extension.
    from idraa.help_content import HelpArticle, _build_derived

    articles_dir = tmp_path / "articles"
    figures_dir = tmp_path / "figures"
    articles_dir.mkdir()
    figures_dir.mkdir()
    (articles_dir / "a.html").write_text('<h2 id="s">S</h2><p>body</p>')
    (figures_dir / "evil.svg").write_text("<svg><script>1</script></svg>")
    art = (HelpArticle("a", "A", "guide", 1, "s", ()),)
    with pytest.raises(ValueError, match="not allowed in help content"):
        _build_derived(
            articles=art,
            redirects={},
            route_map=(),
            articles_dir=articles_dir,
            figures_dir=figures_dir,
        )


def test_build_derived_rejects_foreign_object_in_figure(tmp_path) -> None:
    # PRSec-4: <foreignObject> is SVG's arbitrary-HTML embedding hatch —
    # it can carry a nested <script>/on* payload through a shape the
    # script-tag and event-handler checks don't anticipate on their own.
    from idraa.help_content import HelpArticle, _build_derived

    articles_dir = tmp_path / "articles"
    figures_dir = tmp_path / "figures"
    articles_dir.mkdir()
    figures_dir.mkdir()
    (articles_dir / "a.html").write_text('<h2 id="s">S</h2><p>body</p>')
    (figures_dir / "evil.html").write_text(
        '<svg><foreignObject width="1" height="1">x</foreignObject></svg>'
    )
    art = (HelpArticle("a", "A", "guide", 1, "s", ()),)
    with pytest.raises(ValueError, match="foreignObject"):
        _build_derived(
            articles=art,
            redirects={},
            route_map=(),
            articles_dir=articles_dir,
            figures_dir=figures_dir,
        )


def test_build_derived_skips_dotfiles_in_figures(tmp_path) -> None:
    # PRArch2-N2: a stray dotfile (.DS_Store from macOS Finder browsing, an
    # editor swapfile, etc.) dropped into figures/ is not a figure — skip it
    # before read_text ever runs, so it can't crash the boot-time scan.
    from idraa.help_content import HelpArticle, _build_derived

    articles_dir = tmp_path / "articles"
    figures_dir = tmp_path / "figures"
    articles_dir.mkdir()
    figures_dir.mkdir()
    (articles_dir / "a.html").write_text('<h2 id="s">S</h2><p>body</p>')
    # Real .DS_Store files open with non-UTF-8 bytes -- if the dotfile skip
    # were missing, read_text(encoding="utf-8") would raise UnicodeDecodeError.
    (figures_dir / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1\xff\xfe\x00\x01")
    art = (HelpArticle("a", "A", "guide", 1, "s", ()),)
    result = _build_derived(
        articles=art,
        redirects={},
        route_map=(),
        articles_dir=articles_dir,
        figures_dir=figures_dir,
    )
    assert "a" in result


def test_build_derived_rejects_non_utf8_figure(tmp_path) -> None:
    # PRArch2-N2: a non-dotfile binary figure (e.g. a stray .png) must fail
    # loud with a legible ValueError, not a raw UnicodeDecodeError.
    from idraa.help_content import HelpArticle, _build_derived

    articles_dir = tmp_path / "articles"
    figures_dir = tmp_path / "figures"
    articles_dir.mkdir()
    figures_dir.mkdir()
    (articles_dir / "a.html").write_text('<h2 id="s">S</h2><p>body</p>')
    (figures_dir / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe\x00\x01")
    art = (HelpArticle("a", "A", "guide", 1, "s", ()),)
    with pytest.raises(ValueError, match="not readable as UTF-8"):
        _build_derived(
            articles=art,
            redirects={},
            route_map=(),
            articles_dir=articles_dir,
            figures_dir=figures_dir,
        )


@pytest.mark.parametrize(
    "figure_src",
    [
        # single-quoted: valid HTML/SVG attribute quoting the pre-PRSec2-2
        # guard missed (it only matched `href\s*=\s*"http`).
        "<svg><use href='http://evil.example/icon'></use></svg>",
        # unquoted: also valid HTML/SVG when the value has no whitespace.
        "<svg><use href=http://evil.example/icon></use></svg>",
        # protocol-relative: no explicit scheme, but the browser still
        # fetches it cross-origin exactly like an explicit http:// would.
        '<svg><image href="//evil.example/icon.png"/></svg>',
        # SVG2 bare href (no xlink: prefix -- valid since SVG2 on
        # <use>/<image>/<a>) pointing at an absolute external URL.
        '<svg><use href="http://evil.example/icon"></use></svg>',
    ],
    ids=["single-quoted", "unquoted", "protocol-relative", "svg2-bare-href"],
)
def test_build_derived_rejects_external_href_in_figure(tmp_path, figure_src) -> None:
    # PRSec2-2: none of these carry an `xlink:` prefix, so the OLD
    # `_XLINK_HREF`/`_ABSOLUTE_HTTP_HREF` pair missed all four -- the
    # broadened `_EXTERNAL_HREF` regex (quote-style- and
    # scheme-optionality-agnostic) is what catches them now.
    from idraa.help_content import HelpArticle, _build_derived

    articles_dir = tmp_path / "articles"
    figures_dir = tmp_path / "figures"
    articles_dir.mkdir()
    figures_dir.mkdir()
    (articles_dir / "a.html").write_text('<h2 id="s">S</h2><p>body</p>')
    (figures_dir / "evil.html").write_text(figure_src)
    art = (HelpArticle("a", "A", "guide", 1, "s", ()),)
    with pytest.raises(ValueError, match="absolute/protocol-relative href not allowed"):
        _build_derived(
            articles=art,
            redirects={},
            route_map=(),
            articles_dir=articles_dir,
            figures_dir=figures_dir,
        )


def test_build_derived_failure_paths(tmp_path) -> None:
    # SC-I5: every _build_derived guard has a pinned failure (injectable
    # params keep the real registry untouched).
    from idraa.help_content import HelpArticle, _build_derived

    (tmp_path / "a.html").write_text('<h2 id="s">S</h2><p>body</p>')
    art = (HelpArticle("a", "A", "guide", 1, "s", ()),)

    ok = _build_derived(articles=art, redirects={}, route_map=(), articles_dir=tmp_path)
    assert "a" in ok

    missing = (HelpArticle("gone", "G", "guide", 1, "s", ()),)
    with pytest.raises(ValueError, match="body missing"):
        _build_derived(articles=missing, redirects={}, route_map=(), articles_dir=tmp_path)

    dangling_rel = (HelpArticle("a", "A", "guide", 1, "s", ("nope",)),)
    with pytest.raises(ValueError, match="dangling related"):
        _build_derived(articles=dangling_rel, redirects={}, route_map=(), articles_dir=tmp_path)

    with pytest.raises(ValueError, match="bad redirect"):
        _build_derived(articles=art, redirects={"x": "nope"}, route_map=(), articles_dir=tmp_path)

    with pytest.raises(ValueError, match="route map"):
        _build_derived(
            articles=art, redirects={}, route_map=(("/x", "nope"),), articles_dir=tmp_path
        )
