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
  B-CAP-K      D8 across the k_capacity bracket -- the honesty curve, per D9''(a)
  B-CAP-DRIFT  realized-mean retention under the cap (the "cost" of the guardrail)
  B-CAP-ALT    the REJECTED quantile-anchored fallback, and why D14 has none
  B-CAP-PORT   portfolio ALE, today -> capped

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
    """Inverse-CDF truncation on (0, max_value].

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
    # 14 of 15 prod vulnerability dicts carry no such key; reading them as "unknown
    # -> 0.0" silently zeroes those scenarios' LEF and collapses the portfolio ALE
    # by ~88x. Verified against prod head b3f8a2d94c1e, 2026-07-25.
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
        # run_executor defaults an ABSENT 'distribution' key to PERT (case-insensitive).
        # Real vulnerability dicts routinely carry no 'distribution' key -- any analytic
        # tool that does not mirror this reads vuln means as 0 and silently zeroes the
        # portfolio ALE by ~2 orders of magnitude.
        lef = _dist_mean(t, None) * (_dist_mean(v, None) if v else 1.0)
        total += lef * (_dist_mean(p, cap) + _dist_mean(s, cap))
    return total


def main() -> None:
    pins()
    print("=" * 78)
    print("CAPACITY-BOUND FIGURES (PR2) — generated; quote these, do not re-derive by hand")
    print(f"k_capacity={K_CAPACITY}  sigma_default={SIGMA_DEFAULT}  z95={Z95}  n_iters={ITERS}")
    print("=" * 78)

    if PROD_DB is None or not PROD_DB.exists():
        print("\nSIGMA_RECAL_PROD_DB not set or missing — prod sections skipped.")
        return

    db, rev, head = _prod()
    b = basis(db, rev, head)
    print("\n[B-CAP-BASIS] input assertions")
    print(f"  alembic head            : {b['head']}")
    print(
        f"  annual_revenue          : ${b['revenue']:,.0f}   (READ FROM the backup, never hardcoded)"
    )
    print(f"  active scenarios        : {b['scenarios']}")
    print(f"  lognormal loss fields   : {b['lognormal_fields']}")
    print(
        f"  post-PR1 sigma check    : max (sigma-{SIGMA_DEFAULT}) = {b['worst_sigma_dev']:+.3e} "
        f"[{b['worst_sigma_field']}]  (must be <= {SIGMA_BASIS_TOL:.0e}; narrow-only sweep, D6')"
    )

    scen = _active_scenarios(db)
    ln, pert_bound = _lognormal_bearing(scen)
    cap = K_CAPACITY * rev

    print(
        f"\n[B-CAP-SIM] simulated max single-event LM, seeds={SIM_SEEDS}, active lognormal-bearing"
    )
    print(
        f"  per-distribution cap = k x revenue = ${cap:,.0f}  (event total bounded at 2k x revenue)"
    )
    for label, c in (("today (uncapped)", None), (f"capped k={K_CAPACITY}", cap)):
        r = _sim_max(ln, c)
        verdict = "" if c is None else ("   D8 PASS" if r["median"] <= rev else "   D8 FAIL")
        print(
            f"  {label:<18} min ${r['min']:>18,.0f}  median ${r['median']:>18,.0f} "
            f"({r['median'] / rev * 100:7.2f}% rev)  max ${r['max']:>18,.0f}{verdict}"
        )
    print(f"  PERT-only scenarios cannot exceed ${pert_bound:,.0f} (analytic bound high_P+high_S)")
    print(
        f"  HARD analytic bound, n-invariant: maxP+maxS = 2k x revenue = ${2 * cap:,.0f} "
        f"({2 * K_CAPACITY * 100:.0f}% rev)"
    )
    print("  estimator: standalone per-scenario substreams (sha256 name digest); NOT the engine")
    print("  -- no shared stream, no vulnerability thinning. Valid for the seed-spread of the")
    print("  max, not for reproducing a run.")

    print("\n[B-CAP-N] D8's verdict is a FUNCTION OF n -- the uncapped reading is n-conditional")
    print("  The max of an unbounded heavy-tailed sample grows without bound in n, so an")
    print("  uncapped D8 reading is meaningless without a declared iteration count. Only the")
    print("  HARD bound above is a true n-invariant guarantee.")
    print(f"  {'n_iters':>10}  {'uncapped % rev':>15}  {'':>6}  {'capped % rev':>13}  {'':>6}")
    saved = globals()["ITERS"]
    for n in N_SENSITIVITY:
        globals()["ITERS"] = n
        unc = _sim_max(ln, None)["median"] / rev * 100
        cpd = _sim_max(ln, cap)["median"] / rev * 100
        tag = "  <== BASIS (Settings.mc_iterations_max)" if n == saved else ""
        print(
            f"  {n:>10,}  {unc:>14.2f}%  {'PASS' if unc <= 100 else 'FAIL':>6}  "
            f"{cpd:>12.2f}%  {'PASS' if cpd <= 100 else 'FAIL':>6}{tag}"
        )
    globals()["ITERS"] = saved
    print("  NOTE: at Settings.mc_iterations_default the UNCAPPED state passes D8 -- the")
    print("  pathology is invisible there. The basis is pinned to the supported CEILING.")

    print("\n[B-CAP-K] D8 across the k_capacity bracket, with the retention columns")
    print("  D8: median of the simulated max LM over seeds 0-9 <= 100% of annual_revenue")
    print("  D8 IS A ONE-SIDED (UPPER) GATE: it passes for every k below the binding value,")
    print("  including caps aggressive enough to destroy most of the expected loss. The")
    print("  retention columns are what discriminate the low end -- D8 alone does not.")
    print(
        f"  {'k':>7} {'cap':>17} {'median max LM':>17} {'% rev':>8} {'D8':>5} "
        f"{'med.retain':>11} {'worst.retain':>13}"
    )
    for k in K_BRACKET:
        kcap = k * rev
        r = _sim_max(ln, kcap)
        rets = sorted(
            mean_retained(d["mean"], d["sigma"], kcap)
            for _, pld, sld in ln
            for _, d in _loss_fields({"primary_loss": pld, "secondary_loss": sld})
        )
        mark = "  <== CHOSEN" if k == K_CAPACITY else ""
        print(
            f"  {k:>7.3f} ${kcap:>16,.0f} ${r['median']:>16,.0f} {r['median'] / rev * 100:>7.2f}% "
            f"{'PASS' if r['median'] <= rev else 'FAIL':>5} "
            f"{st.median(rets) * 100:>10.3f}% {rets[0] * 100:>12.3f}%{mark}"
        )

    print(f"\n[B-CAP-DRIFT] realized-mean retention under the k={K_CAPACITY} cap (100% = no cost)")
    print("  the cap is a TAIL guardrail: post-PR1 it costs essentially nothing in expected loss.")
    print("  PR2's per-run disclosure will therefore read ~0.0% — that is correct, not broken.")
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
        med = st.median([r for r, _ in rets])
        print(
            f"  {label:<22} n={len(rets):<3} median retained {med * 100:7.3f}%   "
            f"worst retained {rets[0][0] * 100:7.3f}%  [{rets[0][1]}]"
        )

    print("\n[B-CAP-ALT] the REJECTED quantile-anchored fallback (why D14 has no fallback)")
    print("  This row is an ANALYTIC IDENTITY, not an N-field measurement. Retention under a")
    print("  quantile anchor at level q is Phi(z_q - sigma)/Phi(z_q) -- a function of (sigma, q)")
    print("  ONLY: mu cancels. Every catastrophic library field carries the same sigma, so there")
    print("  is exactly ONE degree of freedom and the median/worst/count columns below carry no")
    print("  information beyond a single evaluation. They are printed to show that spread is")
    print("  zero BY CONSTRUCTION -- which is precisely the point: a quantile anchor removes the")
    print("  same slice of a tail-fed mean on EVERY field regardless of scenario size, while a")
    print("  capacity anchor is ABSOLUTE and bites only the large ones.")
    print("  Basis caveat: the fallback would only ever fire where annual_revenue is NULL, so")
    print("  this compares 'quantile everywhere' with 'capacity everywhere', not the literal")
    print("  counterfactual. The supported claim is: wherever it fired, it would cost the")
    print("  retention shown, uniformly.")
    lib = [(e["slug"], f, d) for e in _entries() for f, d in _loss_fields(e)]
    for label, fn in (
        ("p99.9 of parent", lambda d: math.exp(d["mean"] + norm.ppf(0.999) * d["sigma"])),
        ("p99.99 of parent", lambda d: math.exp(d["mean"] + norm.ppf(0.9999) * d["sigma"])),
        (f"k={K_CAPACITY} x revenue", lambda d: cap),
    ):
        rets = [mean_retained(d["mean"], d["sigma"], fn(d)) for _, _, d in lib]
        print(
            f"  {label:<22} median retained {st.median(rets) * 100:7.3f}%   "
            f"worst {min(rets) * 100:7.3f}%   fields below 99%: {sum(1 for r in rets if r < 0.99)}/{len(rets)}"
        )
    zs = [(math.log(cap) - d["mean"]) / d["sigma"] for _, _, d in lib]
    print(
        f"  where the capacity cap sits on those {len(lib)} fields: z = {min(zs):.3f} .. {max(zs):.3f}  "
        f"(P(event>cap) = {1 - ndtr(max(zs)):.2e} .. {1 - ndtr(min(zs)):.2e})"
    )

    print("\n[B-CAP-MIX] per-component vs mixture-conditioned truncation — the semantic deviation")
    print("  The sampler truncates EACH component at the shared cap, retaining density")
    print("  sum(w_i f_i / F_i(M)). Conditioning the MIXTURE on X<=M would retain")
    print("  sum(w_i f_i) / sum(w_j F_j(M)). Per-component therefore over-weights heavy")
    print("  components; the effective-weight distortion is F_bar / F_i(M).")
    print("  Task 4's floor requires M > every component's p95, so F_i(M) > Phi(z95) for all i,")
    print("  which bounds the worst-case distortion analytically:")
    floor_bound = 1.0 / ndtr(Z95) - 1.0
    print(
        f"    max weight distortion at the floor = 1/Phi(z95) - 1 = {floor_bound * 100:.3f}%  "
        "(the binding worst case)"
    )
    deep_b = (math.log(cap) - min(d["mean"] for _, _, d in lib)) / SIGMA_DEFAULT
    print(
        f"    at the SHIPPED cap the components sit at z >= {min(zs):.3f}, so F_i > "
        f"{ndtr(min(zs)):.9f} and the distortion is <= {(1 / ndtr(min(zs)) - 1) * 100:.5f}%"
    )
    print(f"    (deepest library component sits at z = {deep_b:.3f})")
    print("  So the deviation is bounded by the validator's own floor and is negligible at the")
    print("  shipped cap. Per-component is the CHOSEN 'each regime capped at capacity' semantics.")

    print("\n[B-CAP-PORT] analytic portfolio ALE — population=active-only")
    today = portfolio_ale(db, None)
    after = portfolio_ale(db, cap)
    print(f"  population {len(scen)} scenarios")
    print(f"  today ${today:,.0f} -> capped ${after:,.0f}  = {(after / today - 1) * 100:+.4f}%")


if __name__ == "__main__":
    main()
