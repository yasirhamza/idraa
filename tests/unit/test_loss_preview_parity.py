"""Merge-path parity harness: pins `static/js/loss_preview.js`'s math core
against fair_cam (sigma-recal PR3 Task 1, D22).

``loss_preview.js`` is a first-party, labeled PREVIEW MIRROR of fair_cam's
lognormal/PERT math -- fair_cam remains the sole source of truth for FAIR
calculation (CLAUDE.md "Architectural rules"). This test is the tripwire
that keeps the two in lockstep: any drift in the JS formulas fails here,
in the merge path, before it reaches a browser.

Constants pinned in this file were generated and executed in-session, never
pasted from memory (CLAUDE.md "Numeric constants" rule):
  - Z95 == repr(fair_cam.quantile_pooling.Z_0_95) (asserted below).
  - Z99: `python -c "from scipy.stats import norm; print(repr(norm.ppf(0.99)))"`
    -> 2.3263478740408408 (executed 2026-07-30).
"""

import json
import math
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, NamedTuple

import pytest
from fair_cam.quantile_pooling import (
    Z_0_95,
    lognormal_from_quantiles,
    lognormal_mixture_to_pert_approx,
    lognormal_quantiles,
    truncated_lognormal_mean,
)
from fair_cam.quantile_pooling._types import LogNormalTruncFit, LognormMixture
from scipy.stats import beta as scipy_beta
from scipy.stats import norm

# __file__-anchored (Arch-N6): survives pytest invoked from any cwd.
JS = Path(__file__).resolve().parents[2] / "src/idraa/static/js/loss_preview.js"
SIGMAS = [0.4, 1.3567, 1.7, 2.2, 2.93, 3.47]

# Per-key relative tolerances (plan-gate B-M4/Arch-I5). A&S 7.1.26 bounds
# ABSOLUTE error at 1.5e-7; meanCapped divides Phi(b-sigma)/Phi(b), and at
# sigma=3.47/cap=300k, Phi(b-sigma) ~= 3.2e-4 -- the abs bound propagates to
# ~2e-4 RELATIVE. A flat 1e-5 fails a CORRECT implementation there (executed
# below: see test_golden_vector_parity docstring). Typo discrimination is the
# coefficient-pin tests' job, not this one -- this test checks STRUCTURAL
# parity at tolerances derived by propagating the published abs bound through
# each formula.
TOL = {
    "median": 1e-9,
    "mean": 1e-9,
    "p95": 1e-9,  # pure exp/log paths
    "meanCapped": 1e-3,  # deep-tail Phi ratio
    "p99Capped": 2e-5,  # worst exec'd 6.27e-6 -> 3.19x headroom (N-1)
    "pertLow": 1e-9,
    "pertMode": 1e-9,
    "pertHigh": 1e-9,
    "pertMean": 1e-9,  # (low+4*mode+high)/6
    "realizedMedianPos": 5e-3,  # analytic-front-cell grid: <=4.8e-4 exec'd
    #                             over the FULL sigma grid (round-4 M4-3)
}


def _require_node() -> None:
    # Arch-I2: in CI (GH Actions sets CI=true) a missing node must HARD-FAIL,
    # not skip -- a runner-image change must never silently degrade a
    # merge-path parity gate to a skip line. Loud-skip is local-only.
    if shutil.which("node") is None:
        if os.environ.get("CI"):
            pytest.fail("node missing on CI runner -- parity gate cannot run")
        pytest.skip("node missing locally -- parity still gates in CI")


def one_component_mix(mu: float, sigma: float) -> LognormMixture:
    """Single-SME lognormal mixture, pl/sl support (min_support=0,
    max_support=+inf) -- the exact shape ``combine_lognorm_trunc`` builds
    for the wizard's dominant single-SME production path
    (services/wizard_finalize.py fieldset_support: tef/pl/sl are
    [0, +inf)). Verified byte-identical to the untruncated closed form at
    these support bounds (executed 2026-07-30: `_qlnormtrunc` vs
    `exp(mu + sigma*norm.ppf(p))` agree to 0.0 relative error across the
    full SIGMAS grid at p in {0.05, 0.95, 0.99} -- the truncnorm lower
    bound sits ~700/sigma below the 5th percentile, far into float
    underflow territory, so truncation is a no-op at these bounds)."""
    return LognormMixture(
        components=(
            LogNormalTruncFit(meanlog=mu, sdlog=sigma, min_support=0.0, max_support=math.inf),
        ),
        weights=(1.0,),
    )


