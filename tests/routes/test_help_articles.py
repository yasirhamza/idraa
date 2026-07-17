"""Content-pinning tests for Help article bodies (design 2026-06-13)."""

from __future__ import annotations

import pytest


async def _body(client, slug: str) -> str:
    r = await client.get(f"/help/{slug}", headers={"HX-Request": "true"})
    assert r.status_code == 200
    return r.text


@pytest.mark.asyncio
async def test_getting_started_covers_purpose_roles_flow_caveats(authed_analyst):
    client, _ = authed_analyst
    body = await _body(client, "getting-started")
    assert "risk in money" in body.lower() or "in money" in body.lower()
    for role in ["analyst", "reviewer", "admin"]:
        assert role in body.lower()
    # scenario -> analysis -> report flow named
    assert "scenario" in body.lower() and "report" in body.lower()
    # migrated Caveats framing (decision-support, not a crystal ball)
    assert "decision-support" in body.lower() or "not a crystal ball" in body.lower()


@pytest.mark.asyncio
async def test_build_a_scenario_inherent_vuln_and_event_conditional_loss(authed_analyst):
    client, _ = authed_analyst
    body = await _body(client, "build-a-scenario")
    # Meth-B2: inherent vuln framing (migrated verbatim phrase) + must NOT regress
    assert "before counting your controls" in body
    assert "inherent" in body.lower()
    assert "chance an attempt succeeds once it happens" not in body
    # Meth-B1: PL/SL framed per-event, not annual
    assert "per event" in body.lower() or "per loss event" in body.lower()
    # wizard nodes named
    for term in ["Threat Event Frequency", "Vulnerability", "Primary Loss", "Secondary Loss"]:
        assert term in body
    # migrated worked example (manufacturer)
    assert "anufactur" in body  # "manufacturer"/"Manufacturing"
    # Meth-I4: calibration baselines are IRIS medians, NOT averages (#131 guard).
    assert "median" in body.lower()
    assert "average" not in body.lower()
    # Meth-N1: SME estimates are POOLED, not averaged.
    assert "pool" in body.lower()
    # Methodology: ALE is the MEAN/expectation, never the "median" (right-skewed
    # loss → mean > median). Guard the #131-class mean/median conflation.
    assert "expected (median)" not in body.lower()
    assert "median) yearly loss" not in body.lower()


@pytest.mark.asyncio
async def test_run_and_read_var_es_and_outputs(authed_analyst):
    client, _ = authed_analyst
    body = await _body(client, "run-and-read-analyses")
    assert "Monte Carlo" in body
    # Meth-I2: ES is the conditional tail mean, NOT "worst case"
    assert "expected shortfall" in body.lower()
    assert "at or above" in body.lower()  # conditional-tail-mean phrasing
    assert "Value at Risk" in body
    assert "Loss Exceedance" in body
    # migrated outputs table audiences
    assert "Annualized Loss Expectancy" in body
    # p99.9 reliability caveat at the default iteration count
    assert "99.9" in body or "deepest" in body.lower() or "reliab" in body.lower()
    # Meth-I2 (author note, not a blanket assert): the ES *definition* must not
    # say "worst case" (it's the conditional tail mean). A blanket
    # `"worst case" not in body` is intentionally NOT used because the migrated
    # outputs table legitimately calls the VaR/LEC region the "worst-case tail".
    # The positive `"at or above"` assertion above pins ES correctness; the
    # methodology reviewer checks the ES sentence at the PR-gate.
    # Meth-I1: no portfolio-finance overclaim in aggregate prose
    for term in ["diversif", "correlation-adjusted", "solvency", "capital adequacy"]:
        assert term not in body.lower(), f"portfolio-finance overclaim: {term}"


@pytest.mark.asyncio
async def test_methodology_primer_glossary_and_nodes(authed_analyst):
    client, _ = authed_analyst
    body = await _body(client, "methodology-primer")
    # migrated full acronym expansions
    for full_term in [
        "Factor Analysis of Information Risk",
        "Annualized Loss Expectancy",
        "Threat Event Frequency",
        "Loss Exceedance",
        "Value at Risk",
        "Return on Investment",
        "Monte Carlo",
        "Net Present Value",
    ]:
        assert full_term in body, f"missing expansion: {full_term}"
    # migrated glossary inherent-vuln entry
    assert "before your controls" in body
    # lognormal authoring stated as p5/p95 (Meth-I3)
    assert "5th" in body and "95th" in body


@pytest.mark.asyncio
async def test_libraries_covers_suite(authed_analyst):
    client, _ = authed_analyst
    body = (await _body(client, "libraries")).lower()
    for term in ["scenario library", "control library", "crosswalk", "recommend", "adopt"]:
        assert term in body


@pytest.mark.asyncio
async def test_libraries_cross_industry_guidance(authed_analyst):
    """Cross-industry adopt-and-tweak section: revenue-tier rescale, industry advisory."""
    client, _ = authed_analyst
    body = (await _body(client, "libraries")).lower()
    # Browsable across all industries
    assert "any industry" in body or "all industries" in body
    # Rescale is revenue-tier only, not industry
    assert "revenue tier" in body
    # Entry industry is advisory only
    assert "advisory" in body
    # Calibration banner mentioned
    assert "calibration banner" in body
    # Must NOT claim industry drives the rescale
    assert "re-calibrated for your industry" not in body


@pytest.mark.asyncio
async def test_import_export_covers_csv_json_roundtrip(authed_analyst):
    client, _ = authed_analyst
    body = (await _body(client, "import-export")).lower()
    assert "csv" in body and "json" in body
    assert "import" in body and "export" in body
    assert "round-trip" in body or "round trip" in body


@pytest.mark.asyncio
async def test_reports_covers_pdf_and_attribution(authed_analyst):
    client, _ = authed_analyst
    body = (await _body(client, "reports")).lower()
    assert "pdf" in body
    assert "attribution" in body or "shapley" in body
    # Meth-I1: attribution labeled as a view-model derivation, not FAIR-grounded overclaim
    assert "not fair-grounded" in body or "view-model" in body or "reporting derivation" in body


@pytest.mark.asyncio
async def test_controls_overlays_covers_both(authed_analyst):
    client, _ = authed_analyst
    body = (await _body(client, "controls-overlays")).lower()
    assert "control" in body and "overlay" in body
    assert "reduce" in body  # controls reduce modeled risk


@pytest.mark.asyncio
async def test_run_and_read_covers_seed(authed_analyst):
    client, _ = authed_analyst
    body = (await _body(client, "run-and-read-analyses")).lower()
    assert "seed" in body
    assert "reproducible" in body
    assert "vary" in body  # vary the seed for sampling variability
