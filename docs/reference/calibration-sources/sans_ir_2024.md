---
title: "SANS 2024 Detection and Response Survey (+ SANS 2023 Incident Response Survey cross-check)"
year: 2024
url: "https://www.sans.org/white-papers/sans-2024-detection-response-survey"
accessed: 2026-05-15
permalink: "N/A — SANS gates PDF behind registration; partner-mirror PDFs (Rapid7, CardinalOps, Infoblox) are not commit-stable. Primary access path is the SANS landing page; mirror URLs captured per-row."
methodology_summary: "SANS 2024 D&R: ~400 respondents (search-result derived; not verified on PDF cover), global cybersecurity practitioners, online survey administered by SANS Institute; authored by Josh Lemon (SANS Principal Instructor); published 19 Nov 2024. SANS 2023 IR Survey: published 12 Sep 2023, authors Megan Roddie-Fonseca & Terrence Williams; respondent count not surfaced in partner summaries reviewed."
---

# SANS 2024 Detection and Response Survey — Reference Data

**Source:** SANS Institute, "SANS 2024 Detection and Response Survey:
Transforming Cybersecurity Operations: AI, Automation, and Integration
in Detection and Response," authored by Josh Lemon, published
19 November 2024.
[SANS landing page](https://www.sans.org/white-papers/sans-2024-detection-response-survey)
(full PDF gated behind SANS login or partner-form registration).

**Companion source — SANS 2023 IR Survey:** SANS Institute, "2023 Survey:
Event & Incident Response," authored by Megan Roddie-Fonseca &
Terrence Williams, published 12 September 2023.
[SANS landing page](https://www.sans.org/white-papers/2023-survey-event-incident-response).

**Population covered:** Self-selected cybersecurity practitioners
(SOC analysts, IR engineers, detection engineers, security leaders)
who responded to SANS's annual online survey. Skews toward
SANS-trained, English-speaking, larger-org practitioners. Industry
mix and exact geographic distribution are documented inside the
gated PDF only; not surfaced in any partner-mirror summary reviewed.

**Methodology summary:** Online practitioner survey, self-reported
metrics. Respondents answer multiple-choice and time-bucket questions
about their org's detection/response tooling, automation maturity,
and (in the 2023 IR Survey) detection-to-containment-to-remediation
timing. SANS does NOT publish raw per-respondent timing distributions
the way IBM CODB or Verizon DBIR do — figures are bucketed
percentages and headline aggregates.

**Why this is reference-only (not calibration):** Self-reported
practitioner survey, no incident-by-incident loss data, sample-
selection bias toward SANS-trained orgs. Population mismatch with
FAIR's loss-event distribution. Use as supplementary cross-check
for τ calibration of `VMC_CORR_TREATMENT_SELECTION` and
`LEC_RESP_RESILIENCE` and for treatment-selection-cadence
qualitative anchors.

## Caveat — title under task brief did not exist

The task brief targeted "SANS Incident Response Survey 2024." That
title does NOT exist. SANS's 2024 release in this space is the
"SANS 2024 Detection and Response Survey" (D&R Survey, the inaugural
edition consolidating prior IR-focused surveys). The most recent
SANS publication carrying the literal "Incident Response Survey"
label is the 2023 edition. This file documents BOTH so that τ
calibrators have the most recent IR-specific timing data (2023)
AND the most recent SANS framing of detection-response practice
(2024).

## Headlines

| Metric | Value | Source |
| --- | --- | --- |
| 2024 D&R Survey publish date | 19 November 2024 | [SANS landing page, accessed 2026-05-15](https://www.sans.org/white-papers/sans-2024-detection-response-survey) |
| 2024 D&R Survey author | Josh Lemon (SANS Principal Instructor) | [SANS landing page, accessed 2026-05-15](https://www.sans.org/white-papers/sans-2024-detection-response-survey) |
| 2024 D&R Survey approx. sample size | ~400 respondents | Partner / search-result aggregate, accessed 2026-05-15. Not verified on gated PDF cover. |
| 2023 IR Survey publish date | 12 September 2023 | [SANS landing page, accessed 2026-05-15](https://www.sans.org/white-papers/2023-survey-event-incident-response) |
| 2023 IR Survey authors | Megan Roddie-Fonseca; Terrence Williams | [SANS landing page, accessed 2026-05-15](https://www.sans.org/white-papers/2023-survey-event-incident-response) |
| Orgs tracking MTTR (2024) | 67% | Search-result-derived from 2024 D&R Survey body, accessed 2026-05-15. Page-level citation unavailable without PDF access. |
| Orgs tracking MTTD (2024) | 52% | Search-result-derived from 2024 D&R Survey body, accessed 2026-05-15. Page-level citation unavailable without PDF access. |
| Orgs with integrated automated response (2024) | 64% | [CardinalOps landing page, accessed 2026-05-15](https://cardinalops.com/white-papers/sans-2024-detection-and-response-survey-transforming-cybersecurity-operations-ai-automation-and-integration-in-detection-and-response/) |
| Orgs with fully-automated response (2024) | 16% | [CardinalOps landing page, accessed 2026-05-15](https://cardinalops.com/white-papers/sans-2024-detection-and-response-survey-transforming-cybersecurity-operations-ai-automation-and-integration-in-detection-and-response/) |
| Orgs struggling to craft quality detection rules (2024) | 73% | [CardinalOps landing page, accessed 2026-05-15](https://cardinalops.com/white-papers/sans-2024-detection-and-response-survey-transforming-cybersecurity-operations-ai-automation-and-integration-in-detection-and-response/) |
| SOC teams overwhelmed by false positives (2024) | 64% | [CardinalOps landing page, accessed 2026-05-15](https://cardinalops.com/white-papers/sans-2024-detection-and-response-survey-transforming-cybersecurity-operations-ai-automation-and-integration-in-detection-and-response/) |
| Top obstacle: budget constraints (2024) | 47% | [Resilience Forward summary, accessed 2026-05-15](https://resilienceforward.com/results-from-the-2024-detection-response-survey-highlight-cybersecurity-challenges-and-adaptations/) |
| Limited cloud-security expertise (2024) | 56% | [Resilience Forward summary, accessed 2026-05-15](https://resilienceforward.com/results-from-the-2024-detection-response-survey-highlight-cybersecurity-challenges-and-adaptations/) |
| Multi-cloud management complexity (2024) | 51% | [Resilience Forward summary, accessed 2026-05-15](https://resilienceforward.com/results-from-the-2024-detection-response-survey-highlight-cybersecurity-challenges-and-adaptations/) |

## IR timing by org class / maturity tier

The 2023 SANS IR Survey is the better source for IR-timing data
than the 2024 D&R Survey (the 2024 D&R Survey shifted emphasis to
detection-rule and automation maturity).

| Metric (2023 IR Survey) | Value | Source / accessed |
| --- | --- | --- |
| Orgs detecting incidents within 60 minutes | top 25% | Search-result-derived from SANS 2023 IR Survey body, [via deepstrike.io MTTR review citing SANS 2023 IR, accessed 2026-05-15](https://deepstrike.io/blog/what-is-mttr-mean-time-to-respond). Page-level citation unavailable without PDF access. |
| Orgs detecting incidents within five hours | "more than half" | Search-result-derived from SANS 2023 IR Survey body, accessed 2026-05-15. Page-level citation unavailable without PDF access. |
| Orgs remediating incidents within 24 hours after containment | 54% | Search-result-derived from SANS 2023 IR Survey body, accessed 2026-05-15. Page-level citation unavailable without PDF access. |
| Change in containment-to-remediation time vs. 2019 baseline | +11% (slower) | Search-result-derived from SANS 2023 IR Survey body, accessed 2026-05-15. Page-level citation unavailable without PDF access. |
| Manual rogue-file removal effort vs. prior wave | −15% (improved) | Search-result-derived from SANS 2023 IR Survey body, accessed 2026-05-15. Page-level citation unavailable without PDF access. |

Granular breakdowns by industry, organization headcount, revenue
band, or maturity tier are NOT surfaced in any of the partner
mirrors or search-result summaries reviewed for this file. The
gated SANS PDF likely contains them in chart form; obtaining them
requires either SANS account login or registration on a partner-
gated form (Rapid7, CardinalOps, Infoblox, Devo). Recommend a
follow-up extraction PR once a paginated reference copy is
acquired.

## Treatment-selection cadence (for VMC_CORR_TREATMENT_SELECTION)

`VMC_CORR_TREATMENT_SELECTION` represents the time-constant between
threat/vuln intel becoming actionable and the org selecting a
treatment (patch / mitigate / accept / transfer). Current
fair_cam canonical τ = 14 days, flagged as "v3 default, no
canonical source" (`fair_cam/calibration/elapsed_time_taus.py:57`).

The SANS 2024 D&R Survey does NOT publish a direct
"playbook-review cadence" or "detection-rule refresh interval"
number in any partner mirror reviewed. The closest qualitative
anchors:

| Qualitative anchor | Value | Source / accessed |
| --- | --- | --- |
| Orgs struggling to craft quality detection rules | 73% | [CardinalOps landing page, accessed 2026-05-15](https://cardinalops.com/white-papers/sans-2024-detection-and-response-survey-transforming-cybersecurity-operations-ai-automation-and-integration-in-detection-and-response/) — implies detection-rule treatment-selection is friction-heavy across the population, suggesting τ should NOT be aggressively shortened in calibration. |
| Manual updates to policies/rules from IoC findings (2023) | 40% | Search-result-derived from SANS 2023 IR Survey body, accessed 2026-05-15. Page-level citation unavailable without PDF access. — suggests substantial human-in-loop delay for treatment-selection, consistent with multi-day τ. |
| Orgs with fully-automated response | 16% | [CardinalOps landing page, accessed 2026-05-15](https://cardinalops.com/white-papers/sans-2024-detection-and-response-survey-transforming-cybersecurity-operations-ai-automation-and-integration-in-detection-and-response/) — confirms majority of orgs are NOT in a near-instantaneous-treatment-selection regime; a 14-day default τ is not contradicted. |

**Calibration implication:** Neither the 2023 IR nor 2024 D&R
SANS Survey publishes a clean median-days-to-select-treatment
number suitable to override the current heuristic τ = 14 days.
The qualitative data supports the heuristic's order of magnitude
(multi-day, not multi-hour and not multi-week) but does not
provide a defensible derivation. Recommend keeping the
`VMC_CORR_TREATMENT_SELECTION = 14.0` value flagged as
"v3 default, no canonical source" and treating SANS data as
soft cross-validation only.

## Resilience recovery times (for LEC_RESP_RESILIENCE)

`LEC_RESP_RESILIENCE` represents the time-constant for an org to
restore operations to a healthy steady-state after containment
(business continuity recovery, not breach containment). Current
fair_cam canonical τ = 33 days, flagged as "v3 default, no
canonical source" (`fair_cam/calibration/elapsed_time_taus.py:47`,
labeled "3-week recovery target common in BCM literature").

| Resilience-recovery anchor | Value | Source / accessed |
| --- | --- | --- |
| Orgs remediating within 24 hours after containment (2023) | 54% | Search-result-derived from SANS 2023 IR Survey body, accessed 2026-05-15. Note: SANS "remediation" ≠ FAIR "resilience" — SANS remediation here is the immediate post-containment cleanup window, NOT full business-resilience steady-state restoration. |
| Containment-to-remediation time slowdown 2019 → 2023 | +11% | Search-result-derived from SANS 2023 IR Survey body, accessed 2026-05-15. |

**Cross-check vs. IBM CODB 2024 (T0a parallel work):** IBM CODB
2024 reports:

- Mean Time to Identify (MTTI): 194 days
- Mean Time to Contain (MTTC): 64 days
- Total breach lifecycle: 258 days
- Multi-environment breach lifecycle: 283 days
- AI/automation accelerated detection+containment: 98 days faster

IBM CODB does NOT publish a "time-to-full-resilience-recovery"
metric distinct from MTTC. The 258-day lifecycle ENDS at
containment, not at full operational steady-state restoration.
Therefore IBM CODB 2024 does NOT provide a τ value for
`LEC_RESP_RESILIENCE` directly. The closest signal is the gap
between MTTC and operational-resilience milestones, which neither
SANS nor IBM publishes in a directly-citable form.

**Calibration implication:** Neither SANS 2024 D&R nor SANS 2023
IR nor IBM CODB 2024 publishes a median time-to-resilience-
recovery suitable to override the 33-day heuristic. The SANS
"54% remediated within 24 hours of containment" figure pertains
to immediate post-containment cleanup, not full business
resilience restoration, so it is NOT a substitute. Recommend
keeping `LEC_RESP_RESILIENCE = 33.0` flagged as "v3 default,
no canonical source" and capturing this negative-extraction
outcome in the calibration methodology doc.

## Known anomalies / errata

- **Task-brief title mismatch.** The task brief named "SANS
  Incident Response Survey 2024." This title does NOT exist.
  The 2024 SANS publication in this space is the
  "SANS 2024 Detection and Response Survey." The 2023 IR Survey
  is the most recent SANS publication carrying the literal IR
  Survey label. Both are captured in this file.
- **PDF access gating.** The full SANS PDFs are accessible only
  via SANS account login or partner-form registration
  (Rapid7, CardinalOps, Infoblox, Devo, Swimlane, etc.).
  Page-numbered citations are NOT available for most metrics
  in this file because the gated PDFs could not be paginated-
  parsed in this research pass. Partner-landing-page citations
  are used instead with explicit accessed dates per Sec3-I2
  tamper-evidence policy.
- **WebFetch PDF rendering issue.** Multiple attempts to
  WebFetch the partner-hosted PDFs returned binary / design-
  asset noise (apparently HubSpot is serving a marketing
  prelude design asset under the `Survey_2024-Detection-Response_Prelude (1).pdf`
  filename rather than the actual survey PDF). This is a tooling
  limitation of this research pass, not a SANS errata.
- **Sample size unverified.** The "~400 respondents" headline
  is search-result-aggregated; the gated PDF cover should be
  consulted to verify exact respondent count and demographic
  cuts before any τ derivation cites this number.
- **Partner-mirror drift risk.** The CardinalOps and Resilience-
  Forward landing pages summarize the same source; if SANS later
  revises the 2024 D&R Survey, partner summaries may not be
  updated in sync. All partner-mirror citations in this file
  are accessed 2026-05-15.

## When this source informs an overlay or calibration override

- Soft cross-validation for canonical τ:
  - `LEC_RESP_RESILIENCE` (currently 33.0, "v3 default, no canonical
    source") — SANS 2023 IR + IBM CODB 2024 cross-check produced a
    negative extraction; no canonical override candidate identified.
  - `VMC_CORR_TREATMENT_SELECTION` (currently 14.0, "v3 default, no
    canonical source") — SANS 2024 D&R + SANS 2023 IR cross-check
    produced a negative extraction; no canonical override
    candidate identified. Qualitative data is consistent with the
    heuristic's order of magnitude.
- Benchmark doc cells: none yet — pending PR-α canonical
  benchmark-doc grid landing.
- Overlays: none directly. The SANS automation-maturity findings
  (16% fully-automated, 64% partially-automated) MAY inform a
  future `automation_mature` overlay, but only after a primary
  source publishes per-control automation-impact multipliers.

(Update this section whenever a new overlay or override
`sources` field adds this file. Bidirectional citation rule
per spec §6.6.2.)
