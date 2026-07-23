# Loss-Magnitude Tiering Framework

> **Superseded for distribution shape (Milestone B, 2026-07-09):** loss
> distribution shape is now governed by the independent `loss_shape` field
> (`capped` → bounded PERT, `catastrophic` → uncapped lognormal — see
> `loss-representation.md`), NOT by citation tier. This document remains
> authoritative for citation/evidence tiering (`loss_tier`) only; its
> tier→shape mappings below are historical.

**Epic C #335 — spec §1**
Defines the source-credibility ladder for library entries' loss distributions.
Every entry stores its `loss_tier` so that tier↔citation consistency is a
testable invariant (see `tests/integration/test_seed_library_lognormal.py`).

---

## The Tier Ladder

### TIER-1 — paginated primary source

A published statistic at an unambiguous citable location (figure number, table
number, or page number):

- IRIS 2025 Figure 12 (p50 + p95 by industry)
- IBM CODB Table N, p. M
- IC3 annual report p. M

Both `p50` and `p95` (or any other two percentiles) are read **directly** from
the source and fed to `lognormal_from_quantiles`. Full lognormal confidence.

**Mixed-source TIER-1 rule:** When the p95 (tail) leg is paginated (figure /
table / page number) and the p50 (location) leg is a named vendor report with
year, the entry is classified **TIER-1**. BOTH citations are recorded in
`source_citations`, and the p50 vendor source is surfaced. A TIER-1 entry
requires the **tail** leg (p95) to be paginated; the location leg (p50) may
be a named vendor report. The both-legs traceability requirement (Hard Rule 1)
still applies: both legs must be cited in `source_citations`.

Note: after Epic C-iii-a re-anchor (2026-06-11), the manufacturing entry
was promoted from mixed-source TIER-1 (p50 = NetDiligence 2024 vendor report;
p95 = IRIS 2025 Figure A3) to **pure-paginated TIER-1** (both legs from IRIS
2025 Figure A3, p. 35). The mixed-source TIER-1 rule still applies to future
entries where the tail leg is paginated but the location leg is a named vendor
report (no pure-paginated pair available).

`loss_tier = "paginated"` — no badge shown to the user; confidence is assumed.

### TIER-2 — named vendor report + year, non-paginated

A vendor report named with year but without a page/figure:

- NetDiligence Cyber Claims Study (year)
- Coveware Quarterly Ransomware Report (year)
- Verizon DBIR (year, breach-cost table)

Loss distribution is stored as lognormal with `loss_tier = "vendor"` and
rendered with a **"Vendor-sourced loss estimate — lower confidence"** badge.
The badge is never suppressed; TIER-2 confidence must be surfaced to the user,
never silently equated with TIER-1.

### TIER-3 — anecdotal / single-incident

One news-reported breach cost, a blog post, or a single-org disclosure.

`loss_tier = "anecdotal"` — **stays PERT**. The incident is recorded in
`example_incidents` / `calibration_anchor` as context, never as the
distribution's cited anchor.

---

## σ-Derivation Sub-Policies (TIER-1 and TIER-2)

All three paths produce the same `{mean_log, sigma}` input to fair_cam
and require that **both σ legs** (i.e. the two statistics from which σ is
derived) trace to the entry's own cited source in `source_citations`.

### Policy A — Two percentiles → `lognormal_from_quantiles`

**When:** TIER-1 paginated source gives p50 + p95 (or any two distinct
quantiles), OR a TIER-2 vendor report explicitly tabulates two percentiles.

**Formula** (closed-form, from fair_cam)

```
lognormal_from_quantiles(lo=p50, lo_q=0.50, hi=p95, hi_q=0.95)
  -> {mean_log, sigma}
```

**Worked example — manufacturing sector (pure-paginated TIER-1, Epic C-iii-a):**

