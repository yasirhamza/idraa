"""Structural presence check for docs/reference/product-form-tail-approximation.md.

Guards that the limitation doc (issue #412) contains every load-bearing token
and that its sibling reference docs cross-reference it.
"""

from __future__ import annotations

from pathlib import Path


def test_product_form_tail_doc_states_the_limitation() -> None:
    doc = Path("docs/reference/product-form-tail-approximation.md").read_text(encoding="utf-8")
    for token in (
        "#412",
        "NOT A BUG",
        "risk = lef * loss_magnitude",
        "fair_core.py",
        "Wald",
        "mean is exact",
        "_build_tail_metrics",
        "_build_loss_percentile_band",
        "_build_loss_exceedance_curve",
        "39.2%",
        "Future work",
        "Poisson",
    ):
        assert token in doc, f"product-form-tail doc missing {token!r}"


def test_sibling_docs_cross_reference_product_form_tail_doc() -> None:
    for sibling in (
        "docs/reference/vulnerability-semantics.md",
        "docs/reference/fair-cam-methodology.md",
    ):
        assert "product-form-tail-approximation.md" in Path(sibling).read_text(encoding="utf-8"), (
            f"{sibling} does not cross-reference the product-form-tail doc"
        )
