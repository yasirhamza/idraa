"""Structural presence check for docs/reference/tef-vuln-detemplating.md (Epic D §3, D-ii-b).

Guards that the TEF/vulnerability de-templating reference carries its load-bearing
tokens: the available threat-intel signals, the rubric, and the standing invariants
(TEF stays PERT; vulnerability stays the analyst-judged inherent baseline; neither
is loss magnitude). Doc-only — no math, no src touch.
"""

from __future__ import annotations

from pathlib import Path


def test_detemplating_doc_carries_signals_and_rubric() -> None:
    doc = Path("docs/reference/tef-vuln-detemplating.md").read_text(encoding="utf-8")
    for token in (
        # the four in-repo directional signals
        "IC3 2025",
        "DBIR 2024",
        "ATT&CK crosswalk",
        "CISA sector advisories",
        # TEF invariants
        "stays PERT",
        "de-templat",
        # vulnerability invariants (§1b)
        "inherent (control-naive) industry-baseline",
        "vulnerability-semantics.md",
        "fair_core.py:267",
        # the honesty framing (parity with Amendment A1)
        "not cleanly sourceable",
        "Amendment A1",
        # boundary: TEF/vuln are NOT loss magnitude / envelope
        "loss_form_envelopes.json",
    ):
        assert token in doc, f"de-templating doc missing {token!r}"
