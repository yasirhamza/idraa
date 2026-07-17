# Calibration-Sources — Tiered Source Catalogue

**Epic C #335, Task 3 (C-ii-a).**
Index of every per-source note in this directory, classified per the C-i tiering framework
(`docs/reference/loss-magnitude-tiering.md`). Use this catalogue to select sources for the
C-ii-b loss-research sweep and C-iii curation.

**Tier vocabulary (from `loss-magnitude-tiering.md`):**

| Tier token | Meaning |
|------------|---------|
| `paginated` | Figure / table / page number citable; TIER-1 loss anchor |
| `vendor` | Named report + year, no page; TIER-2 loss anchor |
| `anecdotal` | Single incident / advisory; TIER-3 context only |

**Carries vocabulary:**

- **loss-magnitude** — publishes per-org financial loss distributions (percentiles, means, medians)
  usable to derive lognormal σ legs for scenario entries.
- **frequency** — publishes incident-rate or prevalence data (annual probability, complaint counts,
  advisory volumes, survival curves).
- **frequency-technique** — publishes operational-timing metrics (MTTI, MTTC, patch SLA) usable
  for τ calibration rather than loss-magnitude distributions.

---

## Master Index

| Source stem | Short title | Carries | Tier | Citable locations | TIER-1/2 loss anchor? |
|-------------|-------------|---------|------|-------------------|-----------------------|
| `ibm_codb_2024` | IBM Cost of a Data Breach 2024 | frequency-technique | paginated | p.8 Fig 1 (global avg cost), p.10 Fig 4 (MTTI/MTTC), p.14 Fig 8 (per-vector lifecycle), p.22 Fig 24 (recovery buckets), p.6 (industrial sector) | **NOT a p50/median loss anchor.** IBM CODB publishes activity-based-cost **means** (e.g. $4.88M global avg, Fig 1 p.8; per-industry avg, Fig 3 p.10) — NOT medians/percentiles. Mean ≠ median for lognormal loss distributions (mean = median · e^{σ²/2}); feeding a mean as a p50 into `lognormal_from_quantiles` produces a wrong σ. Usage: (a) **τ-calibration anchor only** for MTTI/MTTC figures (Fig 4 p.10, genuinely paginated); (b) loss-magnitude input ONLY via **Policy B (`lognormal_from_median_mean`)** if a separate industry median is sourced externally. Do NOT use IBM CODB averages as p50 inputs to `lognormal_from_quantiles`. Primary τ anchor for LEC_DET_MONITORING + LEC_RESP_EVENT_TERMINATION. |
| `ibm_codb_2025` | IBM Cost of a Data Breach 2025 | control-attribution evidence | paginated | p.6 (global avg $4.44M / US $10.22M), pp.42–43 Fig 39 (30 factor cost-deltas; caption baseline anomaly — see note), pp.44–45 Figs 40–42 (high-vs-low factor costs) | **NOT a loss anchor and NOT a τ anchor.** Same mean-based activity-costing caveats as `ibm_codb_2024` (which remains the pinned τ source — this note re-pins nothing). Purpose: external empirical evidence + κ coherence band for meta-control attribution (#434/#439 Slice 2); associational, magnitude-side, prevalence-confounded — full caveats in the source note. |
| `iris_2024` | FAIR Institute / Cyentia IRIS Ransomware Sub-study 2024 | frequency | vendor | Cyentia press release 2024-08-05 (no page numbers); public URL only | **NOT a loss anchor.** Ransomware sub-study only; no loss-percentile table published publicly. Use for frequency context (ransomware sector shares: Manufacturing 51%, Financial 15%). |
| `iris-vs-ic3-reconciliation` | IRIS 2025 vs IC3 2025 Reconciliation | frequency | vendor | Internal cross-reference doc; cites `fair_cam.data.iris_2025` + `ic3_2025.md` | Not a primary source — a reconciliation guide. Points to `fair_cam.data.iris_2025` for TIER-1 IRIS loss data; no independent citable location. |
| `dbir_2024` | Verizon DBIR 2024 | frequency, frequency-technique | paginated | p.21 Fig 19 (CISA KEV survival curve — 55-day median), p.22 Fig 20 (first-scan latency), p.9/p.40 Fig 39 (phishing time-to-fall) | **Frequency/technique only** — no per-org loss-magnitude percentiles published. Primary τ anchor for VMC_CORR_IMPLEMENTATION. |
| `ic3_2025` | FBI IC3 2025 Internet Crime Report | frequency | paginated | p.6 (total losses), pp.7–8 (crime-type losses), p.14 (ransomware), p.16 (critical infra counts), pp.25–26 (3-year comparison), pp.27–31 (per-state data) | **NOT a standalone loss anchor** — IC3 publishes aggregate complaint **totals** (e.g. BEC $3.046B / 24,768 complaints), NOT per-organisation loss percentiles or distributions. Per-complaint averages are noise-diluted by zero-loss complaints (per `iris-vs-ic3-reconciliation`). Consumer + enterprise complaints mixed; no enterprise-only stratum. Use for: (a) **frequency/sector sanity-checks** (complaint counts, sector prevalence); (b) **NOT** as per-event lognormal legs — do not feed IC3 aggregate totals as p50 or p95 inputs to `lognormal_from_quantiles`. |
| `sans_ir_2024` | SANS 2024 Detection & Response Survey (+ 2023 IR Survey) | frequency-technique | vendor | Partner landing-page summaries only; full PDF gated. Page-level citations NOT available. | **NOT a loss anchor.** Qualitative cross-validation for VMC_CORR_TREATMENT_SELECTION and LEC_RESP_RESILIENCE τ. No median-days-to-treat extracted; heuristic defaults unchanged. |
| `sec_cyber_disclosures` | SEC Cybersecurity Disclosures | loss-magnitude | anecdotal | STUB — content TBD; no population stats extracted yet | **TIER-3 / stub** — disclosure population biased toward large public firms; loss-figure field inconsistently populated. Cannot feed FAIR distributions until content extracted. |
| `cisa_year_in_review_2024` | CISA Year in Review 2024 | frequency | anecdotal | STUB — content TBD; advisory-volume aggregates, not org-loss-event level | **NOT a loss anchor.** Frequency context only (advisory counts, CI sector engagement). |
| `cisa_dib_advisories` | CISA Defense Industrial Base Advisories | frequency | anecdotal | STUB — content TBD; advisory IDs to be listed when extracted | **NOT a loss anchor.** Informs defense_industrial_base overlay posture (TTP patterns, control failures). |
| `ffiec_advisories` | FFIEC Cybersecurity Advisories | frequency | anecdotal | STUB — content TBD; regulatory-guidance artifacts, no loss aggregation | **NOT a loss anchor.** Informs regulated_financial overlay posture (what controls regulators expect). |
| `veris_dataset` | VERIS Community Database (VCDB) | frequency-technique | vendor | GitHub commit `5a64739` (permalink); schema at `verisc.json` | **NOT a loss anchor for phase 1.** Sparse timeline population (59 incidents with specific containment values per Farhang & Grossklags 2017); sampling bias (healthcare oversample). Deferred to per-org override calibration. **T2b loss-value evaluation (2026-06-11, adversarially verified):** `impact.overall_amount` populated in ~3.2% of the 10,037-incident validated corpus at pinned commit 5a6473980ab6f0ad151d8fd2c7b0e9a818aecb95 (full-SHA permalink https://github.com/vz-risk/VCDB/tree/5a6473980ab6f0ad151d8fd2c7b0e9a818aecb95, accessed 2026-06-11). All populated records in two independent random samples (research n=598, verifier n=60) date from incident years 2008–2019; ~0 records year ≥ 2020 carry amounts. Vintage rule (a) (≥2020 window) yields an empty set; rule (b) (CPI-adjust pre-2020) yields ~321 records with single-digit per-class/per-sector cells for all 25 T2b target archetypes — below any defensible quantile floor. NOT a loss anchor for any evaluated archetype. May inform example_incidents annotations only for records with verified public source URLs. The prior τ-calibration rejection stands independently. |
| `naic_cyber_supplement` | NAIC Annual Report on the Cybersecurity Insurance Market | frequency (aggregate market context) | paginated (for aggregate figures only) | 2025 report (data year 2024) https://content.naic.org/sites/default/files/inline-files/2025_Cybersecurity_Insurance%20Report.pdf (accessed 2026-06-11); 2024 report https://content.naic.org/sites/default/files/cmte-h-cyber-wg-2024-cyber-ins-report.pdf (accessed 2026-06-11); 2023 report https://content.naic.org/sites/default/files/inline-files/Final%202023%20Cyber%20Report.pdf (accessed 2026-06-11); 2022 report https://content.naic.org/sites/default/files/cmte-c-cyber-supplement-report-2022-for-data-year-2021.pdf (accessed 2026-06-11) | **NOT a loss anchor at any tier (T2b evaluation, 2026-06-11, adversarially verified).** The Cyber Supplement schema collects aggregate DWP, claim counts, direct losses paid/incurred, and loss ratios ONLY — no per-event loss distributions, percentiles, means, or medians exist in any report (2021–2024 data years verified). Policy A inapplicable (no quantile pair); Policy B inapplicable (no per-event median). Verified aggregate figures usable as frequency/market context: ~50,000 claims reported 2024 (2025 report, Overview); claims closed with payment 9,941 vs without 28,555 (2025 report Figure 9, p.12); 33,561 claims 2023 (2024 report); top-20 loss ratio 66.4% data-year 2021 (2022 report, Figure 2). |
| `sub_sector_chemical_manufacturing` | Sub-sector: Chemical Manufacturing | loss-magnitude, frequency | vendor | Cyentia IRIS 2025 Fig 8 (NAICS-2 manufacturing p_annual ≈ 11.2%), Norsk Hydro postmortem (disclosed ~$70M in 2019 Q1 annual report), JBS Foods ($11M disclosed ransom), NetDiligence 2024 manufacturing supplement | **TIER-2 loss anchor** — magnitude lift (2.5×) anchored to vendor reports + named postmortems (no page-level citations). frequency_multiplier = 1.7× over NAICS-2 manufacturing baseline. |
| `sub_sector_electric_utility` | Sub-sector: Electric Utility | loss-magnitude, frequency | vendor | Cyentia IRIS 2025 Fig 8 (NAICS-2 utilities p_annual ≈ 4.5%), Ukraine BlackEnergy/Industroyer postmortems (CISA advisory permalinks), NERC E-ISAC annual reviews, NetDiligence 2024 utilities supplement | **TIER-2 loss anchor** — magnitude lift (2.7×) anchored to societal-impact estimates + NERC CIP regulatory-tail analysis + vendor reports (no audited US-grid financial disclosure). frequency_multiplier = 2.0× over NAICS-2 utilities baseline. |
| `sub_sector_oil_and_gas` | Sub-sector: Oil and Gas | loss-magnitude, frequency | vendor | Cyentia IRIS 2025 Fig 8 (NAICS-2 mining p_annual ≈ 1.9%), Colonial Pipeline postmortem ($4.4M ransom + $20–50M operational-impact range from analyst commentary), Shamoon 2012 ($15M direct IT), NetDiligence 2024 energy vertical | **TIER-2 loss anchor** — magnitude lift (3.0×) anchored to Colonial-class operational-impact analyst range (no audited public figure). frequency_multiplier = 2.5× over NAICS-2 mining baseline. |
| `sub_sector_pipeline` | Sub-sector: Pipeline | loss-magnitude, frequency | vendor | Cyentia IRIS 2025 (NAICS-2 transportation baseline), Colonial Pipeline ($4.4M ransom, analyst $20–50M operational range), TSA SD-Pipeline series (GAO-22-104506), NetDiligence 2024 energy vertical | **TIER-2 loss anchor** — magnitude lift (3.0×) anchored almost entirely to Colonial Pipeline 2021 (no audited disclosure). frequency_multiplier = 2.0× over NAICS-2 transportation baseline. Note: magnitude anchor is **near-TIER-3** (single incident, unaudited analyst range $20–50M — not an empirical midpoint); the 3.0× multiplier is a documented floor of the plausible range, not an empirically grounded central estimate. |
| `sub_sector_water_utility` | Sub-sector: Water Utility | frequency | vendor | Cyentia IRIS 2025 Fig 8 (NAICS-2 utilities p_annual ≈ 4.5%), Oldsmar 2021 + Aliquippa 2023 postmortems (CISA advisories), EPA 2023 inspection findings (~70% baseline-control gaps), NetDiligence 2024 public-sector/utilities supplement | **NOT a standalone lognormal anchor.** Zero realized US water-utility dollar loss disclosures exist (Oldsmar/Aliquippa are CISA advisories with no financial figures). TIER-2 label applies only to a **multiplier (1.8×, floor) applied over a NAICS-22 baseline lognormal** — it cannot produce a standalone lognormal distribution (no dollar anchors to feed σ functions). C-ii-b MUST NOT attempt `lognormal_from_quantiles` for this sub-sector, and MUST NOT borrow another sector's tail (no cross-sector tail borrowing). frequency_multiplier = 1.7× over NAICS-2 utilities baseline is supported. |

---

## TIER-1 Loss Anchors (usable for C-ii-b sweep with full lognormal confidence)

Sources that provide **paginated** loss-magnitude data (figure/table/page citable) usable as
either the p50 or p95 leg for `lognormal_from_quantiles`:

| Source | What it provides | Key citable location |
|--------|-----------------|---------------------|
| *(none in this catalogue — see note below)* | — | — |

**Important:** Neither `ibm_codb_2024` nor `ic3_2025` qualifies here:

- `ibm_codb_2024` publishes activity-based-cost **means**, not medians/percentiles. It is a
  **τ-calibration anchor** (MTTI/MTTC, paginated) and a **Policy B** input only (requires a
  separate median source to use with `lognormal_from_median_mean`). Do NOT use as a p50 leg.
- `ic3_2025` publishes aggregate complaint **totals**, not per-organisation loss distributions.
  It is a **frequency/sanity-check source** only. Do NOT feed IC3 totals into
  `lognormal_from_quantiles`.

`fair_cam.data.iris_2025` (the IRIS 2025 module, not the `iris_2024.md` stub) is the
primary TIER-1 anchor for enterprise loss percentiles (Fig 12 p50/p95 by industry) — see
`loss-magnitude-tiering.md`. The `iris_2024.md` note here is the **2024 ransomware sub-study**
which does NOT publish loss percentiles publicly.

## TIER-2 Loss Anchors (vendor-confidence badge required)

Sources that provide **named vendor report + year** loss data, no page-level citation:

| Source | What it provides |
|--------|-----------------|
| `sub_sector_chemical_manufacturing` | 2.5× magnitude lift over NAICS-2 manufacturing; anchored to Norsk Hydro ($70M) and JBS ($11M) postmortems |
| `sub_sector_electric_utility` | 2.7× magnitude lift over NAICS-2 utilities; anchored to societal-impact + NERC CIP regulatory tail |
| `sub_sector_oil_and_gas` | 3.0× magnitude lift over NAICS-2 mining; anchored to Colonial Pipeline analyst range |
| `sub_sector_pipeline` | 3.0× magnitude lift over NAICS-2 transportation; anchored to Colonial Pipeline |
| `sub_sector_water_utility` | 1.8× **multiplier over a NAICS-22 baseline lognormal only** — no dollar anchors; cannot produce standalone lognormal; no cross-sector tail borrowing |

## Frequency-Technique Sources (τ calibration, not loss-magnitude)

Sources that carry operational-timing or frequency data but NOT enterprise loss distributions:

| Source | What it provides |
|--------|-----------------|
| `dbir_2024` | CISA KEV remediation survival curve (55-day median, p.21 Fig 19); phishing time-to-fall (<60s, p.40 Fig 39) |
| `sans_ir_2024` | Qualitative IR-timing cross-validation only; no canonical τ override extracted |
| `veris_dataset` | VERIS schema vocabulary; timeline field definitions; no maintained per-class median table; T2b loss-value evaluation (2026-06-11) confirmed NOT a loss anchor for any of the 25 T2b target archetypes |

## Aggregate-Frequency Sources (market context only, not a loss anchor)

Sources that publish aggregate market / claims-volume data usable for frequency/market context but NOT for per-event loss distributions:

| Source | What it provides |
|--------|-----------------|
| `naic_cyber_supplement` | Aggregate DWP, claim counts, loss ratios for the US cyber-insurance market (2021–2024 data years). NOT a loss anchor (T2b evaluation, 2026-06-11, adversarially verified). Verified context figures: ~50,000 claims 2024 (2025 report Overview); 33,561 claims 2023 (2024 report); top-20 loss ratio 66.4% data-year 2021 (2022 report Figure 2). |

## Stubs / Not Yet Extracted

These notes exist as placeholders; their content is TBD and they carry no currently extractable
calibration data:

| Source | Status |
|--------|--------|
| `sec_cyber_disclosures` | STUB — no population stats extracted |
| `cisa_year_in_review_2024` | STUB — advisory-volume aggregates only when extracted |
| `cisa_dib_advisories` | STUB — advisory posture data only when extracted |
| `ffiec_advisories` | STUB — regulatory guidance posture data only when extracted |

---

## Notes for C-ii-b Sweep

1. **Enterprise loss anchors** for new scenario entries must draw from
   `fair_cam.data.iris_2025` (Figure 12 p50/p95 by industry) as the sole TIER-1 lognormal
   source in this catalogue. `ibm_codb_2024` publishes activity-based-cost **means** (not
   medians/p50) — it is a τ-calibration anchor and a Policy B input only; do NOT use IBM CODB
   averages as p50 or p95 legs for `lognormal_from_quantiles`. `ic3_2025` publishes aggregate
   complaint totals, not per-organisation distributions — use for frequency/sanity-checks only,
   not for lognormal σ derivation.

2. **Sub-sector multipliers** (`sub_sector_*`) are TIER-2 and apply on TOP of a NAICS-2
   baseline — they are not standalone loss anchors. Per spec §6.4 stacking discipline, the
   CI overlay and sub-sector overlay must not both be applied without stacking-pin tests.

3. **Frequency data** for scenario `probability_annual` fields routes to DBIR 2024 (survival
   curves, technique prevalence), IC3 2025 (critical-infra complaint counts), and
   `fair_cam.data.iris_2025` (sector incident rates, Fig 8).

4. **τ calibration** for FAIR-CAM ELAPSED_TIME sub-functions routes to `ibm_codb_2024`
   (LEC_DET_MONITORING, LEC_RESP_EVENT_TERMINATION) and `dbir_2024`
   (VMC_CORR_IMPLEMENTATION). SANS and VCDB produced negative extraction outcomes.

5. **The `iris_2024.md` note** documents the 2024 IRIS Ransomware sub-study, NOT the IRIS
   2025 main report. Loss percentiles from IRIS 2025 live in `fair_cam.data.iris_2025`;
   use that module directly for TIER-1 lognormal derivations, not this source note.
