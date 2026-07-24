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
    # #419 plain-English explainer; slug renamed control-value-robustness ->
    # why-values-are-ranges (help-overhaul P1 T1).
    assert "why-values-are-ranges" in slugs
    assert "raw-samples-export" in slugs  # #109 raw sample export


def test_by_slug_index_matches():
    assert set(HELP_BY_SLUG) == {a.slug for a in HELP_ARTICLES}


def test_related_slugs_all_resolve():
    for a in HELP_ARTICLES:
        for rel in a.related:
            assert rel in HELP_BY_SLUG, f"{a.slug} -> dangling related {rel!r}"
            assert rel != a.slug, f"{a.slug} relates to itself"


def test_every_article_has_title_and_summary():
    # `cluster` field removed (help-overhaul P1 T1: track/order replace it);
    # track coherence now covered by test_tracks_and_orders_are_coherent below.
    for a in HELP_ARTICLES:
        assert a.title and a.summary


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


def test_tracks_and_orders_are_coherent() -> None:
    from idraa.help_content import HELP_ARTICLES, TRACK_TITLES

    assert set(TRACK_TITLES) == {"guide", "methodology"}
    for track in TRACK_TITLES:
        orders = [a.order for a in HELP_ARTICLES if a.track == track]
        assert orders == sorted(orders)
        assert len(set(orders)) == len(orders), f"duplicate order in {track}"
    assert all(a.track in TRACK_TITLES for a in HELP_ARTICLES)


def test_redirect_map_targets_are_live_and_acyclic() -> None:
    from idraa.help_content import HELP_BY_SLUG, HELP_REDIRECTS

    for old, new in HELP_REDIRECTS.items():
        assert old not in HELP_BY_SLUG, f"redirect source {old} still live"
        assert new in HELP_BY_SLUG, f"redirect target {new} dangling"


def test_route_map_longest_prefix_wins() -> None:
    from idraa.help_content import HELP_BY_SLUG, HELP_ROUTE_MAP, help_slug_for_path

    for _, slug in HELP_ROUTE_MAP:
        assert slug in HELP_BY_SLUG
    assert help_slug_for_path("/scenarios/import") == "import-export"
    assert help_slug_for_path("/scenarios/123") == "build-a-scenario"
    assert help_slug_for_path("/nowhere") is None
