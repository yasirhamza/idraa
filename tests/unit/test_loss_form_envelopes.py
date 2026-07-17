"""Guard for data/loss_form_envelopes.json (Epic D-ii-b, post-Amendment-A1).

The per-sector IRIS loss ENVELOPE table — E_sector ~ LN(mean, sigma) — that
D-iii scales by an archetype's Sigma(form-shares) (design Amendment A1). Reuses
Epic C's adversarially-verified IRIS Figure A3 (p50, p95) reads; NOT app-runtime
seed data.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from fair_cam.quantile_pooling import lognormal_from_quantiles

_CORE_SECTORS = {
    "manufacturing",
    "energy_utilities",
    "healthcare",
    "financial_services",
    "retail_ecommerce",
    "technology_saas",
    "government_public",
    "education",
    "professional_services",
    "transportation_logistics",
    "telecom",
    "hospitality",
    "food_agriculture",
}


def _rows() -> list[dict]:
    return json.loads(Path("data/loss_form_envelopes.json").read_text(encoding="utf-8"))


def test_all_13_sectors_present_once() -> None:
    secs = [r["sector"] for r in _rows()]
    assert set(secs) == _CORE_SECTORS, f"sector set mismatch: {set(secs) ^ _CORE_SECTORS}"
    assert len(secs) == len(set(secs)) == 13, "each sector exactly once"


def test_envelope_shape_and_bounds() -> None:
    for r in _rows():
        assert r["p95"] > r["p50"] > 0, f"{r['sector']}: need p95 > p50 > 0"
        assert math.isfinite(r["mean"]) and 0 < r["sigma"] <= 10, (
            f"{r['sector']}: bad lognormal params"
        )
        assert r["citations"], f"{r['sector']}: envelope needs a citation"


def test_lognormal_fit_reproducible_from_quantiles() -> None:
    # every envelope's stored (mean, sigma) must be the exact lognormal fit of its
    # (p50, p95) at q=(0.50, 0.95) — no hand-typed drift (D-i machinery, verified).
    for r in _rows():
        fit = lognormal_from_quantiles(r["p50"], r["p95"], q_low=0.50, q_high=0.95)
        assert r["mean"] == round(fit["mean"], 10), f"{r['sector']}: mean != fit"
        assert r["sigma"] == round(fit["sigma"], 10), f"{r['sector']}: sigma != fit"


def test_fallback_sectors_document_their_reason() -> None:
    # food_agriculture has no usable IRIS row (Agriculture p50/p95 is a
    # near-point-mass artifact + wrong NAICS family) -> documented fallback.
    for r in _rows():
        if r.get("is_fallback"):
            assert r.get("fallback_reason"), f"{r['sector']}: fallback must state its reason"
            assert r.get("fallback_of"), f"{r['sector']}: fallback must name its source sector"
