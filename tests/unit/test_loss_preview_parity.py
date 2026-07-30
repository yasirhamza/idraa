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
    "p99": 1e-9,  # pure exp/log path (T1.a B-I4b: previously zero coverage)
    "meanCapped": 1e-3,  # deep-tail Phi ratio. NOTE (T1.a NTH): this bound is
    #   GRID-TUNED to the (sigma, cap) pairs actually exercised below — not a
    #   universal accuracy claim. At an out-of-grid "keystroke" value
    #   sigma=6.3 (mu=ln(250_000), cap=300_000), executed 2026-07-30 against
    #   this task's OWN fix (unaffected by the guards added here, since
    #   Phi(b-sigma) is nonzero there): expected (fair_cam
    #   truncated_lognormal_mean) = 36409.05126011712, actual (JS) =
    #   36565.01418537023, relative error = 4.284e-3 (0.4284%) -- i.e. a
    #   CORRECT implementation already exceeds this 1e-3 TOL at sigma values
    #   between the pinned grid points, purely from the A&S 7.1.26 erf
    #   approximation's fixed absolute error propagating through the
    #   Phi(b-sigma)/Phi(b) ratio. (A predecessor review pass cited 0.344% at
    #   this same sigma=6.3 keystroke case; this task could not reproduce
    #   that exact figure across several nearby cap values -- see the T1.a
    #   report -- so this comment quotes only the figure re-executed here.)
    "p99Capped": 2e-5,  # worst exec'd 6.27e-6 -> 3.19x headroom (N-1)
    "capBindProb": 1e-2,  # T1.a NTH: worst exec'd 6.893e-3 at sigma=1.3567,
    #   cap=4e9 (non-binding cap -> capBindProb = 1 - Phi(b) ~ 4.87e-13
    #   (re-gate B3: it is the COMPLEMENT that is tiny; Phi(b) ~ 1), same
    #   fixed-abs-error-
    #   through-a-tiny-ratio mechanism as meanCapped/p99Capped above).
    "pertLow": 1e-9,
    "pertMode": 1e-9,
    "pertHigh": 1e-9,
    "pertMean": 1e-9,  # (low+4*mode+high)/6
    "impliedSigma": 1e-9,  # T1.a NTH: sigma round-trip through pertStats;
    #   executed max relative error 2.6e-16 (machine precision) over SIGMAS.
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


# NaN/Infinity-preserving JSON replacer for node harnesses (T1.a). Plain
# `JSON.stringify` silently maps both NaN and +-Infinity to `null` (verified
# via `node -e 'console.log(JSON.stringify({a: NaN, b: null}))'` ->
# `{"a":null,"b":null}`, executed 2026-07-30) -- which would make an
# UNGUARDED NaN indistinguishable, over the wire, from a properly-guarded
# `null` return. That ambiguity is exactly the gap that would let the B-I1
# regression this file guards against pass silently. Harnesses that need to
# tell "guarded null" apart from "still-NaN bug" embed this replacer.
_NAN_SAFE_REPLACER_JS = (
    "function replacer(k, v) {\n"
    "  if (typeof v === 'number') {\n"
    "    if (Number.isNaN(v)) return '__NaN__';\n"
    "    if (v === Infinity) return '__Infinity__';\n"
    "    if (v === -Infinity) return '__-Infinity__';\n"
    "  }\n"
    "  return v;\n"
    "}\n"
)

_SENTINELS = {"__NaN__": math.nan, "__Infinity__": math.inf, "__-Infinity__": -math.inf}


def _denanify(value: Any) -> Any:
    """Reverse `_NAN_SAFE_REPLACER_JS`'s sentinel strings back to Python
    math.nan/inf so callers can compare numerically."""
    return _SENTINELS.get(value, value) if isinstance(value, str) else value


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


