---
title: "IBM Cost of a Data Breach Report 2024"
year: 2024
url: https://www.ibm.com/reports/data-breach
accessed: 2026-05-15
permalink: https://cdn.table.media/assets/wp-content/uploads/2024/07/30132828/Cost-of-a-Data-Breach-Report-2024.pdf
methodology_summary: "604 organizations across 17 industries and 16 countries/regions, breached between March 2023 and February 2024; 3,556 individual interviewees; breaches sized 2,100–113,000 compromised records; activity-based costing across 4 cost centers (detection & escalation, notification, post-breach response, lost business); research conducted by Ponemon Institute, sponsored and analyzed by IBM; 19th annual edition."
---

# IBM Cost of a Data Breach Report 2024 — Calibration Source

**Source:** IBM Security / Ponemon Institute, *Cost of a Data Breach Report 2024*, published 30 July 2024 (19th annual edition). Landing page: <https://www.ibm.com/reports/data-breach>. Full PDF (mirrored, accessed 2026-05-15): <https://cdn.table.media/assets/wp-content/uploads/2024/07/30132828/Cost-of-a-Data-Breach-Report-2024.pdf>.

**Population covered:** 604 organizations across 17 industries (Healthcare, Financial, Industrial, Technology, Energy, Pharmaceuticals, Professional services, Transportation, Entertainment, Communications, Media, Consumer, Hospitality, Research, Education, Retail, Public) and 16 countries/regions (US, Middle East, Benelux, Germany, Italy, Canada, UK, Japan, France, Latin America, South Korea, ASEAN, Australia, South Africa, India, Brazil). Breaches sized between 2,100 and 113,000 compromised records (mega-breach >1M records is handled in a separate simulation framework on 17 events). Sampling frame is "judgmental" — biased toward organizations with more mature privacy or info-security programs (page-44, "Sampling-frame bias"). Statistical inferences, margins of error, and confidence intervals cannot be applied (page-44, "Nonstatistical results").

**Methodology summary:** Activity-based costing. Respondents marked range-variable estimates on a number line rather than supplying point estimates (page-41). Four cost centers measured: detection-and-escalation, notification, post-breach response, lost business (page-42). Time metrics (MTTI, MTTC) are means in days reported directly by respondents for the specific breach incident under study. 2024 global average excludes Benelux (new region, outsized influence) from the headline 258-day figure (page-10).

**How this source informs calibration:** IBM CODB 2024 is the primary calibration anchor for FAIR-CAM **ELAPSED_TIME** sub-functions — specifically detection-monitoring τ (MTTI), event-termination τ (MTTC), and resilience/recovery τ (recovery-time distribution). It also provides per-attack-vector lifecycle breakdowns suitable for benchmark doc cells (stolen credentials, phishing, malicious insider, BEC, social engineering, zero-day, etc.) and a coarse industry-cost breakout (Figure 3) usable for industrial/healthcare overlays.

## Headlines

| Metric | Value | Source |
| --- | --- | --- |
| MTTI (mean time to identify breach), global avg, 2024 | 194 days | page-10, Figure 4 |
| MTTC (mean time to contain breach), global avg, 2024 | 64 days | page-10, Figure 4 |
| Total breach lifecycle (MTTI + MTTC), global avg, 2024 | 258 days (7-year low) | page-10, Figure 4 |
| MTTI/MTTC, 2023 (prior year, for trend) | 204 d / 73 d = 277 d | page-10, Figure 4 |
| MTTI/MTTC, 2022 | 207 d / 70 d = 277 d | page-10, Figure 4 |
| MTTI/MTTC, 2021 | 212 d / 75 d = 287 d | page-10, Figure 4 |
| MTTI/MTTC, 2020 | 207 d / 73 d = 280 d | page-10, Figure 4 |
| Industrial sector MTTI/MTTC, 2024 | 199 d / 73 d = 272 d (above median industry) | page-6 (highlight) |
| Global avg breach cost, 2024 | USD 4.88M (10% YoY increase) | page-8, Figure 1 |
| Sample size | 604 organizations; 3,556 interviewees | page-3 (executive summary); page-43 (FAQ) |
| Data collection window | March 2023 – February 2024 | page-3 |

**Re-verification of PR μ.1 citations:** PR μ.1's referenced figures (194d MTTI, 64d MTTC) are CORRECT and citable to **page-10, Figure 4** of the 2024 report. These figures represent the 2024 global average and are a 7-year low. They supersede the 2023 figures (204d / 73d) for any current calibration anchor. No correction needed to PR μ.1.

## MTTI/MTTC by initial attack vector (for benchmark doc cells, per attack-class τ)