Sources:
- p50 = $1,000,000 — IRIS 2025 Figure A3, p. 35 (Appendix — "Losses observed
  per sector"): Manufacturing sector median
- p95 = $42,000,000 — IRIS 2025 Figure A3, p. 35: Manufacturing sector 95th
  percentile

```
p50 = $1 000 000  (lo)  q_lo = 0.50   source: IRIS 2025 Figure A3, p. 35
p95 = $42 000 000 (hi)  q_hi = 0.95   source: IRIS 2025 Figure A3, p. 35

z_lo = ppf(0.50) = 0.0
z_hi = ppf(0.95) ≈ 1.6449

sigma = (ln(42 000 000) − ln(1 000 000)) / (1.6449 − 0.0)
      = (17.5532 − 13.8155) / 1.6449
      ≈ 3.7377 / 1.6449
      ≈ 2.272

mean_log = ln(1 000 000) − 0.0 × sigma = 13.816
```

Both legs trace to IRIS 2025 Figure A3, p. 35 — a pure-paginated TIER-1 entry.
This supersedes the prior mixed-source anchor (p50 = $2.8M from NetDiligence
Cyber Claims Study 2024; p95 = $23M conservative within-sector estimate;
σ ≈ 1.281). Prior mixed-source entries remain valid TIER-1 under the
Mixed-source TIER-1 rule above; manufacturing was simply promoted to
pure-paginated when both legs became available from Figure A3.

**Hypothetical worked example — mixed-source TIER-1:**

For illustration (not a live entry): suppose a sector's published median is
$2,000,000 from a named vendor report (year known) and the 95th percentile
is $18,000,000 from IRIS 2025 Figure A3.

```
p50 = $2 000 000  (lo)  q_lo = 0.50   source: Hypothetical Vendor Report 2024
p95 = $18 000 000 (hi)  q_hi = 0.95   source: IRIS 2025 Figure A3, p. 35

sigma = (ln(18 000 000) − ln(2 000 000)) / 1.6449
      = (16.7060 − 14.5087) / 1.6449
      ≈ 2.1973 / 1.6449
      ≈ 1.336

mean_log = ln(2 000 000) = 14.509
```

Both legs must appear verbatim in `source_citations`. This is a valid
mixed-source TIER-1 entry: the tail leg (p95) is paginated (Figure A3),
and the location leg (p50) is a named vendor report with year.

---

### Policy B — Median + mean → `lognormal_from_median_mean`

**When:** A vendor report gives only a summary median and mean but no
explicit percentiles. Available via `fair_cam.quantile_pooling`.

**Identity:**

```
For a lognormal:  mean = median · exp(σ²/2)
=> mean/median = exp(σ²/2)
=> σ = sqrt(2 · ln(mean/median))
=> μ = ln(median)
```

**Implementation in fair_cam:**

```python
lognormal_from_median_mean(median, mean) -> {"mean": ln(median), "sigma": sqrt(2·ln(mean/median))}
```

**Constraints enforced by the helper:**

- `median > 0`
- `mean > median` strictly (a lognormal's mean always exceeds its median for
  σ > 0; `mean == median` would yield σ = 0, a degenerate point mass, which is
  meaningless as a TIER-2 loss distribution and **raises `ValueError`**)

**Worked example — vendor report: median = $1 000 000, mean = $1 648 721:**

```
mean/median = 1 648 721 / 1 000 000 = 1.648721
ln(mean/median) = ln(1.648721) ≈ 0.5000
σ = sqrt(2 · 0.5000) = sqrt(1.0) = 1.0

μ = ln(1 000 000) ≈ 13.8155

Verification:
  lognormal_mean(μ=13.8155, σ=1.0) = exp(13.8155 + 0.5) = exp(14.3155)
                                    ≈ 1 648 721  ✓
  exp(μ) = exp(13.8155) = 1 000 000  (median check) ✓
```

Both legs (`median = $1 000 000` and `mean = $1 648 721`) plus the vendor
report name and year must appear in `source_citations`.

---

### Policy C — Median + stated range → documented percentile mapping or TIER-3 fallback

**When:** A vendor report gives a median and a "range" (e.g. "$50k–$2M").

**Approach:**

1. Determine which quantiles the range endpoints represent. Many vendor
   reports describe their range as approximately the 10th–90th or 5th–95th
   percentile; if the report states the methodology, use the stated percentiles
   and apply Policy A.
2. **If no defensible percentile mapping exists,** do NOT guess. Fall back to
   TIER-3/PERT and record the range in `calibration_anchor` as context only.

**Worked example — vendor report: median = $300 000, range $20 000–$4 000 000
(stated as "10th–90th percentile"):**

```
Treat lo = $20 000 at q_lo = 0.10, hi = $4 000 000 at q_hi = 0.90

z_lo = ppf(0.10) ≈ -1.2816
z_hi = ppf(0.90) ≈  1.2816

sigma = (ln(4 000 000) - ln(20 000)) / (1.2816 - (-1.2816))
      = (15.2018 - 9.9035) / 2.5633
      ≈ 5.2983 / 2.5633
      ≈ 2.067

mean_log = ln(20 000) - (-1.2816) * 2.067
         = 9.9035 + 2.649 = 12.552
```

Document the assumed percentile mapping in `calibration_anchor`. If the vendor
report does NOT state the percentile semantics of its range, record `loss_tier =
"anecdotal"` and keep PERT.

---

## Hard Rules (methodology-gated)

1. **Both-legs traceability.** A lognormal entry's stored tier MUST be
   `paginated` or `vendor`, and BOTH statistics used to derive σ MUST trace to
   that tier's citation(s) in `source_citations`. Partial citation (one leg
   cited, one inferred) is prohibited.

2. **No cross-sector tail borrowing.** σ for an entry is derived from THAT
   entry's own cited source for THAT entry's sector. Importing σ or percentile
   values from a different sector is forbidden, even when the citing source
   covers multiple sectors. This rule is explicit and non-negotiable; see the
   brainstorm record for rationale.

