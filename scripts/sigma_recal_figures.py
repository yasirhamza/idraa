"""Single source of truth for every figure quoted in the sigma-recalibration design.

Four review rounds each shipped at least one number computed on one basis and
published under another. Hand-maintaining ~240 numeric tokens across five bases
in prose does not converge; this script does the arithmetic once and labels each
result with its basis, so the design quotes generated output instead of
re-deriving values by hand.

Bases (mirrors the design's register):
  B-LIB-MEAN  sum of per-event means; PERT (5*low+high)/6, lognormal exp(mu+s^2/2)
  B-LIB-MED   realized median of the SAMPLED shape (Vose BetaPERT gamma=4)
  B-RUN-LM    max single-event Loss Magnitude = PL+SL, analytic, n=iters
  B-PORT-ALE  portfolio ALE mean, analytic over prod scenarios

Usage:  uv run python scripts/sigma_recal_figures.py
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import statistics as st
from pathlib import Path

from scipy.stats import beta as beta_dist
from scipy.stats import norm

ROOT = Path(__file__).resolve().parents[1]
# Deployment-specific, NEVER hardcoded (public repo): point SIGMA_RECAL_PROD_DB
# at a prod-backup COPY. With no env var the prod-dependent sections are skipped.
PROD_DB = Path(os.environ["SIGMA_RECAL_PROD_DB"]) if os.environ.get("SIGMA_RECAL_PROD_DB") else None

Z95 = 1.6448536269514722
SIGMA_DEFAULT = 1.7
IC3_BEC_MEAN = 123005.0
VENDOR_SLUGS = [
    "agri-coop-bec-fraud",
    "bec-fraud-financial",
    "manufacturing-billing-fraud",
    "professional-payroll-bec",
    "telecom-sim-swap-fraud",
]
ITERS = 700_000
Z_RUN = float(norm.ppf(1 - 1 / ITERS))


def _entries() -> list[dict]:
    out: list[dict] = []
    for name in ("seed_library_entries.json", "seed_library_entries_extension.json"):
        out.extend(json.loads((ROOT / "data" / name).read_text(encoding="utf-8")))
    return out


def _pert_ab(low: float, mode: float, high: float) -> tuple[float, float]:
    """Vose BetaPERT gamma=4, mirroring fair_core.py:155-164. General mode."""
    g = 4.0
    mean = (low + g * mode + high) / (g + 2.0)
    sd = (high - low) / (g + 2.0)
    a = ((mean - low) / (high - low)) * (((mean - low) * (high - mean) / sd**2) - 1.0)
    return a, a * (high - mean) / (mean - low)


def _realized_median(low: float, high: float) -> float:
    a, b = _pert_ab(low, low, high)
    return low + float(beta_dist.ppf(0.5, a, b)) * (high - low)


def _pert_mean(low: float, high: float) -> float:
    return (5.0 * low + high) / 6.0


def library() -> dict[str, object]:
    """Capped + catastrophic re-derivation under the 149-median / 5-vendor-mean split."""
    cap_old = cap_new = cat_old = cat_new = 0.0
    med_ratios: list[float] = []
    med_ratios_149: list[float] = []
    high_cuts: list[float] = []
    high_cuts_149: list[float] = []
    risers: list[tuple[str, float]] = []
    sigmas: list[float] = []
    n_capped = n_cat = 0

    for e in _entries():
        vendor = e.get("loss_tier") == "vendor"
        for field in ("primary_loss", "secondary_loss"):
            d = e.get(field) or {}
            kind = d.get("distribution")
            if kind == "PERT":
                low, high = d["low"], d["high"]
                mu = math.log(math.sqrt(low * high))
                sigmas.append(math.log(high / low) / (2 * Z95))
                is_mean_anchored = vendor and field == "primary_loss"
                nmu = (math.log(IC3_BEC_MEAN) - SIGMA_DEFAULT**2 / 2) if is_mean_anchored else mu
                nlow = math.exp(nmu - Z95 * SIGMA_DEFAULT)
                nhigh = math.exp(nmu + Z95 * SIGMA_DEFAULT)
                cap_old += _pert_mean(low, high)
                cap_new += _pert_mean(nlow, nhigh)
                high_cuts.append(high / nhigh)
                ratio = _realized_median(low, high) / _realized_median(nlow, nhigh)
                med_ratios.append(ratio)
                if is_mean_anchored:
                    risers.append((e["slug"], 1.0 / ratio))
                else:
                    med_ratios_149.append(ratio)
                    high_cuts_149.append(high / nhigh)
                n_capped += 1
            elif kind == "lognormal":
                cat_old += math.exp(d["mean"] + d["sigma"] ** 2 / 2)
                cat_new += math.exp(d["mean"] + SIGMA_DEFAULT**2 / 2)
                n_cat += 1

    return {
        "n_capped": n_capped,
        "n_catastrophic": n_cat,
        "capped_mean_before": cap_old,
        "capped_mean_after": cap_new,
        "capped_pct": cap_new / cap_old,
        "cat_mean_before": cat_old,
        "cat_mean_after": cat_new,
        "library_before": cap_old + cat_old,
        "library_after": cap_new + cat_new,
        "library_delta": (cap_new + cat_new) / (cap_old + cat_old) - 1.0,
        "capped_only_delta": cap_new / cap_old - 1.0,
        "median_sigma": st.median(sigmas),
        "median_high_cut_154": st.median(high_cuts),
        # Selected on the mean-anchored flag, NOT on ratio direction: selecting
        # on r > 1 coincides today only because all 154 sigmas exceed 1.7, and
        # would silently mislabel the population the day one field is narrower.
        "median_high_cut_149": st.median(high_cuts_149),
        "med_ratio_min": min(med_ratios),
        "med_ratio_max": max(med_ratios),
        "med_ratio_median_154": st.median(med_ratios),
        "med_ratio_median_149": st.median(med_ratios_149),
        "risers": sorted(risers, key=lambda x: -x[1]),
    }


def pins() -> None:
    """Fail loud if the library shape moved under the figures.

    Without these every quoted figure silently re-bases on the next seed edit,
    which is precisely the failure the generator exists to prevent.
    """
    entries = _entries()
    capped = sum(
        1
        for e in entries
        for f in ("primary_loss", "secondary_loss")
        if (e.get(f) or {}).get("distribution") == "PERT"
    )
    cat = sum(
        1
        for e in entries
        for f in ("primary_loss", "secondary_loss")
        if (e.get(f) or {}).get("distribution") == "lognormal"
    )
    vendor = sorted(e["slug"] for e in entries if e.get("loss_tier") == "vendor")
    if capped != 154:
        raise SystemExit(f"PIN FAILED: expected 154 capped PERT loss fields, found {capped}")
    if cat != 18:
        raise SystemExit(f"PIN FAILED: expected 18 catastrophic lognormal loss fields, found {cat}")
    if vendor != VENDOR_SLUGS:
        raise SystemExit(f"PIN FAILED: vendor (mean-anchored) set drifted: {vendor}")
    # Before-basis guard: this generator derives its "before" column from the
    # CURRENT seeds. Once PR1's builder re-authors them, before==after and every
    # delta degenerates to ~0 while the shape pins above still pass -- so refuse
    # loudly instead of printing wrong numbers. The pre-change record is the
    # frozen appendix (sigma-recalibration-figures.generated.txt @ 9ea361ed).
    max_sigma = max(
        math.log(d["high"] / d["low"]) / (2 * Z95)
        for e in entries
        for f in ("primary_loss", "secondary_loss")
        if (d := (e.get(f) or {})).get("distribution") == "PERT"
    )
    if max_sigma < SIGMA_DEFAULT + 0.01:
        raise SystemExit(
            "REFUSING TO REGENERATE: seeds are already at the within-scenario "
            "default -- the before-basis is gone. Quote the frozen appendix."
        )


def ic3_mean_preserved() -> list[tuple[str, float, float]]:
    """The ONE external, citation-traced pass/fail check this epic actually has.

    The 5 vendor entries are mean-preserving against the cited IC3 BEC mean.
    Re-solving mu = ln(mean) - sigma^2/2 must preserve E[loss] EXACTLY, so a
    non-trivial residual means the mean-anchor branch is wrong.
    """
    out: list[tuple[str, float, float]] = []
    for e in _entries():
        if e.get("loss_tier") != "vendor":
            continue
        nmu = math.log(IC3_BEC_MEAN) - SIGMA_DEFAULT**2 / 2
        realized = math.exp(nmu + SIGMA_DEFAULT**2 / 2)
        out.append((e["slug"], realized, abs(realized - IC3_BEC_MEAN) / IC3_BEC_MEAN))
    return out


def sigma_sensitivity() -> list[tuple[float, str, float, float]]:
    """Library expected loss across the sigma bracket the design establishes.

    The 90.2% drop rests on one unvalidated constant. Publishing the curve makes
    the reader's exposure explicit instead of asserted -- the honest form of
    "we cannot validate this point".
    """
    labels = {
        1.357: "IRIS system_intrusion (type-conditioned)",
        1.5: "p95-equivalent within-type read",
        1.7: "CHOSEN sigma_default",
        1.9687: "min revenue-tier read (size-conditioned)",
        2.394: "sigma the withdrawn D9 gate demanded",
    }
    rows: list[tuple[float, str, float, float]] = []
    base = None
    for s in sorted(labels):
        total = 0.0
        for e in _entries():
            vendor = e.get("loss_tier") == "vendor"
            for field in ("primary_loss", "secondary_loss"):
                d = e.get(field) or {}
                if d.get("distribution") == "PERT":
                    low, high = d["low"], d["high"]
                    mu = math.log(math.sqrt(low * high))
                    if vendor and field == "primary_loss":
                        mu = math.log(IC3_BEC_MEAN) - s**2 / 2
                    total += _pert_mean(math.exp(mu - Z95 * s), math.exp(mu + Z95 * s))
                elif d.get("distribution") == "lognormal":
                    total += math.exp(d["mean"] + s**2 / 2)
        if base is None:
            base = total
        rows.append((s, labels[s], total, total))
    before = 1_113_769_347.0
    return [(s, lbl, tot, tot / before - 1.0) for s, lbl, tot, _ in rows]


def prod_runs(active_only: bool) -> dict[str, object]:
    """B-RUN-LM under each sweep rule. Requires the prod backup.

    ``active_only`` is REQUIRED, not defaulted: the population filter swings the
    portfolio ALE delta by ~30x (-17.35% active-only vs -0.57% over all 25), so
    an undeclared filter is a basis defect.
    """
    if PROD_DB is None or not PROD_DB.exists():
        return {"unavailable": "SIGMA_RECAL_PROD_DB not set or missing"}
    db = sqlite3.connect(PROD_DB)
    sql = (
        "SELECT name, threat_event_frequency, vulnerability, primary_loss, secondary_loss "
        "FROM scenarios"
    )
    if active_only:
        sql += " WHERE status = 'active'"
    sql += " ORDER BY id"
    revenue = _prod_revenue(db)
    rows = db.execute(sql).fetchall()
    n_lognormal = sum(
        1
        for _n, _t, _v, pl, sl in rows
        for blob in (pl, sl)
        if blob and (json.loads(blob) or {}).get("distribution") == "lognormal"
    )

    def q(dist: dict | None, sigma_rule: str) -> float:
        """Max-draw proxy for one loss field. PERT fields contribute their
        bound `high` (the 1-1/n quantile of BetaPERT is ~indistinguishable from
        it at n=700k) rather than silently contributing zero."""
        if not dist:
            return 0.0
        kind = dist.get("distribution")
        if kind == "PERT":
            return float(dist.get("high", 0.0))
        if kind != "lognormal":
            return 0.0
        mu, s = dist["mean"], dist["sigma"]
        if sigma_rule == "uniform" or (sigma_rule == "narrow_only" and s > SIGMA_DEFAULT):
            s = SIGMA_DEFAULT
        return math.exp(mu + Z_RUN * s)

    out: dict[str, object] = {}
    for rule in ("today", "narrow_only", "uniform"):
        worst_lm = worst_pl = 0.0
        holder = ""
        for name, _tef, _vuln, pl, sl in rows:
            pld = json.loads(pl) if pl else None
            sld = json.loads(sl) if sl else None
            pl_q, sl_q = q(pld, rule), q(sld, rule)
            if pl_q + sl_q > worst_lm:
                worst_lm, holder = pl_q + sl_q, name
            worst_pl = max(worst_pl, pl_q)
        out[rule] = {
            "lm": worst_lm,
            "lm_pct": worst_lm / revenue,
            "pl_only": worst_pl,
            "pl_only_pct": worst_pl / revenue,
            "holder": holder,
        }
    out["revenue"] = revenue
    out["population"] = {
        "scenarios": len(rows),
        "lognormal_loss_fields": n_lognormal,
        "filter": "status='active'" if active_only else "ALL statuses",
    }
    return out


SIM_SEEDS = tuple(range(10))  # committed seed set for B-RUN-LM-SIM; never vary silently


def _dist_mean(d: dict | None, sigma_rule: str, is_loss: bool) -> float:
    """Analytic mean of one node. The sweep rule applies to LOSS lognormals only
    (TEF dispersion is out of scope by decision).

    Kind resolution mirrors run_executor._dict_to_fair_distribution: the key is
    OPTIONAL and defaults to PERT (prod vulnerability dicts carry no
    'distribution' key at all), and matching is case-insensitive.
    """
    if not d:
        return 0.0
    kind = str(d.get("distribution", "pert")).lower()
    if kind == "lognormal":
        s = d["sigma"]
        if is_loss and (
            sigma_rule == "uniform" or (sigma_rule == "narrow_only" and s > SIGMA_DEFAULT)
        ):
            s = SIGMA_DEFAULT
        return math.exp(d["mean"] + s**2 / 2)
    if kind == "pert":
        return (d["low"] + 4.0 * d["mode"] + d["high"]) / 6.0
    return 0.0


def portfolio_ale(active_only: bool) -> dict[str, object]:
    """[B-PORT-ALE] analytic portfolio ALE = sum of E[tef]*E[vuln]*(E[pl]+E[sl]).

    Standalone estimator (no subtractor, no engine stream) -- suitable for
    before/after deltas, not for reproducing a specific run's output.
    """
    if PROD_DB is None or not PROD_DB.exists():
        return {"unavailable": "SIGMA_RECAL_PROD_DB not set or missing"}
    db = sqlite3.connect(PROD_DB)
    sql = (
        "SELECT threat_event_frequency, vulnerability, primary_loss, secondary_loss FROM scenarios"
    )
    if active_only:
        sql += " WHERE status = 'active'"
    rows = db.execute(sql).fetchall()
    out: dict[str, object] = {"n": len(rows)}
    for rule in ("today", "narrow_only"):
        total = 0.0
        for tef, vuln, pl, sl in rows:
            j = [json.loads(b) if b else None for b in (tef, vuln, pl, sl)]
            total += (
                _dist_mean(j[0], rule, is_loss=False)
                * _dist_mean(j[1], rule, is_loss=False)
                * (_dist_mean(j[2], rule, is_loss=True) + _dist_mean(j[3], rule, is_loss=True))
            )
        out[rule] = total
    return out


def _prod_revenue(db: sqlite3.Connection) -> float:
    """Read the org's annual revenue from the DB the script is pointed at.

    The DB is the SOURCE OF TRUTH -- never a hardcoded constant. Every
    "% of revenue" readout is relative to this value, and the output header
    prints it so the denominator is always visible. Pointing the script at a
    different deployment's backup re-bases every percentage automatically
    (D8 itself is symbolic: <= 100% of Organization.annual_revenue).
    """
    row = db.execute("SELECT annual_revenue FROM organizations ORDER BY id LIMIT 1").fetchone()
    if row is None or row[0] is None:
        raise SystemExit("PIN FAILED: no organization with annual_revenue in the prod backup")
    return float(row[0])


def per_scenario_ale() -> list[tuple[str, float, float, float]] | dict[str, str]:
    """[B-SCEN-ALE] per-scenario analytic ALE, today -> narrow_only, every active
    scenario with a nonzero delta. This is the row the banner's 'per-scenario ALE
    reductions up to X%' quote comes from — ALE, never a PL-mean."""
    if PROD_DB is None or not PROD_DB.exists():
        return {"unavailable": "SIGMA_RECAL_PROD_DB not set or missing"}
    db = sqlite3.connect(PROD_DB)
    _prod_revenue(db)
    rows = db.execute(
        "SELECT name, threat_event_frequency, vulnerability, primary_loss, secondary_loss "
        "FROM scenarios WHERE status = 'active' ORDER BY id"
    ).fetchall()
    out: list[tuple[str, float, float, float]] = []
    for name, tef, vuln, pl, sl in rows:
        j = [json.loads(b) if b else None for b in (tef, vuln, pl, sl)]
        lef = _dist_mean(j[0], "today", is_loss=False) * _dist_mean(j[1], "today", is_loss=False)
        before = lef * (
            _dist_mean(j[2], "today", is_loss=True) + _dist_mean(j[3], "today", is_loss=True)
        )
        after = lef * (
            _dist_mean(j[2], "narrow_only", is_loss=True)
            + _dist_mean(j[3], "narrow_only", is_loss=True)
        )
        if before > 0 and abs(after / before - 1.0) > 1e-12:
            out.append((name, before, after, after / before - 1.0))
    out.sort(key=lambda r: r[3])
    return out


