"""NetDiligence Cyber Claims Study 2025 — sigma consistency + anchor reference bands.

Transcribed literals from the licensed PDF (docs/reference/, UNTRACKED —
gitignored + *.pdf deny-globbed; owner-attested transcription, O-RA
precedent). Each table cites its PRINTED page number. Conditioning register:
claims-process-visible incident cost, NOT FAIR loss magnitude — see
docs/reference/calibration-sources/netdiligence_2025.md.

Populations (p.7): demographic analyses use all 10,402 claims; COST analyses
use the 9,171 claims that reported incident cost >= $1,000. Every table this
script reads is a cost table (>= $1K population). Table-9 N differs from the
body figures' N for the same causes (wire fraud 260 here vs Figure 5/36's
438) along an axis the report does NOT state: Table 9's claims column sums
to 8,936 (= Figure 9's SME N) while Figure 5 declares N=8,278 — two
different populations, not the demographic-vs->=$1K split. Table 9's row is
self-consistent (its n pairs with its max), so it is the one used.

THREE estimators per row, all printed:

1. QUANTILE PLUG-IN (labeled exactly that — NOT E[max]): substitute the
   observed max at the (1 - 1/n) quantile of a lognormal with
   mu = ln(mean) - sigma^2/2 (mean-anchored), giving
       sigma^2 - 2*z_n*sigma + 2*ln(max/mean) = 0,  z_n = norminv(1 - 1/n).
   Both roots printed; the SMALLER is selected on branch grounds: the
   quantile exponent f(sigma) = z_n*sigma - sigma^2/2 turns over at
   sigma = z_n, so the larger root sits on the DECREASING branch where
   higher dispersion lowers the modeled max — not a physically sensible
   read. Bias is n-DEPENDENT: at large n the plug-in over-reads sigma
   (E[max] exceeds the (1-1/n) quantile); at small n the z_n ceiling plus
   root-conditional truncation flip the REALIZED bias LOW (each printed
   band shows its own median-vs-truth offset). A negative discriminant is
   "no root under the quantile plug-in" — a plug-in artifact, NEVER
   evidence the data is heavier than lognormal.
2. EXACT E[max] root: solve E[max of n iid LN(mu, sigma)] == observed max
   with the same mean anchor, via h(sigma) = ln E[e^(sigma*M_n)]
   - sigma^2/2 - ln(max/mean) = 0 where M_n = max of n iid N(0,1) with
   density n*phi(x)*Phi(x)^(n-1) (scipy quad + brentq). Deterministic.
   Bracket guard: h is bounded above by ln n, so a root exists iff
   max/mean < n — guarded, reported honestly when absent.
3. PER-BAND SAMPLING BAND at truth sigma = 1.7 (seeded MC): p5/p50/p95 of
   the plug-in implied sigma over REPS reps, ALWAYS printed with that
   band's spurious-no-root rate (the band is conditional on a root
   existing) and the structural z_n ceiling (smaller root <= z_n, so
   small-n bands mechanically cannot express large sigma). The band
   describes the PLUG-IN estimator only. The MC draws an untruncated
   lognormal while the register names the >=$1K filter as live selection —
   negligible on the included bands (mean share below $1K <= 0.01%;
   nano ~0.0083%); stated in the output.

Output at 2-3 significant figures — four-decimal sigma from a max statistic
is false precision; the per-band width is the message.
"""

from __future__ import annotations

import math
import sys

import numpy as np
from scipy import integrate
from scipy.optimize import brentq
from scipy.stats import norm

SEED = 20260730
REPS = 2000
SIGMA_TRUE = 1.7  # WITHIN_SCENARIO_SIGMA_DEFAULT (services/calibration.py)

# Table 3 (printed p.52): Incident Cost by Revenue Size, claims >= $1K,
# 2020-2024. ALL SEVEN rows transcribed (silent row-dropping makes the
# reading selection-dependent). (band, n_claims, mean_usd, max_usd)
REVENUE_BANDS: list[tuple[str, int, float, float]] = [
    ("nano_lt_50m", 4_009, 142_000.0, 10_400_000.0),
    ("micro_50m_300m", 1_775, 374_000.0, 25_000_000.0),
    ("small_300m_2b", 508, 2_000_000.0, 108_000_000.0),
    ("mid_2b_10b", 187, 5_100_000.0, 268_000_000.0),
    ("large_10b_100b", 43, 30_500_000.0, 503_500_000.0),
    ("mega_gt_100b", 4, 38_300_000.0, 75_000_000.0),
    ("unknown_rev", 2_645, 47_000.0, 2_700_000.0),
]

