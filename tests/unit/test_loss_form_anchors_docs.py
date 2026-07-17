"""Presence check for the D-ii-a loss-form-anchors companion docs."""

from __future__ import annotations

from pathlib import Path


def test_research_protocol_states_verification_gate() -> None:
    doc = Path("docs/reference/loss-form-anchors/research-protocol.md").read_text(encoding="utf-8")
    for token in (
        "adversarial",  # the second-agent re-fetch gate
        "primary",  # primary-source-only locators
        "permalink",  # vendor tier requirement
        "accessed",  # accessed-date requirement
        "no cross-sector",  # borrowing ban
        "sector × form",  # the D-ii keying
        "needs_fresh_research",  # the D-ii-a -> D-ii-b handoff flag
        "linkify_https",  # security: citation-URL render tripwire carried to D-iii
        "anecdotal",  # pinned D-ii-b anchor-table evidence-tier vocabulary
        "blended",  # meth M1: single-form-slice rule carried into the sweep
    ):
        assert token in doc, f"protocol doc missing {token!r}"


def test_source_corpus_doc_lists_the_six_forms() -> None:
    doc = Path("docs/reference/loss-form-anchors/source-corpus.md").read_text(encoding="utf-8")
    for form in (
        "productivity",
        "response",
        "replacement",
        "fines",
        "competitive_advantage",
        "reputation",
    ):
        assert form in doc, f"source-corpus doc missing form {form!r}"
