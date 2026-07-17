"""Structural presence check for docs/reference/loss-magnitude-tiering.md.

Guards that the framework doc contains every load-bearing token defined in
spec §1 (Epic C #335 C-i).  This is a doc-only test — no math, no src touch.
"""

from __future__ import annotations

from pathlib import Path


def test_loss_tiering_doc_defines_all_tiers() -> None:
    """The framework doc must contain every load-bearing spec §1 token."""
    doc = Path("docs/reference/loss-magnitude-tiering.md").read_text(encoding="utf-8")
    for token in (
        "TIER-1",
        "TIER-2",
        "TIER-3",
        "paginated",
        "vendor",
        "anecdotal",
        "sqrt(2",
        "mean/median",
        "No cross-sector",
    ):
        assert token in doc, f"framework doc missing {token!r}"