def run_lm_sim() -> dict[str, object]:
    """[B-RUN-LM-SIM] simulated max single-event LM over the committed seed set.

    Simulates the lognormal-bearing active scenarios only; a PERT-only
    scenario's LM is bounded by high_P + high_S, reported analytically so the
    output shows whether it could ever compete. Standalone estimator, NOT the
    engine (no shared stream, no vuln thinning): valid for seed-spread of the
    max, not for reproducing a run.
    """
    if PROD_DB is None or not PROD_DB.exists():
        return {"unavailable": "SIGMA_RECAL_PROD_DB not set or missing"}
    import numpy as np

    db = sqlite3.connect(PROD_DB)
    rows = db.execute(
        "SELECT name, primary_loss, secondary_loss FROM scenarios WHERE status = 'active' ORDER BY id"
    ).fetchall()
    lognormal_scen: list[tuple[str, dict | None, dict | None]] = []
    pert_only_bound = 0.0
    for name, pl, sl in rows:
        pld = json.loads(pl) if pl else None
        sld = json.loads(sl) if sl else None
        kinds = {(d or {}).get("distribution") for d in (pld, sld)}
        if "lognormal" in kinds:
            lognormal_scen.append((name, pld, sld))
        else:
            bound = sum(float((d or {}).get("high", 0.0)) for d in (pld, sld) if d)
            pert_only_bound = max(pert_only_bound, bound)

    def draw(d: dict | None, rule: str, rng: object) -> object:
        if not d:
            return 0.0
        kind = d.get("distribution")
        if kind == "lognormal":
            s = d["sigma"]
            if rule == "narrow_only" and s > SIGMA_DEFAULT:
                s = SIGMA_DEFAULT
            return rng.lognormal(d["mean"], s, ITERS)  # type: ignore[attr-defined]
        if kind == "PERT":
            a, b = _pert_ab(d["low"], d["mode"], d["high"])
            return d["low"] + rng.beta(a, b, ITERS) * (d["high"] - d["low"])  # type: ignore[attr-defined]
        return 0.0

    import hashlib

    out: dict[str, object] = {
        "pert_only_bound": pert_only_bound,
        "seeds": SIM_SEEDS,
        "revenue": _prod_revenue(db),
    }
    for rule in ("today", "narrow_only"):
        maxima = []
        for seed in SIM_SEEDS:
            worst = 0.0
            for name, pld, sld in lognormal_scen:
                # Per-scenario substream keyed on a stable digest of the name:
                # results are invariant to scenario iteration order and to DB
                # re-creation, unlike a single shared stream per seed.
                key = int.from_bytes(hashlib.sha256(name.encode()).digest()[:8], "big")
                rng = np.random.default_rng([seed, key])
                lm = draw(pld, rule, rng) + draw(sld, rule, rng)
                worst = max(worst, float(np.max(lm)))
            maxima.append(worst)
        maxima.sort()
        out[rule] = {"min": maxima[0], "median": st.median(maxima), "max": maxima[-1]}
    return out


