"""In-memory help search: AND semantics, field weighting, prefix match."""

from __future__ import annotations

from idraa.services.help_search import search_help


def test_title_hit_outranks_body_hit() -> None:
    # SC3-N2: "build" title-prefix-matches exactly ONE title ("Build a
    # scenario") — "scenario" also hits "Import & export scenarios" and
    # would pass only via the alphabetical tiebreak.
    hits = search_help("build")
    assert hits, "expected hits for 'build'"
    assert hits[0].slug == "build-a-scenario"  # title match ranks first


def test_all_terms_must_match() -> None:
    assert search_help("scenario zzzznope") == []


def test_prefix_matching() -> None:
    assert any(h.slug == "build-a-scenario" for h in search_help("scenar"))


def test_matched_heading_is_surfaced() -> None:
    hits = search_help("glossary")
    top = hits[0]
    assert top.slug == "fair-in-idraa-terms"
    assert top.heading  # some heading text carried for context


def test_empty_and_short_queries() -> None:
    assert search_help("") == []
    assert search_help("a") == []