Top 5 categories shown in Figure 8, page-14. MTTI + MTTC breakouts measured in days. Source: page-14, Figure 8.

| Attack vector | MTTI (d) | MTTC (d) | Total lifecycle (d) | % of breaches | Avg cost (USDM) |
| --- | --- | --- | --- | --- | --- |
| Stolen or compromised credentials | 229 | 63 | **292** | 16% | 4.81 |
| Malicious insider | 219 | 68 | **287** | 7% | 4.99 |
| Phishing | 195 | 66 | **261** | 15% | 4.88 |
| Social engineering | 197 | 60 | **257** | 6% | 4.77 |
| Unknown zero-day vulnerability | 183 | 69 | **252** | 11% | 4.46 |

**Citations:** Lifecycle days — page-14, Figure 8 (top-5 only). Attack-vector frequency and cost — page-13, Figure 7 (scatter plot of cost × frequency).

**Other attack vectors with cost+frequency (but NOT lifecycle days in this report):** Business email compromise (10%, USD 4.88M); Known unpatched vulnerability (6%, USD 4.33M); Accidental data loss / lost-or-stolen device (6%, USD 4.28M); Physical security compromise (6%, USD 4.19M); System error (6%, USD 4.07M); Cloud misconfiguration (12%, USD 3.98M). Source: page-13, Figure 7.

**Negative search outcome:** Searched §"Initial attack vectors and root causes" (pages 13–14, Figures 7 and 8). Figure 8 publishes per-vector lifecycle days ONLY for the top 5 categories. Lifecycle days for BEC, known unpatched vulnerability, accidental data loss, physical security compromise, system error, and cloud misconfiguration are NOT individually published in the 2024 report. Do not invent τ values for these vectors from this source.

## MTTI/MTTC by AI/automation maturity tier (for VMC/LEC tiered calibration)

Figure 16, page-18. Three usage levels: extensive use / limited use / no use of security AI and automation. Reference chart 14 (page-17, Figure 14) defines the three tiers based on respondent self-report.

| AI/automation maturity | MTTI (d) | MTTC (d) | Total (d) | Cost (USDM) |
| --- | --- | --- | --- | --- |
| Extensive use | 158 | 51 | **209** | 3.84 |
| Limited use | 182 | 59 | **241** | 4.64 |
| No use | 228 | 79 | **307** | 5.72 |

**Citations:** Lifecycle days — page-18, Figure 16. Cost — page-17, Figure 15.

Per-function tier breakdown (Figure 19, page-19), useful for control-function-specific overlays:

| Security function | None (MTTI/MTTC/total) | Extensive use (MTTI/MTTC/total) |
| --- | --- | --- |
| Prevention | 230 / 82 / 312 | 153 / 48 / **201** |
| Detection | 227 / 81 / 308 | 155 / 49 / **204** |
| Investigation | 224 / 77 / 301 | 158 / 53 / **211** |
| Response | 230 / 74 / 304 | 164 / 54 / **218** |

**Source:** page-19, Figure 19. Caveat: "From the organizations that reported extensive use of AI and automation; reference chart 14" — so the "extensive" column is conditional on extensive overall AI use, not pure per-function extensive use.

## Recovery time data (for LEC_RESP_RESILIENCE τ)

Recovery is defined (page-21) as: business operations back to normal; compliance obligations met (fines paid); customer/employee trust restored; controls and technologies put in place to avoid future breaches. **Recovery is a SEPARATE phase AFTER MTTC** — i.e. after containment ends, the recovery clock starts.

**Recovery rate (page-21, Figure 23):**
- 12% of breached organizations had fully recovered at time of survey
- 88% were still in the process of recovering

**Time-to-recover distribution (conditional on having fully recovered, page-22, Figure 24):**

| Recovery time bucket | Share of fully-recovered organizations |
| --- | --- |
| > 150 days | 35% |
| 126 – 150 days | 24% |
| 101 – 125 days | 19% |
| 76 – 100 days | 14% |
| 51 – 75 days | 5% |
| < 50 days | 3% |

**Derived statistics:**
- >100 days: 35% + 24% + 19% = **78%** (matches the IBM newsroom headline)
- >150 days: **35%** ("roughly one-third", per page-22 narrative)
- <50 days: 3% (page-22 narrative confirms "small share, 3%")
- Median bucket: 126–150 days (cumulative 35% + 24% = 59% above this bucket; 78% above the 100d threshold)

**Citation:** page-22, Figure 24 (chart titled "Average time to recover from a data breach", measured in days; reference chart 23).

