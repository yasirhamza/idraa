"""Derives per-sdlog PERT-fidelity tolerance bounds for MD-2. Per Meth-7 R2
+ Meth-10 R3: 1.5x observed max-deviation as engineering safety margin;
MC SE reported separately for transparency.

Meth-2 T1 review fix: reference samples are drawn from the FITTED
TRUNCATED LOGNORMAL (matching _qlnormtrunc semantics in
fair_cam/quantile_pooling/_lognormal.py), NOT the raw untruncated
lognormal. The PERT collapser is approximating the fitted truncated
distribution; comparing PERT samples to raw-lognormal samples is
apples-to-oranges and produced wildly inflated deviations (sdlog=2.0
previously hit ~1132% — the inflation was an artifact of the wrong
reference, not a real PERT-fidelity defect)."""

from __future__ import annotations

import json
import math
import pathlib

import numpy as np
from fair_cam.quantile_pooling import LogNormalTruncFit, lognormal_to_pert_approx
from scipy.stats import truncnorm

SDLOGS = [0.25, 0.5, 1.0, 1.5, 2.0]
MEANLOGS = [2.0, 4.0, 6.0]
N_SAMPLES = 100_000
RNG = np.random.default_rng(seed=42)


def _truncated_lognormal_samples(
    meanlog: float,
    sdlog: float,
    min_support: float,
    max_support: float,
    n: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """Draw from the same fitted-truncated-lognormal that _qlnormtrunc
    represents. Mirrors EnvStats::qlnormTrunc semantics: truncated normal
    on the log scale, then exp-transform back."""
    a = (math.log(max(min_support, 1e-300)) - meanlog) / sdlog
    b = math.inf if math.isinf(max_support) else (math.log(max_support) - meanlog) / sdlog
    trunc_log_samples = truncnorm.rvs(
        a,
        b,
        loc=meanlog,
        scale=sdlog,
        size=n,
        random_state=rng,
    )
    return np.exp(trunc_log_samples)


def main() -> None:
    results: dict = {"per_sdlog": {}}
    for sdlog in SDLOGS:
        deviations: list[float] = []
        for meanlog in MEANLOGS:
            fit = LogNormalTruncFit(
                meanlog=meanlog,
                sdlog=sdlog,
                min_support=0.0,
                max_support=math.inf,
            )
            pert, _ = lognormal_to_pert_approx(fit)
            mean = (pert.low + 4 * pert.mode + pert.high) / 6
            alpha = 6 * ((mean - pert.low) / (pert.high - pert.low))
            beta = 6 * ((pert.high - mean) / (pert.high - pert.low))
            beta_samples = RNG.beta(alpha, beta, size=N_SAMPLES)
            pert_samples = pert.low + beta_samples * (pert.high - pert.low)
            # Meth-2 T1: sample from the fitted TRUNCATED lognormal, not the
            # raw untruncated lognormal.
            ln_samples = _truncated_lognormal_samples(
                meanlog=meanlog,
                sdlog=sdlog,
                min_support=fit.min_support,
                max_support=fit.max_support,
                n=N_SAMPLES,
                rng=RNG,
            )
            for p in (0.05, 0.95):
                pert_q = float(np.percentile(pert_samples, p * 100))
                ln_q = float(np.percentile(ln_samples, p * 100))
                rel_dev = abs(pert_q - ln_q) / max(abs(ln_q), 1e-9)
                deviations.append(rel_dev)
        observed_max = max(deviations)
        mc_se_approx = 1.0 / math.sqrt(N_SAMPLES)
        results["per_sdlog"][str(sdlog)] = {
            "observed_max_deviation": observed_max,
            "mc_se_approx": mc_se_approx,
            "bound_with_1_5x_safety": 1.5 * observed_max,
        }
    out = pathlib.Path("fair_cam/tests/quantile_pooling/fixtures/pert_fidelity_bounds.json")
    out.write_text(json.dumps(results, indent=2))
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