def vose_alpha_beta(low: float, mode: float, high: float) -> tuple[float, float]:
    """Replicate fair_core.py's PERT-branch Vose(gamma=4) alpha/beta formula
    IN-TEST (READ AT IMPL: fair_cam/risk_engine/fair_core.py lines ~203-209).
    Mirrors it EXACTLY -- the classic alpha+beta=6 form is explicitly
    banned there (fair_core's own comment records a ~0.5%/~2% divergence
    vs the equivalence-gated pyfair oracle)."""
    gamma = 4.0
    mean = (low + gamma * mode + high) / (gamma + 2.0)
    stdev = (high - low) / (gamma + 2.0)
    g1 = (mean - low) / (high - low)
    g2 = ((mean - low) * (high - mean)) / (stdev**2)
    alpha = g1 * (g2 - 1.0)
    beta = alpha * (high - mean) / (mean - low)
    return alpha, beta


def _run_node(harness_src: str, payload: object, tmp_path: Path, name: str) -> Any:
    # Return type is Any (not object): callers index/iterate the parsed
    # JSON immediately, and json.loads itself is untyped upstream (Any) --
    # forcing `object` here would just relocate the same casts into every
    # caller for no safety gain.
    payload_path = tmp_path / f"{name}.json"
    payload_path.write_text(json.dumps(payload))
    harness_path = tmp_path / f"{name}.js"
    harness_path.write_text(harness_src)
    # argv: [execPath, script, payload] -- harness reads process.argv[2]
    # (plan-gate Arch-I1 caught an off-by-one placeholder here).
    res = subprocess.run(
        ["node", str(harness_path), str(payload_path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def test_z95_literal_matches_fair_cam() -> None:
    assert f"{Z_0_95!r}" in JS.read_text()


def test_as7126_coefficients_present_and_formula_accurate() -> None:
    # Coefficients from Abramowitz & Stegun 7.1.26 (Hastings 1955).
    src = JS.read_text()
    for lit in (
        "0.254829592",
        "-0.284496736",
        "1.421413741",
        "-1.453152027",
        "1.061405429",
        "0.3275911",
    ):
        assert lit in src, lit

    # Re-implement in Python; max abs err vs math.erf <= 1.6e-7 on a grid.
    def as_erf(x: float) -> float:
        s = 1.0 if x >= 0 else -1.0
        x = abs(x)
        t = 1.0 / (1.0 + 0.3275911 * x)
        y = 1.0 - (
            ((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t
            + 0.254829592
        ) * t * math.exp(-x * x)
        return s * y

    for i in range(-400, 401):
        x = i / 100.0
        # 1.6e-7 = published 1.5e-7 bound + float-eval slack; executed max
        # on this grid is 1.388e-7 (methodology lane), comfortably inside.
        assert abs(as_erf(x) - math.erf(x)) < 1.6e-7


def test_acklam_coefficients_present_and_formula_accurate() -> None:
    # Symmetric pin for the OTHER approximation (SC-3): grep the JS for the
    # Acklam coefficient literals (Peter Acklam's rational approximation to
    # the inverse standard normal CDF, primary source:
    # https://web.archive.org/web/20151030215612/http://home.online.no/~pjacklam/notes/invnorm/
    # -- the algorithm's own README states the published max relative error
    # is 1.15e-9), re-implement it in Python in-test, and assert relative
    # error vs scipy.stats.norm.ppf <= 1.2e-9 (published bound + slack) over
    # p in {1e-6 ... 1-1e-6} including the region-switch boundaries
    # p_low=0.02425 / 1-p_low. Executed 2026-07-30: max observed relative
    # error 1.1288438475563716e-09 at p=0.7077123263027906 -- inside 1.2e-9.
    src = JS.read_text()
    coefficients = (
        "-3.969683028665376e+01",
        "2.209460984245205e+02",
        "-2.759285104469687e+02",
        "1.383577518672690e+02",
        "-3.066479806614716e+01",
        "2.506628277459239e+00",
        "-5.447609879822406e+01",
        "1.615858368580409e+02",
        "-1.556989798598866e+02",
        "6.680131188771972e+01",
        "-1.328068155288572e+01",
        "-7.784894002430293e-03",
        "-3.223964580411365e-01",
        "-2.400758277161838e+00",
        "-2.549732539343734e+00",
        "4.374664141464968e+00",
        "2.938163982698783e+00",
        "7.784695709041462e-03",
        "3.224671290700398e-01",
        "2.445134137142996e+00",
        "3.754408661907416e+00",
    )
    for lit in coefficients:
        assert lit in src, lit

    a1, a2, a3, a4, a5, a6 = (
        -3.969683028665376e01,
        2.209460984245205e02,
        -2.759285104469687e02,
        1.383577518672690e02,
        -3.066479806614716e01,
        2.506628277459239e00,
    )
    b1, b2, b3, b4, b5 = (
        -5.447609879822406e01,
        1.615858368580409e02,
        -1.556989798598866e02,
        6.680131188771972e01,
        -1.328068155288572e01,
    )
    c1, c2, c3, c4, c5, c6 = (
        -7.784894002430293e-03,
        -3.223964580411365e-01,
        -2.400758277161838e00,
        -2.549732539343734e00,
        4.374664141464968e00,
        2.938163982698783e00,
    )
    d1, d2, d3, d4 = (
        7.784695709041462e-03,
        3.224671290700398e-01,
        2.445134137142996e00,
        3.754408661907416e00,
    )
    p_low = 0.02425
    p_high = 1 - p_low

    def acklam(p: float) -> float:
        if p < p_low:
            q = math.sqrt(-2 * math.log(p))
            return (((((c1 * q + c2) * q + c3) * q + c4) * q + c5) * q + c6) / (
                (((d1 * q + d2) * q + d3) * q + d4) * q + 1
            )
        if p <= p_high:
            q = p - 0.5
            r = q * q
            return (
                (((((a1 * r + a2) * r + a3) * r + a4) * r + a5) * r + a6)
                * q
                / (((((b1 * r + b2) * r + b3) * r + b4) * r + b5) * r + 1)
            )
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c1 * q + c2) * q + c3) * q + c4) * q + c5) * q + c6) / (
            (((d1 * q + d2) * q + d3) * q + d4) * q + 1
        )

    ps = [1e-6]
    p = 1e-6
    while p < 1.0:
        p *= 1.0011
        ps.append(p)
    for boundary in (
        p_low - 1e-9,
        p_low,
        p_low + 1e-9,
        p_high - 1e-9,
        p_high,
        p_high + 1e-9,
        1 - 1e-6,
    ):
        ps.append(boundary)

    max_rel = 0.0
    for p in ps:
        if not (0.0 < p < 1.0):
            continue
        got = acklam(p)
        want = float(norm.ppf(p))
        if want == 0.0:
            continue
        rel = abs(got - want) / abs(want)
        max_rel = max(max_rel, rel)
    assert max_rel < 1.2e-9, max_rel


