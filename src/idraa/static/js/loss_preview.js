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
 * mu=ln(250k)/cap=300k: <0.02% at authored sigma <= 3.5 (0.017% at 3.5),
 * ~0.43% at sigma=6.3, ~15% at sigma=8.2, NON-monotone just under the
 * null threshold — up to ~90% at sigma=8.39, where Phi(b-sigma) quantizes
 * at 5.55e-17 — and null >= 8.4 via the underflow guard) —
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
        medianCapped: null,
        p95Capped: null,
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
    var medianCapped = null;
    var p95Capped = null;
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
        // M1 (PR3 T2.a gate fix): medianCapped/p95Capped via the SAME
        // q*Phi(b) closed form as p99Capped -- the quantile of X | X<=cap
        // at probability q is the UNCAPPED quantile at q*Phi(b) (the
        // capped CDF is Phi(z)/Phi(b) for z<=b, so inverting F_capped(x)=q
        // is exactly F_uncapped(x) = q*Phi(b)). Pre-fix, the numbers row
        // read UNTRUNCATED median/p95 beside a TRUNCATED mean/p99 --
        // basis-mixing across the same panel (executed repro: P95 $5.0M
        // rendered beside P99 $2.84M and cap $3.0M at sigma=1.189,
        // mu=ln(sqrt(100_000*5_000_000))).
        var medianCappedRaw = Math.exp(mu + sigma * normInv(0.5 * phiB));
        medianCapped = Number.isFinite(medianCappedRaw) ? medianCappedRaw : null;
        var p95CappedRaw = Math.exp(mu + sigma * normInv(0.95 * phiB));
        p95Capped = Number.isFinite(p95CappedRaw) ? p95CappedRaw : null;
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
      medianCapped: medianCapped,
      p95Capped: p95Capped,
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
  // routes/scenario_loss_pin.py:_stored_loss_sigma's PERT read: ln(high/low)/(2*Z95).
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

  // ===========================================================================
  // lossDispersionReadout — Alpine factory + SVG chart (sigma-recal PR3 Task 2,
  // D22). Everything below this line is CHART/UI GLUE, not math-core parity
  // surface: it composes the pinned functions above but is not itself
  // parity-pinned by tests/unit/test_loss_preview_parity.py (there is no
  // fair_cam analog of "an SVG path string" to pin against). The underlying
  // statistics it reads (lognormalStats/capPertFromFit/pertStats/cdfGrid/
  // medianPosFromGrid) ARE pinned above.
  // ===========================================================================

  // Scenario-ALE composition store (Task 2 Interfaces): each mounted readout
  // publishes its OWN field's engine-realized mean (+ the epistemic "claim"
  // that mean carries, used for the ALE line's state-dependent label) here,
  // keyed by fieldKey ("pl"/"sl"). Registered via alpine:init so it exists
  // before any x-data walks the DOM (guarded — this file also runs inside a
  // bare `node` parity-test subprocess with no `document`/`Alpine`).
  if (typeof document !== "undefined") {
    document.addEventListener("alpine:init", function () {
      if (typeof Alpine === "undefined") return;
      Alpine.store("lossPreview", {
        pl: null,
        sl: null,
        claims: { pl: null, sl: null },
        // PR-gate M-2: true when the published mean is one previewed SME
        // row's mean out of a multi-row fieldset — the composed ALE label
        // must disclose the row scope (client-side pooling is out of
        // scope per D22; the engine-realized field mean pools at finalize).
        rowScoped: { pl: null, sl: null },
      });
    });
  }

  // Waterline drag clamp (T1-gate NTH-1 precedent applied here too): normInv
  // and the grid inversion below are IEEE-conventional and return +/-Infinity
  // / NaN at the p=0/p=1 limits, so any probability derived from a pointer
  // position is clamped into this band before use.
  var _WATERLINE_P_MIN = 0.001;
  var _WATERLINE_P_MAX = 0.999;

  // N7 (PR3 T2.a gate fix): the pl/sl floor
  // fair_cam.quantile_pooling.clean_quantile_pair applies at finalize time
  // (floor = 1000.0 -- see that module's `elif fieldset in ("pl", "sl"):`
  // branch). Mirrored here as an ADVISORY CONDITION ONLY, never a
  // transform of the analyst's entered values.
  var PL_SL_FLOOR = 1000.0;

  // Fixed chart geometry (viewBox units, not CSS pixels — the <svg> scales
  // via preserveAspectRatio="none" + a CSS width/height, matching the
  // house macros/chart.html convention of a server/JS-fixed viewBox).
  var _CHART_W = 600;
  var _CHART_H = 160;
  var _CHART_PAD_L = 8;
  var _CHART_PAD_R = 8;
  var _CHART_Y_PAD = 4;

  function _num(v) {
    if (typeof v === "number") return v;
    if (v === null || v === undefined) return NaN;
    return parseFloat(String(v).replace(/,/g, ""));
  }

  // Standard lognormal PDF. Chart-rendering-only (not part of the pinned
  // math core above): used to draw the lognormal-mode density curve. The
  // formula itself is the textbook lognormal density, not a fair_cam mirror
  // needing a parity test.
  function _lognormalPdf(x, mu, sigma) {
    if (!(x > 0) || !Number.isFinite(mu) || !Number.isFinite(sigma) || !(sigma > 0)) {
      return 0;
    }
    var z = (Math.log(x) - mu) / sigma;
    return Math.exp(-0.5 * z * z) / (x * sigma * Math.sqrt(2 * Math.PI));
  }

  // M7 (PR3 T2.a gate fix): dollar VALUE -> probability, the INVERSE
  // direction of the pre-fix `_gridValueAt` this replaces. The waterline
  // now maps pointer-x to a dollar value via the axis's own log scale
  // FIRST (the handle-stays-under-the-cursor fix), then looks up that
  // value's probability here for the readout text — never the reverse
  // (treating raw pixel-fraction as if it WERE a probability, the pre-fix
  // bug: a log-dollar axis is not linear in probability, so pixel-fraction
  // and probability only coincide by coincidence). Kept private — only the
  // chart's waterline needs it, unlike medianPosFromGrid which Task 1's
  // parity harness also exercises directly.
  function _gridProbAt(grid, pert, value) {
    if (!grid || !pert || !Number.isFinite(value)) return null;
    var x = grid.x,
      cdf = grid.cdf;
    var low = pert.low,
      high = pert.high;
    if (!Number.isFinite(low) || !Number.isFinite(high) || !(high > low)) return null;
    if (value <= low) return 0;
    if (value >= high) return 1;
    var i = 1;
    while (i < x.length && x[i] < value) i++;
    if (i >= x.length) i = x.length - 1;
    if (i < 1) i = 1;
    var x0 = x[i - 1],
      x1 = x[i];
    var c0 = cdf[i - 1],
      c1 = cdf[i];
    if (x1 === x0) return c0;
    var t = (value - x0) / (x1 - x0);
    return c0 + t * (c1 - c0);
  }

  // Lognormal-mode chart data: density path over a fixed [-3z, Z99] window
  // (widened to include the cap when the cap sits beyond that window),
  // median/mean marker pixel positions, and — when a cap is within the
  // drawn axis range — a cap boundary line + a shaded "clipped tail" area
  // (the mass beyond the cap, per the mode-honest chart contract).
  //
  // `medianVal`/`meanVal` (M1, PR3 T2.a gate fix): the CALLER passes
  // whichever basis the numbers row is displaying -- medianCapped/
  // meanCapped when a cap is present, the raw median/mean otherwise -- so
  // the chart markers never disagree with the panel's own numbers (pre-fix:
  // the marker always used the untruncated Math.exp(mu)/Math.exp(mu+sigma^2/2)
  // even when the numbers row showed the truncated basis, so e.g. the mean
  // marker could land visibly inside the clipped tail while the Mean cell
  // read a small in-range dollar figure). Both fall back to the untruncated
  // value when the truncated one is non-finite (deep-tail A&S underflow —
  // see lognormalStats' own B-I2/B-I1 guards) so the chart still draws
  // SOMETHING rather than a NaN-positioned line.
  function _lognormalChartData(mu, sigma, cap, medianVal, meanVal) {
    var lo = Math.exp(mu - sigma * 3);
    var hi = Math.exp(mu + sigma * Z99);
    if (cap !== null && cap > hi) hi = cap * 1.05;
    // Re-gate N-b (T2.c form): a cap OR truncated marker below the default
    // axis window would otherwise render off-canvas while every cell shows
    // the truncated basis — widen the window downward far enough that the
    // cap line AND both truncated markers stay visible (the T2.b micro-gate
    // executed the cap-only widening leaving medianPx/meanPx at -0.26/-4.77
    // in the p5=$5M/p95=$50M/cap=$1M case; marker-aware widening puts them
    // at 25.6/21.3).
    if (cap !== null && cap < lo) lo = cap * 0.95;
    if (medianVal !== null && Number.isFinite(medianVal) && medianVal * 0.9 < lo)
      lo = medianVal * 0.9;
    if (meanVal !== null && Number.isFinite(meanVal) && meanVal * 0.9 < lo) lo = meanVal * 0.9;
    if (!Number.isFinite(lo) || !Number.isFinite(hi) || !(hi > lo) || !(lo > 0)) return null;
    var n = 128;
    var logLo = Math.log(lo),
      logHi = Math.log(hi);
    var xs = new Array(n);
    var density = new Array(n);
    var maxD = 0;
    var i, x, d;
    for (i = 0; i < n; i++) {
      x = Math.exp(logLo + (i / (n - 1)) * (logHi - logLo));
      d = _lognormalPdf(x, mu, sigma);
      xs[i] = x;
      density[i] = d;
      if (Number.isFinite(d) && d > maxD) maxD = d;
    }
    function toPx(xv) {
      var t = (Math.log(xv) - logLo) / (logHi - logLo);
      return _CHART_PAD_L + t * (_CHART_W - _CHART_PAD_L - _CHART_PAD_R);
    }
    function toPy(dv) {
      var t = maxD > 0 && Number.isFinite(dv) ? dv / maxD : 0;
      return _CHART_H - _CHART_Y_PAD - t * (_CHART_H - 2 * _CHART_Y_PAD);
    }
    var path = "";
    for (i = 0; i < n; i++) {
      path += (i === 0 ? "M " : "L ") + toPx(xs[i]).toFixed(2) + " " + toPy(density[i]).toFixed(2) + " ";
    }
    var capPx = null;
    var clippedTailPath = null;
    if (cap !== null && cap >= lo && cap <= hi) {
      capPx = toPx(cap);
      var tail = "M " + capPx.toFixed(2) + " " + (_CHART_H - _CHART_Y_PAD).toFixed(2) + " ";
      for (i = 0; i < n; i++) {
        if (xs[i] < cap) continue;
        tail += "L " + toPx(xs[i]).toFixed(2) + " " + toPy(density[i]).toFixed(2) + " ";
      }
      tail += "L " + toPx(hi).toFixed(2) + " " + (_CHART_H - _CHART_Y_PAD).toFixed(2) + " Z";
      clippedTailPath = tail;
    }
    var medianForChart = Number.isFinite(medianVal) ? medianVal : Math.exp(mu);
    var meanForChart = Number.isFinite(meanVal) ? meanVal : Math.exp(mu + (sigma * sigma) / 2);
    return {
      axisLow: lo,
      axisHigh: hi,
      densityPath: path.trim(),
      clippedTailPath: clippedTailPath,
      medianPx: toPx(medianForChart),
      meanPx: toPx(meanForChart),
      capPx: capPx,
    };
  }

  // Capped-PERT-mode chart data: reuses pertDensityPath/medianPosFromGrid
  // directly (the SAME grid the wizard stores through) — realized-median +
  // sampled-mean marker positions, NEVER a cap line (the engine never caps
  // PERT; the PERT `high` IS the bound).
  function _pertChartData(pert, grid) {
    var dp = pertDensityPath(pert);
    if (!dp) return null;
    var xs = dp.x,
      density = dp.density;
    var n = xs.length;
    var logLo = Math.log(pert.low),
      logHi = Math.log(pert.high);
    var maxD = 0;
    var i;
    for (i = 0; i < n; i++) {
      if (Number.isFinite(density[i]) && density[i] > maxD) maxD = density[i];
    }
    function toPx(xv) {
      var t = (Math.log(xv) - logLo) / (logHi - logLo);
      return _CHART_PAD_L + t * (_CHART_W - _CHART_PAD_L - _CHART_PAD_R);
    }
    function toPy(dv) {
      // +Infinity endpoint (alpha<1/beta<1 singularity) caps at the chart's
      // own y-max, per pertDensityPath's own documented endpoint contract.
      var v = Number.isFinite(dv) ? dv : maxD;
      var t = maxD > 0 ? Math.min(1, v / maxD) : 0;
      return _CHART_H - _CHART_Y_PAD - t * (_CHART_H - 2 * _CHART_Y_PAD);
    }
    var path = "";
    for (i = 0; i < n; i++) {
      path += (i === 0 ? "M " : "L ") + toPx(xs[i]).toFixed(2) + " " + toPy(density[i]).toFixed(2) + " ";
    }
    var medPos = medianPosFromGrid(grid, pert);
    var realizedMedianX = medPos !== null ? pert.low + medPos * (pert.high - pert.low) : null;
    var ps = pertStats(pert);
    return {
      axisLow: pert.low,
      axisHigh: pert.high,
      densityPath: path.trim(),
      clippedTailPath: null,
      capPx: null,
      realizedMedianPx: realizedMedianX !== null ? toPx(realizedMedianX) : null,
      meanPx: ps.mean !== null ? toPx(ps.mean) : null,
    };
  }

  // root.lossDispersionReadout(cfg) — Alpine factory. Follows the
  // window.subFunctionCombobox single-file factory precedent
  // (sub_function_combobox.js:29): registered as a global so
  // x-data="lossDispersionReadout(cfg)" resolves directly, loaded non-defer
  // (Task 1 Step 5) so it is registered before the deferred Alpine bundle
  // walks x-data.
  //
  // cfg keys (server-rendered, `| tojson`'d into the template — see
  // templates/scenarios/_loss_readout.html): mode ("lognormal"|
  // "capped_pert"), quantileBasis ("p5p95"|"p50p95"), sigmaDefault,
  // warnThreshold, cap, currency, tefMean, vulnMean, fieldKey ("pl"|"sl"),
  // label, and the two seed-only extras initialLow/initialHigh (server
  // reads the field's last-persisted SME row so the readout is not blank on
  // first paint, before any row focus/blur has fired a loss-row-input
  // event — see routes/scenarios.py:_build_readout_cfg).
  //
  // Cross-component wiring: the SME-row grid (a SIBLING Alpine component in
  // _fair_params_form_inner.html, not an ancestor/descendant of this one)
  // dispatches a bubbling+window CustomEvent named "loss-row-input" with
  // {fieldset, idx, low, high} on each pl/sl row's focus/blur. This mount
  // listens via `@loss-row-input.window` (wired in the partial) and filters
  // on `detail.fieldset === cfg.fieldKey` — window-scoped because a DOM
  // ancestor-bubble path does not exist between siblings, and because two
  // mounts (pl + sl) share the page and must not cross-wire.
  root.lossDispersionReadout = function (cfg) {
    return {
      cfg: cfg,
      qLo: null,
      qHi: null,
      focusedRow: null,
      // M2 (PR3 T2.a gate fix): true only while `focusedRow` was set by
      // init()'s SEED read (the field's last-persisted row), never by a
      // real loss-row-input event. Drives the disclosure line's wording
      // ("previewing last saved row N" vs "previewing SME row N") so the
      // first-paint seed is attributed instead of silently unlabeled.
      seededFromInit: false,
      stats: { valid: false },
      waterlineProb: 0.5,
      waterlineValue: null,
      isDragging: false,
      _debounceHandle: null,
      // T3.a gate fix (METH I-3): true unless a LIVE `#entry_currency`
      // selector exists AND currently reads a non-USD code. The selector
      // only exists on the CREATE form (form.html) — the edit form and
      // wizard mounts have no such element, so this stays `true` there by
      // construction, matching "genuinely USD" for both (Global
      // Constraints: wizard elicits USD; edit-form entry currency is fixed
      // at creation). Gates the money cells / cap line / ceiling verdict in
      // _loss_readout.html; σ is NEVER gated by this (pure log-ratio of the
      // two typed quantiles, currency-free by construction).
      entryCurrencyIsUsd: true,

      init: function () {
        var c = this.cfg;
        if (
          c.initialLow !== null &&
          c.initialLow !== undefined &&
          c.initialHigh !== null &&
          c.initialHigh !== undefined
        ) {
          this.qLo = _num(c.initialLow);
          this.qHi = _num(c.initialHigh);
          // M2: attribute the seed to its row index (server-computed —
          // routes/scenarios.py:_build_readout_cfg's initialRowIndex, the
          // same last-row index initialLow/initialHigh were read from) so
          // the disclosure line renders from FIRST PAINT instead of staying
          // hidden until a focus/blur event fires.
          if (c.initialRowIndex !== null && c.initialRowIndex !== undefined) {
            this.focusedRow = c.initialRowIndex;
            this.seededFromInit = true;
          }
        }
        var entryCurrencyEl =
          typeof document !== "undefined" ? document.getElementById("entry_currency") : null;
        if (entryCurrencyEl) {
          this.entryCurrencyIsUsd = entryCurrencyEl.value === "USD";
        }
        this._recomputeNow();
      },

      // Called by the readout's own @loss-row-input.window listener.
      onRowEvent: function (detail) {
        if (!detail || detail.fieldset !== this.cfg.fieldKey) return;
        this.bindRow(detail.idx, detail.low, detail.high);
      },

      // Called by the readout's own @entry-currency-changed.window listener
      // (form.html's #entry_currency select dispatches this on @change).
      onEntryCurrencyChanged: function (detail) {
        this.entryCurrencyIsUsd = !detail || detail.value === "USD";
      },

      // Public per Task 2 Interfaces: wizard multi-SME row binding. Sets the
      // "previewing SME row N" label (idx is 0-based; the label adds 1).
      bindRow: function (idx, lo, hi) {
        this.focusedRow = idx;
        this.seededFromInit = false; // a real row event supersedes the seed.
        this.qLo = _num(lo);
        this.qHi = _num(hi);
        this.recompute();
      },

      // Debounced 150ms per Task 2 Interfaces (avoids re-fitting on every
      // keystroke of a fast typist).
      recompute: function () {
        var self = this;
        if (this._debounceHandle) clearTimeout(this._debounceHandle);
        this._debounceHandle = setTimeout(function () {
          self._recomputeNow();
        }, 150);
      },

      _recomputeNow: function () {
        var cfg = this.cfg;
        var out = { valid: false, mode: cfg.mode, warn: false };
        var qLo = this.qLo,
          qHi = this.qHi;
        if (qLo === null || qHi === null || !Number.isFinite(qLo) || !Number.isFinite(qHi)) {
          this.stats = out;
          this._publish(null, null);
          return;
        }
        var fit = cfg.quantileBasis === "p50p95" ? fitP50P95(qLo, qHi) : fitP5P95(qLo, qHi);
        if (fit.mu === null || fit.sigma === null || !(fit.sigma > 0)) {
          this.stats = out;
          this._publish(null, null);
          return;
        }
        out.valid = true;
        out.mu = fit.mu;
        out.sigma = fit.sigma;
        out.warn = fit.sigma > cfg.warnThreshold;
        // N7 (PR3 T2.a gate fix): mirrors the CONDITION of
        // fair_cam.quantile_pooling.clean_quantile_pair's pl/sl branch
        // (floor = 1000.0; a low/high below it is FLOORED to $1,000 at
        // finalize time) — advisory text only, never a client-side
        // transform of the analyst's entered values.
        out.floorAdvisory = qLo < PL_SL_FLOOR || qHi < PL_SL_FLOOR;

        if (cfg.mode === "lognormal") {
          var cap =
            cfg.cap !== null && cfg.cap !== undefined && Number.isFinite(cfg.cap) && cfg.cap > 0
              ? cfg.cap
              : null;
          var ln = lognormalStats({ mu: fit.mu, sigma: fit.sigma, cap: cap });
          out.cap = cap;
          out.median = ln.median;
          out.mean = ln.mean;
          out.p95 = ln.p95;
          out.p99 = ln.p99;
          out.meanCapped = ln.meanCapped;
          out.medianCapped = ln.medianCapped;
          out.p95Capped = ln.p95Capped;
          out.p99Capped = ln.p99Capped;
          out.capBindProb = ln.capBindProb;
          out.capClamped = ln.capClamped;
          // Capacity-ceiling state (Task 2 Interfaces I-M5): the derived
          // sigma at which the cap would sit AT the median (sigma above
          // this means the chokepoint rejects outright — distinct from,
          // and can sit below, the advisory 2.2 warn badge).
          if (cap !== null && ln.median !== null && ln.median > 0) {
            out.sigmaCeiling = Math.log(cap / ln.median) / Z95;
            out.ceilingExceeded = fit.sigma >= out.sigmaCeiling;
          } else {
            out.sigmaCeiling = null;
            out.ceilingExceeded = false;
          }
          // M1 (PR3 T2.a gate fix): the chart markers use the SAME basis as
          // the numbers row -- the truncated median/mean when a cap is
          // present, falling back to the untruncated value only when the
          // truncated one is unrepresentable (deep-tail underflow).
          var chartMedian = cap !== null ? ln.medianCapped : ln.median;
          var chartMean = cap !== null ? ln.meanCapped : ln.mean;
          out.chart = _lognormalChartData(fit.mu, fit.sigma, cap, chartMedian, chartMean);
          this.stats = out;
          // Scenario-ALE publish: capped lognormal -> the truncated mean
          // (the engine truncates draws at max); uncapped -> the raw mean.
          var meanToPublish = cap !== null ? out.meanCapped : out.mean;
          var claim =
            cap !== null ? (out.meanCapped !== null ? "capped_lognormal" : null) : "uncapped_lognormal";
          this._publish(meanToPublish, claim);
          return;
        }

        // capped_pert mode.
        var pert = capPertFromFit(fit.mu, fit.sigma);
        if (pert.low === null || !(pert.low > 0) || !(pert.high > pert.low)) {
          out.valid = false;
          this.stats = out;
          this._publish(null, null);
          return;
        }
        var ps = pertStats(pert);
        var grid = cdfGrid(pert);
        var medPos = grid ? medianPosFromGrid(grid, pert) : null;
        out.pertLow = pert.low;
        out.pertMode = pert.mode;
        out.pertHigh = pert.high;
        out.pertMean = ps.mean;
        out.impliedSigma = ps.impliedSigma;
        // Realized median: the STORED BetaPERT's own median, from the same
        // numeric CDF grid the wizard persists through — NEVER the
        // analyst's entered median (they diverge up to 1.9x at sigma=1.7,
        // T1-gate B-N7). Labeled as such in the template, not just here.
        out.realizedMedian = medPos !== null ? pert.low + medPos * (pert.high - pert.low) : null;
        out.grid = grid;
        out.pert = pert;
        out.chart = grid ? _pertChartData(pert, grid) : null;
        this.stats = out;
        this._publish(ps.mean, ps.mean !== null ? "pert_bounded" : null);
      },

      _publish: function (mean, claim) {
        var store = this.$store && this.$store.lossPreview;
        if (!store) return;
        var key = this.cfg.fieldKey;
        var value = typeof mean === "number" && Number.isFinite(mean) ? mean : null;
        store[key] = value;
        store.claims[key] = value !== null ? claim : null;
        // initialRowIndex = rows.length - 1, so > 0 iff the fieldset has
        // more than one SME row (null/undefined compare false).
        store.rowScoped[key] = value !== null && this.cfg.initialRowIndex > 0;
      },

      // M7 (PR3 T2.a gate fix): pointer x -> DOLLAR VALUE via the axis's own
      // log-linear scale (the exact inverse of the chart's toPx/waterlinePx
      // mapping), THEN dollar -> probability via normCdf (lognormal, with
      // the M1 Phi(b) adjustment when a cap is present) or the CDF grid
      // (capped_pert). Pre-fix, pixel-fraction was treated AS the
      // probability directly and fed straight into normInv/grid-inversion
      // to derive a dollar value -- correct only on an axis linear in
      // probability, which this log-dollar axis never is, so the rendered
      // handle drifted away from the actual cursor position. Routing
      // through the SAME log scale waterlinePx already uses for the
      // opposite direction keeps the handle glued under the cursor.
      // Clamp stays [0.001, 0.999] (same constants as before), now applied
      // to the axis FRACTION (keeps the handle inside the viewBox) rather
      // than to a probability.
      onWaterlineDrag: function (event) {
        var stats = this.stats;
        if (!stats || !stats.valid || !stats.chart) return;
        var svg = this.$refs.svg;
        if (!svg || typeof svg.getBoundingClientRect !== "function") return;
        var rect = svg.getBoundingClientRect();
        if (!(rect.width > 0)) return;
        var clientX =
          event.touches && event.touches.length ? event.touches[0].clientX : event.clientX;
        if (typeof clientX !== "number") return;
        var frac = (clientX - rect.left) / rect.width;
        // Re-gate N-a: the cursor fraction spans the FULL svg width, but the
        // axis occupies [PAD_L, W - PAD_R] -- undo the padding before the log
        // interpolation so the handle sits under the cursor (T2.b micro-gate
        // executed: worst in-axis drift 7.79px -> 0.58px; the residual is the
        // [0.001, 0.999] probability clamp, not the pad). DELIBERATE
        // exception: right of the cap the I1 clamp pins the handle at capPx,
        // detaching it from the cursor -- the truncated supremum outranks
        // cursor-glue there.
        frac =
          (frac * _CHART_W - _CHART_PAD_L) / (_CHART_W - _CHART_PAD_L - _CHART_PAD_R);
        frac = Math.max(_WATERLINE_P_MIN, Math.min(_WATERLINE_P_MAX, frac));
        var chart = stats.chart;
        var logLo = Math.log(chart.axisLow);
        var logHi = Math.log(chart.axisHigh);
        var value = Math.exp(logLo + frac * (logHi - logLo));
        if (!Number.isFinite(value)) {
          this.waterlineValue = null;
          this.waterlineProb = null;
          return;
        }
        // Re-gate I1: under the engine's truncation the distribution's
        // supremum IS the cap -- a dollar value above it is a quantile
        // statement that cannot be true (the numbers-row contract M1 just
        // established, applied to the waterline). Clamp before display.
        if (
          stats.mode === "lognormal" &&
          stats.cap !== null &&
          stats.cap !== undefined &&
          Number.isFinite(stats.cap) &&
          value > stats.cap
        ) {
          value = stats.cap;
        }
        this.waterlineValue = value;
        var prob = null;
        if (stats.mode === "lognormal") {
          var z = (Math.log(value) - stats.mu) / stats.sigma;
          var p = normCdf(z);
          if (stats.cap !== null && stats.cap !== undefined && Number.isFinite(stats.cap)) {
            // M1: the capped distribution's CDF at value<=cap is
            // Phi(z)/Phi(b) (the SAME Phi(b) normalization the numbers-row
            // fix applies) -- values beyond the cap never occur, so a
            // pointer dragged past the cap line stays pinned at p=1.
            var b = (Math.log(stats.cap) - stats.mu) / stats.sigma;
            var phiB = normCdf(b);
            prob = phiB > 0 ? Math.min(1, p / phiB) : p;
          } else {
            prob = p;
          }
        } else if (stats.grid && stats.pert) {
          prob = _gridProbAt(stats.grid, stats.pert, value);
        }
        this.waterlineProb = prob !== null && Number.isFinite(prob) ? prob : null;
      },

      get waterlinePx() {
        if (this.waterlineValue === null || !this.stats || !this.stats.chart) return null;
        var chart = this.stats.chart;
        var t =
          (Math.log(this.waterlineValue) - Math.log(chart.axisLow)) /
          (Math.log(chart.axisHigh) - Math.log(chart.axisLow));
        return _CHART_PAD_L + t * (_CHART_W - _CHART_PAD_L - _CHART_PAD_R);
      },

      // Scenario-ALE composition (Task 2 Interfaces): tefMean x vulnMean x
      // (store.pl + store.sl), with ONE state-dependent label that degrades
      // to the WEAKEST contributing claim — uncapped lognormal (no bound at
      // all on the tail) is weaker than PERT-bounded, which is weaker than
      // every contributor being a capacity-truncated mean.
      //
      // M5 (PR3 T2.a gate fix): PL is a REQUIRED fieldset (the wizard never
      // finalizes without it), so an absent PL mean means the preview is
      // INCOMPLETE, not zero -- publishing a partial ALE that silently
      // drops PL would understate the loss, not degrade honestly. SL is
      // OPTIONAL: an absent SL mean correctly contributes 0 to the sum
      // (unchanged from before).
      get aleLine() {
        var cfg = this.cfg;
        if (
          cfg.tefMean === null ||
          cfg.tefMean === undefined ||
          cfg.vulnMean === null ||
          cfg.vulnMean === undefined
        ) {
          return null;
        }
        var store = this.$store && this.$store.lossPreview;
        if (!store) return null;
        var plOk = typeof store.pl === "number" && Number.isFinite(store.pl);
        var slOk = typeof store.sl === "number" && Number.isFinite(store.sl);
        if (!plOk) return null;
        var claims = [];
        if (plOk) claims.push(store.claims.pl);
        if (slOk) claims.push(store.claims.sl);
        var sumLm = (plOk ? store.pl : 0) + (slOk ? store.sl : 0);
        var value = cfg.tefMean * cfg.vulnMean * sumLm;
        var label;
        if (claims.indexOf("uncapped_lognormal") !== -1) {
          label = "mean basis (uncapped)";
        } else if (claims.indexOf("pert_bounded") !== -1) {
          label = "mean basis (PERT-bounded)";
        } else {
          label = "capacity-bounded mean basis";
        }
        // PR-gate M-2: each mount publishes its PREVIEWED row's mean, not
        // the pooled field mean — on a multi-row fieldset the composed ALE
        // is row-scoped and the label must say so (same field-vs-row
        // scoping principle as the T2 M3(b) ceiling-warning re-scope).
        var rowScoped =
          store.rowScoped &&
          ((plOk && store.rowScoped.pl) || (slOk && store.rowScoped.sl));
        if (rowScoped) {
          label += ", previewed row only";
        }
        return { value: value, label: label };
      },

      fmtMoney: function (v) {
        if (v === null || v === undefined || !Number.isFinite(v)) return "—";
        var sym = this.cfg.currency === "USD" ? "$" : this.cfg.currency + " ";
        var abs = Math.abs(v);
        if (abs >= 1e9) return sym + (v / 1e9).toFixed(1).replace(/\.0$/, "") + "B";
        if (abs >= 1e6) return sym + (v / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
        if (abs >= 1e3) return sym + (v / 1e3).toFixed(0) + "K";
        return sym + Math.round(v).toLocaleString("en-US");
      },

      fmtSigma: function (v) {
        return v === null || v === undefined || !Number.isFinite(v) ? "—" : v.toFixed(2);
      },
    };
  };
})();
