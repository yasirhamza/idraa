---
title: "IBM Cost of a Data Breach Report 2025"
year: 2025
url: https://www.ibm.com/reports/data-breach
accessed: 2026-07-02
permalink: https://www.bakerdonelson.com/webfiles/Publications/20250822_Cost-of-a-Data-Breach-Report-2025.pdf
methodology_summary: "600 organizations across 17 industries and 16 countries/regions, breached between March 2024 and February 2025; 3,470 individual interviews; breaches sized 2,960–113,620 compromised records; activity-based costing across 4 cost centers (detection & escalation, notification, post-breach response, lost business); research conducted by Ponemon Institute, published by IBM; 20th year of the research (6,485 organizations studied since 2005)."
---

# IBM Cost of a Data Breach Report 2025 — Calibration Source

**Source:** IBM / Ponemon Institute, *Cost of a Data Breach Report 2025* ("The AI Oversight Gap"), published July 2025. Landing page: <https://www.ibm.com/reports/data-breach>. Full PDF (mirror, accessed 2026-07-02): <https://www.bakerdonelson.com/webfiles/Publications/20250822_Cost-of-a-Data-Breach-Report-2025.pdf>. Page numbers below are the report's printed footer pages, verified against the PDF.

**Population covered:** 600 organizations, 17 industries, 16 countries/regions; breaches occurred March 2024 – February 2025; 3,470 interviews (page-57, "How do you collect the data?"). Breach sizes 2,960–113,620 compromised records (page-56). Sampling frame is judgmental and "biased toward organizations with more mature privacy or information security programs" (page-58, "Sampling-frame bias"). "Statistical inferences, margins of error and confidence intervals can't be applied to this data, given that our sampling methods weren't scientific" (page-58, "Nonstatistical results").