**Caveat for τ calibration:** This distribution is **conditional on full recovery achieved** at survey time. The 88% still-recovering tail is right-censored — true population recovery τ is longer than the 12%-completer subsample suggests. Use these buckets as a LOWER BOUND on recovery τ for organizations that actually complete recovery, NOT as the unconditional mean. Calibration of `LEC_RESP_RESILIENCE` τ should reflect the censoring (e.g. via a parametric survival model rather than naive bucket midpoints).

## Recovery time / lifecycle by industry sector

**Industrial sector** (page-6 headline + page-10 Figure 3 cost context):
- MTTI 199 d / MTTC 73 d / Total 272 d (above median industry)
- Cost USD 5.56M in 2024, up USD 830,000 YoY (largest cost increase of any industry)
- Definition (page-40): "Chemical processing and engineering, and manufacturing companies"

**Healthcare sector** (page-10, Figure 3 + narrative):
- Avg cost USD 9.77M (top-cost industry for 14 consecutive years)
- 10.6% decrease YoY from USD 10.93M (2023)
- Lifecycle days for healthcare NOT broken out separately in 2024 report

**Financial sector** (page-10, Figure 3):
- Avg cost USD 6.08M (#2 industry)
- Lifecycle days NOT broken out

**Energy sector** (page-10, Figure 3):
- Avg cost USD 5.29M
- Lifecycle days NOT broken out

**Negative search outcome:** Searched pages 10 (Figure 3 industry costs), 6 (industrial highlight), and the full body of the report. The 2024 report publishes per-industry **cost** for all 17 industries (Figure 3) but per-industry **MTTI/MTTC lifecycle days** only for INDUSTRIAL (page-6). Healthcare, Financial, Energy, Public, Pharmaceuticals, and the remaining 12 sectors do NOT have a published MTTI/MTTC breakout in the 2024 report. Do not invent industry-tier τ values for these from this source.

## Time-to-resolve by extortion-attack type (for VMC_CORR_TREATMENT_SELECTION candidate)

Figure 31, page-26. All three extortion-attack types take 284–294 days to identify and contain.

| Extortion attack type | MTTI (d) | MTTC (d) | Total (d) | Avg cost (USDM) |
| --- | --- | --- | --- | --- |
| Data exfiltration | 226 | 68 | 294 | 5.21 |
| Destructive attack | 220 | 74 | 294 | 5.68 |
| Ransomware | 211 | 73 | 284 | 4.91 |

**Source:** Lifecycle — page-26, Figure 31. Cost — page-25, Figure 30.

**Law enforcement involvement effect on ransomware lifecycle (page-27, Figure 34):**
- Law enforcement involved: 213 d MTTI / 68 d MTTC / **281 d total**
- Law enforcement not involved: 220 d MTTI / 77 d MTTC / **297 d total**
- Delta: 16 days saved when LE involved (page-6 headline rounds to "297 → 281 days")

## Lifecycle by data-storage location (for shadow-data / multi-environment overlays)

Figure 39, page-30. Useful for treatment-selection τ when the asset/data location is known.

| Storage location | MTTI (d) | MTTC (d) | Total (d) | Cost (USDM) |
| --- | --- | --- | --- | --- |
| Across multiple environments | 213 | 70 | **283** | 5.03 |
| Public cloud | 201 | 67 | **268** | 5.17 |
| Private cloud | 176 | 71 | **247** | 4.33 |
| On premises | 170 | 54 | **224** (23.3% less than multi-env) | 4.18 |

**Source:** Lifecycle — page-30, Figure 39. Cost — page-29, Figure 38. Distribution of breaches across these 4 locations: 40% multi-env, 25% public cloud, 20% on-premises, 15% private cloud (page-29, Figure 37).

## Lifecycle by breach-discovery channel (for LEC_DET_MONITORING attribution)

Figure 13, page-16. Useful for distinguishing internal-detection vs attacker-disclosure τ.

| Discovery channel | MTTI (d) | MTTC (d) | Total (d) | Cost (USDM) |
| --- | --- | --- | --- | --- |
| Disclosure from attacker (2024) | 212 | 77 | 289 | 5.53 |
| Disclosure from attacker (2023) | 233 | 87 | 320 | — |
| Benign third party (2024) | 179 | 61 | 240 | 4.57 |
| Security teams and tools (2024) | 178 | 50 | 228 | 4.55 |

**Source:** Lifecycle — page-16, Figure 13. Cost — page-16, Figure 12. Channel distribution: 42% security teams, 34% benign 3rd party, 24% attacker (page-15, Figure 11).

## Known anomalies / errata

- **Benelux exclusion from headline lifecycle (page-10 footnote).** Global mean of MTTI/MTTC for 2024 EXCLUDES Benelux because as a new region it had outsized influence and would skew the trend comparison vs 2023. Cost figures (page-9, Figure 2A) DO include Benelux (USD 5.90M). When calibrating τ, the 194d/64d figures are NOT directly comparable to a Benelux-inclusive cost average — note the asymmetric treatment.
- **Year of report vs year of data window.** "Cost of a Data Breach 2024" covers breaches March 2023 – February 2024 (page-3). Some derivative sources confuse the report year with the breach year — always cite the report year (2024).
- **Sampling frame bias (page-44).** "We believe the current sampling frame was biased toward organizations with more mature privacy or information security programs." This means published MTTI/MTTC numbers likely UNDERESTIMATE the population-wide τ for less-mature organizations. Conservative τ calibration should treat IBM figures as lower bounds for the broader market.
- **Nonstatistical sample (page-44).** "Statistical inferences, margins of error and confidence intervals can't be applied to this data, given that our sampling methods weren't scientific." Calibration overlays should NOT cite IBM CODB figures with implied confidence intervals — they are point benchmarks, not statistical estimates.
- **Acsense secondary source year-confusion (web, accessed 2026-05-15).** The Acsense blog summary cites "204 days to identify a breach and an additional 73 days to contain it" attributed to the 2024 report — these are actually the 2023 figures per page-10, Figure 4 of the 2024 PDF. Always cite the primary PDF, not derivative blogs.
- **"Top 5" attack-vector lifecycle restriction (page-14, Figure 8).** Only the top 5 attack vectors by lifecycle days are published; ~6 other attack vectors (BEC, cloud misconfig, etc.) do not have per-vector MTTI/MTTC breakouts despite having cost+frequency data on the same page.

## When this source informs calibration

**Canonical τ candidates for FAIR-CAM ELAPSED_TIME sub-functions:**

- **`LEC_DET_MONITORING`** — primary anchor for detection τ. Use MTTI (194 d global; 178 d for security-team-detected breaches; 158 d with extensive AI use; 155 d with extensive AI in Detection). Source: page-10 Figure 4; page-16 Figure 13; page-18 Figure 16; page-19 Figure 19.
- **`LEC_RESP_EVENT_TERMINATION`** — primary anchor for containment τ. Use MTTC (64 d global; 50 d for security-team-detected; 51 d with extensive AI use; 49 d with extensive AI in Detection). Source: page-10 Figure 4; page-16 Figure 13; page-18 Figure 16; page-19 Figure 19.
- **`LEC_RESP_RESILIENCE`** — recovery τ with caveat. Use the 6-bucket distribution from page-22 Figure 24 (conditional on full recovery), with a censoring-aware survival model OR a lower-bound estimate. **Do NOT** use the bucket means as unconditional τ — only 12% of breached orgs had fully recovered at survey time.

**Benchmark doc cells (per-attack-class τ for the v3 benchmark suite):**

- Stolen credentials: 292 d (page-14 Figure 8)
- Malicious insider: 287 d (page-14 Figure 8)
- Phishing: 261 d (page-14 Figure 8)
- Social engineering: 257 d (page-14 Figure 8)
- Zero-day vulnerability: 252 d (page-14 Figure 8)
- Ransomware: 284 d (page-26 Figure 31)
- Data exfiltration / extortion: 294 d (page-26 Figure 31)
- Destructive attack: 294 d (page-26 Figure 31)

**Overlays (multiplicative deltas, NOT canonical):**

- **AI/automation extensive-use overlay** for LEC_DET_MONITORING and LEC_RESP_EVENT_TERMINATION: extensive-use τ ÷ no-use τ = 158/228 = 0.693 for detect; 51/79 = 0.646 for contain (page-18 Figure 16). Per-function variants on page-19 Figure 19.
- **Industrial sector overlay**: 199/194 = 1.026 for detect; 73/64 = 1.141 for contain (page-6 + page-10 Figure 4). Industrial = Chemical processing + Manufacturing per page-40 definitions.
- **Multi-environment data-storage overlay**: 213/194 = 1.098 for detect; 70/64 = 1.094 for contain (page-30 Figure 39 vs page-10 Figure 4 global).
- **Attacker-disclosure (vs security-team-detection) overlay**: 212/178 = 1.191 for detect; 77/50 = 1.540 for contain (page-16 Figure 13).
- **Law enforcement involvement (ransomware)**: 213/220 = 0.968 for detect; 68/77 = 0.883 for contain (page-27 Figure 34).

## Reverse-traceable list

(Update this section whenever an `overlay`, `override`, or scenario `sources` field references `ibm_codb_2024.md`.)

- _(none yet — populate as v3 calibration entries cite this file)_
