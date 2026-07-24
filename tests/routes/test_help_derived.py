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
