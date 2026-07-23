"""Structural presence check for docs/reference/loss-magnitude-forms.md.

Guards that the Epic D-i loss-form model doc contains every load-bearing token
defined in the design spec §1. Doc-only test — no math, no src touch.
"""

from __future__ import annotations

from pathlib import Path


def test_loss_forms_doc_defines_model() -> None:
    doc = Path("docs/reference/loss-magnitude-forms.md").read_text(encoding="utf-8")
    for token in (
        # the six FAIR forms of loss
        "productivity",
        "response",
        "replacement",
        "fines",
        "competitive advantage",
        "reputation",
        # primary/secondary split
        "Primary Loss",
        "Secondary Loss",
        # composition method
        "Fenton",
        "moment",
        "independence",
        # primary/secondary is the stakeholder test, not a fixed partition
        "stakeholder",
        # the six-forms taxonomy must be primary-cited to its FAIR source
        "Freund",
        # rules
        "composed-envelope-only",
        "No cross-sector",
        "materiality",
        # engine boundary
        "authoring-time",
        "fair_core.py:511",
    ):
        assert token in doc, f"loss-form model doc missing {token!r}"
