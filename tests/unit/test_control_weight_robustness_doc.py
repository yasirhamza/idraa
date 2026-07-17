"""Structural presence check for docs/reference/control-weight-robustness.md.

Guards that the methodology doc (issue #419) contains every load-bearing token
and that cross-referencing docs mention it.
"""

from __future__ import annotations

from pathlib import Path

DOC = Path("docs/reference/control-weight-robustness.md")


def test_doc_states_key_claims() -> None:
    """Assert all load-bearing methodology claims are present in the doc."""
    t = DOC.read_text(encoding="utf-8")
    for tok in (
        "#419",
        "logit-normal",
        "perturbation kernel",
        "not a non-informative",
        "1 - E",
        "co-vary",
        "rank-stability",
        "not validated",
        # Slice 2 (#439): meta.kappa replaced vmc.vuln as the fifth canonical
        # parameter; the live sampler is sample_ensemble_draw returning the
        # (node_mapping, kappa) EnsembleDraw (sample_node_mapping deleted).
        "meta.kappa",
        "sample_ensemble_draw",
        # vmc.vuln stays required: the doc keeps a "Retired parameters" note so
        # the #439 retirement is discoverable from the doc itself.
        "vmc.vuln",
        "RETIRED by #439",
        "representative-value",
        "exp",
        "SINGLE",
    ):
        assert tok in t, f"control-weight-robustness doc missing {tok!r}"


def test_sibling_docs_cross_reference_weight_robustness_doc() -> None:
    """Product-form tail and vulnerability-semantics docs must mention this doc."""
    for sibling in (
        "docs/reference/product-form-tail-approximation.md",
        "docs/reference/vulnerability-semantics.md",
    ):
        assert "control-weight-robustness.md" in Path(sibling).read_text(encoding="utf-8"), (
            f"{sibling} does not cross-reference control-weight-robustness.md"
        )