class FitCase(NamedTuple):
    kind: str
    a: float
    b: float


def test_fit_functions_parity(tmp_path: Path) -> None:
    _require_node()
    # Two (p50,p95) and two (p5,p95) pairs; expected via
    # lognormal_from_quantiles(q_low=0.5|0.05); a tiny second harness calls
    # fitP50P95/fitP5P95 and prints {mu, sigma}; assert rel 1e-12. Same
    # subprocess shape as the golden harness.
    cases: list[FitCase] = [
        FitCase("p50p95", 50_000.0, 200_000.0),
        FitCase("p50p95", 120_000.0, 5_000_000.0),
        FitCase("p5p95", 20_000.0, 200_000.0),
        FitCase("p5p95", 5_000.0, 10_000_000.0),
    ]
    expected: list[dict[str, float]] = []
    for kind, a, b in cases:
        if kind == "p50p95":
            exp = lognormal_from_quantiles(a, b, q_low=0.5, q_high=0.95)
        else:
            exp = lognormal_from_quantiles(a, b, q_low=0.05, q_high=0.95)
        expected.append(exp)

    harness = (
        "const fs = require('node:fs');\n"
        # eval() here loads our OWN first-party loss_preview.js (a fixed,
        # repo-local path, never attacker/user input) into this throwaway
        # node subprocess so its `globalThis.lossPreviewMath` becomes
        # callable -- the module has no bundler/export step, so this is
        # the parity harness's load mechanism, not data processing.
        f"eval(fs.readFileSync({str(JS)!r}, 'utf8'));\n"
        "const cases = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));\n"
        "const out = cases.map(c => {\n"
        "  const fn = c.kind === 'p50p95'\n"
        "    ? globalThis.lossPreviewMath.fitP50P95\n"
        "    : globalThis.lossPreviewMath.fitP5P95;\n"
        "  return fn(c.a, c.b);\n"
        "});\n"
        "process.stdout.write(JSON.stringify(out));\n"
    )
    payload = [{"kind": kind, "a": a, "b": b} for kind, a, b in cases]
    got = _run_node(harness, payload, tmp_path, "fit_cases")
    for case, exp, actual in zip(cases, expected, got, strict=True):
        assert actual["mu"] == pytest.approx(exp["mean"], rel=1e-12), case
        assert actual["sigma"] == pytest.approx(exp["sigma"], rel=1e-12), case


