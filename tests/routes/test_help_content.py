"""Help article registry integrity (design 2026-06-13)."""

from __future__ import annotations

from pathlib import Path

import pytest

from idraa.help_content import HELP_ARTICLES, HELP_BY_SLUG, help_url

_ARTICLES_DIR = (
    Path(__file__).resolve().parents[2] / "src" / "idraa" / "templates" / "help" / "articles"
)


def test_registry_has_eleven_unique_slugs():
    slugs = [a.slug for a in HELP_ARTICLES]
    assert len(slugs) == 11
    assert len(set(slugs)) == 11  # unique
    assert "control-value-robustness" in slugs  # #419 plain-English explainer
    assert "raw-samples-export" in slugs  # #109 raw sample export


def test_by_slug_index_matches():
    assert set(HELP_BY_SLUG) == {a.slug for a in HELP_ARTICLES}


def test_related_slugs_all_resolve():
    for a in HELP_ARTICLES:
        for rel in a.related:
            assert rel in HELP_BY_SLUG, f"{a.slug} -> dangling related {rel!r}"
            assert rel != a.slug, f"{a.slug} relates to itself"


def test_every_article_has_title_summary_cluster():
    for a in HELP_ARTICLES:
        assert a.title and a.summary and a.cluster


def test_help_url_validates_slug():
    assert help_url("getting-started") == "/help/getting-started"
    with pytest.raises(KeyError):
        help_url("does-not-exist")


def test_help_url_registered_as_jinja_global():
    from idraa.app import templates

    assert templates.env.globals.get("help_url") is help_url


def test_every_slug_has_a_template():
    for a in HELP_ARTICLES:
        assert (_ARTICLES_DIR / f"{a.slug}.html").is_file(), f"missing body for {a.slug}"


def test_no_orphan_article_templates():
    slugs = {a.slug for a in HELP_ARTICLES}
    on_disk = {p.stem for p in _ARTICLES_DIR.glob("*.html")}
    assert on_disk == slugs, f"orphan/missing: {on_disk ^ slugs}"