def test_z99_literal_matches_scipy() -> None:
    """B-I4a (methodology gate finding, PR3 T1.a): Z99 previously had zero
    coverage (only Z95 was grep-pinned). Executed 2026-07-30:
      python -c "from scipy.stats import norm; print(repr(float(norm.ppf(0.99))))"
      -> 2.3263478740408408
    Unlike Z95 (which differs from fair_cam.quantile_pooling.Z_0_95 by 1 ULP
    vs a fresh scipy call -- N-M2, see test_z95_literal_matches_fair_cam
    above), Z99 has no fair_cam constant to anchor to (it is used only
    inside this preview mirror's p99/p99Capped fields), so this test
    compares directly against a fresh scipy computation rather than a
    fair_cam re-export. Executed here: repr(float(norm.ppf(0.99))) equals
    the JS literal exactly (no 1-ULP divergence in this case)."""
    z99 = float(norm.ppf(0.99))
    assert repr(z99) in JS.read_text()


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
    # the inverse standard normal CDF, primary source (Wayback capture
    # accessed 2015-10-30, per the archive timestamp in the URL itself):
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


def test_fit_lognormal_ordering_guard(tmp_path: Path) -> None:
    """B-I3 (methodology gate finding, PR3 T1.a): fitLognormal (and its
    fitP50P95/fitP5P95 presets) must reject qHi<=qLo -- as well as
    non-finite/non-positive inputs, already covered pre-fix -- rather than
    silently returning a negative or zero sigma.

    fair_cam.lognormal_from_quantiles RAISES on an inverted pair (executed
    2026-07-30 below); the JS preview mirror has no exception channel, so
    {mu: null, sigma: null} is the equivalent "invalid input" signal.

    Pre-fix repro (executed 2026-07-30 against the file BEFORE this task's
    fix): fitP5P95(200_000, 100_000) returned
    {mu: 11.8594990552502, sigma: -0.2107017819708997} -- a NEGATIVE sigma
    silently rendered as if it were a valid fit. An equal pair
    (fitP5P95(150_000, 150_000)) returned sigma: 0 (degenerate, but not
    null either) -- also rejected here per the guard's stated contract
    (qHi<=qLo, inclusive of equality)."""
    _require_node()

    # Side-by-side: fair_cam's own behavior on the same inverted pair.
    with pytest.raises(ValueError, match="high must be >= low"):
        lognormal_from_quantiles(200_000.0, 100_000.0, q_low=0.05, q_high=0.95)
    # fair_cam does NOT raise on an equal pair (returns sigma=0 -- a
    # technically-valid degenerate point-mass fit); the JS guard is
    # deliberately MORE conservative here (see the fitLognormal docstring
    # in loss_preview.js), since every downstream sigma<=0 guard in this
    # module already treats sigma:0 as unusable, so leaking it un-nulled
    # from fitLognormal itself would just move the failure mode elsewhere.
    equal_result = lognormal_from_quantiles(150_000.0, 150_000.0, q_low=0.05, q_high=0.95)
    assert equal_result["sigma"] == 0.0

    harness = (
        "const fs = require('node:fs');\n"
        f"eval(fs.readFileSync({str(JS)!r}, 'utf8'));\n"
        "const M = globalThis.lossPreviewMath;\n"
        "const Z95 = 1.6448536269514722;\n"
        "const out = {\n"
        "  inverted: M.fitP5P95(200000, 100000),\n"
        "  equal: M.fitP5P95(150000, 150000),\n"
        "  zeroQLo: M.fitLognormal(0, 100, -Z95, Z95),\n"
        "  negQLo: M.fitLognormal(-5, 100, -Z95, Z95),\n"
        "  negQHi: M.fitP50P95(50000, -1),\n"
        "  zeroQHi: M.fitP50P95(50000, 0),\n"
        "};\n"
        "process.stdout.write(JSON.stringify(out));\n"
    )
    harness_path = tmp_path / "fit_ordering_guard.js"
    harness_path.write_text(harness)
    res = subprocess.run(["node", str(harness_path)], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    got = json.loads(res.stdout)
    for name, result in got.items():
        assert result == {"mu": None, "sigma": None}, (name, result)


def test_mean_capped_never_nan_on_overflow_underflow_product(tmp_path: Path) -> None:
    """B-I1 (methodology gate finding, PR3 T1.a): meanCapped must never
    surface as NaN. `mean` (=exp(mu+sigma^2/2)) can overflow to Infinity
    while normCdf(b-sigma) simultaneously underflows to 0 -- IEEE754
    Infinity*0 = NaN.

    Pre-fix repro (executed 2026-07-30 against the file BEFORE this task's
    fix, via bare `node -e ...console.log(...)` -- plain console.log, not
    JSON, so the NaN was directly visible and not silently coerced):
      case A: fitP5P95(1, 1e60) -> sigma computed LIVE below (not
              hand-pinned) as ~41.996; cap = 0.5*median forces
              capClamped=true. Pre-fix meanCapped: NaN.
      case B: mu=ln(250_000), sigma=38, cap=300_000 (capClamped=false, a
              "hard but not degenerate" cap). Pre-fix meanCapped: NaN.
    Post-fix (this task): both cases return meanCapped=null.

    Uses `_NAN_SAFE_REPLACER_JS` because plain JSON.stringify maps NaN to
    `null` too -- without the replacer this test could not tell "still
    NaN" apart from "properly guarded null" and would pass even on the
    UNFIXED file."""
    _require_node()
    harness = (
        "const fs = require('node:fs');\n"
        f"eval(fs.readFileSync({str(JS)!r}, 'utf8'));\n"
        f"{_NAN_SAFE_REPLACER_JS}"
        "const M = globalThis.lossPreviewMath;\n"
        "const fit = M.fitP5P95(1, 1e60);\n"
        "const capA = Math.exp(fit.mu) * 0.5;\n"
        "const caseA = M.lognormalStats({mu: fit.mu, sigma: fit.sigma, cap: capA});\n"
        "const mu2 = Math.log(250000);\n"
        "const caseB = M.lognormalStats({mu: mu2, sigma: 38, cap: 300000});\n"
        "process.stdout.write(JSON.stringify({caseA, caseB}, replacer));\n"
    )
    harness_path = tmp_path / "meancapped_nan_guard.js"
    harness_path.write_text(harness)
    res = subprocess.run(["node", str(harness_path)], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    got = json.loads(res.stdout)
    case_a, case_b = got["caseA"], got["caseB"]
    assert case_a["capClamped"] is True, case_a
    assert case_a["meanCapped"] is None, case_a
    assert case_b["capClamped"] is False, case_b
    assert case_b["meanCapped"] is None, case_b


def test_mean_capped_null_on_true_underflow_not_spurious_zero(tmp_path: Path) -> None:
    """B-I2 (methodology gate finding, PR3 T1.a; rationale corrected at
    the re-gate, B1): when normCdf(b-sigma) underflows to EXACTLY 0 while
    normCdf(b) > 0 (re-gate-executed at mu=ln(250k)/cap=300k: sigma in
    [8.40, 37.34]; mean overflows to Infinity from ~37.35 where the B-I1
    guard governs), the pre-fix code returned meanCapped=0 -- a
    genuine-looking $0 truncated mean that is actually a float-underflow
    artifact. The closed form's true value is ALWAYS > 0 for any cap > 0,
    so an exact 0 is never a real answer; null says "unrepresentable at
    this approximation's precision".

    Pre-fix repro (executed 2026-07-30 against the file BEFORE this task's
    fix): sigma=9 and sigma=12 at mu=ln(250_000), cap=300_000 both returned
    meanCapped=0 (confirmed via a direct console.log of
    normCdf(b-sigma) === 0 while normCdf(b) ~= 0.5, i.e. NOT the
    degenerate-cap state -- capClamped is False at cap=300_000 > median
    250_000 throughout). Unlike B-I1, this one needs no NaN-safe replacer:
    the pre-fix value is a real 0, not NaN, so plain JSON already
    distinguishes it from a guarded null."""
    _require_node()
    harness = (
        "const fs = require('node:fs');\n"
        f"eval(fs.readFileSync({str(JS)!r}, 'utf8'));\n"
        "const mu = Math.log(250000);\n"
        "const out = [9, 12].map(sigma =>\n"
        "  globalThis.lossPreviewMath.lognormalStats({mu: mu, sigma: sigma, cap: 300000}));\n"
        "process.stdout.write(JSON.stringify(out));\n"
    )
    harness_path = tmp_path / "meancapped_underflow_guard.js"
    harness_path.write_text(harness)
    res = subprocess.run(["node", str(harness_path)], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    got = json.loads(res.stdout)
    for sigma, actual in zip((9, 12), got, strict=True):
        assert actual["capClamped"] is False, (sigma, actual)  # cap(300k) > median(250k)
        assert actual["meanCapped"] is None, (sigma, actual)


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
# beta 3.33-4.65 across the SIGMAS grid; re-gate B5 corrected the stale
# 3.39 upper end -- sigma=0.4's interior mode gives beta=4.6548) but
# cdfGrid is public -- the
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
        median = math.exp(mu)
        # mu = ln(250k): 4e9 non-binding / 300k binding-hard / 200k DEGENERATE
        # (cap < median -> capClamped state; Arch-N8 -- the jj/kk mirror must
        # have parity coverage, and 300k is NOT degenerate, it is merely hard).
        # T1.a NTH additions (2026-07-30):
        #   - deep cap = exp(mu - 2.5*sigma) -> Phi(b) = Phi(-2.5) = 0.006210
        #     < ACKLAM_P_LOW (0.02425), so 0.99*Phi(b) also falls under
        #     0.02425 -> exercises normInv's LOW branch inside p99Capped
        #     (previously untested). meanCapped is intentionally EXCLUDED
        #     for this case -- TOL["meanCapped"] is grid-tuned to the four
        #     cases above (see the TOL dict's comment); this deeper cap
        #     pushes meanCapped's own relative error to ~3.5e-3 at
        #     sigma=3.47 (executed 2026-07-30), which is expected fixed-
        #     abs-error-through-a-smaller-ratio behavior, not a bug, and
        #     not what this case exists to test (p99Capped/capBindProb stay
        #     well inside their tolerances here: executed <=3.4e-6 and
        #     <=1.5e-8 respectively).
        #   - boundary cap = exp(mu) exactly -- loss_preview.js's
        #     capClamped test uses `cap <= median` (inclusive); this case
        #     pins the `<=` boundary itself, not just `<`.
        cap_specs: list[tuple[float | None, bool]] = [
            (None, False),
            (4e9, True),
            (300_000.0, True),
            (200_000.0, True),
            (math.exp(mu - 2.5 * sigma), False),  # deep normInv-lower-branch case
            (median, True),  # cap == median boundary (JS `<=`)
        ]
        for cap, include_mean_capped in cap_specs:
            exp: dict[str, float] = {
                "median": median,
                "mean": math.exp(mu + sigma * sigma / 2),
                "p95": float(lognormal_quantiles(mu, sigma, (0.95,))[0]),
                # B-I4b: p99 previously had zero coverage (Z99 itself was
                # only grep-pinned, never exercised end-to-end).
                "p99": math.exp(mu + sigma * float(norm.ppf(0.99))),
            }
            if cap is not None:
                b = (math.log(cap) - mu) / sigma
                phi_b = float(norm.cdf(b))
                # T1.a NTH: capBindProb = 1 - Phi(b), independently from scipy.
                exp["capBindProb"] = 1 - phi_b
                # p99Capped's "expected" here re-derives the SAME closed
                # form loss_preview.js implements
                # (exp(mu + sigma*normInv(0.99*Phi(b)))) via an independent
                # scipy call -- fair_cam has no capped/truncated-p99 helper
                # to pin against, so this is a structural self-referential
                # parity check (JS formula vs. an independent Python
                # re-implementation of the same formula), not an oracle
                # check against a second, unrelated source of truth. Its
                # SEMANTICS (that this is the right definition of a
                # capped p99) were verified by the methodology gate review
                # that produced this task, not by this test.
                exp["p99Capped"] = math.exp(mu + sigma * float(norm.ppf(0.99 * phi_b)))
                if include_mean_capped:
                    exp["meanCapped"] = truncated_lognormal_mean(mu, sigma, cap)
            # Golden through the LIVE collapser on a one-component mixture
            # (wizard single-SME capped case).
            pert, _clamp = lognormal_mixture_to_pert_approx(one_component_mix(mu, sigma))
            exp["pertLow"], exp["pertMode"], exp["pertHigh"] = pert.low, pert.mode, pert.high
            exp["pertMean"] = (pert.low + 4 * pert.mode + pert.high) / 6
            # T1.a NTH: impliedSigma round-trips sigma through the PERT
            # low/high by construction (low/high = exp(mu -+ sigma*Z95)),
            # so the expected value IS sigma itself (executed max relative
            # error 2.6e-16 -- machine precision -- over SIGMAS).
            exp["impliedSigma"] = sigma
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
        "          pertMean: ps.mean, impliedSigma: ps.impliedSigma,\n"
        "          realizedMedianPos: globalThis.lossPreviewMath.medianPosFromGrid(grid, p)};\n"
        "});\n"
        "process.stdout.write(JSON.stringify(out));\n"
    )
    payload = [{"mu": c.mu, "sigma": c.sigma, "cap": c.cap} for c in cases]
    got = _run_node(harness, payload, tmp_path, "golden")
    for case, actual in zip(cases, got, strict=True):
        for key, want in case.expected.items():
            assert actual[key] == pytest.approx(want, rel=TOL[key]), (case.sigma, case.cap, key)
        # T1.a NTH: generalized capClamped check covers the True branch
        # (cap < median), the False branch (cap > median, e.g. the 4e9/
        # 300k cases -- previously unasserted), AND the cap==median
        # boundary (JS uses `cap <= median`; the boundary cap_spec above is
        # built as exactly `median`, so `case.cap <= math.exp(case.mu)` is
        # True by construction here, which is what pins the `<=`, not `<`).
        if case.cap is not None:
            expected_clamped = case.cap <= math.exp(case.mu)
            assert actual["capClamped"] is expected_clamped, (
                case.sigma,
                case.cap,
                expected_clamped,
            )


def _pert_grid_x(low: float, high: float, n: int = 256) -> list[float]:
    """Python replica of loss_preview.js's `pertGridX` (log-x grid, n
    points, endpoints snapped exactly to low/high). Used only so
    `test_pert_density_path_golden` can find each grid point's EXACT (x, u)
    pair without interpolating the returned density array -- interpolating
    a Beta density near a singularity would introduce its own error,
    defeating the point of a tight rel=1e-4 golden check."""
    log_low = math.log(low)
    log_high = math.log(high)
    n_points = n - 1
    xs = [math.exp(log_low + (i / n_points) * (log_high - log_low)) for i in range(n)]
    xs[0] = low
    xs[-1] = high
    return xs


def test_pert_density_path_golden(tmp_path: Path) -> None:
    """B-I5 (methodology gate finding, PR3 T1.a): `pertDensityPath` had
    zero coverage (only `cdfGrid` was exercised, via
    `medianPosFromGrid`/`realizedMedianPos` in test_golden_vector_parity).

    For each sigma in SIGMAS, build the capped PERT triple via
    capPertFromFit (mu=ln(250_000), matching the golden test's fixed mu),
    then check the 3 grid points NEAREST to u=0.25/0.5/0.75 of [low, high]
    against scipy.stats.beta.pdf(u, alpha, beta)/range, using the SAME Vose
    alpha/beta this file already replicates (`vose_alpha_beta`). Grid
    points are matched via `_pert_grid_x` (an exact replica of
    `pertGridX`) rather than interpolated, so both sides evaluate at the
    IDENTICAL u -- no interpolation error is introduced on either side of
    the rel=1e-4 comparison.

    Also pins the ENDPOINT CONTRACT documented in loss_preview.js's own
    docstring above `pertDensityPath` (Task 2's chart renderer is
    documented to rely on it, so it is pinned here explicitly rather than
    left implicit): alpha<1 -> density[0] == +Infinity; alpha>1 ->
    density[0] == 0 (alpha==1 is the finite-constant edge case, not
    exercised by any sigma in SIGMAS -- executed 2026-07-30: alpha ranges
    0.6667-2.2348 across the grid, never exactly 1). beta stays > 1
    throughout SIGMAS (executed range 3.33-4.65), so density[-1] == 0 is
    asserted for every case; the beta<1 sibling has no live coverage here
    (capPertFromFit's pl/sl support never produces beta<1 -- documented in
    loss_preview.js as "unreachable... but this function is public").

    Uses `_NAN_SAFE_REPLACER_JS` + `_denanify`: plain JSON.stringify would
    otherwise turn a genuine density[0]==Infinity into `null`, making the
    endpoint-contract assertions below meaningless.
    """
    _require_node()
    mu = math.log(250_000.0)
    harness = (
        "const fs = require('node:fs');\n"
        f"eval(fs.readFileSync({str(JS)!r}, 'utf8'));\n"
        f"{_NAN_SAFE_REPLACER_JS}"
        "const cases = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));\n"
        "const out = cases.map(c => {\n"
        "  const pert = globalThis.lossPreviewMath.capPertFromFit(c.mu, c.sigma);\n"
        "  const dp = globalThis.lossPreviewMath.pertDensityPath(pert);\n"
        "  return {low: pert.low, mode: pert.mode, high: pert.high, density: dp.density};\n"
        "});\n"
        "process.stdout.write(JSON.stringify(out, replacer));\n"
    )
    payload = [{"mu": mu, "sigma": sigma} for sigma in SIGMAS]
    got = _run_node(harness, payload, tmp_path, "pert_density")

    for sigma, actual in zip(SIGMAS, got, strict=True):
        low, mode, high = actual["low"], actual["mode"], actual["high"]
        alpha, beta_ = vose_alpha_beta(low, mode, high)
        rng = high - low
        xs = _pert_grid_x(low, high)
        density = [_denanify(v) for v in actual["density"]]

        for u_target in (0.25, 0.5, 0.75):
            idx = min(range(len(xs)), key=lambda i: abs((xs[i] - low) / rng - u_target))
            u_exact = (xs[idx] - low) / rng
            expected = float(scipy_beta.pdf(u_exact, alpha, beta_)) / rng
            assert density[idx] == pytest.approx(expected, rel=1e-4), (
                sigma,
                u_target,
                idx,
                expected,
                density[idx],
            )

        # Endpoint contract (documented in loss_preview.js above
        # pertDensityPath): alpha<1 -> +Infinity at x=low; alpha>1 -> 0.
        if alpha < 1:
            assert density[0] == math.inf, (sigma, alpha, density[0])
        elif alpha > 1:
            assert density[0] == 0, (sigma, alpha, density[0])
        if beta_ < 1:
            assert density[-1] == math.inf, (sigma, beta_, density[-1])
        elif beta_ > 1:
            assert density[-1] == 0, (sigma, beta_, density[-1])