# Named, test-asserted exclusions from the sigma read (printed with reasons;
# anchor-set-exclusion precedent, within-scenario-sigma-calibration.md):
SIGMA_READ_EXCLUDED: dict[str, str] = {
    "mega_gt_100b": "n=4 with a $10.6M minimum — a differently-truncated population",
    "unknown_rev": "no revenue conditioning, which is the entire point of the per-band read",
}

# Table 9 (printed p.59): Incident Cost by Cause of Loss — SMEs, claims
# >= $1K (the >=$1K qualifier is stated on p.7, not the table subtitle).
CAUSE_ROWS_SME: list[tuple[str, int, float, float]] = [
    ("business_email_compromise", 1_864, 98_000.0, 30_000_000.0),
    ("ransomware", 2_571, 631_000.0, 108_000_000.0),
    ("hacker", 1_191, 135_000.0, 22_000_000.0),
    ("wire_transfer_fraud", 260, 178_000.0, 3_800_000.0),
    ("theft_of_money", 834, 38_000.0, 500_000.0),
]

# [B-ND-REF] mapping: NetD cause class -> library scenario classes. Reference
# bands ONLY — direction vs FAIR loss magnitude NOT established (loss-form
# blindness pushes NetD BELOW FAIR loss; claim-reporting selection + the
# >=$1K filter push it ABOVE; Table 9 is SME-conditioned). A library
# per-event mean far below its mapped class mean prompts a DOCUMENTED
# REVIEW, never an automatic violation. NEVER percentile anchors.
REF_CLASS_MAP: list[tuple[str, str]] = [
    ("business_email_compromise", "vendor/BEC mean-hold family (IC3 $123,005 anchor)"),
    ("ransomware", "intrusion/ransomware-class catastrophic entries"),
    ("wire_transfer_fraud", "fraud-transfer entries"),
]


# Fail-loud transcription pins — a drifted edit dies here, not in a doc
# (SystemExit, not assert: scripts/** has no S101 allowance and these must
# fire under -O too; same idiom as surface_map.py's guards).
def _die(msg: str) -> None:
    raise SystemExit(f"NETD TRANSCRIPTION PIN FAILED: {msg}")


if len(REVENUE_BANDS) != 7:
    _die("Table 3 has SEVEN rows; do not drop any silently")
if len(CAUSE_ROWS_SME) != 5:
    _die("five transcribed cause rows expected")
if set(SIGMA_READ_EXCLUDED) != {"mega_gt_100b", "unknown_rev"}:
    _die("exclusion set drifted")
for _name, _n, _mean, _max in REVENUE_BANDS + CAUSE_ROWS_SME:
    if not (_n > 0 and 0 < _mean < _max):
        _die(f"transcription sanity failed: {_name}")


def z_of(n: int) -> float:
    return float(norm.ppf(1.0 - 1.0 / n))


def implied_sigma_roots(n: int, mean: float, max_: float) -> tuple[float, float] | None:
    """Quantile PLUG-IN roots (NOT E[max]) — biased HIGH; None = plug-in artifact."""
    z = z_of(n)
    disc = z * z - 2.0 * math.log(max_ / mean)
    if disc < 0:
        return None
    r = math.sqrt(disc)
    return (z - r, z + r)


def _ln_e_exp_sigma_max(sigma: float, n: int) -> float:
    """ln E[e^(sigma*M_n)], M_n = max of n iid N(0,1); quad over +/-12 sd."""
    val, _err = integrate.quad(
        lambda x: math.exp(sigma * x) * n * norm.pdf(x) * norm.cdf(x) ** (n - 1),
        -12.0,
        12.0 + 2.0 * sigma,
        limit=200,
    )
    return math.log(val)