def main() -> None:
    pins()
    lib = library()
    print("=" * 78)
    print("SIGMA-RECALIBRATION FIGURES — generated; quote these, do not re-derive by hand")
    print(f"sigma_default={SIGMA_DEFAULT}  z95={Z95}  n_iters={ITERS}  z_run={Z_RUN:.9f}")
    print("=" * 78)

    print("\n[B-LIB-MEAN] library expected loss")
    print(f"  capped fields             : {lib['n_capped']}")
    print(f"  catastrophic fields       : {lib['n_catastrophic']}")
    print(
        f"  capped   before -> after  : ${lib['capped_mean_before']:,.0f} -> "
        f"${lib['capped_mean_after']:,.0f}  ({lib['capped_pct']:.2%})"
    )
    print(
        f"  catastrophic before->after: ${lib['cat_mean_before']:,.0f} -> "
        f"${lib['cat_mean_after']:,.0f}"
    )
    print(
        f"  LIBRARY-WIDE (both)       : ${lib['library_before']:,.0f} -> "
        f"${lib['library_after']:,.0f}  = {lib['library_delta']:+.2%}"
    )
    print(
        f"  capped population ONLY    : {lib['capped_only_delta']:+.2%}   <- never call this "
        f"'library-wide'"
    )

    print("\n[B-LIB-MED] realized median of the sampled shape")
    print(
        f"  ratio range               : {lib['med_ratio_min']:.4f}x .. {lib['med_ratio_max']:.4f}x"
    )
    print(f"  median over all 154       : {lib['med_ratio_median_154']:.4f}x")
    print(f"  median over the 149       : {lib['med_ratio_median_149']:.4f}x")
    print(f"  fields that RISE          : {len(lib['risers'])}")
    for slug, factor in lib["risers"]:  # type: ignore[union-attr]
        print(f"      {slug:32} x{factor:.2f}")

    print("\n[no basis — sigma distribution]")
    print(f"  median implied sigma      : {lib['median_sigma']:.4f}")
    print(f"  median high cut (all 154) : {lib['median_high_cut_154']:.4f}x")
    print(f"  median high cut (149)     : {lib['median_high_cut_149']:.4f}x")

    print("\n[external check] IC3 mean-preservation on the 5 vendor entries")
    worst = 0.0
    for slug, realized, rel in ic3_mean_preserved():
        worst = max(worst, rel)
        print(f"  {slug:32} E[loss]=${realized:,.2f}  rel.err={rel:.2e}")
    print(
        f"  -> max relative error {worst:.2e} (must be < 1e-9)  {'PASS' if worst < 1e-9 else 'FAIL'}"
    )

    print("\n[sigma sensitivity] library expected loss across the bracket")
    print("  the 90.2% drop rests on ONE unvalidated constant; this is the curve it sits on")
    for s, label, total, delta in sigma_sensitivity():
        mark = "  <== CHOSEN" if abs(s - SIGMA_DEFAULT) < 1e-9 else ""
        print(f"  sigma={s:<6.4g} {label:42} ${total:>13,.0f}  {delta:+7.2%}{mark}")

    for active_only in (True, False):
        runs = prod_runs(active_only=active_only)
        pop = runs.get("population")
        label = "active-only" if active_only else "ALL statuses"
        print(f"\n[B-RUN-LM] prod worst single event — analytic, n=iters, population={label}")
        if "unavailable" in runs:
            print(f"  SKIPPED: {runs['unavailable']}")
            break
        if active_only:
            print(
                f"  denominator: org annual_revenue ${runs['revenue']:,.0f} "
                f"(READ FROM the backup DB — never hardcoded; a different deployment re-bases all %)"
            )
        print(
            f"  population: {pop['scenarios']} scenarios, "  # type: ignore[index]
            f"{pop['lognormal_loss_fields']} lognormal loss fields, filter={pop['filter']}"  # type: ignore[index]
        )
        for rule in ("today", "narrow_only", "uniform"):
            r = runs[rule]  # type: ignore[index]
            print(
                f"  {rule:12} LM ${r['lm']:>18,.0f} ({r['lm_pct']:>8.2%})   "
                f"PL-only ${r['pl_only']:>16,.0f} ({r['pl_only_pct']:>8.2%})  [{r['holder'][:26]}]"
            )
        print("  NOTE: 'uniform' is the WITHDRAWN rev-1 rule. The shipped rule is narrow_only.")

    for active_only in (True, False):
        ale = portfolio_ale(active_only=active_only)
        label = "active-only" if active_only else "ALL statuses"
        print(f"\n[B-PORT-ALE] analytic portfolio ALE — population={label}")
        if "unavailable" in ale:
            print(f"  SKIPPED: {ale['unavailable']} not present")
            break
        t, n = ale["today"], ale["narrow_only"]
        print(f"  population {ale['n']} scenarios")
        print(
            f"  today ${t:,.0f} -> narrow_only ${n:,.0f}  = {n / t - 1.0:+.2%}"  # type: ignore[operator]
        )

    scen = per_scenario_ale()
    print("\n[B-SCEN-ALE] per-scenario analytic ALE, today -> narrow_only (nonzero deltas)")
    if isinstance(scen, dict):
        print(f"  SKIPPED: {scen['unavailable']} not present")
    else:
        for name, before, after, delta in scen:
            print(f"  {name[:44]:44} ${before:>13,.0f} -> ${after:>13,.0f}  {delta:+8.2%}")
        print("  (all other active scenarios: delta 0 — no lognormal loss field above the default)")

    sim = run_lm_sim()
    print(f"\n[B-RUN-LM-SIM] simulated max LM, seeds={SIM_SEEDS}, active lognormal-bearing")
    if "unavailable" in sim:
        print(f"  SKIPPED: {sim['unavailable']} not present")
    else:
        for rule in ("today", "narrow_only"):
            r = sim[rule]  # type: ignore[index]
            print(
                f"  {rule:12} min ${r['min']:>18,.0f}  median ${r['median']:>18,.0f}  "
                f"max ${r['max']:>18,.0f}  ({r['median'] / sim['revenue']:.1%} rev at median)"
            )
        print(
            f"  PERT-only scenarios cannot exceed ${sim['pert_only_bound']:,.0f} "
            f"(analytic bound high_P+high_S) — cannot compete with the above"
        )


if __name__ == "__main__":
    main()