**Primary purpose in RiskFlow:** external empirical evidence for **meta-control value attribution** (#434 / #439 Slice-2 κ coupling) — see "How this source informs calibration" below. Secondary: refresh of 2024 headline anchors (`ibm_codb_2024.md` remains the pinned source for the #131 τ calibration cells; nothing in this doc re-pins those).

## Headlines (verified pages)

| Metric | Value | Source |
| --- | --- | --- |
| Global avg breach cost, 2025 | USD 4.44M (−9% YoY, "a return to 2023 cost levels") | page-6 "Key findings"; pages-8–9 |
| US avg breach cost, 2025 | USD 10.22M (all-time high for any region) | page-6; page-9 |
| Cost savings from extensive security AI/automation | USD 1.9M lower avg cost; breach times shortened by 80 days | page-6 |
| Shadow-AI added breach cost | +USD 670K (high shadow-AI orgs: 4.74 vs 4.07 low/none) | page-6; page-44 Figure 40 |
| Malicious insider attacks avg cost | USD 4.92M (costliest initial vector) | page-6 |
| Orgs experiencing operational disruption from a breach | 86% | page-41 |
| Breaches involving attacker use of AI | 16% (37% AI-phishing, 35% deepfake) | page-6; page-38 Figure 33 |

## Figure 39 (pages 42–43) — Factors that increase or decrease breach costs

**Caption (verbatim):** "Figure 39. Cost difference from USD 4.88M breach average; measured in USD."

**⚠ Baseline anomaly (record before citing):** the caption's USD 4.88M is the **2024** global average; the 2025 report's own global average is USD 4.44M (page-6). The body text says the analysis "examined 30 contributing factors and the impact of each in isolation against the global average" (page-42) without naming the number. Whether the 4.88M caption is a deliberate prior-year baseline or an uncorrected carry-over from the 2024 report's Figure 25 (whose caption is word-identical) is not resolvable from the report. **Consequence: treat the USD deltas as relative magnitudes for comparing factors within this figure; do not anchor absolute dollar math on the 4.88M baseline.**

Comparison note: the 2024 report's equivalent chart is **Figure 25, page-23** (also captioned "Cost difference from USD 4.88M breach average") with 20 mitigators / 8 amplifiers and different values (top: Employee training −258,629; DevSecOps −240,499). The 2025 figure re-ranks DevSecOps to #1 and adds Quantum security tools, AI governance technology/policies, Machine learning SecOps, Adoption of AI tools, and Shadow AI.

### Cost-mitigating factors (21)

| Factor | Δ vs avg (USD) | FAIR-CAM class (v3 judgment — see note) |
| --- | --- | --- |
| DevSecOps approach | −227,192 | **Meta — VMC** (variance prevention in the SDLC) |
| AI-driven and ML-driven insights | −223,503 | Mixed (LEC detection + decision support) |
| Security analytics or SIEM | −212,061 | Direct — LEC detection |
| Threat intelligence | −211,906 | Mixed (DSC-adjacent: informs decisions; also feeds detection) |
| Encryption | −208,087 | Direct — LEC prevention/resistance |
| SOAR tools | −201,201 | Direct — LEC response |
| Quantum security tools | −196,951 | Direct — LEC prevention |
| Proactive threat hunting | −193,242 | Mixed (LEC detection / VMC identification) |
| Employee training | −192,266 | **Meta — DSC** (SAT class in v3 library) |
| AI governance technology | −191,893 | Mixed (VMC monitoring / DSC) |
| IAM | −189,838 | Direct — LEC prevention |
| Machine learning SecOps | −186,559 | Mixed |
| Offensive security testing | −184,109 | **Meta — VMC identification** (surfaces control deficiencies; same class as CSPM/SCA) |
| Endpoint detection and response tools | −168,361 | Direct — LEC detection/response |
| Gen AI security tools | −162,574 | Mixed |
| Attack surface management tools | −160,547 | Mixed (VMC-identification-adjacent) |
| Data security and protection software | −157,456 | Direct — LEC prevention |
| AI governance policies | −147,097 | **Meta — DSC** |
| Managed security service provider (MSSP) | −128,087 | Mixed (outsourced LEC operation + VMC monitoring) |
| CISO appointed | −113,840 | **Meta — DSC** (governance) |
| Board-level oversight | −110,772 | **Meta — DSC** (governance) |

### Cost-amplifying factors (9)

| Factor | Δ vs avg (USD) |
| --- | --- |
| Remote workforce | +131,212 |
| Security skills shortage | +173,400 |
| Noncompliance with regulations | +173,692 |
| Migration to the cloud | +174,538 |
| IoT and OT environment impacted | +175,010 |
| Adoption of AI tools | +193,511 |
| Shadow AI | +200,321 |
| Security system complexity | +207,914 |
| Supply chain breach | +227,244 |

**Classification note:** the FAIR-CAM class column is a **v3 view-model judgment**, not IBM's taxonomy and not a FAIR-CAM Standard mapping. IBM factor names are program-level ("DevSecOps approach"), not control-level; several straddle classes. The quantitative summary below therefore uses only the *unambiguous* subsets and reports the mixed factors separately. Sensitivity: reasonable reclassifications (Employee training → direct-side human control; Proactive threat hunting and/or AI governance technology / ASM → meta; Quantum security tools dropped from direct) move the central ratio ≈ 0.79–0.89; the 0.6–1.2 band is insensitive to any single classification call.

## Figures 40–42 (pages 44–45) — high vs low levels of key factors (USD millions)

| Factor | High level | Low level / none | Spread | Figure/page |
| --- | --- | --- | --- | --- |
| Supply chain breach (amplifier) | 4.81 | 4.01 | +20.0% | Fig 40, p.44 |
| Security system complexity (amplifier) | 4.78 | 4.04 | +18.3% | Fig 40, p.44 |
| Shadow AI (amplifier) | 4.74 | 4.07 | +16.5% | Fig 40, p.44 |
| Security analytics and SIEM (mitigator) | 3.91 | 4.83 | −19.0% | Fig 41, p.44 |
| **DevSecOps approach (mitigator)** | **3.89** | **5.02** | **−22.5%** | Fig 41, p.44 |
| AI-driven and ML-driven insights (mitigator) | 3.85 | 4.90 | −21.4% | Fig 41, p.44 |
| Security skills shortage 2025 (amplifier) | 5.22 | 3.65 | +43.0% | Fig 42, p.45 |

Note: of the three spotlighted mitigators, the **largest** high-vs-low spread belongs to DevSecOps — a variance-management-class factor — exceeding SIEM (a direct detection control).

## How this source informs calibration (#434 / #439 — meta-control κ coupling)

**What it supports:**

1. **The #434 central hypothesis** (meta-controls that govern portfolio reliability/decision quality carry material value): the #1 cost-mitigating factor of 30 is DevSecOps (VMC-class), and the unambiguous meta subset sits in the same band as the unambiguous direct subset. Under RiskFlow's pre-Slice-2 attribution, every bolded meta factor above would score ~$0.
2. **A coherence band for the pinned κ starting value (relative prior, NOT identification).** Unambiguous-meta subset {DevSecOps, Employee training, Offensive security testing, AI governance policies, CISO appointed, Board-level oversight}: mean −162,546 (range 110,772–227,192). Unambiguous-direct subset {SIEM, Encryption, SOAR, Quantum, IAM, EDR, Data security software}: mean −190,565. **Meta:direct ratio ≈ 0.85 at the means; individual meta factors span ≈ 0.6×–1.2× of the direct mean.** Derivation: 975,276/6 = 162,546; 1,333,955/7 = 190,565; 162,546/190,565 = 0.853. Slice-2 usage: the pinned κ must place a representative co-present VMC/DSC control's ensemble-mean Shapley share within ≈ 0.6–1.2× of a representative co-present direct LEC control's share — ~0.85× is the illustrative central of the band, **not a point target to tune toward** — then report ranges via the #419 weight-perturbation ensemble as usual.
3. **Negative-side corroboration:** the amplifier side is dominated by variance/governance *absences* — security skills shortage (+173,400; Fig 42: 5.22 vs 3.65), security system complexity (+207,914), shadow AI (+200,321), noncompliance (+173,692) — i.e., degraded control reliability and decision quality raise loss, the same coupling direction Slice 2 models.

**What it does NOT support (caveats — read before citing):**

- **Associational, not causal.** Factor presence is self-reported; factors co-occur in mature programs; IBM examines "the impact of each in isolation" (page-42) so deltas are neither additive nor confounder-adjusted. "Statistical inferences, margins of error and confidence intervals can't be applied" (page-58).
- **Loss-magnitude-side only.** These are cost-given-breach deltas over a breached-org population. They carry **no frequency information**, while the κ coupling moves reliability → effectiveness → LEF/vulnerability (and response). The ratio is therefore usable only as a cross-domain *coherence* check on relative attributed shares (control-value in RiskFlow is an ALE delta spanning both sides), never as a κ derivation.
- **κ stays `implementation-calibration` and single-org non-identifiable** (#419 decision; charter Q6). This source narrows the plausible band; it does not identify κ, and per-org calibration toward a "true" κ remains off the table.
- **Delta scales with non-adoption prevalence.** The Figure-39 estimator (factor-present mean minus global mean) satisfies Δ_f = (1−p_f)·(E[cost|f]−E[cost|¬f]) where p_f is the factor's adoption prevalence — a near-universal factor is mechanically compressed toward 0 and a rare one inflated, and IBM publishes no per-factor adoption rates. Cross-factor ratios therefore conflate effect size with prevalence. (Governance factors are plausibly high-prevalence, which would make the ~0.85 central conservative for meta — but the confound is unquantifiable from this source.) The Fig 41 high-vs-low spreads (DevSecOps −22.5% vs SIEM −19.0%) are the prevalence-free comparator and independently support the same direction.
- **Baseline anomaly** (above): use deltas as relative magnitudes only.
- **Population skew:** judgmental sample biased toward mature programs (page-58); mid-size breach band only (2,960–113,620 records; mega-breaches excluded, page-56).

**Consumption pointers:** charter §10 in the internal design doc 2026-06-30-faircam-meta-control-attribution; Slice-2 open question in the internal design doc 2026-07-01-indirect-control-attribution-design. The classification column and ratio band above must be re-reviewed by the methodology persona at the Slice-2 plan-gate before κ is pinned.
