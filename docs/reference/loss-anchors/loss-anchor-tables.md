# Loss-Anchor Tables — Human-Readable Companion

> **Source of truth:** `data/loss_anchor_tables.json` (82 rows, 13 sectors).
> This document is generated from that file; do NOT edit values here — edit the JSON.
> Generated as part of Epic C-ii-b (#335), Task 15, 2026-06-10.

---

## Tier-Distribution Summary

| Metric | Count |
|--------|-------|
| Total archetypes | 82 |
| **paginated** (TIER-1: figure/page locator in a primary report) | **36** |
| **vendor** (TIER-2: named vendor report, permalink + accessed) | **11** |
| **anecdotal** (TIER-3: no qualifying primary source) | **35** |
| Verified rows (anchor values asserted, adversarial gate passed) | **47** |
| `anchor_type = none` (PERT-only, no asserted values) | **35** |
| Lognormal-ready (`quantile_pair`) | **36** |
| Multiplier-over-baseline (TIER-2; feeds PERT via baseline × multiplier) | **11** |

**Honest headline:** 35 of 82 archetypes (43%) ended with `anchor_type = none` — no qualifying primary-source loss distribution was found. Of the remaining 47 verified rows, 36 carry a directly lognormal-ready `quantile_pair` anchor (p50/p95), and 11 carry a `multiplier_over_baseline` anchor (energy/utilities sub-sectors over the paginated Utilities baseline). No row carries `median_mean` in this sweep. TIER-2/3 dominance was expected and is consistent with the protocol's stated prior.

---

## Per-Sector Tables

Legend: **Tier** = paginated / vendor / anecdotal | **Type** = quantile\_pair / multiplier\_over\_baseline / none | **Values** = p50/p95 in USD, or multiplier × baseline, or — | **V** = verified (Y/N) | **Primary source** = first citation (source + locator, truncated)

---

### Education (4 archetypes)

| Archetype | Tier | Type | Values | V | Primary source |
|-----------|------|------|--------|---|----------------|
| phishing-ad-compromise-ransomware | paginated | quantile\_pair | p50 $249K / p95 $6M | Y | Cyentia IRIS 2025 — Figure A3, p. 35 (Education row) |
| education-student-records-insider | paginated | quantile\_pair | p50 $249K / p95 $6M | Y | Cyentia IRIS 2025 — Figure A3, p. 35 (Education row) |
| education-research-ip-exfiltration | anecdotal | none | — | N | No qualifying source (nation-state IP exfil; harm is long-term strategic, not per-org financial) |
| education-campus-facility-tamper | anecdotal | none | — | N | No qualifying source (physical campus tamper; no cyber-loss percentile source) |

---

### Energy / Utilities (13 archetypes)

All 11 sub-sector OT archetypes carry a `vendor` / `multiplier_over_baseline` anchor derived from the Cyentia IRIS 2025 Utilities sector baseline (p50 $146K / p95 $3M) with a sub-sector multiplier drawn from in-corpus sub-sector calibration docs. Two IT-class archetypes carry a `paginated` / `quantile_pair` anchor directly from the Utilities row.

| Archetype | Tier | Type | Values | V | Primary source |
|-----------|------|------|--------|---|----------------|
| ransomware-on-historian | vendor | multiplier\_over\_baseline | 2.7× energy\_utilities | Y | Cyentia IRIS 2025 — p. 35, 'Losses observed per sector' table |
| denial-of-control | vendor | multiplier\_over\_baseline | 2.7× energy\_utilities | Y | Cyentia IRIS 2025 — p. 35, 'Losses observed per sector' table |
| hmi-credential-compromise | vendor | multiplier\_over\_baseline | 2.7× energy\_utilities | Y | Cyentia IRIS 2025 — p. 35, 'Losses observed per sector' table |
| it-ot-bridge-compromise | vendor | multiplier\_over\_baseline | 2.7× energy\_utilities | Y | Cyentia IRIS 2025 — p. 35, 'Losses observed per sector' table |
| nation-state-ics-supply-chain | vendor | multiplier\_over\_baseline | 2.7× energy\_utilities | Y | Cyentia IRIS 2025 — p. 35, 'Losses observed per sector' table |
| hacktivist-ot-disruption | vendor | multiplier\_over\_baseline | 2.7× energy\_utilities | Y | Cyentia IRIS 2025 — p. 35, 'Losses observed per sector' table |
| ot-network-scanning-reconnaissance | vendor | multiplier\_over\_baseline | 2.7× energy\_utilities | Y | Cyentia IRIS 2025 — p. 35, 'Losses observed per sector' table |
| process-view-manipulation | vendor | multiplier\_over\_baseline | 2.7× energy\_utilities | Y | Cyentia IRIS 2025 — p. 35, 'Losses observed per sector' table |
| oem-remote-maintenance-abuse | vendor | multiplier\_over\_baseline | 2.7× energy\_utilities | Y | Cyentia IRIS 2025 — p. 35, 'Losses observed per sector' table |
| grid-protective-relay-manipulation | vendor | multiplier\_over\_baseline | 2.7× energy\_utilities | Y | Cyentia IRIS 2025 — p. 35, 'Losses observed per sector' table |
| pipeline-scada-integrity | vendor | multiplier\_over\_baseline | 3.0× energy\_utilities | Y | Cyentia IRIS 2025 — p. 35, 'Losses observed per sector' table |
| watering-hole-industry-targeted | paginated | quantile\_pair | p50 $146K / p95 $3M | Y | Cyentia IRIS 2025 — p. 35, 'Losses observed per sector' table (Utilities) |
| energy-billing-system-tamper | paginated | quantile\_pair | p50 $146K / p95 $3M | Y | Cyentia IRIS 2025 — p. 35, 'Losses observed per sector' table (Utilities) |

---

### Financial Services (9 archetypes)

| Archetype | Tier | Type | Values | V | Primary source |
|-----------|------|------|--------|---|----------------|
| insider-data-theft-financial | paginated | quantile\_pair | p50 $1M / p95 $194M | Y | Cyentia IRIS 2025 — p. 35, "Losses observed per sector" table (Financial) |
| ddos-extortion-financial | paginated | quantile\_pair | p50 $1M / p95 $194M | Y | Cyentia IRIS 2025 — p. 35, "Losses observed per sector" table (Financial) |
| ddos-financial-seasonal-peak | paginated | quantile\_pair | p50 $1M / p95 $194M | Y | Cyentia IRIS 2025 — p. 35, "Losses observed per sector" table (Financial) |
| web-app-exploitation | paginated | quantile\_pair | p50 $1M / p95 $194M | Y | Cyentia IRIS 2025 — p. 35, "Losses observed per sector" table (Financial) |
| third-party-processor-breach | paginated | quantile\_pair | p50 $1M / p95 $194M | Y | Cyentia IRIS 2025 — p. 35, "Losses observed per sector" table (Financial) |
| bec-fraud-financial | anecdotal | none | — | N | No qualifying two-leg anchor for BEC-specific loss in financial services |
| branch-atm-physical-tamper | anecdotal | none | — | N | No TIER-1/2 source for per-org ATM jackpotting/skimming loss percentiles |
| financial-transaction-tampering | anecdotal | none | — | N | No TIER-1/2 source for per-org insider financial transaction tampering |
| financial-call-center-social-eng | anecdotal | none | — | N | No TIER-1/2 source for per-org call-center social engineering loss percentiles |

---

### Food / Agriculture (5 archetypes)

All 5 rows are anecdotal. IRIS 2025 does publish an Agriculture sector row (p50 $2M, p95 data present), but the verify agent determined the publicly-accessible figure could not be confirmed at the cited locator with sufficient precision to assert a `quantile_pair` — the rows were held at `none` pending C-iii re-verification.

| Archetype | Tier | Type | Values | V | Primary source |
|-----------|------|------|--------|---|----------------|
| food-cold-chain-ransomware | anecdotal | none | — | N | IRIS 2025 Agriculture row — locator confirmation insufficient for C-ii-b gate |
| food-recall-data-tampering | anecdotal | none | — | N | No qualifying source for per-org loss magnitude for food recall/data tampering |
| agri-equipment-physical-tamper | anecdotal | none | — | N | No cyber-loss source for physical tamper of agricultural equipment |
| agri-coop-bec-fraud | anecdotal | none | — | N | No per-org source for agricultural co-op BEC loss magnitude |
| crop-science-ip-exfiltration | anecdotal | none | — | N | No per-org source for crop-science IP exfiltration loss magnitude |

---

### Government / Public Sector (4 archetypes)

| Archetype | Tier | Type | Values | V | Primary source |
|-----------|------|------|--------|---|----------------|
| public-sector-targeted-intrusion | paginated | quantile\_pair | p50 $214K / p95 $18M | Y | Cyentia IRIS 2025 — Figure A3, p. 35 (Public row) |
| gov-citizen-portal-ddos | paginated | quantile\_pair | p50 $214K / p95 $18M | Y | Cyentia IRIS 2025 — Figure A3, p. 35 (Public row) |
| gov-records-tampering | paginated | quantile\_pair | p50 $214K / p95 $18M | Y | Cyentia IRIS 2025 — Figure A3, p. 35 (Public row) |
| gov-employee-insider-leak | paginated | quantile\_pair | p50 $214K / p95 $18M | Y | Cyentia IRIS 2025 — Figure A3, p. 35 (Public row) |

---

### Healthcare (5 archetypes)

Two archetypes carry a `paginated` anchor from IRIS 2025 Figure A3 p. 35 (Healthcare row: p50 $557K / p95 $14M) — values corrected from an earlier vendored-calibration cross-contamination (see Demotion/Correction Log below). Three archetypes are anecdotal.

| Archetype | Tier | Type | Values | V | Primary source |
|-----------|------|------|--------|---|----------------|
| ransomware-on-ehr | paginated | quantile\_pair | p50 $557K / p95 $14M | Y | Cyentia IRIS 2025 — Figure A3, p. 35 (Appendix — Loss magnitude statistics by sector) |
| healthcare-staff-credential-phish | paginated | quantile\_pair | p50 $557K / p95 $14M | Y | Cyentia IRIS 2025 — Figure A3, p. 35 (Appendix — Loss magnitude statistics by sector) |
| ransomware-healthcare-small-practice | anecdotal | none | — | N | Sophos locator gated/unreachable (re-verify-eligible; see Demotion Log §D) |
| accidental-insider-exposure | anecdotal | none | — | N | No source for per-org median/mean loss for accidental healthcare exposure |
| healthcare-record-alteration | anecdotal | none | — | N | No TIER-1/2 source for per-org EHR integrity tampering loss magnitude |

---

### Hospitality (4 archetypes)

| Archetype | Tier | Type | Values | V | Primary source |
|-----------|------|------|--------|---|----------------|
| hospitality-pos-card-skimming | paginated | quantile\_pair | p50 $600K / p95 $62M | Y | Cyentia IRIS 2025 — p. 35, 'Losses observed per sector' table (Accommodation) |
| hospitality-loyalty-account-takeover | paginated | quantile\_pair | p50 $600K / p95 $62M | Y | Cyentia IRIS 2025 — p. 35, 'Losses observed per sector' table (Accommodation) |
| hospitality-guest-data-insider | paginated | quantile\_pair | p50 $600K / p95 $62M | Y | Cyentia IRIS 2025 — p. 35, 'Losses observed per sector' table (Accommodation) |
| hospitality-booking-ddos-peak-season | anecdotal | none | — | N | No per-org source for seasonal-peak hospitality DDoS loss magnitude |

---

### Manufacturing (10 archetypes)

Six archetypes carry the IRIS 2025 Manufacturing row anchor (p50 $1M / p95 $42M). Three OT archetypes (ransomware-on-control-layer, field-instrument-spoofing, chemical-process-safety-attack) were demoted to anecdotal after citation verification failure (see Demotion Log §C).

| Archetype | Tier | Type | Values | V | Primary source |
|-----------|------|------|--------|---|----------------|
| unauthorized-plc-modification | paginated | quantile\_pair | p50 $1M / p95 $42M | Y | Cyentia IRIS 2025 — p. 35, "Losses observed per sector" table (Manufacturing) |
| safety-system-bypass | paginated | quantile\_pair | p50 $1M / p95 $42M | Y | Cyentia IRIS 2025 — p. 35, "Losses observed per sector" table (Manufacturing) |
| ransomware-on-virtualization-stack | paginated | quantile\_pair | p50 $1M / p95 $42M | Y | Cyentia IRIS 2025 — p. 35, "Losses observed per sector" table (Manufacturing) |
| insider-ip-theft-manufacturing | paginated | quantile\_pair | p50 $1M / p95 $42M | Y | Cyentia IRIS 2025 — p. 35, "Losses observed per sector" table (Manufacturing) |
| ip-theft-by-competitor | paginated | quantile\_pair | p50 $1M / p95 $42M | Y | Cyentia IRIS 2025 — p. 35, "Losses observed per sector" table (Manufacturing) |
| manufacturing-billing-fraud | paginated | quantile\_pair | p50 $1M / p95 $42M | Y | Cyentia IRIS 2025 — p. 35, "Losses observed per sector" table (Manufacturing) |
| ransomware-on-control-layer | anecdotal | none | — | N | Norsk Hydro NOK figure failed primary contact; JBS locator secondary-only (see §C) |
| field-instrument-spoofing | anecdotal | none | — | N | Norsk Hydro NOK figure failed primary contact; JBS locator secondary-only (see §C) |
| chemical-process-safety-attack | anecdotal | none | — | N | Norsk Hydro NOK figure failed primary contact; JBS locator secondary-only (see §C) |
| manufacturing-facility-sabotage | anecdotal | none | — | N | No TIER-1/2 source for per-org loss percentiles for physical/cyber facility sabotage |

---

### Professional Services (4 archetypes)

| Archetype | Tier | Type | Values | V | Primary source |
|-----------|------|------|--------|---|----------------|
| moveit-class-zero-day-mft | paginated | quantile\_pair | p50 $736K / p95 $17M | Y | Cyentia IRIS 2025 — Figure A3, p. 35 (Professional row) |
| ransomware-on-fileshare | paginated | quantile\_pair | p50 $736K / p95 $17M | Y | Cyentia IRIS 2025 — Figure A3, p. 35 (Professional row) |
| professional-payroll-bec | paginated | quantile\_pair | p50 $736K / p95 $17M | Y | Cyentia IRIS 2025 — Figure A3, p. 35 (Professional row) |
| professional-office-physical-theft | anecdotal | none | — | N | No per-org cyber-loss source for physical office equipment theft |

---

### Retail / E-Commerce (5 archetypes)

| Archetype | Tier | Type | Values | V | Primary source |
|-----------|------|------|--------|---|----------------|
| credential-stuffing-consumer-portal | paginated | quantile\_pair | p50 $746K / p95 $45M | Y | Cyentia IRIS 2025 — p. 35, 'Losses observed per sector' table (Retail) |
| data-breach-notification-regulatory-tail | paginated | quantile\_pair | p50 $746K / p95 $45M | Y | Cyentia IRIS 2025 — p. 35, 'Losses observed per sector' table (Retail) |
| retail-pos-card-skimming | anecdotal | none | — | N | No primary-source per-org POS skimming loss percentiles (re-verify-eligible) |
| retail-ecommerce-checkout-ddos | anecdotal | none | — | N | No primary-source per-org e-commerce DDoS checkout loss percentiles (re-verify-eligible) |
| retail-store-employee-fraud | anecdotal | none | — | N | No primary-source per-org retail insider fraud loss percentiles (re-verify-eligible) |

---

### Technology / SaaS (11 archetypes)

| Archetype | Tier | Type | Values | V | Primary source |
|-----------|------|------|--------|---|----------------|
| cloud-account-takeover | paginated | quantile\_pair | p50 $718K / p95 $217M | Y | Cyentia IRIS 2025 — p. 35, 'Losses observed per sector' table (Technology) |
| api-key-leak-devops | paginated | quantile\_pair | p50 $718K / p95 $217M | Y | Cyentia IRIS 2025 — p. 35, 'Losses observed per sector' table (Technology) |
| session-hijack-post-mfa-bypass | paginated | quantile\_pair | p50 $718K / p95 $217M | Y | Cyentia IRIS 2025 — p. 35, 'Losses observed per sector' table (Technology) |
| s3-misconfiguration-data-exposure | paginated | quantile\_pair | p50 $718K / p95 $217M | Y | Cyentia IRIS 2025 — p. 35, 'Losses observed per sector' table (Technology) |
| mfa-fatigue-prompt-bombing | paginated | quantile\_pair | p50 $718K / p95 $217M | Y | Cyentia IRIS 2025 — p. 35, 'Losses observed per sector' table (Technology) |
| solarwinds-class-supply-chain | anecdotal | none | — | N | No per-org percentile source for nation-state supply chain loss magnitude |
| package-registry-supply-chain | anecdotal | none | — | N | No per-org percentile source for package-registry supply chain loss magnitude |
| generative-ai-prompt-injection | anecdotal | none | — | N | No per-org percentile source for GenAI prompt-injection loss magnitude |
| competitor-trade-secret-recruit | anecdotal | none | — | N | No per-org percentile source for trade-secret theft via recruitment loss magnitude |
| datacenter-physical-breach | anecdotal | none | — | N | No per-org percentile source for datacenter physical breach loss magnitude |
| saas-revenue-outage-sabotage | anecdotal | none | — | N | No per-org percentile source for insider SaaS outage sabotage loss magnitude |

---

### Telecom (5 archetypes)

| Archetype | Tier | Type | Values | V | Primary source |
|-----------|------|------|--------|---|----------------|
| telecom-subscriber-data-breach | paginated | quantile\_pair | p50 $718K / p95 $217M | Y | Cyentia IRIS 2025 — p. 35, 'Losses observed per sector' table (Technology/Telecom) |
| telecom-ddos-core-network | anecdotal | none | — | N | No per-org percentile source for carrier-side core DDoS loss magnitude |
| telecom-sim-swap-fraud | anecdotal | none | — | N | No carrier-side per-org SIM-swap loss percentile source |
| telecom-bgp-route-hijack | anecdotal | none | — | N | No per-org percentile source for BGP route-hijack loss magnitude |
| telecom-field-cabinet-tamper | anecdotal | none | — | N | No per-org percentile source for physical field-cabinet tamper loss magnitude |

---

### Transportation / Logistics (3 archetypes)

| Archetype | Tier | Type | Values | V | Primary source |
|-----------|------|------|--------|---|----------------|
| logistics-disruption | paginated | quantile\_pair | p50 $490K / p95 $23M | Y | Cyentia IRIS 2025 — p. 35, 'Losses observed per sector' table (Transportation) |
| logistics-tms-data-tampering | anecdotal | none | — | N | No per-org source for TMS data-tampering loss magnitude |
| logistics-warehouse-physical-intrusion | anecdotal | none | — | N | No per-org cyber-loss source for warehouse physical intrusion loss magnitude |

---

## Demotion / Correction Log

Every catch that changed a row from its initially proposed form.

### §A — Healthcare: vendored-calibration cross-contamination (fixed in-branch)

**Commit:** d48e949 (batch A healthcare rows).
**What happened:** initial research pass proposed $275K/$16M for `ransomware-on-ehr` and `healthcare-staff-credential-phish`, drawn from an internal vendored calibration file (`calibration_sources/healthcare_estimates.md`) that had blended IRIS 2024 and IBM CODB figures without surfacing the individual locators. The methodology gate identified this as vendored-calibration cross-contamination: the values were not directly readable from a single primary locator.
**Fix:** re-anchored both rows to the IRIS 2025 primary directly: **p50 $557K / p95 $14M** per Figure A3, p. 35 (Appendix — Loss magnitude statistics by sector, Healthcare row). Corrected values are confirmed at source.

### §B — Batch C: gated member-vault locator → re-anchored to public Information row

**Affected archetypes:** technology/SaaS archetypes in batch C initially cited a Cyentia "member vault" locator accessible only via authenticated download (not publicly reachable). The verify agent demoted these rows as `verified: false`.
**Fix:** re-anchored to the publicly reachable IRIS 2025 Technology/Information sector row at **p50 $718K / p95 $217M** (p. 35, 'Losses observed per sector' table). This is the same underlying source — the public figure is identical to the member-vault figure for this sector. Five technology/SaaS archetypes and one telecom archetype carry this anchor.

### §C — Batch A: Norsk Hydro NOK figure failed primary contact (3 manufacturing rows demoted)

**Affected archetypes:** `ransomware-on-control-layer`, `field-instrument-spoofing`, `chemical-process-safety-attack`.
**What happened:** the research pass proposed a `multiplier_over_baseline` anchor citing Norsk Hydro Q1 2019 Report for "NOK 450M (~$70M USD at 2019 rates)". The verify agent fetched the Norsk Hydro Q1 2019 Report directly: the document states **"NOK 300-350 million in Q1"** — NOK 450M does not appear. Additionally, the `secondary_url` (hydro.com/globalassets/.../q1-2019/q1-2019-report-en.pdf) returns HTTP 404. The JBS USD citation in the same rows used a WSJ news article as the `locator` (secondary source, not a JBS primary press release or CISA advisory), violating B-METH-7.
**Result:** all three rows demoted to `anchor_type: none`, `loss_tier: anecdotal`. The 2.5× multiplier value is preserved in the sub-sector calibration doc (`sub_sector_chemical_manufacturing.md`) for C-iii re-evaluation once a corrected primary locator is confirmed.
**Re-verify-eligible:** Yes — if a researcher can confirm the correct NOK figure and a working Norsk Hydro primary URL, these three rows can be promoted to TIER-2 multiplier rows in C-iii.

#### §C-iii-a re-verify attempt (2026-06-11) — attempt failed

**Attempt:** C-iii-a implementer attempted the Norsk Hydro Q2-2019 report and CISA advisory `aa21-243a` per the task specification. Both conditions of the restoration gate (BOTH sources reachable AND confirming) failed:

1. **Norsk Hydro Q2-2019 report** (`https://www.hydro.com/Document/Index?name=Report+Q2+2019.pdf&id=105855`): fetched successfully as binary PDF (HTTP 200, 451.8 KB). The associated news page (`https://www.hydro.com/en/global/media/news/2019/second-quarter-2019-results-down-on-lower-realized-prices/`) discloses Q2-only impact of **"NOK 250–300 million in Q2 2019"** — this is the quarter-only figure, not the cumulative H1 figure. The cumulative NOK 550–650M figure appears in the **Q3-2019 report** (confirmed via web search; Q3 PDF is reachable at `https://www.hydro.com/Document/Index?name=Report+Q3+2019.pdf&id=252245`), not Q2 as the task specification stated. The task gate requires "the Hydro Q2-2019 report (the cumulative NOK 550–650M primary)" — the Q2 report does not contain the cumulative figure; the Q3 report does. Gate condition mismatches source document.

2. **CISA advisory `aa21-243a`** (`https://www.cisa.gov/news-events/cybersecurity-advisories/aa21-243a`): returns HTTP 403 Forbidden (both the HTML page and the PDF at `https://www.cisa.gov/sites/default/files/publications/AA21-243A-Ransomware_Awareness_for_Holidays_and_Weekends.pdf`). Additionally, `aa21-243a` is a general *Ransomware Awareness for Holidays and Weekends* advisory — it references JBS Foods as an example incident but does not contain a specific financial disclosure of the $11M ransom. The advisory does not function as a primary source for a JBS-specific loss figure.

**Decision:** restoration gate requires BOTH sources reachable AND confirming. CISA `aa21-243a` is unreachable (403) and is structurally the wrong document for a per-incident financial primary (general awareness advisory, not a JBS-specific advisory). Gate fails. All three rows remain demoted. Secondary coverage suggests a ~800 MNOK all-quarters total, but no primary locator was captured — context only, NOT a citable anchor. The Q3-2019 report (`id=252245`) is the correct primary for the cumulative H1 figure; note that Q3-2019 reachability was confirmed via web search only — NOT direct-fetched in this pass; a future implementer should verify HTTP status before relying on it. A future C-iii pass should gate against Q3-2019 (direct-fetch confirmed) + a JBS-specific primary rather than Q2-2019 + aa21-243a.

**Candidate JBS-specific primaries for the corrected future gate — existence NOT verified this pass:**
- JBS USA cyber-attack press statement on jbsfoodsgroup.com (e.g. `https://jbsfoodsgroup.com/articles/jbs-usa-cyber-attack-updates` — URL pattern to-be-confirmed; NOT fetched or verified in this pass).
- FBI flash alert on JBS attribution: MC-000148-MW (public permalink to be located; NOT fetched or verified in this pass).

### §D — Sophos healthcare locator: gated report (1 row re-verify-eligible)

**Affected archetype:** `ransomware-healthcare-small-practice`.
**What happened:** The Sophos State of Ransomware in Healthcare 2024 whitepaper was reachable via a third-party copy confirming the numbers, but the official Sophos primary landing page (`sophos.com/en-us/whitepaper/state-of-ransomware-in-healthcare`) now serves the 2025 edition, and the 2024 URL (`sophos.com/en-us/whitepaper/state-of-ransomware-in-healthcare-2024`) returns HTTP 404. Per B-METH-7, primary-unreachable = TIER-3 regardless of secondary corroboration.
**Result:** row demoted to `anchor_type: none`, `loss_tier: anecdotal`.
**Re-verify-eligible:** Yes. To restore: cite 'Sophos State of Ransomware in Healthcare 2024 whitepaper, p. [N] — Recovery Costs in Healthcare section' with a working official Sophos-published PDF URL as `secondary_url`. If Sophos does not re-publish the 2024 edition, the row remains anecdotal.

#### §D-iii-a re-verify attempt (2026-06-11) — attempt failed

**Attempt:** C-iii-a implementer attempted the official Sophos 2024 whitepaper URL per the task specification.

- `https://www.sophos.com/en-us/whitepaper/state-of-ransomware-in-healthcare-2024` — still returns HTTP 404 (confirmed again).
- `https://www.sophos.com/en-us/whitepaper/state-of-ransomware-in-healthcare` — still serves the 2025 edition only; no link to 2024 edition.
- `https://www.sophos.com/en-us/blog/the-state-of-ransomware-in-healthcare-2024/` (official Sophos blog, redirected from `news.sophos.com`): confirms mean recovery cost $2.57M; PDF download buttons link to the whitepaper landing page (now 2025), not a direct PDF URL for 2024.
- `https://www.sophos.com/en-us/press/press-releases/2024/09/two-thirds-healthcare-organizations-hit-ransomware-four-year-high` (official Sophos press release): confirms mean recovery cost $2.57M only; the $750K **median recovery cost** figure does not appear on official Sophos pages — it surfaces only in search-engine summaries of the whitepaper PDF contents.
- No `assets.sophos.com` URL for the 2024 healthcare PDF is publicly discoverable (confirmed via search and pattern-guessing attempts).
- Third-party copy at `https://getmediguard.com/wp-content/uploads/2025/05/sophos-state-of-ransomware-healthcare-2024.pdf` appears to contain the report but is not an official Sophos-hosted URL and cannot satisfy B-METH-7.

**Decision:** official Sophos-hosted 2024 whitepaper PDF is still unreachable. Mean recovery cost ($2.57M) is confirmed on official Sophos pages, but median recovery cost ($750K) has no official-page locator. Gate fails on both counts: no official PDF URL and no official page showing the median figure. Row remains anecdotal.

---

## Notes for C-iii

### Re-verify-eligible rows

The following rows are `anchor_type: none` but have a known path to promotion if a corrected primary locator can be confirmed.

**C-iii-a re-verify attempts completed 2026-06-11 — 0 of 4 eligible rows restored. All remain demoted. Detail in §C-iii-a and §D-iii-a above.**

Rows where a future attempt may succeed (corrected gate conditions noted):

1. `ransomware-on-control-layer` — Norsk Hydro / JBS: C-iii-a gate was (Q2-2019 + aa21-243a); **corrected gate should be (Q3-2019 report `id=252245` for cumulative NOK 550–650M H1 figure) + (JBS-specific primary: JBS press release permalink or USDA/FBI disclosure — NOT aa21-243a which is a general awareness advisory)**. Q3-2019 PDF is reachable. JBS primary source is the open item.
2. `field-instrument-spoofing` — same corrected gate as above
3. `chemical-process-safety-attack` — same corrected gate as above; 2.5× multiplier documented in `sub_sector_chemical_manufacturing.md`
4. `ransomware-healthcare-small-practice` — Sophos 2024 PDF: official `assets.sophos.com` URL for 2024 edition not publicly discoverable as of 2026-06-11. Row upgrades to anecdotal-final if Sophos does not re-publish the 2024 PDF with a stable URL.
5. `retail-pos-card-skimming` — DBIR 2024 has frequency data; a future edition may add per-org loss percentiles for retail skimming
6. `retail-ecommerce-checkout-ddos` — NETSCOUT / Coveware: watch for a report that segments per-org loss by sector + attack type
7. `retail-store-employee-fraud` — NRF NRSS / Coveware: aggregate-only at present; watch for per-org breakdown

### `fair_cam/data/iris_2025.py` LOSS\_BY\_SECTOR\_TREND constant

The `LOSS_BY_SECTOR_TREND` dict in `fair_cam/data/iris_2025.py` contains **trend-display constants** (year-over-year directional indicators for the dashboard). These are NOT loss anchors and MUST NOT be used as p50/p95 values in C-iii. Loss anchors are sourced exclusively from `data/loss_anchor_tables.json`.

### Locator-convention note

The first two batch commits (healthcare, government/education/professional — batches A and B) carry locators in the form **"Figure A3, p. 35"**. Subsequent batches carry the harmonized form **"p. 35, 'Losses observed per sector' table (above Figure A3 caption)"** — this reflects the same page in the same document. Both locator forms point to the same physical page and table. They are functionally equivalent; the convention was harmonized mid-sweep and the earlier rows were not rewritten to avoid invalidating the verify-pass record on committed rows.

### C-iii href-allowlist handoff constraint (Sec-I1)

Citation `locator` and `secondary_url` values from `data/loss_anchor_tables.json` may end up in `source_citations` and be rendered as hyperlinks in C-iii templates.

**C-iii MUST allowlist URL schemes (`https://` only) before any `href` use.** Jinja's `autoescape` covers text nodes but does NOT sanitize `href` attribute values — a `javascript:` or `data:` URI in a `locator` field would pass autoescape and execute. The allowlist check belongs at the template layer before any `href="{{ citation.locator }}"` rendering.

**Status (2026-06-12): IMPLEMENTED** — gate is `riskflow.formatting.linkify_https` (https-only scheme allowlist, explicit `urlsplit` check), applied in `templates/library/entry_detail.html` citations block; regression tests in `tests/unit/test_formatting_linkify.py` + `tests/integration/test_library_routes.py`. Issue #349 closed.

---

## C-iii-a Applied — Supersession Record

**Sub-PR:** C-iii-a of Epic C (#335). **Applied:** 2026-06-11.

**Task commits (T1–T4 + Step-0 NTHs):**

- T1: re-anchor IRIS priors + T1 review fixes + unmappable-industry pin test
- T2: re-verify attempts (0 restored) + T2 review fixes
- T3: schema extension; seed re-curation; T3M-B1 credential-stuffing secondary R fix; T3M-B2 idempotency fix; T3SC-1 provenance note restoration
- T4: in-place UPDATE migration (rev 3d7b9e357d52) + triage of extension citation-count assertion
- T5/Step 0: T4 review NTHs — heal-path vuln_posture assertion + seed-sourced sigma

### Tier supersession summary

**Total seed entries:** 44 (31 base + 13 extension)

| Tier after C-iii-a | Count | Description |
|--------------------|-------|-------------|
| `paginated` / lognormal | **23** | IRIS 2025 Figure A3 sector quantile_pair converted from PERT |
| `vendor` / lognormal | **11** | energy_utilities sector baseline × sub-sector multiplier (σ=1.8377) |
| `anecdotal` / PERT | **10** | anchor_type=none; pre-existing PERT values untouched |

**Paginated conversion sector mapping (23 C-iii-a-converted entries):**

C-iii-b will extend these counts with the ~13 new archetypes from `target_archetypes.json`.

| Sector (IRIS Figure A3 p.35) | p50 | p95 | σ (ln(p95/p50)/Z₀.₉₅) | Entry count |
|------------------------------|-----|-----|------------------------|-------------|
| Healthcare | $557K | $14M | 1.9602 | 1 |
| Utilities | $146K | $3M | 1.8377 | 1 (IT-class paginated) |
| Education | $249K | $6M | 1.9346 | 1 |
| Information (Technology) | $718K | $217M | 3.4722 | 5 (tech/SaaS) |
| Manufacturing | $1M | $42M | 2.2723 | 4 |
| Financial | $1M | $194M | 3.2026 | 5 |
| Retail | $746K | $45M | 2.4924 | 2 |
| Transportation | $490K | $23M | 2.3399 | 1 |
| Professional services | $736K | $17M | 1.9088 | 2 |
| Public | $214K | $18M | 2.6946 | 1 |
| Accommodation (Hospitality) | $600K | $62M | 2.8197 | 0 (C-iii-b new) |

**Vendor conversion (11 entries):** all 11 OT archetypes in the Energy/Utilities sector.
- 9 entries: Utilities baseline × 2.7 (`mean = ln(146K × 2.7)`, `sigma = 1.8377`)
- 1 entry (`pipeline-scada-integrity`): Utilities baseline × 3.0 (`mean = ln(146K × 3.0)`, `sigma = 1.8377`)
- σ is inherited from the Utilities baseline `quantile_pair` (p50=$146K, p95=$3M); multiplier scales location only — C-ii-b gate-verified algebra.

**Retained PERT / anecdotal (10 entries, slugs):**

1. `bec-fraud-financial` — no qualifying two-leg BEC anchor for financial services
2. `solarwinds-class-supply-chain` — no per-org percentile source for nation-state supply chain
3. `package-registry-supply-chain` — no per-org percentile source for package-registry supply chain
4. `ransomware-healthcare-small-practice` — Sophos 2024 PDF unreachable (re-verify-eligible; see §D)
5. `generative-ai-prompt-injection` — no per-org percentile source (see §114 disposition below)
6. `ransomware-on-control-layer` — Norsk Hydro / JBS gate failed (see §C)
7. `field-instrument-spoofing` — same as above
8. `chemical-process-safety-attack` — same as above
9. `accidental-insider-exposure` — no qualifying source for accidental healthcare exposure loss
10. `retail-pos-card-skimming` — no primary-source per-org POS skimming loss percentiles

### Vuln re-curations (#338)

Every entry gained `calibration_anchor.vuln_posture = "inherent (control-naive) per fair-cam-methodology 'Vulnerability anchor'"` — this is the #338 declaration across all 44 entries.

**Two entries additionally had vuln values raised to inherent posture:**

1. **`credential-stuffing-consumer-portal`** — vuln raised from `{low:0.15, mode:0.35, high:0.65}` (controlled) to `{low:0.10, mode:0.30, high:0.60}` (inherent). Rationale: the old values modeled a credential-stuffing attack with assumed rate-limiting and credential-rotation controls already in place — a controlled posture inconsistent with the "inherent (control-naive)" #338 declaration. The new values reflect a consumer portal without dedicated anti-stuffing controls.

2. **`bec-fraud-financial`** — vuln raised from `{low:0.03, mode:0.08, high:0.20}` (controlled) to `{low:0.05, mode:0.20, high:0.45}` (inherent). Rationale: the old values modeled BEC success with assumed email-control (DMARC, secure email gateway) uplift in place. The inherent posture reflects finance-staff susceptibility without those controls.

### Credential-stuffing campaign-level reinterpretation (resolves #113)

The `credential-stuffing-consumer-portal` entry's FAIR model was **reinterpreted from "per-attempt background process" to "per-campaign incident-level" model**. This was required because raising vuln to 0.30 while leaving the old per-attempt TEF (`{low:5, mode:50, high:500}`) would produce ~15 loss events/yr × Retail p50 anchor ($746K) ≈ $11M/yr expected primary loss — incoherent for a scenario that materially injures a portal perhaps a handful of times per year.

**What changed:**
- `threat_event_frequency`: `{low:5, mode:50, high:500}` per-attempt → `{low:1, mode:5, high:20}` campaigns/year
- `secondary_loss.R`: was `200` (per-ATO × expected successful attempts — a per-attempt multiplier inapplicable at campaign level) → `1.0` (campaign model; secondary losses accompany the campaign incident, not each attempted login)
- `canonical_fair_gap`: rewritten from the per-attempt phrasing ("loss frequency equals the successful stuffing rate") to the campaign-level description
- `calibration_anchor.loss_anchor`: records the reinterpretation provenance

**Basis for TEF values:** analyst-judged campaign frequency for a major consumer portal at inherent posture, consistent with Akamai State-of-the-Internet campaign-frequency reporting. Methodology-gated (plan-gate round 1 ruled the reinterpretation and values defensible).

**#113 closes on TEF coherence grounds** — the model was reinterpreted to campaign-level; TEF, vuln, and loss are now coherent. The issue is not closed merely on the loss-anchor supersession.

---

## T2b Applied — C-iii-b Supersession Record

**Sub-PR:** C-iii-b of Epic C (#335). **Applied:** 2026-06-11.

### Outcome summary

**DOUBLE NEGATIVE — zero anchor upgrades.** Both evaluated sources (NAIC Cyber Supplement and VERIS/VCDB) were disqualified as loss anchors at all tiers. No row in `data/loss_anchor_tables.json` was modified.

- **NAIC Annual Report on the Cybersecurity Insurance Market** (data years 2021–2024, four verified PDFs): the Cyber Supplement schema collects aggregate DWP, claim counts, direct losses paid/incurred, and loss ratios ONLY. No per-event loss distributions, percentiles, means, or medians exist in any report year. Policy A (quantile pair) inapplicable; Policy B (per-event median) inapplicable. NOT a loss anchor at any tier.

- **VERIS Community Database (VCDB)** at pinned commit 5a6473980ab6f0ad151d8fd2c7b0e9a818aecb95: `impact.overall_amount` populated in ~3.2% of the 10,037-incident validated corpus. All populated records in two independent random samples (research n=598, verifier n=60) date from incident years 2008–2019; ~0 records year ≥ 2020 carry amounts. Vintage rule (a) (≥2020 window) yields an empty set; rule (b) (CPI-adjust pre-2020) yields ~321 records with single-digit per-class/per-sector cells for all 25 T2b target archetypes — below any defensible quantile floor. NOT a loss anchor for any evaluated archetype.

### Per-archetype disposition

All 25 T2b target archetypes remain unchanged from their pre-T2b state. No archetype was upgraded, demoted, or modified. Every archetype retains its existing loss_tier and anchor_type as recorded in `data/loss_anchor_tables.json`; the double-negative outcome produced no qualifying citations to apply.

### Adversarial verifier verdict

The verifier independently reviewed both sources against 9 citation claims:

- **8 CONFIRMED** — all NAIC claims and the VCDB structural claims confirmed independently.
- **1 PARTIAL** — the researcher's VCDB sample-file UUID was garbled in the research report. Correct file: `011c5034-b0a2-47e6-a9fc-8e725cde01db.json`, confirmed year=2016, amount=100000, action=hacking. This is a documentation error only; the underlying data claim (that populated records are pre-2020 and single-record cells fall below quantile floor) is upheld. The double-negative conclusion is unaffected.

### T4/T5 batch-count projection

Because zero upgrades were made, the T4/T5 batch counts are UNCHANGED from the pre-T2b projection: **T4 = 10 archetypes, T5 = 15 archetypes.** No row in `data/loss_anchor_tables.json` was touched by this sub-PR.

### Issue dispositions (for C-iv closure)

**#113 — Credential-stuffing TEF/vuln coherence:** RESOLVED in C-iii-a via campaign-level reinterpretation (see above). TEF {1,5,20} campaigns/year + vuln {0.10,0.30,0.60} + Retail lognormal loss anchor + secondary R=1.0 form a coherent campaign-level FAIR model. Closure note: "model reinterpreted to campaign-level; TEF, vuln, and loss now coherent (C-iii-a T3, rule-7 override)."

**#114 — generative-ai-prompt-injection no real-incident anchor:** ADDRESSED BY HONEST TIER in C-iii-a. The entry retains PERT/anecdotal with `loss_tier = "anecdotal"` explicitly recorded in-model — the absence of a citeable anchor is documented, not papered over. The issue cannot be "fixed" until a qualifying primary source exists. Closure note: "addressed-by-honest-tier (C-iii-a T3); issue is a content gap, not a code gap — re-evaluate when IRIS 2026 or a specialist report adds a GenAI-specific sector loss row."

**#338 — vuln_posture declarations on all entries:** COMPLETE in C-iii-a. Every entry carries `calibration_anchor.vuln_posture` after migration `3d7b9e357d52` (T4). Schema extension landed in T3 Step 1. Heal-path coverage added in the T5 NTH pass (test_stale_heal asserts vuln_posture present after migration). Closure note: "all 44 entries carry vuln_posture; migration heals stale rows (C-iii-a)."

---

## C-iii-b Applied — Expansion + Rebalance Record

**Sub-PR:** C-iii-b of Epic C (#335). **Applied:** 2026-06-11.

**Task labels:** T1 (calibration guard + freeze banner) · T2 (rebalance audit) · T2b (NAIC + VERIS evaluation — see T2b section above) · T3 (batch A, 13 paginated) · T4 (batch B, 10 anecdotal) · T5 (batch C, 15 anecdotal) · T6 (balance assertions + migration) · T7 (this record).

### Final composition

**OT predicate (MB-I1 — exact):** `asset_class in {"ot_systems", "safety_systems"}`. The `threat_event_type.startswith("ot_")` predicate is prohibited — it undercounts by 3 (`ransomware-on-historian`, `it-ot-bridge-compromise`, `nation-state-ics-supply-chain` are OT by asset_class with generic threat types).

**Totals (status == "published" entries only):**

| Metric | Value |
|--------|-------|
| Total published entries | **82** |
| paginated tier (TIER-1 primary-cited lognormal) | **36** |
| vendor tier (TIER-2 named-report lognormal) | **11** |
| anecdotal tier (TIER-3 analyst PERT) | **35** |
| OT entries (`asset_class ∈ {ot_systems, safety_systems}`) | **16 / 82 = 19.5%** |
| OT share ≤ 0.32 spec cap | ✓ (0.195) |
| Total in [70, 90] spec range | ✓ (82) |

**Per-sector composition (published-only; OT predicate column is OT entries within sector):**

| Sector | Entries | OT | paginated | vendor | anecdotal |
|--------|---------|----|-----------|--------|-----------|
| manufacturing | 10 | 3 | 6 | 0 | 4 |
| energy_utilities | 13 | 13 | 2 | 11 | 0 |
| healthcare | 5 | 0 | 2 | 0 | 3 |
| financial_services | 9 | 0 | 5 | 0 | 4 |
| retail_ecommerce | 5 | 0 | 2 | 0 | 3 |
| technology_saas | 11 | 0 | 5 | 0 | 6 |
| government_public | 4 | 0 | 3 | 0 | 1 |
| education | 4 | 0 | 2 | 0 | 2 |
| professional_services | 4 | 0 | 3 | 0 | 1 |
| transportation_logistics | 3 | 0 | 1 | 0 | 2 |
| telecom | 5 | 0 | 1 | 0 | 4 |
| hospitality | 4 | 0 | 3 | 0 | 1 |
| food_agriculture | 5 | 0 | 0 | 0 | 5 |
| **Total** | **82** | **16** | **36** | **11** | **35** |

Note: sector counts reflect the §3 SECTOR_PREDICATES — entries with `applicable_industries = ["information"]` + `"telecom" in tags` count under telecom, not technology_saas. Multi-sector entries (e.g. energy OT archetypes also matching manufacturing via `"manufacturing"` in tags) appear once per sector cell they match. The composition table is sector-additive (each row's applicable_industries drives exactly one sector cell for OT-share purposes).

### Trim / merge record

**Zero trims. Zero merges.**

Decision documented in `docs/reference/loss-anchors/ciiib-rebalance-decisions.md` (T2). All 10 pre-existing anecdotal entries were reviewed against the spec §3 trim criteria (weakly-cited AND near-duplicate archetype). Each of the 10 was found to represent a distinct archetype with no near-duplicate in the existing or new-entry set. The 3 Norsk-Hydro-demoted entries (`ransomware-on-control-layer`, `field-instrument-spoofing`, `chemical-process-safety-attack`) are explicitly KEPT as distinct OT archetypes with re-verify-eligible anchors (see §C). No paginated or vendor entries were considered for trim (hard rule).

### T2b pointer

See the **T2b Applied** section above for the NAIC + VERIS/VCDB double-negative outcome. Zero anchor upgrades resulted; T4/T5 batch counts proceeded unchanged.

### Batch A — 13 paginated new entries (T3)

All 13 entries authored from `target_archetypes.json` ∩ anchor rows with `anchor_type = quantile_pair`. Every entry: primary_loss = lognormal(`mean=ln(p50)`, `sigma=ln(p95/p50)/Z₀.₉₅`); secondary_loss = lognormal with `sigma=sigma_primary` and analyst-judged R ∈ [0.1, 1.0]; `loss_tier = "paginated"`; `calibration_anchor.vuln_posture` declared.

**New paginated slugs:**
`telecom-subscriber-data-breach` · `hospitality-pos-card-skimming` · `hospitality-loyalty-account-takeover` · `hospitality-guest-data-insider` · `education-student-records-insider` · `gov-citizen-portal-ddos` · `gov-records-tampering` · `gov-employee-insider-leak` · `ip-theft-by-competitor` · `manufacturing-billing-fraud` · `healthcare-staff-credential-phish` · `professional-payroll-bec` · `energy-billing-system-tamper`

**T3 review-fix themes applied:**
- ERISA → FLSA relabel on `professional-payroll-bec` (the regulatory framing: FLSA/state wage law governs payroll BEC misclassification, not ERISA which covers benefit plans)
- ATO campaign-TEF statement on `education-student-records-insider` (TEF reframed as campaign/incident frequency, not per-login-attempt)
- Insider example replacement on `gov-employee-insider-leak` (fabricated-source example removed, replaced with research-recorded incident)

**Numeric verification (PGSC-1 — per project convention, §Verification reporting):** σ values derived as `ln(p95/p50) / Z₀.₉₅` where `Z₀.₉₅ = scipy.stats.norm.ppf(0.95) = 1.6448536269514722`. Hand-math and stored values are independently computed; the "Match" column reflects `|hand − stored| < 1e-10`.

*Primary σ (3 representative entries):*

| Entry | p50 | p95 | σ expected (hand-math) | σ stored | Match |
|-------|-----|-----|------------------------|----------|-------|
| `telecom-subscriber-data-breach` | $718K | $217M | 3.4721527617380321 | 3.4721527617380317 | ✓ |
| `education-student-records-insider` | $249K | $6M | 1.9345562423344826 | 1.9345562423344822 | ✓ |
| `hospitality-pos-card-skimming` | $600K | $62M | 2.8196794734902668 | 2.8196794734902664 | ✓ |

*Secondary σ = primary σ (σ\_sec == σ\_pri design rule; 2 entries verified from seed JSON):*

| Entry | σ\_primary (stored) | σ\_secondary (stored) | Match |
|-------|--------------------|-----------------------|-------|
| `hospitality-guest-data-insider` | 2.8196794734902664 | 2.8196794734902664 | ✓ |
| `ip-theft-by-competitor` | 2.272341779864428 | 2.272341779864428 | ✓ |

### Batch B — 10 anecdotal new entries (T4)

The none-anchored archetypes of the three previously-unrepresented sectors: telecom (4), food_agriculture (5), hospitality (1). Every entry: analyst PERT both legs, `loss_tier = "anecdotal"`, inherent TEF/vuln, `calibration_anchor.vuln_posture` declared.

**New anecdotal slugs:**
`telecom-ddos-core-network` · `telecom-sim-swap-fraud` · `telecom-bgp-route-hijack` · `telecom-field-cabinet-tamper` · `food-cold-chain-ransomware` · `food-recall-data-tampering` · `agri-equipment-physical-tamper` · `agri-coop-bec-fraud` · `crop-science-ip-exfiltration` · `hospitality-booking-ddos-peak-season`

**Food_agriculture bound bucket per entry (MB-B2 — exact):**

| Slug | Bucket | `calibration_anchor.loss_anchor` bound statement |
|------|--------|--------------------------------------------------|
| `food-cold-chain-ransomware` | Manufacturing IRIS proxy | "sanity-bounded against Manufacturing IRIS (NAICS family proxy, food-processing→manufacturing mapping per C-ii-b)" |
| `food-recall-data-tampering` | Manufacturing IRIS proxy | "sanity-bounded against Manufacturing IRIS (NAICS family proxy, food-processing→manufacturing mapping per C-ii-b)" |
| `agri-equipment-physical-tamper` | Unbounded | "unbounded analyst PERT — physical equipment tampering on precision agriculture assets outside the IRIS cyber-loss corpus" |
| `agri-coop-bec-fraud` | IC3 BEC context | "analyst PERT informed by IC3 BEC per-complaint average — an order-of-magnitude reference, not an anchor" |
| `crop-science-ip-exfiltration` | Unbounded | "unbounded analyst PERT — crop-science IP exfiltration outside the IRIS cyber-loss corpus" |

IRIS Agriculture row (p50=$2M, p95=$3M) is **prohibited as a bound** for all five entries: σ≈0.247 is a near-point-mass disqualification; NAICS mismatch (IRIS Agriculture = NAICS 01-02 crop farming ≠ food processing); bounding analyst PERT against another analyst PERT is circular. The carve-out (MB-B2) applied to each entry individually.

**T4 review-fix themes applied:**
- Campaign-TEF statements on `telecom-sim-swap-fraud` and `agri-coop-bec-fraud` (TEF explicitly stated as campaigns/year with per-attempt reinterpretation documented)
- Attribution transparency on `telecom-bgp-route-hijack` and `telecom-ddos-core-network` (mixed-actor framing clarified)
- DDoS TEF unit correction on `telecom-ddos-core-network`

### Batch C — 15 anecdotal new entries (T5)

The remaining 15 none-anchored archetypes across the other sectors. Every entry: analyst PERT both legs, `loss_tier = "anecdotal"`, inherent TEF/vuln, `calibration_anchor.vuln_posture` declared.

**New anecdotal slugs:**
`education-research-ip-exfiltration` · `logistics-tms-data-tampering` · `logistics-warehouse-physical-intrusion` · `competitor-trade-secret-recruit` · `datacenter-physical-breach` · `branch-atm-physical-tamper` · `financial-transaction-tampering` · `healthcare-record-alteration` · `retail-ecommerce-checkout-ddos` · `saas-revenue-outage-sabotage` · `professional-office-physical-theft` · `retail-store-employee-fraud` · `manufacturing-facility-sabotage` · `financial-call-center-social-eng` · `education-campus-facility-tamper`

**T5 review-fix themes applied:**
- Bound-text corrections (EAST framework scope caveat added to `branch-atm-physical-tamper`: EAST covers European cash crime only, not a global per-org anchor; text clarified as context-only)
- NRF re-attribution on `retail-store-employee-fraud` (NRF NRSS cited as aggregate context; re-attributed to correct publication year)

### Balance assertions (T6)

**Tests in `tests/unit/test_seed_balance.py`** enforce the §3 coverage matrix over `status == "published"` entries only. All assertions green post-T5:

| Assertion | Threshold | Result |
|-----------|-----------|--------|
| (a) total published | ∈ [70, 90] | 82 ✓ |
| (b) OT share | ≤ 0.32 | 0.195 ✓ |
| (c) every §3 sector | ≥2 entries, ≥2 threat types | all 13 sectors ✓ |
| (d) named TET coverage | ≥2 each for 5 types | all 5 ✓ |
| (e) underused asset classes | ≥1 each for 5 classes | all 5 ✓ |
| (f) competitors actor | ≥1 | ✓ |
| (T6M-1e NTH) full-partition guard | zero-bucket set empty | ✓ |

**Migration:** `alembic/versions/60ff242180f6_seed_ciiib_expansion.py` — insert-if-absent for the 38 new entries; `.hex` no-hyphen UUIDs (`uuid4().hex` per `0897a0ff350e` precedent); `source = 'seed'`-guarded downgrade DELETE; convergence-rationale docstring matching `3d7b9e357d52`.
