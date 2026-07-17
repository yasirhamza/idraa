# IRIS 2025 vs IC3 2025 — Dataset Reconciliation

RiskFlow draws on two 2025 reference datasets — `fair_cam.data.iris_2025`
(the IRIS module shipped inside `fair_cam`) and the IC3 2025 reference
sheet at `docs/reference/calibration-sources/ic3_2025.md`. Both publish
2025 cyber-loss numbers, but they measure fundamentally different
populations with fundamentally different methods. This document explains
when to use which and how to read the inevitable headline disagreements.

## Two datasets, two questions

**IRIS 2025** (Cyentia Institute, June 2025) measures *realized loss events
at organizations* — ~150,000 public-record incidents from Zywave (formerly
Advisen) Cyber Loss Data, surfaced via insurance claims, SEC filings,
regulatory disclosures, and litigation, enriched with classification
models and ATT&CK mapping.

**IC3 2025** (FBI Internet Crime Complaint Center, published 2026) measures
*self-reported cybercrime complaints* submitted to www.ic3.gov. The 2025
corpus is 1,008,597 complaints with $20.877 billion in reported losses
across 200+ countries.

Same year, very different surfaces. IRIS shows what happens to
organizations large enough that an event leaves a regulatory or insurance
footprint. IC3 shows what people thought worth reporting to the FBI.

## Population differences

| Dimension | IRIS 2025 | IC3 2025 |
| --- | --- | --- |
| Reporters | Mid-to-large organizations | Consumers + small biz + some large orgs |
| Capture mechanism | Insurance claim / SEC filing / regulatory | Self-submitted complaint |
| Geographic | Global (with US-favored visibility) | US-centric (200+ countries; US dominates) |
| Size filter | Implicit — only orgs above the disclosure threshold | None — household victims through Fortune 500 mixed |
| Year coverage | 2008–2024 (15-year longitudinal) | 2001–2025 (25-year, but consistent classification only recent years) |
| Inflation adjustment | Yes — 2024 dollars | No — nominal dollars |

The "no size filter" line is the critical one. IC3's $20.9B 2025 loss total
mixes a $50K romance-scam victim with a $50M BEC wire to a Fortune 500 —
they each count as one complaint. IRIS implicitly filters out the
romance-scam victim because individual consumer fraud rarely surfaces in
Zywave/Advisen.

## Crime taxonomy

IRIS classifies by **incident pattern** (system intrusion, ransomware,
accidental disclosure, insider misuse) plus **MITRE ATT&CK** for the
threat-actor angle (Figure 16: T1078 Valid Accounts = 46% of intrusions).

IC3 classifies by **crime type** — 26 categories that mix attack types
(BEC, ransomware) with fraud schemes (romance, lottery, investment,
tech support) and adds three "descriptors" that overlap any crime type
(Cryptocurrency, AI Related, Crimes Against Children).

These taxonomies do not align. IC3 "Phishing/Spoofing" (191,561 complaints,
$215M) is not a 1:1 match for ATT&CK T1566 in IRIS — the IC3 count includes
consumer-victim reports that would never appear in Zywave/Advisen.

## Headline-number cross-check

The two datasets disagree on every shared headline. The disagreements are
not errors; they're a function of what each is measuring.

| Headline | IRIS 2025 | IC3 2025 | Why they differ |
| --- | --- | --- | --- |
| Loss median (overall) | $603K (Figure 9, 2024 USD) | $20,699 average per complaint | IRIS measures org-level loss events; IC3 averages over million-complaint denominator that includes individual victims |
| Ransomware loss p50 (2024) | $3.2M (Figure 15) | $32.3M total / 3,611 complaints ≈ $8,950 per complaint | IRIS's $3.2M is per-incident at orgs; IC3 average is heavily diluted by reports without out-of-pocket loss |
| BEC | not separately broken out (subset of system intrusion) | $3.0B in losses across 24,768 complaints | IC3 captures BEC-specific dollar wires; IRIS treats BEC as an event-pattern overlay, not a distinct headline |
| Investment fraud | not separately broken out | $8.6B in losses across 72,984 complaints | IC3 owns this category; IRIS's incident corpus rarely captures consumer investment scams |
| Identity theft | not a category | $186M in losses across 31,675 complaints | IRIS focuses on organizations; identity theft is a consumer-side outcome of a breach |
| Healthcare frequency | 9.1% annual incident probability (Figure 8) | 460 ransomware + 182 data-breach complaints in critical-infra healthcare (page 16) | IRIS gives a per-firm probability; IC3 gives a count of healthcare reporters |

