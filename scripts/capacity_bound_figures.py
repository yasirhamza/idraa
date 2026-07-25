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
  B-CAP-SCALE  residual-path divergence: scaling `max` by k vs leaving it unscaled
  B-CAP-FLOOR  D19's band: which library entries are uninstantiable at which revenue
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

# Sourced from norm.ppf, NOT hand-typed: the hand-typed literal was 1 ULP above
# float(norm.ppf(0.95)), which is what lognormal_from_quantiles uses to FIT sigma.
# Numerically inert (3.8e-16 relative on a recomputed p95), but the D19 floor is a
# STRICT `max > p95`, so an analyst typing a p95 of exactly one year's revenue -- a
# plausible authoring act -- has the block decided by a 1-ULP disagreement between
# two spellings of the same constant rather than by policy. Same source as the fit
# means the equality case is decided by D19, which is where that decision belongs.
Z95 = float(norm.ppf(0.95))
SIGMA_DEFAULT = 1.7
# Stored sigma is NOT exactly the default on fields that went through the wizard
# re-spread (quantiles round-trip through dollar values, so sigma is re-derived
# rather than assigned). The magnitude is deployment data and is PRINTED in
# B-CAP-BASIS, never restated here. The post-PR1 basis assertion therefore needs a
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
# to the SHIPPED default of Settings.mc_iterations_max. (The parked design's
# 700_000 was an artifact of one exemplar run and justified against neither
# Settings value; at Settings.mc_iterations_default = 10_000 the UNCAPPED state
# passes D8, which would have made the pathology invisible.)
#
# NOT the worst case over the SUPPORTED range: mc_iterations_max is itself
# tunable, and its own Field permits up to 10_000_000 (config.py:47), so a
# deployment can legitimately request 10x this basis. N_SENSITIVITY therefore
# carries that configurable ceiling as a row -- D8 must be shown to hold across
# the whole range an operator can configure, not just at the shipped default.
ITERS = 1_000_000
_MC_ITERATIONS_FIELD_CEILING = 10_000_000  # config.py:47 Field(le=...)
N_SENSITIVITY = (10_000, 100_000, 700_000, 1_000_000, _MC_ITERATIONS_FIELD_CEILING)
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

    THE PUBLIC VARIANT CARRIES ONLY CONTENT SAFE BY CONSTRUCTION. Round 6 cut it
    to three kinds and moved everything else to the full appendix, so there is no
    prod-derived figure in the public text to invert to revenue. A line may go
    PUBLIC only if it is one of:
      (a) an analytic identity in (sigma_default, z_q, k_capacity) with NO
          deployment input -- mu cancels, e.g. the quantile-anchor retentions
          (B-CAP-ALT) and the mixture distortion bounds (B-CAP-MIX);
      (a') a figure over the TRACKED PUBLIC seed library evaluated at a
          HYPOTHETICAL revenue sweep, with no deployment input anywhere in it
          (B-CAP-FLOOR; B-CAP-SIM's library-PERT sweep). Dollar-denominated and
          named, so it looks private -- it is public because the dollars are the
          library's own and the revenue column is a stated hypothetical, NOT this
          deployment's. This is why the informal "no `$` in the public variant"
          heuristic is false and why assert_public_artifact_is_clean checks
          registered VALUES, not shapes;
      (c) a PASS/FAIL verdict, an iteration count n, a k value, 2k as a
          percentage, or another non-deployment constant.

    EVERYTHING ELSE IS PRIVATE (full appendix only). In particular NO prod-derived
    statistic is published -- not %-of-revenue, retention curves, seed dispersion,
    per-scenario effect, the portfolio ALE ratio, or the PERT-over-cap ratio. An
    earlier design published prod-derived rows under a "homogeneous statistic /
    gauge-invariance" rule (b) backed by a mu-derivability census; round 6 removed
    both, because (i) the invariance claim was FALSE for published rows -- the ALE
    ratio and the PERT-bound ratio move under (R,{mu})->(lambda R,{mu+ln lambda})
    since the portfolio mixes scaling lognormal means with absolute PERT means --
    and (ii) a live prod mu was bit-exactly derivable from the public repo, so the
    census precondition was false while its guard passed. The structural fix (cut
    the public surface) dominates any guard: what the PR body needs -- that the cap
    makes D8 pass -- is fully carried by (a)/(a')/(c).
    """

    def __init__(self) -> None:
        self._lines: list[tuple[str, str]] = []

    def both(self, text: str = "") -> None:
        """Deployment-independent narrative or figure: appears in both artifacts."""
        self._lines.append(("both", text))

    def priv(self, text: str = "") -> None:
        """Full appendix only, NEVER public: any prod-derived or deployment figure."""
        self._lines.append(("private", text))

    def pub(self, text: str = "") -> None:
        """Public-only line: safe-by-construction content (rules a / a' / c)."""
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


def assert_settings_basis() -> None:
    """The two Settings numbers this whole basis rests on are ASSERTED, not copied.

    ITERS and _MC_ITERATIONS_FIELD_CEILING are hand-copied from config.py so the
    committed artifact stays deterministic -- but a hand-copy is exactly the
    stale-fact hazard this generator exists to remove. CLAUDE.md records
    mc_iterations_max going stale in a doc for ~3 weeks, and the design makes the
    ceiling row's margin the number to quote everywhere, so a moved Field(le=)
    would keep printing a row LABELLED "configurable ceiling" that no longer is one.
    """
    from idraa.config import Settings

    field = Settings.model_fields["mc_iterations_max"]
    default = field.default
    le = next((getattr(m, "le", None) for m in field.metadata if hasattr(m, "le")), None)
    if default != ITERS:
        raise SystemExit(
            f"BASIS FAILED: Settings.mc_iterations_max default is {default!r}, but this "
            f"generator's ITERS basis is {ITERS!r}. The shipped basis moved -- update ITERS "
            "and N_SENSITIVITY, then regenerate every quoted figure."
        )
    if le != _MC_ITERATIONS_FIELD_CEILING:
        raise SystemExit(
            f"BASIS FAILED: config.py's Field(le=) is {le!r}, but this generator labels "
            f"{_MC_ITERATIONS_FIELD_CEILING!r} as the CONFIGURABLE CEILING. The design quotes "
            "that row's margin as the worst case, so the label must not go stale."
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


# The public-mu census and derivability guard that used to live here were REMOVED
# in round 6. Once the public variant was cut to non-deployment content only
# (verdicts + analytic identities in (sigma, z, k) + library figures at a
# HYPOTHETICAL revenue sweep -- see Report), no prod-derived statistic is published,
# so there is nothing to invert to revenue and no mu-anchor to defend against. The
# census (_public_mu_candidates / _iris_loss_mus), MU_PUBLIC_TOL, _mu_matches,
# derivable_prod_fields, assert_public_prod_basis_safe, public_mu_margins and
# _dominant_max_field are all gone as dead. assert_public_artifact_is_clean remains
# as the mechanical backstop over the emitted public text.


def _renderings(value: float) -> list[str]:
    """Every plausible way a future author might render a private number.

    The check is a substring test, so registering only the ``,.0f`` form lets an
    identical value through in any other format. Verified evasions before this
    existed: ``.0f``, ``repr``, ``int``, ``.3e``, and underscore separators all
    passed while ``,.0f`` was caught.
    """
    out = [f"{value:,.0f}", f"{value:.0f}", f"{value:,.2f}", f"{value:.2f}", f"{value:.3e}"]
    out += [repr(value), repr(int(value)), f"{int(value):_}"]
    return [s for s in out if len(s) >= 6]  # short strings collide with counts/years


def assert_public_artifact_is_clean(
    text: str, *, numbers: list[tuple[float, str]], strings: list[tuple[str, str]]
) -> None:
    """[I-SEC-4] Check the EMITTED public text, not just each author's intent.

    Report moves classification into code, but nothing inspected the OUTPUT -- so a
    NEW line mis-classified by a future author still leaks, which is precisely what
    round 2's blocker was. B-CAP-FLOOR (rule a') also makes the naive prose
    heuristic ("no $ in the public variant") FALSE, removing the last informal
    backstop. This kills the whole literal-value class mechanically.

    THREE evasions this now covers that the round-4 version did not:
      * non-comma renderings of the same value (see _renderings);
      * TRUNCATED scenario labels -- the appendix prints ``name[:34]``, and every
        active name is longer than that, so a full-name substring test could not
        see the truncated form. Prefixes are registered explicitly;
      * dollar figures that were never registered at all (the PERT bound, the
        simulated min/max, every B-CAP-K cap and median column).

    WHAT IT STRUCTURALLY CANNOT CATCH, stated so nobody mistakes it for complete:
    small integers whose digit strings collide with legitimate public values
    (population counts like 15 or 9 collide with seed indices and n_iters digits).
    Report's per-line classification remains the primary control; this is the
    mechanical backstop.
    """
    for value, label in numbers:
        for rendered in _renderings(value):
            if rendered in text:
                raise SystemExit(
                    f"PUBLIC ARTIFACT LEAK: {label} rendered as {rendered!r} appears in the "
                    "public variant. A line was classified pub()/both() that must be priv()."
                )
    for value, label in strings:
        # Register the truncated forms the appendix actually prints, not just the full string.
        for candidate in {value, value[:34], value[:36]}:
            if len(candidate) >= 12 and candidate in text:
                raise SystemExit(
                    f"PUBLIC ARTIFACT LEAK: {label} ({candidate!r}) appears in the public "
                    "variant. A line was classified pub()/both() that must be priv()."
                )


def _prod() -> tuple[sqlite3.Connection, float, str]:
    # READ-ONLY by construction. The module comment and the design both instruct
    # pointing SIGMA_RECAL_PROD_DB at a prod-backup COPY, but that was
    # documentation-only and already not followed in practice (the carryover's own
    # reproduce command points at the single local backup). mode=ro enforces it,
    # and cannot create -wal/-shm files beside the backup.
    db = sqlite3.connect(f"file:{PROD_DB}?mode=ro", uri=True)
    revs = db.execute(
        "SELECT id, annual_revenue FROM organizations WHERE annual_revenue IS NOT NULL ORDER BY id"
    ).fetchall()
    if not revs or not revs[0][1]:
        raise SystemExit("no organization with annual_revenue in the backup")
    # SINGLE-ORG basis assertion. Every figure applies ONE org's capacity
    # (k x annual_revenue) to the active scenarios; with >1 revenue-bearing org the
    # `ORDER BY id LIMIT 1` pick would silently blend orgs (a wrong org's cap on
    # another's scenarios). v3 is single-org today, so fail loud rather than blend
    # if that ever changes.
    if len(revs) != 1:
        raise SystemExit(
            f"BASIS FAILED: expected exactly one org with annual_revenue, found {len(revs)}. "
            "The figures assume a single-org deployment; a multi-org backup would blend orgs."
        )
    org_id = revs[0][0]
    orphan = db.execute(
        "SELECT COUNT(*) FROM scenarios WHERE status = 'active' AND organization_id != ?",
        (org_id,),
    ).fetchone()[0]
    if orphan:
        raise SystemExit(
            f"BASIS FAILED: {orphan} active scenario(s) belong to an org other than the one "
            "supplying the capacity basis — the figures would apply the wrong org's cap."
        )
    head = db.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    return db, float(revs[0][1]), str(head)


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


def _prod_loss_fields(
    db: sqlite3.Connection, *, exclude: set[str] | None = None
) -> list[tuple[str, dict]]:
    """Labeled active prod lognormal loss fields, optionally minus a label set.

    One accessor for both bases so a PUBLIC row cannot accidentally be computed
    over the unfiltered population -- the exclusion is a keyword the caller must
    pass, and every pub()/both() per-field row below passes it.
    """
    exclude = exclude or set()
    return [
        (label, d)
        for name, pld, sld in _active_scenarios(db)
        for f, d in _loss_fields({"primary_loss": pld, "secondary_loss": sld})
        if (label := f"{name} {f}") not in exclude
    ]


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
    # [I-METH-4] The MIGRATION backfills `scenarios` with NO status filter, but
    # every other basis here is active-only. Quoting the active-only count as the
    # migration's expected row count is a population-filter error by construction.
    # Both counts are published so the plan can name the right one.
    all_status = 0
    for pl, sl in db.execute("SELECT primary_loss, secondary_loss FROM scenarios"):
        for raw in (pl, sl):
            d = json.loads(raw) if raw else None
            if isinstance(d, dict) and d.get("distribution") in ("lognormal", "lognormal_mixture"):
                all_status += 1
    return {
        "head": head,
        "revenue": rev,
        "scenarios": len(scen),
        "lognormal_fields": len(fields),
        "lognormal_fields_all_status": all_status,
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

    Closed at 0, open at max_value: rng.random() is [0, 1),
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
    return {
        "min": maxima[0],
        "median": st.median(maxima),
        "max": maxima[-1],
        "stdev": st.stdev(maxima) if len(maxima) > 1 else 0.0,
        "maxima": maxima,
    }


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
    assert_settings_basis()
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
    r.priv(f"  lognormal loss fields   : {b['lognormal_fields']}  (active-only)")
    r.priv(
        f"  lognormal loss fields   : {b['lognormal_fields_all_status']}  (ALL statuses — this is "
        "the migration's population, NOT the active-only count above)"
    )
    r.priv(
        f"  post-PR1 sigma check    : max (sigma-{SIGMA_DEFAULT}) = {b['worst_sigma_dev']:+.3e} "
        f"[{b['worst_sigma_field']}]  (must be <= {SIGMA_BASIS_TOL:.0e}; narrow-only sweep, D6')"
    )
    r.pub("")
    r.pub("[B-CAP-BASIS] WITHHELD — revenue, population, alembic head, authored scenario names.")
    r.pub("  The basis assertions ran and passed. The PUBLIC variant carries ONLY non-deployment")
    r.pub("  content: PASS/FAIL verdicts, analytic identities in (sigma, z, k), and library")
    r.pub("  figures at a HYPOTHETICAL revenue sweep. Every prod-derived magnitude — %-of-revenue,")
    r.pub("  retention, seed dispersion, per-scenario effect, portfolio ALE — is in the FULL")
    r.pub("  appendix ONLY and is never published, so there is nothing here to anchor to revenue.")

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
    leak_sim: list[tuple[float, str]] = []
    for label, c in (("today (uncapped)", None), (f"capped k={K_CAPACITY}", cap)):
        res = _sim_max(ln, c)
        leak_sim += [
            (res["min"], f"simulated min LM [{label}]"),
            (res["median"], f"simulated median LM [{label}]"),
            (res["max"], f"simulated max LM [{label}]"),
        ]
        verdict = "" if c is None else ("   D8 PASS" if res["median"] <= rev else "   D8 FAIL")
        pct = res["median"] / rev * 100
        r.priv(
            f"  {label:<18} min ${res['min']:>18,.0f}  median ${res['median']:>18,.0f} "
            f"({pct:7.2f}% rev)  max ${res['max']:>18,.0f}{verdict}"
        )
        # PUBLIC: the D8 VERDICT only (rule c). The % of revenue is prod-derived and
        # would combine with the retention curve to fix revenue, so it stays priv.
        r.pub(
            f"  {label:<18}{verdict if verdict else '   (uncapped baseline — figure in full appendix)'}"
        )
    r.priv(f"  PERT-only scenarios cannot exceed ${pert_bound:,.0f} (analytic bound high_P+high_S)")
    # PUBLIC: qualitative only. The prod PERT bound and its ratio to the cap are an
    # absolute prod dollar over the cap, which inverts to revenue directly, so both
    # are withheld; the library-only hypothetical below carries the quantitative point.
    r.pub("  PERT-only scenarios are bounded by their authored high_P+high_S (prod magnitude and")
    r.pub("  its ratio to the cap WITHHELD — an absolute prod dollar over the cap inverts to")
    r.pub("  revenue). The library-only hypothetical below shows the bound far under the cap.")
    r.priv(
        f"  HARD analytic bound, n-invariant: maxP+maxS = 2k x revenue = ${2 * cap:,.0f} "
        f"({2 * K_CAPACITY * 100:.0f}% rev)"
    )
    r.both("  BUT THE EQUALITY IS CONDITIONAL, NOT AN IDENTITY. `max` is applied to lognormal")
    r.both("  / lognormal_mixture loss fields ONLY, so the n-invariant bound is")
    r.both("    max LM <= bound_P + bound_S,  bound_f = k*revenue (capped lognormal)")
    r.both("                                          = high_f    (PERT — NOT capacity-bounded)")
    r.both("  and it collapses to 2k*revenue only where EVERY loss field's own bound is")
    r.both("  <= k*revenue. D19's floor does not help: it fires on lognormal components only,")
    r.both("  so a PERT-only scenario is never checked against capacity at all.")
    lib_pert = sorted(
        (
            sum(
                float(v.get("high", 0.0))
                for f in ("primary_loss", "secondary_loss")
                if isinstance(v := e.get(f), dict)
                and str(v.get("distribution", "pert")).lower() == "pert"
            ),
            e["slug"],
        )
        for e in _entries()
    )
    worst_lib_pert, worst_lib_slug = lib_pert[-1]
    r.both(
        f"  Worst LIBRARY PERT loss bound (high_P+high_S): ${worst_lib_pert:,.0f} "
        f"[{worst_lib_slug}]"
    )
    r.both("  -- public under rule (a'): library dollars plus a hypothetical revenue sweep.")
    r.both(f"  {'hypothetical revenue':>22}  {'2k*rev':>16}  {'worst PERT entry':>17}  verdict")
    for hyp in (5e6, 1e7, 5e7, 1e8):
        ok = worst_lib_pert <= 2 * K_CAPACITY * hyp
        r.both(
            f"  ${hyp:>21,.0f}  ${2 * K_CAPACITY * hyp:>15,.0f}  ${worst_lib_pert:>16,.0f}  "
            f"{'holds' if ok else 'VIOLATED (' + f'{worst_lib_pert / hyp:.2f}' + 'x rev)'}"
        )
    r.both("  So `2k x revenue` is safe to quote ONLY with the conditional clause. At THIS")
    r.both("  deployment's revenue every library PERT bound is orders of magnitude below the")
    r.both("  cap (row above), but the identity is not universal and must not be published as")
    r.both("  though it were.")
    r.pub(
        f"  HARD analytic bound, n-invariant: maxP+maxS = 2k x revenue = "
        f"{2 * K_CAPACITY * 100:.0f}% rev — subject to the conditional clause above."
    )
    r.both("  estimator: standalone per-scenario substreams (sha256 name digest); NOT the engine")
    r.both("  -- no shared stream, no vulnerability thinning. Valid for the seed-spread of the")
    r.both("  max, not for reproducing a run.")

    r.both("")
    r.both("[B-CAP-N] D8's verdict is a FUNCTION OF n -- the uncapped reading is n-conditional")
    r.both("  The max of an unbounded heavy-tailed sample grows without bound in n, so an")
    r.both("  uncapped D8 reading is meaningless without a declared iteration count. Only the")
    r.both("  HARD bound above is a true n-invariant guarantee.")
    r.both("  The median over the COMMITTED seed set is D8's criterion, so the verdict is")
    r.both("  deterministic -- but a median quoted WITHOUT its dispersion is a point estimate")
    r.both("  presented as a bound. The capped columns therefore carry the seed spread and the")
    r.both("  count of committed seeds that individually breach 100%: the seed set is the")
    r.both("  SECOND conditioning parameter, exactly as n is the first.")
    r.priv(
        f"  {'n_iters':>10}  {'uncapped % rev':>15}  {'':>6}  {'capped % rev':>13}  {'':>6}"
        f"  {'capped min..max':>17} {'sd':>7} {'#seeds>100%':>12}"
    )
    r.pub(f"  {'n_iters':>10}  {'uncapped D8':>12}   {'capped D8':>10}")
    saved = globals()["ITERS"]
    for n in N_SENSITIVITY:
        globals()["ITERS"] = n
        unc = _sim_max(ln, None)["median"] / rev * 100
        cpd_res = _sim_max(ln, cap)
        cpd = cpd_res["median"] / rev * 100
        spread = f"{cpd_res['min'] / rev * 100:.2f}..{cpd_res['max'] / rev * 100:.2f}"
        n_breach = sum(1 for m in cpd_res["maxima"] if m > rev)
        if n == saved:
            tag = "  <== BASIS (shipped Settings.mc_iterations_max)"
        elif n == _MC_ITERATIONS_FIELD_CEILING:
            tag = "  <== CONFIGURABLE CEILING (config.py Field le=)"
        else:
            tag = ""
        # The % columns and dispersion are prod-derived → full appendix only.
        # PUBLIC gets the D8 verdicts (rule c), which is what the PR body needs.
        r.priv(
            f"  {n:>10,}  {unc:>14.2f}%  {'PASS' if unc <= 100 else 'FAIL':>6}  "
            f"{cpd:>12.2f}%  {'PASS' if cpd <= 100 else 'FAIL':>6}"
            f"  {spread:>17} {cpd_res['stdev'] / rev * 100:>6.2f}% {n_breach:>11}{tag}"
        )
        r.pub(
            f"  {n:>10,}  {'PASS' if unc <= 100 else 'FAIL':>12}   "
            f"{'PASS' if cpd <= 100 else 'FAIL':>10}{tag}"
        )
    globals()["ITERS"] = saved
    r.both("  NOTE: at Settings.mc_iterations_default the UNCAPPED state passes D8 -- the")
    r.both("  pathology is invisible there. The basis is the SHIPPED mc_iterations_max; the")
    r.both("  last row is the ceiling that Setting's own Field still permits an operator to")
    r.both("  configure, so D8 is shown to hold across the whole configurable range -- with")
    r.both("  its TIGHTEST margin there, which is the number to quote as the worst case.")
    # The specific margin and dispersion are prod-derived magnitudes → full appendix.
    r.priv("  QUOTE THE MARGIN WITH ITS DISPERSION. At the ceiling the margin is a small")
    r.priv("  fraction of a percent while the committed-seed standard deviation is larger than")
    r.priv("  it, and individual committed seeds already exceed 100% at BOTH 700k and the")
    r.priv("  ceiling (see the #seeds>100% column). [figures in this FULL appendix only]")
    r.pub("  The capped MEDIAN passes across the configurable range, but that median carries")
    r.pub("  seed dispersion larger than its own margin and individual committed seeds can")
    r.pub("  breach 100% (dispersion in the full appendix). D8 is a median criterion, so the")
    r.pub("  PASS is legitimate and deterministic — but the margin is a point estimate, not a")
    r.pub("  bound. The only n-invariant bound is the analytic maxP+maxS row, with its clause.")

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
    # PUBLIC: k and the D8 verdict only (rules c). The % rev and the retention
    # columns are prod-derived — the 11-point retention curve over-determines a
    # field and recovers its sigma, so it stays in the full appendix.
    r.pub(f"  {'k':>7} {'D8':>5}")
    for k in K_BRACKET:
        kcap = k * rev
        res = _sim_max(ln, kcap)
        rets = sorted(mean_retained(d["mean"], d["sigma"], kcap) for _, d in _prod_loss_fields(db))
        mark = "  <== CHOSEN" if k == K_CAPACITY else ""
        pct, verdict = res["median"] / rev * 100, "PASS" if res["median"] <= rev else "FAIL"
        r.priv(
            f"  {k:>7.3f} ${kcap:>16,.0f} ${res['median']:>16,.0f} {pct:>7.2f}% "
            f"{verdict:>5} {st.median(rets) * 100:>10.3f}% {rets[0] * 100:>12.3f}%{mark}"
        )
        r.pub(f"  {k:>7.3f} {verdict:>5}{mark}")

    # The bracket above is measured at the BASIS. Since D8's verdict rises with n,
    # WHICH k values pass is itself n-conditional -- so the neighbourhood of the
    # chosen k is re-measured at the configurable ceiling. Without this, "a slightly
    # larger k also passes" reads as unconditional when it holds only at the basis.
    r.both("  the same neighbourhood re-measured at the CONFIGURABLE CEILING:")
    saved_iters = globals()["ITERS"]
    globals()["ITERS"] = _MC_ITERATIONS_FIELD_CEILING
    for k in (1.00, 1.10, 1.20):
        res = _sim_max(ln, k * rev)
        pct = res["median"] / rev * 100
        mark = "  <== CHOSEN" if k == K_CAPACITY else ""
        r.priv(
            f"  {k:>7.3f} {pct:>7.2f}% {'PASS' if res['median'] <= rev else 'FAIL':>5}"
            f"{'':>26}{mark}"
        )
        r.pub(f"  {k:>7.3f} {'PASS' if res['median'] <= rev else 'FAIL':>5}{mark}")
    globals()["ITERS"] = saved_iters
    r.both("  At the ceiling the chosen k is very nearly BINDING: the next bracket step up")
    r.both("  FAILS. So 'a slightly larger k also passes' holds at the basis and NOT at the")
    r.both("  ceiling -- D13's claim must be stated with its n, exactly as D8's must.")

    # B-CAP-DRIFT is realized-mean retention over PROD fields — entirely prod-derived,
    # so the whole section is full-appendix only. The D16 "near-zero cost is correct,
    # not a bug" point is product copy and lives in the design doc, not the public body.
    r.priv("")
    r.priv(f"[B-CAP-DRIFT] realized-mean retention under the k={K_CAPACITY} cap (100% = no cost)")
    r.priv("  the cap is a TAIL guardrail: post-PR1 it costs essentially nothing in expected loss.")
    r.priv(
        "  PR2's per-run disclosure will therefore read near-zero — correct per D16, not broken."
    )
    prod_ret = [
        (mean_retained(d["mean"], d["sigma"], cap), label[:34])
        for label, d in _prod_loss_fields(db)
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

    # B-CAP-SCEN is the per-SCENARIO retention basis (what the per-run disclosure
    # shows). Prod-derived, so full-appendix only — never public. D16's copy is
    # pinned to THIS basis in the design doc (operator-local); the shipped surface
    # renders the LIVE per-scenario figure, not a baked-in range.
    r.priv("")
    r.priv("[B-CAP-SCEN] per-SCENARIO retention — the basis the per-run disclosure actually shows")
    r.priv("  B-CAP-DRIFT above is per FIELD and B-CAP-PORT below is per PORTFOLIO. The PR2")
    r.priv("  disclosure is per SCENARIO, a THIRD basis: R_scen = sum_f E_f*R_f / sum_f E_f over")
    r.priv("  the scenario's loss fields, E_f the parent mean (lognormal exp(mu+sigma^2/2); PERT")
    r.priv("  (low+4*mode+high)/6), R_f = 1 for PERT. BOTH kinds in BOTH sums; LEF cancels.")
    scen_ret = []
    for name, pld, sld in ln:
        num = sum(_dist_mean(d, cap) for d in (pld, sld) if d)
        den = sum(_dist_mean(d, None) for d in (pld, sld) if d)
        if den > 0:
            scen_ret.append((num / den, name[:34]))
    scen_ret.sort()
    r.priv(
        f"  active lognormal-bearing scenarios n={len(scen_ret)}   median retained "
        f"{st.median([x for x, _ in scen_ret]) * 100:8.4f}%   worst retained "
        f"{scen_ret[0][0] * 100:8.4f}%  [{scen_ret[0][1]}]"
    )
    r.priv(
        f"  disclosed per-scenario cap effect spans {(1 - scen_ret[-1][0]) * 100:.4f}% to "
        f"{(1 - scen_ret[0][0]) * 100:.4f}% at the shipped k -- D16's copy is pinned to THIS"
    )
    r.priv(
        "  range in the design doc, NOT the per-field median, or the copy contradicts the surface."
    )

    r.both("")
    r.both("[B-CAP-SCALE] residual-path divergence: scaling `max` by k vs leaving it unscaled")
    r.both("  Under a magnitude multiplier k, _scale_distribution shifts mu -> mu + ln k. The")
    r.both("  CHOSEN treatment scales the cap too, leaving b = (ln(k*max) - mu - ln k)/sigma")
    r.both("  INVARIANT -- the residual is then exactly k x the inherent capped distribution.")
    r.both("  The rejected alternative leaves `max` fixed, so the residual truncates at a")
    r.both("  LESS binding point. Divergence in the residual mean is therefore")
    r.both("    mean_retained(mu + ln k, sigma, max) / mean_retained(mu, sigma, max) - 1,")
    r.both("  which is >= 0 for k <= 1 and rises monotonically as k falls. Its SUPREMUM over")
    r.both("  k is the k -> 0 limit, 1/mean_retained(mu, sigma, max) - 1.")
    r.both("  This decides nothing: the design's choice rests on the equivariance argument")
    r.both("  alone (common random numbers preserved at every uniform). The figure exists")
    r.both("  only to support the claim that the choice is materially INERT.")

    def _diverge(pairs: list[tuple[float, float]], k: float | None) -> float:
        """Worst-case divergence over a population; k=None gives the k->0 supremum.

        At k -> 0 the unscaled cap stops binding at all, so its retention -> 1.
        """
        worst = 0.0
        for mu, sg in pairs:
            base = mean_retained(mu, sg, cap)
            alt = 1.0 if k is None else mean_retained(mu + math.log(k), sg, cap)
            worst = max(worst, alt / base - 1.0)
        return worst

    # The numeric divergence table is prod-derived (and the library variant inverts
    # via its public mu) → full appendix only. The section's POINT is the prose above:
    # the choice rests on the equivariance argument alone, and the figure only confirms
    # it is materially inert. That argument, carrying no number, stays public.
    prod_pairs = [(d["mean"], d["sigma"]) for _, d in _prod_loss_fields(db)]
    lib_pairs = [(d["mean"], d["sigma"]) for e in _entries() for _, d in _loss_fields(e)]
    r.priv(f"  {'multiplier k':>14}  {'worst divergence (prod)':>24}")
    for k in (0.75, 0.50, 0.25, 0.10):
        r.priv(f"  {k:>14.2f}  {_diverge(prod_pairs, k) * 100:>23.4f}%")
    r.priv(f"  {'k -> 0 (sup)':>14}  {_diverge(prod_pairs, None) * 100:>23.4f}%")
    r.priv(f"  library population, k -> 0 supremum: {_diverge(lib_pairs, None) * 100:.4f}%")
    r.pub("  The worst-case divergence figures (prod and library) are in the full appendix;")
    r.pub("  both are immaterial and neither is needed for the equivariance argument above.")

    # B-CAP-DISC is a spread over PROD scenarios' per-scenario retention → full
    # appendix only. Its methodological point (R_scen is NOT basis-invariant, so a
    # single disclosed number must carry its multiplier bracket) is recorded in the
    # design doc; the measured spread itself is prod-derived and not published.
    r.priv("")
    r.priv("[B-CAP-DISC] is the per-scenario retention BASIS-INVARIANT? (no) — the range")
    r.priv("  Each FIELD's R_f is invariant under the scale-both rule (b is invariant, see")
    r.priv("  B-CAP-SCALE). R_scen is NOT: it is an E_f-WEIGHTED average, and the residual")
    r.priv("  weights are k_f*E_f with INDEPENDENT PL and SL multipliers, so the weights move")
    r.priv("  even though every R_f is pinned. Sweeping independent (k_PL, k_SL) over the")
    r.priv("  bracket below and reporting the spread of the disclosed quantity (1 - R_scen):")
    r.priv(f"  {'scenario':<36} {'min':>10} {'max':>10} {'max/min':>9}")
    disc_bracket = (0.10, 0.25, 0.50, 0.75, 1.00)
    worst_ratio = 0.0
    for name, pld, sld in ln:
        vals = []
        for k_pl in disc_bracket:
            for k_sl in disc_bracket:
                num = k_pl * _dist_mean(pld, cap) + k_sl * _dist_mean(sld, cap)
                den = k_pl * _dist_mean(pld, None) + k_sl * _dist_mean(sld, None)
                if den > 0:
                    vals.append(1.0 - num / den)
        if not vals or max(vals) <= 0:
            continue
        lo_v, hi_v = min(vals), max(vals)
        ratio = hi_v / lo_v if lo_v > 0 else float("inf")
        worst_ratio = max(worst_ratio, ratio if ratio != float("inf") else worst_ratio)
        r.priv(f"  {name[:36]:<36} {lo_v * 100:>9.4f}% {hi_v * 100:>9.4f}% {ratio:>8.2f}x")
    r.priv(
        f"  worst spread over the bracket: {worst_ratio:.2f}x. It is bracket-CONDITIONAL: as the"
    )
    r.priv("  multiplier RATIO diverges the spread tends to (1-R_PL)/(1-R_SL); finite here")
    r.priv("  because every basis scenario is two-lognormal, unbounded if a sibling is PERT. So")
    r.priv("  any single number quoted for it MUST carry its bracket -- the discipline D8 needs")
    r.priv("  for n. The currency subtractor compounds it (E[(Y-c)+] not proportional to E[Y]);")
    r.priv("  its magnitude depends on c and is deliberately NOT quantified rather than asserted.")

    r.both("")
    r.both("[B-CAP-FLOOR] D19's band: which catastrophic library entries are uninstantiable")
    r.both("  at which org revenue, because the minted cap would violate the max > p95 floor.")
    r.both("  Inputs are the PUBLIC seed library (mu, sigma) and the published k ONLY -- no")
    r.both("  deployment data, so this whole basis is publishable under rule (a). The revenue")
    r.both("  column is a HYPOTHETICAL sweep, not this deployment's figure.")
    r.both(
        f"  a field is uninstantiable at revenue R when k*R <= p95 = exp(mu + z95*sigma), k={K_CAPACITY}"
    )
    lib_p95 = sorted(
        (math.exp(d["mean"] + Z95 * d["sigma"]), e["slug"], f)
        for e in _entries()
        for f, d in _loss_fields(e)
    )
    r.both(f"  {'hypothetical revenue':>22}  {'entries blocked':>16}")
    for hyp in (1e7, 2.5e7, 5e7, 1e8, 1e9):
        blocked = sum(1 for p95, _, _ in lib_p95 if K_CAPACITY * hyp <= p95)
        r.both(f"  ${hyp:>21,.0f}  {blocked:>10}/{len(lib_p95):<5}")
    worst_p95, worst_slug, worst_f = lib_p95[-1]
    r.both("  binding threshold: every catastrophic library entry is instantiable only above")
    r.both(f"  revenue = max(p95)/k = ${worst_p95 / K_CAPACITY:,.0f}  [{worst_slug} {worst_f}]")
    r.both("  Below it, D19 blocks with the floor-conflict message rather than clamping the")
    r.both("  cap up (which would void D13's policy meaning) or dropping the floor (which")
    r.both("  would unbound B-CAP-MIX's distortion, whose worst case that floor IS).")

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
    r.both("  The validator floor requires M > EVERY component's p95, so F_i(M) > Phi(z95) for")
    r.both("  all i, which bounds the distortion analytically IN BOTH DIRECTIONS:")
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
    r.both("  They are ATTAINED ONLY IN THE VANISHING-WEIGHT LIMIT; at representative weights the")
    r.both("  UP distortion (other component at F=1, floor at Phi(z95)) is much smaller:")
    r.both(f"    {'w_i':>8}  {'UP distortion':>14}")
    for w in (0.5, 0.1):
        # Analytic in (z95, w): (w*Phi(z95) + (1-w)*1)/Phi(z95) - 1. No deployment
        # input, mu cancels -> rule (a), public. Generated so a floor-quantile retune
        # updates it in lockstep with the +-5% suprema instead of going stale in prose.
        dist_w = (w * ndtr(Z95) + (1.0 - w)) / ndtr(Z95) - 1.0
        r.both(f"    {w:>8.2f}  {dist_w * 100:>13.3f}%")
    r.both("  so the +-5% bounds are EXTREMAL, not typical magnitudes.")
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

    # B-CAP-PORT is prod ALE → full appendix only. The ALE ratio is NOT gauge-invariant
    # (the portfolio mixes lognormal fields, which scale, with PERT fields, whose means
    # are absolute), so it is not safe to publish as a "homogeneous prod ratio"; it stays
    # priv with the dollars and the count.
    r.priv("")
    r.priv("[B-CAP-PORT] analytic portfolio ALE — population=active-only")
    today = portfolio_ale(db, None)
    after = portfolio_ale(db, cap)
    r.priv(f"  population {len(scen)} scenarios")
    r.priv(f"  today ${today:,.0f} -> capped ${after:,.0f}  = {(after / today - 1) * 100:+.4f}%")

    # Defense-in-depth over the PUBLIC text. The primary guarantee is structural:
    # the public variant carries ONLY non-deployment content (verdicts, analytic
    # identities in (sigma, z, k), and library figures at a HYPOTHETICAL revenue
    # sweep), so no prod-derived magnitude is emitted to it in the first place.
    # This check is the mechanical backstop against a future edit that reclassifies
    # a prod figure public by mistake. It registers every deployment-derived dollar
    # the run computed; small-integer population counts remain its documented blind
    # spot, which is why nothing prod-derived is public by construction.
    assert_public_artifact_is_clean(
        r.public(),
        numbers=[
            (rev, "annual_revenue"),
            (cap, "the cap"),
            (2 * cap, "2x the cap"),
            (today, "portfolio ALE (today)"),
            (after, "portfolio ALE (capped)"),
            (pert_bound, "the prod PERT-only analytic bound"),
            *leak_sim,
        ],
        strings=[
            (head, "the alembic head"),
            *((n, "an authored scenario name") for n, _, _ in scen),
        ],
    )
    SPECS.mkdir(parents=True, exist_ok=True)
    FULL_OUT.write_text(r.full(), encoding="utf-8")
    PUBLIC_OUT.write_text(r.public(), encoding="utf-8")
    print(f"wrote {FULL_OUT.relative_to(ROOT)}   ({len(r.full().splitlines())} lines, FULL)")
    print(f"wrote {PUBLIC_OUT.relative_to(ROOT)}   ({len(r.public().splitlines())} lines, PUBLIC)")
    print("The PUBLIC variant is the ONLY one a public PR body or commit message may quote.")


if __name__ == "__main__":
    main()