# pertDensityPath/cdfGrid front-cell rule (round-3 B3-3, figures corrected
# round-4 M4-1 -- SUPERSEDES the round-2 trapezoid clip): whenever the Vose
# alpha < 1 (keyed on ALPHA, not mode==low -- sigma=1.3567 has an INTERIOR
# mode and alpha=0.6967) the Beta pdf has a u^(alpha-1) singularity at
# x=low. The naive clip UNDER-counts front-cell mass, biasing the realized
# median position HIGH. The first cell's CDF mass uses the closed form
# u1^alpha / (alpha * B(alpha, beta)) (the (1-u)^(beta-1) factor is dropped
# -- immaterial at u1 ~ 1e-4 but not "exact"; say approximately), trapezoid
# thereafter, CDF normalized to end at exactly 1. Defensive sibling: a
# beta < 1 singularity at u=1 is unreachable from capPertFromFit (executed
# beta 3.33-3.39 across the SIGMAS grid) but cdfGrid is public -- the
# implementation mirrors the closed-form last cell when beta < 1.
class GoldenCase(NamedTuple):
    mu: float
    sigma: float
    cap: float | None
    expected: dict[str, float]


def test_golden_vector_parity(tmp_path: Path) -> None:
    _require_node()
    cases: list[GoldenCase] = []
    for sigma in SIGMAS:
        mu = math.log(250_000.0)
        # mu = ln(250k): 4e9 non-binding / 300k binding-hard / 200k DEGENERATE
        # (cap < median -> capClamped state; Arch-N8 -- the jj/kk mirror must
        # have parity coverage, and 300k is NOT degenerate, it is merely hard).
        for cap in (None, 4e9, 300_000.0, 200_000.0):
            exp: dict[str, float] = {
                "median": math.exp(mu),
                "mean": math.exp(mu + sigma * sigma / 2),
                "p95": float(lognormal_quantiles(mu, sigma, (0.95,))[0]),
            }
            if cap is not None:
                exp["meanCapped"] = truncated_lognormal_mean(mu, sigma, cap)
                b = (math.log(cap) - mu) / sigma
                exp["p99Capped"] = math.exp(mu + sigma * float(norm.ppf(0.99 * float(norm.cdf(b)))))
            # Golden through the LIVE collapser on a one-component mixture
            # (wizard single-SME capped case).
            pert, _clamp = lognormal_mixture_to_pert_approx(one_component_mix(mu, sigma))
            exp["pertLow"], exp["pertMode"], exp["pertHigh"] = pert.low, pert.mode, pert.high
            exp["pertMean"] = (pert.low + 4 * pert.mode + pert.high) / 6
            a, b_ = vose_alpha_beta(pert.low, pert.mode, pert.high)
            realized_median_pos = float(scipy_beta.ppf(0.5, a, b_))
            exp["realizedMedianPos"] = realized_median_pos
            # Cross-check the mode==low case against the reference doc's
            # DOC value 0.11182 at 5 s.f. only -- scipy on this tree returns
            # ...305, one ULP off a value computed elsewhere (round-4 M4-4);
            # never pin the full double, never use ==.
            if abs(pert.mode - pert.low) < 1e-9:
                assert round(realized_median_pos, 5) == 0.11182
            cases.append(GoldenCase(mu=mu, sigma=sigma, cap=cap, expected=exp))

    harness = (
        "const fs = require('node:fs');\n"
        # See test_fit_functions_parity's comment above: eval() here loads
        # only our own first-party loss_preview.js from a fixed repo-local
        # path, never external input.
        f"eval(fs.readFileSync({str(JS)!r}, 'utf8'));\n"
        "const cases = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));\n"
        "const out = cases.map(c => {\n"
        "  const s = globalThis.lossPreviewMath.lognormalStats({mu: c.mu, sigma: c.sigma, cap: c.cap});\n"
        "  const p = globalThis.lossPreviewMath.capPertFromFit(c.mu, c.sigma);\n"
        "  const ps = globalThis.lossPreviewMath.pertStats(p);\n"
        "  const grid = globalThis.lossPreviewMath.cdfGrid(p);\n"
        "  return {...s, pertLow: p.low, pertMode: p.mode, pertHigh: p.high,\n"
        "          pertMean: ps.mean,\n"
        "          realizedMedianPos: globalThis.lossPreviewMath.medianPosFromGrid(grid, p)};\n"
        "});\n"
        "process.stdout.write(JSON.stringify(out));\n"
    )
    payload = [{"mu": c.mu, "sigma": c.sigma, "cap": c.cap} for c in cases]
    got = _run_node(harness, payload, tmp_path, "golden")
    for case, actual in zip(cases, got, strict=True):
        for key, want in case.expected.items():
            assert actual[key] == pytest.approx(want, rel=TOL[key]), (case.sigma, case.cap, key)
        if case.cap is not None and case.cap < math.exp(case.mu):
            assert actual["capClamped"] is True  # jj/kk-mirror degenerate state