def exact_emax_sigma_root(n: int, mean: float, max_: float) -> float | None:
    """Root of ln E[e^(sigma*M_n)] - sigma^2/2 = ln(max/mean). None iff max/mean >= n."""
    ratio = max_ / mean
    if ratio >= n:  # h(sigma) is bounded above by ln n — no sign change exists
        return None

    def h(sigma: float) -> float:
        return _ln_e_exp_sigma_max(sigma, n) - sigma * sigma / 2.0 - math.log(ratio)

    lo, hi = 1e-6, 1.0
    while h(hi) < 0.0:
        hi *= 2.0
        # sup h = ln n is approached only ASYMPTOTICALLY, so ratio -> n from
        # below can push the root arbitrarily high; 64 is a practical stop
        # for the shipped data (largest ratio/n: mega 1.96/4), not a theorem.
        if hi > 64.0:
            return None
    return float(brentq(h, lo, hi, xtol=1e-10))


def _band_samples(
    n: int, sigma_true: float = SIGMA_TRUE, seed: int = SEED, reps: int = REPS
) -> tuple[np.ndarray, float, float]:
    """Sorted plug-in implied-sigma samples at truth sigma_true + no-root rate + z.

    Scale-free (mu=0). One draw serves both the band quantiles and the
    observed-read percentile so they can never disagree.
    """
    rng = np.random.default_rng(seed)
    z = z_of(n)
    implied: list[float] = []
    no_root = 0
    for _ in range(reps):
        draws = np.exp(sigma_true * rng.standard_normal(n))
        ratio = float(draws.max() / draws.mean())
        disc = z * z - 2.0 * math.log(ratio)
        if disc < 0:
            no_root += 1
            continue
        implied.append(z - math.sqrt(disc))
    return np.sort(np.asarray(implied)), no_root / reps, z


def sampling_band(
    n: int, sigma_true: float = SIGMA_TRUE, seed: int = SEED, reps: int = REPS
) -> dict[str, float]:
    """p5/p50/p95 of the plug-in implied sigma at truth sigma_true + no-root rate.

    Conditional on a root existing — the no-root rate is part of the result,
    never dropped.
    """
    arr, no_root_rate, z = _band_samples(n, sigma_true, seed, reps)

    def q(p: float) -> float:
        return float(arr[min(len(arr) - 1, int(p * len(arr)))]) if len(arr) else float("nan")

    return {
        "p5": q(0.05),
        "p50": q(0.50),
        "p95": q(0.95),
        "no_root_rate": no_root_rate,
        "z_ceiling": z,
    }


def observed_percentile_in_band(n: int, observed: float) -> float:
    """Fraction of the truth-sigma band samples <= the observed plug-in read.

    The EVIDENTIAL direction (plan-gate B3-2): where does the OBSERVED read
    sit inside the estimator's sampling distribution at truth sigma=1.7 —
    never "the band contains 1.7", which is circular by construction.
    """
    arr, _rate, _z = _band_samples(n)
    if len(arr) == 0:
        return float("nan")
    return float(np.searchsorted(arr, observed) / len(arr))


def _fmt(x: float | None, sf: int = 3) -> str:
    if x is None:
        return "no root (plug-in artifact)"
    return f"{x:.{sf - 1}f}" if x >= 1 else f"{x:.{sf}g}"