The pattern: IRIS is denser per data point but covers fewer events; IC3 is
sparser per data point but covers far more events.

## When to use IRIS

Use `fair_cam.data.iris_2025` when:

- Quantifying enterprise-class loss-event distributions (e.g., the p50/p95
  loss for a 1-billion-revenue firm experiencing system intrusion).
- Calibrating Monte Carlo simulations of realized loss events at the
  organization level — IRIS's revenue-tier × industry × event-type grid is
  built for this.
- Conducting ATT&CK-driven scenario analysis — IRIS publishes Initial
  Access technique prevalence (T1078, T1190, T1566) directly.
- Reporting to insurance, regulators, or boards who think in terms of
  insurable / disclosable incidents.
- Modeling multi-year incident frequency for a specific industry +
  revenue-tier cell.

## When to use IC3

Use `docs/reference/calibration-sources/ic3_2025.md` when:

- Modeling individual-employee victimization risk that an enterprise
  inherits via its workforce (sextortion, romance scams that target
  employees, recovery scams targeting prior victims).
- Fraud-driven HR scenarios — employment-scam complaints jumped from 15,443
  (2023) to 24,688 (2025), and HR is the consumer surface.
- US-state-specific deployment risk — IC3 publishes per-100K complaint and
  loss rates for all 50 states + DC + territories. California is the
  highest-volume state (116,414 complaints, $3.7B losses) but DC has the
  highest per-capita loss ($14M per 100K).
- Age-group demographic targeting risk — every IC3 table breaks down by
  five age cohorts, and the 60+ cohort drives 37% of all losses.
- Cryptocurrency-specific scenarios — IC3 dedicates a full appendix to
  crypto nexus ($11.4B in 2025), crypto investment fraud ($7.2B), crypto
  ATM/kiosks ($389M), and recovery scams ($1.4B).
- AI-related risk — IC3 is the only public dataset with crime-by-crime AI
  references (22,364 complaints, $893M losses in 2025).

## Cross-validation: when both sources speak

- **Ransomware frequency:** Use IRIS for the per-firm probability term in
  FAIR; use IC3's critical-infra ranking (Healthcare = 460 complaints, top
  sector) as a sanity check on relative sector ordering.
- **Ransomware loss magnitude:** IRIS p50 of $3.2M (org-level, 2024 USD)
  is the right number for FAIR scenarios. IC3's $32.3M / 3,611 ≈ $8,950
  average is noise-diluted by zero-loss complaints; do not use it for
  magnitude calibration.
- **BEC loss magnitude:** IC3 owns BEC ($3.0B across 24,768 complaints).
  IRIS does not separately break out BEC. Use IC3 for the loss
  distribution, but apply judgement — IC3's $123K average per BEC
  complaint is mostly small-business-tier; large-org BEC is closer to
  IRIS's broader system-intrusion p95.

## Known limitations of using either alone

**IRIS alone:** misses anything that doesn't generate an insurance claim or
public disclosure — most fraud, most consumer-targeting, anything below the
SEC reporting threshold.

**IC3 alone:** misses anything the victim doesn't realize was a crime,
doesn't know how to report, or chose not to report; misses inflation
adjustment; mixes consumer and enterprise tiers without a filter; uses a
crime taxonomy that does not align with security-engineering primitives.

For most v3 calibration work the right approach is: anchor enterprise loss
distributions in IRIS, anchor fraud-and-employee-targeting scenarios in
IC3, and document at every call site which dataset the calibration came
from.
