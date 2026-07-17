"""Structural presence check for docs/reference/loss-form-share-rubric.md (Epic D-iii-a).

Guards that the form-share rubric carries its load-bearing pieces: the envelope×share
principles, the Σ≤1 coherence bound, the threat-type default profiles, the flagship
per-entry adjustments (recon-low, BEC/IP beyond-envelope), and the audit-floor +
family-pinning discipline. Doc-only.
"""

from __future__ import annotations

from pathlib import Path


def test_share_rubric_carries_all_load_bearing_pieces() -> None:
    doc = Path("docs/reference/loss-form-share-rubric.md").read_text(encoding="utf-8")
    for token in (
        # model + anchor (A1)
        "loss_form_envelopes.json",
        "share = 1.0",
        "Amendment A1",
        # the coherence bound
        "Σ(all active shares, primary + secondary) ≤ 1",
        # threat-type defaults present
        "ransomware",
        "denial_of_service",
        "ot_availability",
        "data_disclosure",
        # flagship differentiation + beyond-envelope
        "Reconnaissance",
        "BEYOND-ENVELOPE",
        "BEC / wire fraud",
        "compose_forms_to_lognormal",
        # discipline
        "Family pinning",
        "vulnerability-grade",
        # disclosed A1 biases carried forward
        "location-not-shape",
    ):
        assert token in doc, f"share rubric missing {token!r}"
