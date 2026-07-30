/* loss_preview.js — first-party lognormal/PERT preview math (sigma-recal
 * PR3, D22).
 *
 * AUTHORITATIVE MATH LIVES IN fair_cam; this file is a labeled PREVIEW
 * MIRROR, parity-pinned by tests/unit/test_loss_preview_parity.py. It
 * exists so the wizard/expert-form loss-dispersion readout (Task 2) can
 * render a live density/CDF chart client-side, without a server round trip
 * on every keystroke — but it must never become a second source of truth.
 * If this file and fair_cam ever disagree, fair_cam wins and this file is
 * the bug (CLAUDE.md "Never re-derive FAIR calculations in the app
 * layer" — this module is the one narrow, test-gated exception, scoped to
 * a non-committal live preview). Two DOCUMENTED envelope notes, not
 * disagreements: (1) fitLognormal returns nulls on inverted/equal
 * quantile pairs where fair_cam raises ValueError (keystroke-transient
 * inputs must not throw); (2) meanCapped degrades one-sidedly at extreme
 * sigma via A&S 7.1.26's absolute error (re-gate-executed vs fair_cam at
 * mu=ln(250k)/cap=300k: <1e-3% at authored sigma <= 3.5, ~0.43% at
 * sigma=6.3, ~15% at sigma=8.2, null >= 8.4 via the underflow guard) —
 * keystroke-transient territory only.
 *
 * Citations for the approximations below:
 *   - erf(x): Abramowitz & Stegun, "Handbook of Mathematical Functions"
 *     (1964), formula 7.1.26 (rational approximation attributed to
 *     Hastings 1955). Published max absolute error: 1.5e-7.
 *   - normInv(p) (inverse standard-normal CDF): Peter J. Acklam's rational
 *     approximation, primary source (Wayback capture accessed 2015-10-30,
 *     per the archive timestamp in the URL itself):
 *     https://web.archive.org/web/20151030215612/http://home.online.no/~pjacklam/notes/invnorm/
 *     Published max relative error: 1.15e-9.
 *   - PERT density/sampling shape: Vose, D. (2008), "Risk Analysis: A
 *     Quantitative Guide" (3rd ed.), the gamma=4 BetaPERT parameterization
 *     — parameterised IDENTICALLY to fair_cam.risk_engine.fair_core's PERT
 *     branch (the native engine's sampler), which itself matches pyfair's
 *     utility/beta_pert.py (epic #324 equivalence gate). The alternate
 *     classic alpha+beta=6 form is explicitly NOT used here — fair_core's
 *     own comment records a ~0.5% median / ~2% ALE divergence from it.
 *     No specific page/section number for the gamma=4 form is independently
 *     verified here (do not invent one): cite as "Vose, Risk Analysis: A
 *     Quantitative Guide — modified-PERT form as implemented by fair_cam
 *     fair_core.py PERT branch (the authoritative mirror source)".
 *
 * Loaded non-defer by base.html (see the comment there) so this factory is
 * registered before the deferred Alpine bundle walks x-data.
 */
