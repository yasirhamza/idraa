"""Single source of truth for every figure quoted in the PR2 capacity-bound design.

Sibling of ``sigma_recal_figures.py``. That generator documents PR1's change and
now refuses to run (its "before" basis was consumed when PR1's builder re-authored
the seeds). PR2 has a DIFFERENT basis: "today" here means the deployed post-PR1
state read from a prod backup, and "capped" means that state plus a
per-distribution capacity bound.

The epic's durable lesson (six plan-gate rounds, blockers 19 -> 2 -> 4 -> 5 -> 7 -> 0):
hand-maintained derived numbers in prose do not converge under review, because each
round's fixes create stale siblings elsewhere. So the design quotes this script's
output verbatim and contains zero derived numbers.

Bases:
  B-CAP-BASIS  input assertions: alembic head, revenue, population, post-PR1 sigma
  B-CAP-SIM    simulated max single-event LM over the committed seed set (D8 verdict)
  B-CAP-N      D8's verdict as a function of the iteration count n
  B-CAP-K      D8 across the k_capacity bracket -- the honesty curve, per D9''(a)
  B-CAP-DRIFT  realized-mean retention under the cap (the "cost" of the guardrail)
  B-CAP-ALT    the REJECTED quantile-anchored fallback, and why D14 has none
  B-CAP-MIX    per-component vs mixture-conditioned truncation, and its bounds
  B-CAP-PORT   portfolio ALE, today -> capped

TWO artifacts are written (see Report): the FULL appendix, and a SANITIZED PUBLIC
variant safe to paste into a public PR body. Which rows are public is decided
line-by-line HERE, in code -- never by a reviewer's memory at PR time. See
Report's docstring for the classification rule and why it exists.

Usage:
  SIGMA_RECAL_PROD_DB=<path-to-prod-backup-COPY> uv run python scripts/capacity_bound_figures.py
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import statistics as st
from pathlib import Path

import numpy as np
from scipy.special import ndtr, ndtri
from scipy.stats import norm

ROOT = Path(__file__).resolve().parents[1]
# Deployment-specific, NEVER hardcoded (public repo): point SIGMA_RECAL_PROD_DB at a
# prod-backup COPY. With no env var the prod-dependent sections are skipped.
PROD_DB = Path(os.environ["SIGMA_RECAL_PROD_DB"]) if os.environ.get("SIGMA_RECAL_PROD_DB") else None

SPECS = ROOT / "docs" / "superpowers" / "specs"
FULL_OUT = SPECS / "capacity-bound-figures.generated.txt"
PUBLIC_OUT = SPECS / "capacity-bound-figures.public.txt"

Z95 = 1.6448536269514722
SIGMA_DEFAULT = 1.7
# Stored sigma lands within ~1e-7 of the default on fields that went through the
# wizard re-spread (quantiles round-trip through dollar values, so sigma is
# re-derived rather than assigned). The post-PR1 basis assertion therefore needs a
# tolerance; an exact equality check fails on real data. The observed deviation and
# the field it came from are PRINTED in B-CAP-BASIS rather than restated here, so
# this tracked file carries no deployment-specific value.
SIGMA_BASIS_TOL = 1e-5

K_CAPACITY = 1.0  # owner-signed 2026-07-25 (D13); see B-CAP-K for the bracket
# The bracket spans four orders of magnitude DELIBERATELY. D8 is a one-sided
# (upper) gate: it passes for every k below the binding value, including caps so
# aggressive they destroy most of the expected loss. Publishing only a passing
# range would imply two-sided bracketing that does not exist, so every row also
# carries the retention columns that discriminate the low end.
K_BRACKET = (0.001, 0.01, 0.10, 0.25, 0.50, 0.75, 1.00, 1.10, 1.20, 1.50, 2.00)
# n is LOAD-BEARING on D8's verdict and must be declared, not inherited. The max
# of an unbounded heavy-tailed sample grows without bound in n, so an uncapped
# D8 reading is only meaningful against a stated iteration count. This is pinned
# to Settings.mc_iterations_max -- the server-side ceiling an operator can
# actually request, i.e. the WORST case over the supported range. (The parked
# design's 700_000 was an artifact of one exemplar run and justified against
# neither Settings value; at Settings.mc_iterations_default = 10_000 the
# UNCAPPED state passes D8, which would have made the pathology invisible.)
ITERS = 1_000_000
N_SENSITIVITY = (10_000, 100_000, 700_000, 1_000_000)
SIM_SEEDS = tuple(range(10))  # committed seed set; never vary silently


class Report:
    """Emits the full appendix and a sanitized PUBLIC variant from one run.

    WHY THIS EXISTS. The appendix is operator-local (denylist-untracked); a PR
    body on `yasirhamza/idraa` is PUBLIC. An earlier draft whitelisted the
    "library-only" B-CAP-ALT / B-CAP-MIX rows as safe to publish. They are not:
    they print z, Phi(z) and P(event>cap) computed as (ln(k*revenue) - mu_lib)/
    sigma_lib, where mu_lib and sigma_lib live in the TRACKED, PUBLIC seed-library
    JSON and k_capacity is published in the PR body itself. Inverting the
    9-significant-figure Phi(z) reconstructs annual_revenue to within a few
    hundred dollars. Classifying rows from memory at PR time is exactly the
    failure this generator exists to prevent, so the classification is code.

    A line may go PUBLIC only if it is one of:
      (a) an analytic identity in (sigma_default, z_q, k_capacity) with NO
          deployment input -- mu cancels, e.g. the quantile-anchor retentions
          and the mixture distortion bounds;
      (b) a ratio or retention statistic over PROD fields, whose mu is not
          published -- >= 2 unknowns per field, so it does not invert.
          (Verified 2026-07-25 against the backup: 0 of 9 active prod lognormal
          loss fields carry a mu matching any seed-library mu, and prod sigma is
          NOT uniformly the default -- these fields are independently authored,
          not library copies.);
      (c) a PASS/FAIL verdict, an iteration count, a k value, or another
          non-deployment constant.

    A line is PRIVATE if it is any of:
      (d) dollar-denominated (revenue, cap, LM, ALE);
      (e) computed from the PUBLIC library (mu, sigma) TOGETHER WITH the cap --
          these inverts to annual_revenue via cap = k_capacity * revenue;
      (f) an authored scenario name, a population count, or the alembic head.
    """

    def __init__(self) -> None:
        self._lines: list[tuple[str, str]] = []

    def both(self, text: str = "") -> None:
        """Deployment-independent narrative or figure: appears in both artifacts."""
        self._lines.append(("both", text))

    def priv(self, text: str = "") -> None:
        """Rules (d)/(e)/(f): full appendix only, NEVER public."""
        self._lines.append(("private", text))

    def pub(self, text: str = "") -> None:
        """Public-only line: the sanitized rewrite of an adjacent priv() row."""
        self._lines.append(("public", text))

    def full(self) -> str:
        return "\n".join(t for v, t in self._lines if v != "public") + "\n"

    def public(self) -> str:
        return "\n".join(t for v, t in self._lines if v != "private") + "\n"


def _entries() -> list[dict]:
    out: list[dict] = []
    for name in ("seed_library_entries.json", "seed_library_entries_extension.json"):
        out.extend(json.loads((ROOT / "data" / name).read_text(encoding="utf-8")))
    return out


def _loss_fields(d: dict) -> list[tuple[str, dict]]:
    """Lognormal loss fields. Mixtures are NOT silently skipped -- see _assert_no_mixtures."""
    return [
        (f, v)
        for f in ("primary_loss", "secondary_loss")
        if isinstance(v := d.get(f), dict) and v.get("distribution") == "lognormal"
    ]


def _assert_no_mixtures(dicts: list[dict | None], where: str) -> None:
    """Fail loud on lognormal_mixture rather than mis-bucketing it.

    Every population here is currently mixture-free, and the analytic estimators
    below assume a single {mean, sigma} pair. A mixture would be silently dropped
    by _loss_fields and then mis-read as PERT-only by _lognormal_bearing, so the
    figure would be wrong in a direction no pin detects. Shipped code must handle
    mixtures; this generator refuses to guess at them.
    """
    for d in dicts:
        if isinstance(d, dict) and d.get("distribution") == "lognormal_mixture":
            raise SystemExit(
                f"PIN FAILED: lognormal_mixture found in {where}. The estimators in this "
                "generator assume a single {mean, sigma} pair -- extend them before quoting."
            )


def pins() -> None:
    """Fail loud if the library shape moved under the figures.

    Mirrors sigma_recal_figures.pins(), but pinned to the POST-PR1 shape: the
    library must already be at the within-scenario default, which is the inverse
    of that generator's before-basis guard.
    """
    entries = _entries()
    capped = sum(
        1
        for e in entries
        for f in ("primary_loss", "secondary_loss")
        if (e.get(f) or {}).get("distribution") == "PERT"
    )
    cat = sum(1 for e in entries for _ in _loss_fields(e))
    _assert_no_mixtures(
        [e.get(f) for e in entries for f in ("primary_loss", "secondary_loss")], "the seed library"
    )
    if capped != 154:
        raise SystemExit(f"PIN FAILED: expected 154 capped PERT loss fields, found {capped}")
    if cat != 18:
        raise SystemExit(f"PIN FAILED: expected 18 catastrophic lognormal loss fields, found {cat}")
    worst = max(abs(d["sigma"] - SIGMA_DEFAULT) for e in entries for _, d in _loss_fields(e))
    if worst > SIGMA_BASIS_TOL:
        raise SystemExit(
            f"PIN FAILED: library is not at the post-PR1 basis (max |sigma-{SIGMA_DEFAULT}| "
            f"= {worst:.3e}). This generator's 'today' column assumes PR1 has landed."
        )


def _prod() -> tuple[sqlite3.Connection, float, str]:
    db = sqlite3.connect(PROD_DB)
    rev = db.execute(
        "SELECT annual_revenue FROM organizations WHERE annual_revenue IS NOT NULL "
        "ORDER BY id LIMIT 1"
    ).fetchone()
    if not rev or not rev[0]:
        raise SystemExit("no organization with annual_revenue in the backup")
    head = db.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    return db, float(rev[0]), str(head)


def _active_scenarios(db: sqlite3.Connection) -> list[tuple[str, dict | None, dict | None]]:
    out = []
    for name, pl, sl in db.execute(
        "SELECT name, primary_loss, secondary_loss FROM scenarios "
        "WHERE status = 'active' ORDER BY id"
    ):
        # Prod JSON columns can hold the literal text "null" (truthy string ->
        # json.loads -> None), so isinstance-check after parsing, never `if raw`.
        pld = json.loads(pl) if pl else None
        sld = json.loads(sl) if sl else None
        out.append(
            (name, pld if isinstance(pld, dict) else None, sld if isinstance(sld, dict) else None)
        )
    # The prod population needs the SAME guard as the seed library. It is not
    # hypothetical: the wizard can author a lognormal_mixture, and Task 9 mandates
    # regenerating these figures against a FRESH backup. A mixture arriving later
    # would be dropped by _loss_fields, then mis-read as PERT-only by
    # _lognormal_bearing, silently DEFLATING the D8 reading in the one direction
    # no pin detects.
    _assert_no_mixtures(
        [d for _, pld, sld in out for d in (pld, sld)], "the prod backup (active scenarios)"
    )
    return out


def basis(db: sqlite3.Connection, rev: float, head: str) -> dict[str, object]:
    """[B-CAP-BASIS] assert and report the input basis."""
    scen = _active_scenarios(db)
    fields = [
        (n, f, d)
        for n, pld, sld in scen
        for f, d in _loss_fields({"primary_loss": pld, "secondary_loss": sld})
    ]
    # PR1's scenario sweep was NARROW-ONLY (D6'): fields already at or below the
    # default were deliberately left untouched (SME-elicited narrower ranges are
    # not envelope-contaminated). So the post-PR1 invariant is an UPPER BOUND,
    # not equality -- asserting equality here would fail on correctly-preserved
    # narrow fields.
    worst = max((d["sigma"] - SIGMA_DEFAULT, n, f) for n, f, d in fields)
    if worst[0] > SIGMA_BASIS_TOL:
        raise SystemExit(
            f"BASIS FAILED: {worst[1]} {worst[2]} has sigma = {SIGMA_DEFAULT + worst[0]!r} "
            f"(exceeds the default by {worst[0]:.3e}) -- this backup is NOT the post-PR1 "
            "state. Point at a fresher backup."
        )
    return {
        "head": head,
        "revenue": rev,
        "scenarios": len(scen),
        "lognormal_fields": len(fields),
        "worst_sigma_dev": worst[0],
        "worst_sigma_field": f"{worst[1]} {worst[2]}",
    }


def _pert_ab(low: float, mode: float, high: float) -> tuple[float, float]:
    """Vose BetaPERT gamma=4, mirroring fair_core.py:144-164."""
    g = 4.0
    mean = (low + g * mode + high) / (g + 2.0)
    sd = (high - low) / (g + 2.0)
    if sd <= 0:
        return 1.0, 1.0
    a = ((mean - low) / (high - low)) * (((mean - low) * (high - mean)) / sd**2 - 1)
    return a, a * (high - mean) / (mean - low)


def truncated_lognormal(rng, meanlog: float, sigma: float, size: int, max_value: float):
    """Inverse-CDF truncation with support [0, max_value).

    Half-open at BOTH ends, and the design says so too: rng.random() is [0, 1),
    so u can be exactly 0 -> ndtri(0) = -inf -> x = 0.0 (probability 2**-53), and
    u < Phi(b) strictly, so max_value is never attained. No point mass at the cap.

    Verified 2026-07-25 three ways against fair_cam.quantile_pooling._lognormal
    ._qlnormtrunc (rel err <= 4e-13 across p in [0.01, 0.99999]), against
    rejection sampling (mean agrees to 2.4e-03 at 5M draws, within MC noise),
    and against the closed-form conditional mean (4.4e-04 at 5M draws).
    """
    b = (math.log(max_value) - meanlog) / sigma
    u = rng.random(size) * ndtr(b)
    return np.exp(meanlog + sigma * ndtri(u))


def _draw(d: dict | None, rng, cap: float | None):
    if not d:
        return 0.0
    kind = str(d.get("distribution", "pert")).lower()  # absent -> PERT, per run_executor
    if kind == "lognormal":
        if cap is None:
            return rng.lognormal(d["mean"], d["sigma"], ITERS)
        return truncated_lognormal(rng, d["mean"], d["sigma"], ITERS, cap)
    if kind == "pert":
        a, b = _pert_ab(d["low"], d["mode"], d["high"])
        return d["low"] + rng.beta(a, b, ITERS) * (d["high"] - d["low"])
    raise SystemExit(f"_draw: unhandled distribution kind {kind!r} — refusing to read it as 0.0")


def _sim_max(scen: list, cap: float | None) -> dict[str, float]:
    """Max single-event LM (= PL + SL) over the committed seeds.

    Per-scenario substream keyed on a stable sha256 digest of the name: results
    are invariant to scenario iteration order and to DB re-creation, unlike a
    single shared stream per seed. Standalone estimator, NOT the engine (no
    shared stream, no vuln thinning): valid for the seed-spread of the max, not
    for reproducing a run.
    """
    maxima = []
    for seed in SIM_SEEDS:
        worst = 0.0
        for name, pld, sld in scen:
            key = int.from_bytes(hashlib.sha256(name.encode()).digest()[:8], "big")
            rng = np.random.default_rng([seed, key])
            lm = _draw(pld, rng, cap) + _draw(sld, rng, cap)
            worst = max(worst, float(np.max(lm)))
        maxima.append(worst)
    maxima.sort()
    return {"min": maxima[0], "median": st.median(maxima), "max": maxima[-1]}


def _lognormal_bearing(scen: list) -> tuple[list, float]:
    ln, pert_bound = [], 0.0
    for name, pld, sld in scen:
        if any((d or {}).get("distribution") == "lognormal" for d in (pld, sld)):
            ln.append((name, pld, sld))
        else:
            pert_bound = max(
                pert_bound, sum(float((d or {}).get("high", 0.0)) for d in (pld, sld) if d)
            )
    return ln, pert_bound


def mean_retained(mu: float, sigma: float, max_value: float) -> float:
    """E[X|X<=max]/E[X] = Phi(b-sigma)/Phi(b) -- the closed-form cost of the cap."""
    b = (math.log(max_value) - mu) / sigma
    return float(ndtr(b - sigma) / ndtr(b))


def _dist_mean(d: dict | None, cap: float | None) -> float:
    if not d:
        return 0.0
    # run_executor defaults an ABSENT 'distribution' key to PERT (case-insensitive).
    # Real vulnerability dicts routinely carry no such key, so an analytic tool that
    # does not mirror the default reads those means as "unknown -> 0.0", zeroes the
    # affected scenarios' LEF, and collapses the portfolio ALE by orders of
    # magnitude. B-CAP-BASIS prints the population this actually ran against;
    # deployment counts are deliberately NOT restated in this tracked file.
    kind = str(d.get("distribution", "pert")).lower()
    if kind == "lognormal":
        parent = math.exp(d["mean"] + d["sigma"] ** 2 / 2)
        return parent * (mean_retained(d["mean"], d["sigma"], cap) if cap else 1.0)
    if kind == "pert":
        return (d["low"] + 4.0 * d["mode"] + d["high"]) / 6.0
    raise SystemExit(
        f"_dist_mean: unhandled distribution kind {kind!r} — refusing to read it as 0.0"
    )


def portfolio_ale(db: sqlite3.Connection, cap: float | None) -> float:
    """[B-CAP-PORT] analytic portfolio ALE = sum over active scenarios of LEF x LM."""
    total = 0.0
    for tef, vuln, pl, sl in db.execute(
        "SELECT threat_event_frequency, vulnerability, primary_loss, secondary_loss "
        "FROM scenarios WHERE status = 'active' ORDER BY id"
    ):
        parsed = []
        for raw in (tef, vuln, pl, sl):
            v = json.loads(raw) if raw else None
            parsed.append(v if isinstance(v, dict) else None)
        t, v, p, s = parsed
        # Same absent-'distribution' default as _dist_mean -- see the comment there.
        lef = _dist_mean(t, None) * (_dist_mean(v, None) if v else 1.0)
        total += lef * (_dist_mean(p, cap) + _dist_mean(s, cap))
    return total


def main() -> None:
    pins()
    header = [
        "=" * 78,
        "CAPACITY-BOUND FIGURES (PR2) — generated; quote these, do not re-derive by hand",
        f"k_capacity={K_CAPACITY}  sigma_default={SIGMA_DEFAULT}  z95={Z95}  n_iters={ITERS}",
        "=" * 78,
    ]
    if PROD_DB is None or not PROD_DB.exists():
        # Do NOT write the artifacts from a prod-less run: it would silently
        # replace both files with a stub that still looks generated.
        print("\n".join(header))
        print("\nSIGMA_RECAL_PROD_DB not set or missing — prod sections skipped, nothing written.")
        return

    r = Report()
    for line in header:
        r.both(line)
    r.priv("FULL appendix (operator-local). Public variant: capacity-bound-figures.public.txt")
    r.pub("PUBLIC VARIANT — safe to paste into a public PR body or commit message.")
    r.pub("Generated with the full appendix by scripts/capacity_bound_figures.py; which rows")
    r.pub("are public is decided line-by-line in that script (see Report), NOT at PR time.")
    r.pub("WITHHELD here: dollar values, scenario names, population counts, the alembic head,")
    r.pub("and every figure computed from the PUBLIC seed-library (mu, sigma) TOGETHER WITH the")
    r.pub("cap — since k_capacity is published and cap = k x revenue, those invert to revenue.")

    db, rev, head = _prod()
    b = basis(db, rev, head)
    r.priv("")
    r.priv("[B-CAP-BASIS] input assertions")
    r.priv(f"  alembic head            : {b['head']}")
    r.priv(
        f"  annual_revenue          : ${b['revenue']:,.0f}   (READ FROM the backup, never hardcoded)"
    )
    r.priv(f"  active scenarios        : {b['scenarios']}")
    r.priv(f"  lognormal loss fields   : {b['lognormal_fields']}")
    r.priv(
        f"  post-PR1 sigma check    : max (sigma-{SIGMA_DEFAULT}) = {b['worst_sigma_dev']:+.3e} "
        f"[{b['worst_sigma_field']}]  (must be <= {SIGMA_BASIS_TOL:.0e}; narrow-only sweep, D6')"
    )
    r.pub("")
    r.pub("[B-CAP-BASIS] WITHHELD — revenue, population, alembic head, authored scenario names.")
    r.pub("  The basis assertions ran and passed; the run below is against that asserted state.")

    scen = _active_scenarios(db)
    ln, pert_bound = _lognormal_bearing(scen)
    cap = K_CAPACITY * rev

    r.both("")
    r.both(
        f"[B-CAP-SIM] simulated max single-event LM, seeds={SIM_SEEDS}, active lognormal-bearing"
    )
    r.priv(
        f"  per-distribution cap = k x revenue = ${cap:,.0f}  (event total bounded at 2k x revenue)"
    )
    r.pub("  per-distribution cap = k x revenue  (event total bounded at 2k x revenue)")
    for label, c in (("today (uncapped)", None), (f"capped k={K_CAPACITY}", cap)):
        res = _sim_max(ln, c)
        verdict = "" if c is None else ("   D8 PASS" if res["median"] <= rev else "   D8 FAIL")
        pct = res["median"] / rev * 100
        r.priv(
            f"  {label:<18} min ${res['min']:>18,.0f}  median ${res['median']:>18,.0f} "
            f"({pct:7.2f}% rev)  max ${res['max']:>18,.0f}{verdict}"
        )
        r.pub(f"  {label:<18} median max LM = {pct:7.2f}% of revenue{verdict}")
    r.priv(f"  PERT-only scenarios cannot exceed ${pert_bound:,.0f} (analytic bound high_P+high_S)")
    r.pub("  PERT-only scenarios are bounded by high_P+high_S and cannot approach the cap.")
    r.priv(
        f"  HARD analytic bound, n-invariant: maxP+maxS = 2k x revenue = ${2 * cap:,.0f} "
        f"({2 * K_CAPACITY * 100:.0f}% rev)"
    )
    r.pub(
        f"  HARD analytic bound, n-invariant: maxP+maxS = 2k x revenue = "
        f"{2 * K_CAPACITY * 100:.0f}% rev"
    )
    r.both("  estimator: standalone per-scenario substreams (sha256 name digest); NOT the engine")
    r.both("  -- no shared stream, no vulnerability thinning. Valid for the seed-spread of the")
    r.both("  max, not for reproducing a run.")

    r.both("")
    r.both("[B-CAP-N] D8's verdict is a FUNCTION OF n -- the uncapped reading is n-conditional")
    r.both("  The max of an unbounded heavy-tailed sample grows without bound in n, so an")
    r.both("  uncapped D8 reading is meaningless without a declared iteration count. Only the")
    r.both("  HARD bound above is a true n-invariant guarantee.")
    r.both(f"  {'n_iters':>10}  {'uncapped % rev':>15}  {'':>6}  {'capped % rev':>13}  {'':>6}")
    saved = globals()["ITERS"]
    for n in N_SENSITIVITY:
        globals()["ITERS"] = n
        unc = _sim_max(ln, None)["median"] / rev * 100
        cpd = _sim_max(ln, cap)["median"] / rev * 100
        tag = "  <== BASIS (Settings.mc_iterations_max)" if n == saved else ""
        # Ratios over prod fields (rule b) plus verdicts (rule c): public.
        r.both(
            f"  {n:>10,}  {unc:>14.2f}%  {'PASS' if unc <= 100 else 'FAIL':>6}  "
            f"{cpd:>12.2f}%  {'PASS' if cpd <= 100 else 'FAIL':>6}{tag}"
        )
    globals()["ITERS"] = saved
    r.both("  NOTE: at Settings.mc_iterations_default the UNCAPPED state passes D8 -- the")
    r.both("  pathology is invisible there. The basis is pinned to the supported CEILING.")

    r.both("")
    r.both("[B-CAP-K] D8 across the k_capacity bracket, with the retention columns")
    r.both("  D8: median of the simulated max LM over seeds 0-9 <= 100% of annual_revenue")
    r.both("  D8 IS A ONE-SIDED (UPPER) GATE: it passes for every k below the binding value,")
    r.both("  including caps aggressive enough to destroy most of the expected loss. The")
    r.both("  retention columns are what discriminate the low end -- D8 alone does not.")
    r.priv(
        f"  {'k':>7} {'cap':>17} {'median max LM':>17} {'% rev':>8} {'D8':>5} "
        f"{'med.retain':>11} {'worst.retain':>13}"
    )
    r.pub(f"  {'k':>7} {'% rev':>8} {'D8':>5} {'med.retain':>11} {'worst.retain':>13}")
    for k in K_BRACKET:
        kcap = k * rev
        res = _sim_max(ln, kcap)
        rets = sorted(
            mean_retained(d["mean"], d["sigma"], kcap)
            for _, pld, sld in ln
            for _, d in _loss_fields({"primary_loss": pld, "secondary_loss": sld})
        )
        mark = "  <== CHOSEN" if k == K_CAPACITY else ""
        pct, verdict = res["median"] / rev * 100, "PASS" if res["median"] <= rev else "FAIL"
        r.priv(
            f"  {k:>7.3f} ${kcap:>16,.0f} ${res['median']:>16,.0f} {pct:>7.2f}% "
            f"{verdict:>5} {st.median(rets) * 100:>10.3f}% {rets[0] * 100:>12.3f}%{mark}"
        )
        # Retention over PROD fields: mu is not published, so >= 2 unknowns (rule b).
        r.pub(
            f"  {k:>7.3f} {pct:>7.2f}% {verdict:>5} "
            f"{st.median(rets) * 100:>10.3f}% {rets[0] * 100:>12.3f}%{mark}"
        )

    r.both("")
    r.both(f"[B-CAP-DRIFT] realized-mean retention under the k={K_CAPACITY} cap (100% = no cost)")
    r.both("  the cap is a TAIL guardrail: post-PR1 it costs essentially nothing in expected loss.")
    r.both("  PR2's per-run disclosure will therefore read ~0.0% — that is correct, not broken.")
    prod_ret = [
        (mean_retained(d["mean"], d["sigma"], cap), f"{n[:34]} {f}")
        for n, pld, sld in ln
        for f, d in _loss_fields({"primary_loss": pld, "secondary_loss": sld})
    ]
    lib_ret = [
        (mean_retained(d["mean"], d["sigma"], cap), f"{e['slug'][:34]} {f}")
        for e in _entries()
        for f, d in _loss_fields(e)
    ]
    for label, rets in (("prod active fields", prod_ret), ("library catastrophic", lib_ret)):
        rets.sort()
        med = st.median([x for x, _ in rets])
        r.priv(
            f"  {label:<22} n={len(rets):<3} median retained {med * 100:7.3f}%   "
            f"worst retained {rets[0][0] * 100:7.3f}%  [{rets[0][1]}]"
        )
    # The PROD row publishes (rule b); the LIBRARY row does NOT (rule e) -- its mu is
    # public, so its retention inverts through mean_retained to the cap and to revenue.
    prod_med = st.median([x for x, _ in prod_ret])
    r.pub(
        f"  prod active fields     median retained {prod_med * 100:7.3f}%   "
        f"worst retained {min(x for x, _ in prod_ret) * 100:7.3f}%"
    )
    r.pub("  library catastrophic   WITHHELD — library mu is public, so its retention under the")
    r.pub("                         cap inverts to annual_revenue.")

    r.both("")
    r.both("[B-CAP-ALT] the REJECTED quantile-anchored fallback (why D14 has no fallback)")
    r.both("  This row is an ANALYTIC IDENTITY, not an N-field measurement. Retention under a")
    r.both("  quantile anchor at level q is Phi(z_q - sigma)/Phi(z_q) -- a function of (sigma, q)")
    r.both("  ONLY: mu cancels. Every catastrophic library field carries the same sigma, so there")
    r.both("  is exactly ONE degree of freedom and the median/worst/count columns below carry no")
    r.both("  information beyond a single evaluation. They are printed to show that spread is")
    r.both("  zero BY CONSTRUCTION -- which is precisely the point: a quantile anchor removes the")
    r.both("  same slice of a tail-fed mean on EVERY field regardless of scenario size, while a")
    r.both("  capacity anchor is ABSOLUTE and bites only the large ones.")
    r.both("  Basis caveat: the fallback would only ever fire where annual_revenue is NULL, so")
    r.both("  this compares 'quantile everywhere' with 'capacity everywhere', not the literal")
    r.both("  counterfactual. The supported claim is: wherever it fired, it would cost the")
    r.both("  retention shown, uniformly.")
    lib = [(e["slug"], f, d) for e in _entries() for f, d in _loss_fields(e)]
    for label, fn, is_public in (
        ("p99.9 of parent", lambda d: math.exp(d["mean"] + norm.ppf(0.999) * d["sigma"]), True),
        ("p99.99 of parent", lambda d: math.exp(d["mean"] + norm.ppf(0.9999) * d["sigma"]), True),
        (f"k={K_CAPACITY} x revenue", lambda d: cap, False),
    ):
        rets = [mean_retained(d["mean"], d["sigma"], fn(d)) for _, _, d in lib]
        # The quantile rows are scale-free -- mu cancels exactly, which is the row's whole
        # point, so they carry NO deployment input (rule a). The capacity row is the same
        # library mu evaluated AT THE CAP, which is rule (e): it inverts to revenue.
        line = (
            f"  {label:<22} median retained {st.median(rets) * 100:7.3f}%   "
            f"worst {min(rets) * 100:7.3f}%   fields below 99%: "
            f"{sum(1 for x in rets if x < 0.99)}/{len(rets)}"
        )
        if is_public:
            r.both(line)
        else:
            r.priv(line)
    zs = [(math.log(cap) - d["mean"]) / d["sigma"] for _, _, d in lib]
    r.priv(
        f"  where the capacity cap sits on those {len(lib)} fields: z = {min(zs):.3f} .. {max(zs):.3f}  "
        f"(P(event>cap) = {1 - ndtr(max(zs)):.2e} .. {1 - ndtr(min(zs)):.2e})"
    )
    r.pub("  k x revenue row + the z / P(event>cap) range: WITHHELD (rule e — public library mu")
    r.pub("  evaluated at the cap inverts to annual_revenue). The ARGUMENT does not need them:")
    r.pub("  a quantile anchor's cost is the same on every field by the identity above, while a")
    r.pub("  capacity anchor's depends on mu and so never binds on small scenarios.")

    r.both("")
    r.both("[B-CAP-MIX] per-component vs mixture-conditioned truncation — the semantic deviation")
    r.both("  The sampler truncates EACH component at the shared cap, retaining density")
    r.both("  sum(w_i f_i / F_i(M)). Conditioning the MIXTURE on X<=M would retain")
    r.both("  sum(w_i f_i) / sum(w_j F_j(M)). Component i's effective weight is therefore")
    r.both("  distorted by the factor F_bar / F_i(M), where F_bar = sum(w_j F_j(M)).")
    r.both("  Task 4's floor requires M > every component's p95, so F_i(M) > Phi(z95) for all i,")
    r.both("  which bounds the distortion analytically IN BOTH DIRECTIONS:")
    up_bound = 1.0 / ndtr(Z95) - 1.0
    down_bound = ndtr(Z95) - 1.0
    r.both(
        f"    UP   (F_i at the floor, F_bar -> 1): 1/Phi(z95) - 1 = {up_bound * 100:+.3f}%  "
        "-- heavy components over-weighted"
    )
    r.both(
        f"    DOWN (F_i -> 1, F_bar at the floor): Phi(z95) - 1 = {down_bound * 100:+.3f}%  "
        "-- light components under-weighted"
    )
    r.both("    Both are suprema over admissible configurations, approached but never attained.")
    deep_b = (math.log(cap) - min(d["mean"] for _, _, d in lib)) / SIGMA_DEFAULT
    r.priv(
        f"    at the SHIPPED cap the components sit at z >= {min(zs):.3f}, so F_i > "
        f"{ndtr(min(zs)):.9f} and the distortion is <= {(1 / ndtr(min(zs)) - 1) * 100:.5f}%"
    )
    r.priv(f"    (deepest library component sits at z = {deep_b:.3f})")
    r.pub("    at the SHIPPED cap: WITHHELD (rule e — this z, and its 9-significant-figure")
    r.pub("    Phi(z), reconstruct annual_revenue to within a few hundred dollars).")
    r.both("  So the deviation is bounded by the validator's own floor and is far smaller at the")
    r.both("  shipped cap. Per-component is the CHOSEN 'each regime capped at capacity' semantics.")

    r.both("")
    r.both("[B-CAP-PORT] analytic portfolio ALE — population=active-only")
    today = portfolio_ale(db, None)
    after = portfolio_ale(db, cap)
    r.priv(f"  population {len(scen)} scenarios")
    r.priv(f"  today ${today:,.0f} -> capped ${after:,.0f}  = {(after / today - 1) * 100:+.4f}%")
    # Ratio of two unpublished prod ALEs (rule b); the dollars and the count are not.
    r.pub(f"  portfolio ALE change under the cap = {(after / today - 1) * 100:+.4f}%")

    SPECS.mkdir(parents=True, exist_ok=True)
    FULL_OUT.write_text(r.full(), encoding="utf-8")
    PUBLIC_OUT.write_text(r.public(), encoding="utf-8")
    print(f"wrote {FULL_OUT.relative_to(ROOT)}   ({len(r.full().splitlines())} lines, FULL)")
    print(f"wrote {PUBLIC_OUT.relative_to(ROOT)}   ({len(r.public().splitlines())} lines, PUBLIC)")
    print("The PUBLIC variant is the ONLY one a public PR body or commit message may quote.")


if __name__ == "__main__":
    main()