3. **TIER-2 confidence always surfaced.** The vendor-confidence badge is
   mandatory for any `loss_tier = "vendor"` entry and must never be suppressed.
   TIER-2 must not be silently equated with TIER-1.

4. **TIER-3 stays PERT.** An anecdotal or single-incident source may be
   recorded as context in `example_incidents` / `calibration_anchor`, but it
   must not be used as the distributional anchor. The `loss_tier = "anecdotal"`
   entry stores a PERT distribution.

5. **mean == median raises, not silently degenerates.** Policy B's
   `lognormal_from_median_mean` raises `ValueError` when `mean == median`
   (σ = 0, degenerate point mass). This is intentionally stricter than
   `lognormal_from_quantiles` (which allows lo == hi → σ = 0) because a
   vendor-stat derivation with zero dispersion is always a data-quality error.

---

## Tier Enum Values

The `loss_tier` column on `ScenarioLibraryEntry` and the `LossTier` enum
use these four values:

| Value        | Meaning                                              | Distribution |
|--------------|------------------------------------------------------|--------------|
| `paginated`  | TIER-1: figure/table/page citation                   | lognormal    |
| `vendor`     | TIER-2: named vendor report + year, non-paginated    | lognormal    |
| `anecdotal`  | TIER-3: anecdotal / single-incident                  | PERT         |
| `none`       | No loss anchor asserted (framework or gap entry)     | PERT         |

The server default for existing entries is `anecdotal`.

---

## Guard / Invariant Tests

The seed-validity guard (`tests/integration/test_seed_library_lognormal.py`)
enforces at every CI run:

- Every lognormal entry has `loss_tier ∈ {paginated, vendor}` — and, as of the
  loss-PERT-overhaul (`loss-representation.md`), so does every PERT entry
  produced by collapsing a cited envelope: all 91 current `capped`-shape PERT
  entries carry `loss_tier ∈ {paginated, vendor}` (86 paginated + 5 vendor).
  The guard only enforces the **contrapositive** direction — `loss_tier ∈
  {anecdotal, none}` (or absent) ⇒ no lognormal node, must be PERT — it does
  NOT require every PERT entry to be anecdotal/none-tiered; a PERT entry may
  legitimately carry a paginated/vendor tier once its envelope has been
  collapsed.
- Both σ legs of every lognormal entry trace to its stated tier's citation(s)
- For `paginated`: both legs trace to "IRIS 2025" + "Figure A3" tokens + a named
  p50 primary (NetDiligence / FFIEC / Verizon DBIR / IRIS 2025). Epic C-iii-a
  broadened the guard from the old "IRIS 2025 Figure 12" single-token check to
  the two-token form ("IRIS 2025" ∧ "Figure A3").
- For `vendor`: both legs trace to the entry's own vendor citation(s) in
  `source_citations` (no IRIS-Figure-A3 requirement)

---

## Canonical badge one-liners (report surfaces)

These strings are the single source of truth for all badge text.
`TIER_BADGE_TEXT` in `services/reports.py` must be byte-identical to these values —
doc-drift fails the pin test deliberately (update doc + constant + test together).

| `loss_tier` value | Badge one-liner |
|-------------------|-----------------|
| `paginated` (TIER-1) | Paginated primary source — figure/table/page-cited loss anchor |
| `vendor` (TIER-2) | Vendor-sourced loss estimate — lower confidence |
| `anecdotal` (TIER-3) | Anecdotal / analyst-judged — lowest confidence; no citeable loss anchor |

Note: the web UI suppresses the TIER-1 badge (confidence is assumed for paginated
sources), but the PDF methodology & provenance appendix discloses all three tiers
so the reader can distinguish confidence levels across library-derived scenarios.

---

## References

- IRIS 2025 Cyber Loss Distribution Report — Figure A3, p. 35 (Appendix —
  "Losses observed per sector": per-sector median + 95th percentile for 20
  NAICS-2 sectors). This is the paginated primary source for all 18 re-anchored
  IndustryMagnitudePrior entries after Epic C-iii-a.
- IRIS 2025 Cyber Loss Distribution Report — Figure 12 (industry-level trend
  panels — historical reference; Epic C-iii-a migrated all paginated p95 anchors
  from Figure 12 to Figure A3 where both legs are now available)
- IBM Cost of a Data Breach 2024 (mean + median by industry)
- IC3 2024 Internet Crime Report (median + mean loss by crime type)
- FAIR Standard — Loss Magnitude node definition
- Epic B implementation: `fair_cam/quantile_pooling/_lognormal_native.py`
- `lognormal_from_quantiles`, `lognormal_from_median_mean`, `lognormal_mean`
  exported from `fair_cam.quantile_pooling`

See also: `vulnerability-semantics.md` (vulnerability is the inherent pre-control baseline;
this doc covers loss magnitude only — vulnerability has no equivalent tier ladder).