def _row_block(name: str, n: int, mean: float, max_: float, excluded: str | None) -> list[str]:
    out = [f"  {name}  (n={n}, mean=${mean:,.0f}, max=${max_:,.0f})"]
    if excluded is not None:
        out.append(f"      EXCLUDED from the sigma read: {excluded}")
        return out
    roots = implied_sigma_roots(n, mean, max_)
    if roots is None:
        out.append(
            "      plug-in: no root under the quantile plug-in (artifact of the"
            " plug-in, never tail-heaviness)"
        )
    else:
        out.append(
            f"      plug-in roots: {_fmt(roots[0])} / {_fmt(roots[1])} "
            f"(smaller selected: past the sigma=z_n turnover the (1-1/n) quantile "
            f"DECREASES as sigma rises, so the larger root sits on the decreasing "
            f"branch — not a physically sensible read of a max)"
        )
    exact = exact_emax_sigma_root(n, mean, max_)
    out.append(f"      exact-E[max] root: {_fmt(exact)} (point read; no sampling band computed)")
    band = sampling_band(n)
    realized_bias = band["p50"] - SIGMA_TRUE
    out.append(
        f"      plug-in sampling band @ truth {SIGMA_TRUE}: "
        f"[{_fmt(band['p5'])}, {_fmt(band['p50'])}, {_fmt(band['p95'])}] "
        f"(p5/p50/p95), no-root rate {band['no_root_rate']:.1%}, "
        f"z_n ceiling {band['z_ceiling']:.2f} (smaller root cannot exceed it); "
        f"realized root-conditional bias at this n: band median "
        f"{realized_bias:+.2f} vs truth (bias direction is n-DEPENDENT — high "
        f"at large n, flipped LOW at small n by the ceiling + no-root truncation)"
    )
    if roots is not None:
        pct = observed_percentile_in_band(n, roots[0])
        if pct <= 0.05:
            edge = (
                " — AT/BELOW the band's LOW edge: materially inconsistent with"
                f" truth {SIGMA_TRUE} at this conditioning level, in the LOW"
                " direction"
            )
        elif pct >= 0.95:
            edge = (
                " — AT/ABOVE the band's HIGH edge: materially inconsistent with"
                f" truth {SIGMA_TRUE} at this conditioning level, in the HIGH"
                " direction"
            )
        elif pct <= 0.15 or pct >= 0.85:
            edge = " (near-edge)"
        else:
            edge = ""
        out.append(
            f"      observed plug-in read sits at ~p{round(pct * 100):d} of the "
            f"truth-{SIGMA_TRUE} band (evidential direction){edge}"
        )
    return out


def main() -> None:
    print("=" * 78)
    print("NETDILIGENCE 2025 SIGMA CONSISTENCY + ANCHOR REFERENCE BANDS (generated)")
    print("=" * 78)
    print()
    print("Source: NetDiligence Cyber Claims Study 2025 Report V1.1 (licensed PDF,")
    print("UNTRACKED; transcription owner-attested; printed page cites).")
    print("Cost population: 9,171 claims >= $1K (p.7). Estimator definitions,")
    print("bias directions, and caveats: module docstring + netdiligence_2025.md.")
    print()
    print("[B-ND-BAND] Table 3 (p.52) — incident cost by revenue size")
    for name, n, mean, max_ in REVENUE_BANDS:
        for line in _row_block(name, n, mean, max_, SIGMA_READ_EXCLUDED.get(name)):
            print(line)
    print()
    print("[B-ND-CAUSE] Table 9 (p.59) — incident cost by cause of loss, SMEs")
    for name, n, mean, max_ in CAUSE_ROWS_SME:
        for line in _row_block(name, n, mean, max_, None):
            print(line)
    print()
    print("[B-ND-REF] anchor REFERENCE bands (direction NOT established:")
    print("  loss-form blindness pushes NetD BELOW FAIR loss magnitude;")
    print("  claim-reporting selection + the >=$1K filter push it ABOVE;")
    print("  Table 9 is SME-conditioned. A library per-event mean far below its")
    print("  mapped class mean prompts a DOCUMENTED REVIEW, never an automatic")
    print("  violation. NetD figures are NEVER percentile anchors.)")
    for cause, lib_class in REF_CLASS_MAP:
        row = next(r for r in CAUSE_ROWS_SME if r[0] == cause)
        print(f"  {cause}: SME mean ${row[2]:,.0f} (n={row[1]}) -> {lib_class}")
    print()
    print("[B-ND-COND] conditioning level of every read above: cross-firm x")
    print("  cross-type WITHIN a revenue band — the within-revenue-tier")
    print("  UPPER-BOUND row of within-scenario-sigma-calibration.md §2, NOT the")
    print("  within-scenario quantity. Relation to IRIS 1.97-2.92 at that level")
    print("  is per-row and MIXED (see each row above) — NO summary direction is")
    print("  stated: several rows' z_n ceilings (e.g. small 2.88, wire 2.67) sit")
    print("  below IRIS's upper 2.92, so those rows structurally cannot read")
    print("  above the band regardless of the data. The >=$1K filter's effect on")
    print("  the MC bands: negligible on the revenue bands (mass <= 2%, nano")
    print("  worst ~1.9%; mean share <= 0.01%, nano ~0.0083%); larger but still")
    print("  mean-immaterial on cause rows (worst theft_of_money: ~9.9% of mass,")
    print("  ~0.14% of the mean).")


if __name__ == "__main__":
    sys.exit(main())