"use strict";
(function () {
  var root = typeof window !== "undefined" ? window : globalThis;

  // scipy.stats.norm.ppf(0.95); pinned to equal fair_cam's own Z_0_95
  // EXACTLY (repr-identical — asserted by
  // test_loss_preview_parity.test_z95_literal_matches_fair_cam). NOTE this
  // differs from a fresh `float(norm.ppf(0.95))` call by 1 ULP (N-M2) —
  // this literal is copied from fair_cam.quantile_pooling.Z_0_95 itself,
  // never re-derived.
  var Z95 = 1.6448536269514722;

  // scipy.stats.norm.ppf(0.99); executed 2026-07-30:
  //   python -c "from scipy.stats import norm; print(repr(norm.ppf(0.99)))"
  //   -> 2.3263478740408408
  var Z99 = 2.3263478740408408;

  // ---- erf / normal CDF / normal inverse-CDF -----------------------------

  // Abramowitz & Stegun 7.1.26 (Hastings 1955) coefficients. Max abs error
  // 1.5e-7 (published) — each coefficient kept as its own signed literal
  // (rather than folded into +/- operators) so the source text carries the
  // exact published constants, byte for byte.
  var AS_A1 = 0.254829592;
  var AS_A2 = -0.284496736;
  var AS_A3 = 1.421413741;
  var AS_A4 = -1.453152027;
  var AS_A5 = 1.061405429;
  var AS_P = 0.3275911;

  function erf(x) {
    if (!Number.isFinite(x)) {
      if (x === Infinity) return 1;
      if (x === -Infinity) return -1;
      return NaN;
    }
    var s = x >= 0 ? 1 : -1;
    var ax = Math.abs(x);
    var t = 1 / (1 + AS_P * ax);
    var y =
      1 -
      (((((AS_A5 * t + AS_A4) * t + AS_A3) * t + AS_A2) * t + AS_A1) * t) * Math.exp(-ax * ax);
    return s * y;
  }

  function normCdf(x) {
    if (!Number.isFinite(x)) {
      if (x === Infinity) return 1;
      if (x === -Infinity) return 0;
      return NaN;
    }
    return 0.5 * (1 + erf(x / Math.SQRT2));
  }

  // Peter Acklam's rational approximation to the inverse standard-normal
  // CDF. Published max relative error 1.15e-9 (pinned/verified by
  // test_acklam_coefficients_present_and_formula_accurate: executed max
  // 1.1288438475563716e-09 over p in [1e-6, 1-1e-6]).
  var ACKLAM_A = [
    -3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
    1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00,
  ];
  var ACKLAM_B = [
    -5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
    6.680131188771972e+01, -1.328068155288572e+01,
  ];
  var ACKLAM_C = [
    -7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
    -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00,
  ];
  var ACKLAM_D = [
    7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
    3.754408661907416e+00,
  ];
  var ACKLAM_P_LOW = 0.02425;
  var ACKLAM_P_HIGH = 1 - ACKLAM_P_LOW;

  function normInv(p) {
    if (!Number.isFinite(p) || p <= 0 || p >= 1) {
      if (p === 0) return -Infinity;
      if (p === 1) return Infinity;
      return NaN;
    }
    var a = ACKLAM_A,
      b = ACKLAM_B,
      c = ACKLAM_C,
      d = ACKLAM_D;
    var q, r;
    if (p < ACKLAM_P_LOW) {
      q = Math.sqrt(-2 * Math.log(p));
      return (
        (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) /
        ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
      );
    }
    if (p <= ACKLAM_P_HIGH) {
      q = p - 0.5;
      r = q * q;
      return (
        ((((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q) /
        (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
      );
    }
    q = Math.sqrt(-2 * Math.log(1 - p));
    return (
      -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) /
      ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    );
  }

  // ---- log-space two-quantile lognormal fit ------------------------------

  // General two-quantile fit, algebraically identical to
  // fair_cam.quantile_pooling.lognormal_from_quantiles (verified 2026-07-30:
  // both forms agree to <1e-12 relative error against fair_cam's
  // lognormal_from_quantiles across p50/p95 and p5/p95 anchor pairs — see
  // test_fit_functions_parity).
  //   mu    = (zHi*ln(qLo) - zLo*ln(qHi)) / (zHi - zLo)
  //   sigma = (ln(qHi) - ln(qLo)) / (zHi - zLo)
  //
  // Ordering guard (methodology gate finding B-I3, T1.a): qHi<=qLo
  // must return {mu: null, sigma: null}, mirroring
  // fair_cam.quantile_pooling.lognormal_from_quantiles, which RAISES
  // ValueError on an inverted pair (executed 2026-07-30:
  // lognormal_from_quantiles(200_000, 100_000, q_low=0.05, q_high=0.95) ->
  // "ValueError: high must be >= low"). This JS mirror has no exception
  // channel, so the null-fields sentinel is the equivalent signal. Without
  // this guard, an inverted pair silently produced a NEGATIVE sigma
  // (pre-fix repro: fitP5P95(200_000, 100_000) -> sigma=-0.2107...) and an
  // equal pair silently produced sigma=0 (a degenerate point-mass "fit"
  // that every downstream sigma<=0 guard in this file already treats as
  // invalid, so rejecting it here too keeps fitLognormal's own contract
  // consistent with its callers rather than leaking a technically-truthy
  // sigma:0 that looks unset but isn't null).
  function fitLognormal(qLo, qHi, zLo, zHi) {
    if (
      !Number.isFinite(qLo) ||
      !Number.isFinite(qHi) ||
      qLo <= 0 ||
      qHi <= 0 ||
      qHi <= qLo ||
      !Number.isFinite(zLo) ||
      !Number.isFinite(zHi) ||
      zHi === zLo
    ) {
      return { mu: null, sigma: null };
    }
    var lnLo = Math.log(qLo);
    var lnHi = Math.log(qHi);
    var mu = (zHi * lnLo - zLo * lnHi) / (zHi - zLo);
    var sigma = (lnHi - lnLo) / (zHi - zLo);
    return { mu: mu, sigma: sigma };
  }

  // Preset: zLo=0 (the median has z=0 by definition).
  function fitP50P95(p50, p95) {
    return fitLognormal(p50, p95, 0, Z95);
  }

  // Preset: zLo=-Z95 (norm.ppf(0.05) === -norm.ppf(0.95) to within 4e-16
  // relative — the standard normal's own symmetry, verified 2026-07-30).
  function fitP5P95(p5, p95) {
    return fitLognormal(p5, p95, -Z95, Z95);
  }

  // ---- lognormal summary stats (capped + uncapped) -----------------------

  // Mirrors fair_cam.quantile_pooling.truncated_lognormal_mean /
  // Z_0_95-scaled quantiles. `cap` may be null/undefined (uncapped
  // preview) — every cap-derived field then returns null rather than NaN
  // (non-finite guard rule).
  function lognormalStats(args) {
    var mu = args.mu,
      sigma = args.sigma,
      cap = args.cap;
    if (!Number.isFinite(mu) || !Number.isFinite(sigma) || sigma <= 0) {
      return {
        median: null,
        mean: null,
        p95: null,
        p99: null,
        meanCapped: null,
        p99Capped: null,
        capBindProb: null,
        capClamped: null,
      };
    }
    var median = Math.exp(mu);
    var mean = Math.exp(mu + (sigma * sigma) / 2);
    // p95/p99 use the PINNED Z literals (fixed, well-known quantiles), not
    // normInv(0.95)/normInv(0.99) — the Acklam approximation's ~1e-9
    // relative error in z, scaled by sigma up to ~3.5 through exp(), would
    // exceed this project's 1e-9 relative parity tolerance on p95/p99. Only
    // *floating* probabilities (p99Capped below) go through normInv.
    var p95 = Math.exp(mu + sigma * Z95);
    var p99 = Math.exp(mu + sigma * Z99);

    var meanCapped = null;
    var p99Capped = null;
    var capBindProb = null;
    var capClamped = null;

    if (cap !== null && cap !== undefined && Number.isFinite(cap) && cap > 0) {
      // Degenerate-cap state: cap at or below the distribution's own
      // median (mirrors the PR2 jj/kk degenerate-cap guards' spirit —
      // there is no meaningful "typical" draw left above the cap).
      capClamped = cap <= median;
      var b = (Math.log(cap) - mu) / sigma;
      var phiB = normCdf(b);
      capBindProb = 1 - phiB;
      if (phiB > 0) {
        var phiBMinusSigma = normCdf(b - sigma);
        // B-I2 (methodology gate, PR3 T1.a; rationale corrected at the
        // re-gate, B1): the closed form's true value is ALWAYS > 0 for any
        // cap > 0 (E[X | X <= cap] > 0 with mu/sigma/cap finite), so an
        // exact 0 here is ALWAYS float underflow of Phi(b-sigma) beyond
        // A&S 7.1.26's resolution -- never a real answer. (capClamped is
        // cap <= median, a DIFFERENT state with real nonzero means, as the
        // goldens assert.) Re-gate-executed at mu=ln(250k)/cap=300k: the
        // finite-mean spurious-zero band is sigma in [8.40, 37.34]; from
        // sigma ~ 37.35 the mean overflows and the B-I1 NaN guard governs.
        // Reporting the underflow as a real $0 truncated mean would render
        // a false number instead of admitting the estimate isn't
        // representable at this approximation's precision.
        if (phiBMinusSigma === 0) {
          meanCapped = null;
        } else {
          var meanCappedRaw = mean * (phiBMinusSigma / phiB);
          // B-I1: `mean` can independently overflow to Infinity (large
          // sigma) while phiBMinusSigma is a tiny nonzero underflow-
          // adjacent value -- Infinity * ~0 = NaN in IEEE754 (pre-fix
          // repro: fitP5P95(1, 1e60) -> sigma~=42 -> meanCapped NaN).
          // Guard the PRODUCT itself so a non-finite result never escapes
          // this module as if it were a real number.
          meanCapped = Number.isFinite(meanCappedRaw) ? meanCappedRaw : null;
        }
        var p99CappedRaw = Math.exp(mu + sigma * normInv(0.99 * phiB));
        p99Capped = Number.isFinite(p99CappedRaw) ? p99CappedRaw : null;
      }
    }

    return {
      median: median,
      mean: mean,
      p95: p95,
      p99: p99,
      meanCapped: meanCapped,
      p99Capped: p99Capped,
      capBindProb: capBindProb,
      capClamped: capClamped,
    };
  }

  // ---- PERT collapse (single-component mixture mirror) -------------------

  // Mirror of the SINGLE-COMPONENT path of
  // fair_cam.quantile_pooling.lognormal_mixture_to_pert_approx (the live
  // capped collapser, services/wizard_finalize.py:187) for pl/sl support
  // (min_support=0, max_support=+inf) — the wizard's single-SME case.
  //
  //   low  = quantile(fit, 0.05)
  //   high = quantile(fit, 0.95)
  //   raw_mode = exp(mu - sigma**2)          # TRUE LOGNORMAL MODE
  //   mode = _clamp_mode(raw_mode, min_support=0, max_support=+inf, low, high)
  //
  // _clamp_mode's 4-branch precedence (fair_cam/quantile_pooling/_types.py):
  //   1. raw_mode < min_support -> clamp to max(min_support, low)
  //   2. raw_mode > max_support -> clamp to min(max_support, high)
  //   3. raw_mode > high        -> clamp to high (MODE_ABOVE_PERT_HIGH;
  //      unreachable for a lognormal fit per that module's docstring — for
  //      any sigma>0, raw_mode <= median <= p95 <= high)
  //   4. raw_mode < low         -> clamp to low
  // With min_support=0 and max_support=+inf, branches 1-2 never fire
  // (raw_mode = exp(...) is always > 0 and finite), so only 3-4 matter —
  // kept explicit below for fidelity to the shared clamp function, not as
  // dead code.
  //
  // `_qlnormtrunc(p, mu, sigma, 0, Infinity)` — the truncated-normal
  // quantile fair_cam's collapser actually calls — is byte-identical to
  // the untruncated closed form `exp(mu + sigma*z_p)` at these bounds
  // (verified 2026-07-30: the truncation lower bound sits ~700/sigma below
  // the 5th percentile, far past float underflow, so truncnorm degenerates
  // exactly to the standard normal there). Low/high therefore use the
  // pinned Z95 literal directly (same 1e-9-tolerance rationale as
  // lognormalStats' p95/p99 above), never normInv(0.05)/normInv(0.95).
  function capPertFromFit(mu, sigma) {
    if (!Number.isFinite(mu) || !Number.isFinite(sigma) || sigma <= 0) {
      return { low: null, mode: null, high: null };
    }
    var low = Math.exp(mu - sigma * Z95);
    var high = Math.exp(mu + sigma * Z95);
    var rawMode = Math.exp(mu - sigma * sigma);
    var minSupport = 0;
    var maxSupport = Infinity;
    var mode;
    if (rawMode < minSupport) {
      mode = Math.max(minSupport, low);
    } else if (rawMode > maxSupport) {
      mode = Math.min(maxSupport, high);
    } else if (rawMode > high) {
      mode = high;
    } else if (rawMode < low) {
      mode = low;
    } else {
      mode = rawMode;
    }
    return { low: low, mode: mode, high: high };
  }

  // ---- PERT moment stats ---------------------------------------------------

  // Vose gamma=4 PERT mean; impliedSigma matches
  // routes/scenarios.py:_stored_loss_sigma's PERT read: ln(high/low)/(2*Z95).
  function pertStats(args) {
    var low = args.low,
      mode = args.mode,
      high = args.high;
    if (
      !Number.isFinite(low) ||
      !Number.isFinite(mode) ||
      !Number.isFinite(high) ||
      !(high >= low) ||
      !(low > 0)
    ) {
      return { mean: null, impliedSigma: null };
    }
    var mean = (low + 4 * mode + high) / 6;
    var impliedSigma = high > 0 ? Math.log(high / low) / (2 * Z95) : null;
    return { mean: mean, impliedSigma: impliedSigma };
  }

  // ---- Vose BetaPERT alpha/beta (mirrors fair_core.py's PERT branch) -----

  // READ AT IMPL: fair_cam/risk_engine/fair_core.py PERT branch (~lines
  // 203-209). Mirrored EXACTLY — the classic alpha+beta=6 form is
  // explicitly banned there (that module's own comment records a ~0.5%
  // median / ~2% ALE divergence vs the equivalence-gated pyfair oracle).
  //   gamma = 4.0
  //   mean  = (low + gamma*mode + high) / (gamma + 2.0)
  //   stdev = (high - low) / (gamma + 2.0)
  //   g1    = (mean - low) / (high - low)
  //   g2    = ((mean - low) * (high - mean)) / stdev**2
  //   alpha = g1 * (g2 - 1.0)
  //   beta  = alpha * (high - mean) / (mean - low)
  function vosePertAlphaBeta(low, mode, high) {
    var gamma = 4.0;
    var mean = (low + gamma * mode + high) / (gamma + 2.0);
    var stdev = (high - low) / (gamma + 2.0);
    var g1 = (mean - low) / (high - low);
    var g2 = ((mean - low) * (high - mean)) / (stdev * stdev);
    var alpha = g1 * (g2 - 1.0);
    var beta = alpha * ((high - mean) / (mean - low));
    return { alpha: alpha, beta: beta, mean: mean, stdev: stdev };
  }

  // Lanczos approximation to log(Gamma(x)), g=7, n=9 coefficients
  // (Numerical Recipes / standard reference form). Relative error ~1e-15
  // for Re(x) > 0, far tighter than this module needs (realizedMedianPos
  // TOL is 5e-3) — used only to build the Beta(alpha, beta) normalizing
  // constant for the PERT density/CDF grid below.
  var LANCZOS_G = 7;
  var LANCZOS_COEF = [
    0.99999999999980993, 676.5203681218851, -1259.1392167224028,
    771.32342877765313, -176.61502916214059, 12.507343278686905,
    -0.13857109526572012, 9.9843695780195716e-6, 1.5056327351493116e-7,
  ];

  function lgamma(x) {
    if (x < 0.5) {
      // Reflection formula. Not exercised by this module's alpha/beta
      // domain (both always > 0 for a well-formed PERT triple) but kept
      // for defensiveness rather than assuming callers never hit it.
      return Math.log(Math.PI / Math.sin(Math.PI * x)) - lgamma(1 - x);
    }
    var xm1 = x - 1;
    var a = LANCZOS_COEF[0];
    var t = xm1 + LANCZOS_G + 0.5;
    for (var i = 1; i < LANCZOS_COEF.length; i++) {
      a += LANCZOS_COEF[i] / (xm1 + i);
    }
    return 0.5 * Math.log(2 * Math.PI) + (xm1 + 0.5) * Math.log(t) - t + Math.log(a);
  }

  function logBeta(alpha, beta) {
    return lgamma(alpha) + lgamma(beta) - lgamma(alpha + beta);
  }

  // ---- density / CDF grid --------------------------------------------------

  var GRID_POINTS = 256;

  function pertGridX(low, high) {
    var xs = new Array(GRID_POINTS);
    var logLow = Math.log(low);
    var logHigh = Math.log(high);
    for (var i = 0; i < GRID_POINTS; i++) {
      var t = i / (GRID_POINTS - 1);
      xs[i] = Math.exp(logLow + t * (logHigh - logLow));
    }
    xs[0] = low; // avoid fp round-trip drift at the endpoints
    xs[GRID_POINTS - 1] = high;
    return xs;
  }

  // Standard Beta(alpha, beta) density on the unit interval, in log space.
  // Callers guard u in (0, 1) — the u=0/u=1 singularities (alpha<1 /
  // beta<1) are handled by the analytic front/back-cell closed forms in
  // cdfGrid, never by direct pointwise evaluation here.
  function betaLogPdfUnit(u, alpha, beta, logB) {
    return (alpha - 1) * Math.log(u) + (beta - 1) * Math.log(1 - u) - logB;
  }

  // 256-point log-x density path for the chart (Task 2). Honest math: at
  // the low/high endpoint, the true Beta(alpha, beta) density is +Infinity
  // when alpha<1 / beta<1 respectively (a genuine PERT-shape feature for
  // very wide/narrow anchors), 0 when alpha>1 / beta>1, and a finite
  // nonzero constant only in the (rare, near-measure-zero for elicited
  // data) alpha==1 / beta==1 case. Renderers must treat +Infinity as "cap
  // the drawn point at the chart's own y-max", not as invalid input.
  function pertDensityPath(pert) {
    var low = pert.low,
      mode = pert.mode,
      high = pert.high;
    if (
      !Number.isFinite(low) ||
      !Number.isFinite(mode) ||
      !Number.isFinite(high) ||
      !(high > low)
    ) {
      return null;
    }
    var xs = pertGridX(low, high);
    var ab = vosePertAlphaBeta(low, mode, high);
    var logB = logBeta(ab.alpha, ab.beta);
    var range = high - low;
    var density = new Array(GRID_POINTS);
    for (var i = 0; i < GRID_POINTS; i++) {
      var u = (xs[i] - low) / range;
      if (u <= 0) {
        density[i] = ab.alpha < 1 ? Infinity : ab.alpha === 1 ? 1 / (range * Math.exp(logB)) : 0;
      } else if (u >= 1) {
        density[i] = ab.beta < 1 ? Infinity : ab.beta === 1 ? 1 / (range * Math.exp(logB)) : 0;
      } else {
        density[i] = Math.exp(betaLogPdfUnit(u, ab.alpha, ab.beta, logB)) / range;
      }
    }
    return { x: xs, density: density };
  }

  // 256-point log-x CDF grid. Front-cell rule (plan-gate B3-3/M4-1): when
  // alpha<1 the Beta pdf has a u^(alpha-1) singularity at x=low, so the
  // first cell's mass uses the closed form u1^alpha / (alpha*B(alpha,beta))
  // (dropping the (1-u)^(beta-1) factor — immaterial at u1~1e-4, so
  // "approximately", not exact) instead of a naive trapezoid that would
  // under-count front-cell mass and bias the realized median position
  // high. Trapezoid thereafter. NOTE (T1.a NTH clarification): the closed
  // form is applied unconditionally — strictly better than trapezoid at
  // alpha>=1 too (the trapezoid rule's own error near a Beta density's
  // interior curvature is never smaller than the closed form's, so there
  // is no alpha<1 branch guarding this call; the singularity case is just
  // the one where using the naive trapezoid instead would be visibly
  // wrong, not the only case where the closed form is more accurate).
  // Defensive symmetric sibling: a beta<1 singularity at u=1 is
  // unreachable from capPertFromFit (pl/sl support always yields beta well
  // above 1 there) but this function is public, so the last cell gets the
  // same closed-form treatment when beta<1. The CDF is finally
  // re-normalized so the last point is exactly 1.
  function cdfGrid(pert) {
    var low = pert.low,
      mode = pert.mode,
      high = pert.high;
    if (
      !Number.isFinite(low) ||
      !Number.isFinite(mode) ||
      !Number.isFinite(high) ||
      !(high > low)
    ) {
      return null;
    }
    var xs = pertGridX(low, high);
    var ab = vosePertAlphaBeta(low, mode, high);
    var logB = logBeta(ab.alpha, ab.beta);
    var range = high - low;
    var n = GRID_POINTS;

    function densityAt(x) {
      var u = (x - low) / range;
      if (u <= 0 || u >= 1) return 0; // endpoints: handled analytically below
      return Math.exp(betaLogPdfUnit(u, ab.alpha, ab.beta, logB)) / range;
    }

    var raw = new Array(n).fill(0);
    var u1 = (xs[1] - low) / range;
    raw[1] = Math.pow(u1, ab.alpha) / (ab.alpha * Math.exp(logB));

    for (var i = 2; i < n; i++) {
      var d0 = densityAt(xs[i - 1]);
      var d1 = densityAt(xs[i]);
      raw[i] = raw[i - 1] + 0.5 * (d0 + d1) * (xs[i] - xs[i - 1]);
    }

    if (ab.beta < 1) {
      var v1 = 1 - (xs[n - 2] - low) / range;
      var tailMass = Math.pow(v1, ab.beta) / (ab.beta * Math.exp(logB));
      raw[n - 1] = raw[n - 2] + tailMass;
    }

    var total = raw[n - 1];
    var cdf = new Array(n);
    for (var j = 0; j < n; j++) {
      cdf[j] = total > 0 ? raw[j] / total : 0;
    }
    return { x: xs, cdf: cdf };
  }

  // Inverts the numeric CDF grid at probability 0.5 by linear interpolation
  // between the bracketing grid points, then returns the POSITION
  // (x - low)/(high - low) in [0, 1] — not an x-value. Used by the chart's
  // realized-median marker (Task 2) and by the parity harness.
  function medianPosFromGrid(grid, pert) {
    if (!grid || !pert) return null;
    var x = grid.x,
      cdf = grid.cdf;
    var low = pert.low,
      high = pert.high;
    if (!Number.isFinite(low) || !Number.isFinite(high) || !(high > low)) return null;
    var i = 1;
    while (i < cdf.length && cdf[i] < 0.5) i++;
    if (i >= cdf.length) i = cdf.length - 1;
    if (i < 1) i = 1;
    var c0 = cdf[i - 1],
      c1 = cdf[i];
    var x0 = x[i - 1],
      x1 = x[i];
    var xMed;
    if (c1 === c0) {
      xMed = x0;
    } else {
      var t = (0.5 - c0) / (c1 - c0);
      xMed = x0 + t * (x1 - x0);
    }
    return (xMed - low) / (high - low);
  }

  root.lossPreviewMath = {
    erf: erf,
    normCdf: normCdf,
    normInv: normInv,
    fitLognormal: fitLognormal,
    fitP50P95: fitP50P95,
    fitP5P95: fitP5P95,
    lognormalStats: lognormalStats,
    capPertFromFit: capPertFromFit,
    pertStats: pertStats,
    vosePertAlphaBeta: vosePertAlphaBeta,
    pertDensityPath: pertDensityPath,
    cdfGrid: cdfGrid,
    medianPosFromGrid: medianPosFromGrid,
  };
})();
