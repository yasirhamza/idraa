---
title: "NetDiligence Cyber Claims Study 2025"
year: 2025
url: https://netdiligence.com/cyber-claims-study/
accessed: 2026-07-29
permalink: N/A  # paginated PDF (Report V1.1); printed page cites below
methodology_summary: "10,402 cyber insurance claims from incidents occurring 2020-2024, 14 named contributing insurers (printed p.50); cost analyses use the 9,171 claims reporting incident cost >= $1,000; SME (< $2B annual revenue) vs large-company split; 7 revenue bands, 18 sectors per p.7 (but see errata), 25 causes of loss."
---

# NetDiligence Cyber Claims Study 2025 — Reference Data

**Source:** NetDiligence (Network Standard Corporation), *Cyber Claims Study
2025 Report*, Version 1.1. Fifteenth annual study. The PDF is licensed
material and is NEVER committed (gitignored + `*.pdf` tracked-path
deny-glob); each team member fetches their own copy. Transcribed figures
below are owner-attested against the PDF with printed page cites.

**Population covered:** insurance CLAIMS (not incidents at large) from
insured organizations, 98% SMEs (< $2B revenue, average $108M). Incidents
occurred 2020–2024. Demographic analyses cover all 10,402 claims; every
cost table read here covers the 9,171 claims with reported incident cost
≥ $1,000 (p.7).

**Methodology summary:** insurers report the amount paid on the claim AND an
estimate of total incident cost **including self-insured retention (SIR) and
costs excluded by policy terms** (p.7). Cost tables publish claims count /
minimum / average / maximum / total per category — **no medians, no
percentiles**, which is why no direct mean/median σ back-out exists and the
estimators below work from the max statistic.

**Why this is reference-only (not calibration):** claims-process-visible
incident cost is NOT FAIR loss magnitude, and the conditioning level of
every read is cross-firm × cross-type within a revenue band — the
within-revenue-tier UPPER-BOUND row of
`within-scenario-sigma-calibration.md` §2, never the within-scenario
quantity fair_cam parameterizes.

## Conditioning register (what this data measures, and what biases it)

The incident-cost column is explicitly **not policy-limit-censored and not
SIR-truncated** — p.7 defines it as total cost INCLUDING SIR and
policy-excluded costs; those censoring mechanisms apply to the *payout*
column, which is never read here. The 69% (SME) / 27% (large) payout-to-
incident-cost ratios (p.7) are the **column-selection rationale** — proof
that payout under-measures even claims-visible cost — and nothing more.

Register of live bias mechanisms:

1. **Loss-form blindness** — of FAIR's six forms of loss, claims data
   captures response, business interruption / productivity, replacement,
   and some fines & judgments; **competitive advantage and reputation are
   essentially absent**. Direction: NetD cost sits BELOW FAIR loss
   magnitude.
2. **Claim-reporting selection + the ≥$1K cost filter** — an incident
   becomes a claim only when worth reporting, and sub-$1K claims are cut
   from cost analyses. Direction: observed population sits ABOVE the full
   loss-event population.
3. **Insured-SME conditioning** — Table 9 (causes of loss) is
   SME-conditioned; large-company siblings have n too small to read.

## Usage rules

- **Reference bands only — direction NOT established.** Mechanisms (1) and
  (2) push in opposite directions and neither is quantified, so NetD class
  means are neither floors nor ceilings for library per-event means. A
  library mean far below its mapped class mean prompts a **documented
  review**, never an automatic violation.
- **Never percentile anchors** — the same bar the loss-anchor protocol
  applies to IC3 aggregate figures (`loss-anchors/research-protocol.md` §1).
- **σ reads are consistency signals only**, at the stated conditioning
  level, with the estimator caveats printed beside every figure (two
  distinct bias statements, never conflated: the estimating-equation bias
  is upward at EVERY n, while the REALIZED root-conditional band median
  flips LOW at small n via the z_n ceiling + no-root truncation — each
  printed band shows its own median-vs-truth offset; no-root outcomes are
  plug-in artifacts; sampling bands are conditional on a root existing and
  ceiling-truncated at z_n).
- Every figure in this document is quoted verbatim from
  `scripts/netdiligence_sigma_check.py` (fail-loud transcription pins;
  tests at `tests/unit/test_netdiligence_sigma_check.py`). Regenerate with
  `uv run python scripts/netdiligence_sigma_check.py`; never hand-edit the
  block below.

## Generated figures (verbatim `netdiligence_sigma_check.py` output, 2026-07-30)

```text
==============================================================================
NETDILIGENCE 2025 SIGMA CONSISTENCY + ANCHOR REFERENCE BANDS (generated)
==============================================================================

Source: NetDiligence Cyber Claims Study 2025 Report V1.1 (licensed PDF,
UNTRACKED; transcription owner-attested; printed page cites).
Cost population: 9,171 claims >= $1K (p.7). Estimator definitions,
bias directions, and caveats: module docstring + netdiligence_2025.md.

[B-ND-BAND] Table 3 (p.52) — incident cost by revenue size
  nano_lt_50m  (n=4009, mean=$142,000, max=$10,400,000)
      plug-in roots: 1.60 / 5.36 (smaller selected: under the mean anchor mu = ln(mean) - sigma^2/2 the (1-1/n) quantile DECREASES as sigma rises past the sigma=z_n turnover, so the larger root sits on the decreasing branch — not a physically sensible read of a max)
      exact-E[max] root: 1.44 (point read; no sampling band computed)
      plug-in sampling band @ truth 1.7: [1.47, 1.79, 2.52] (p5/p50/p95), no-root rate 2.0%, z_n ceiling 3.48 (smaller root cannot exceed it); realized root-conditional bias at this n: band median +0.09 vs truth (bias direction is n-DEPENDENT — high at large n, flipped LOW at small n by the ceiling + no-root truncation)
      observed plug-in read sits at ~p20 of the truth-1.7 band (evidential direction)
  micro_50m_300m  (n=1775, mean=$374,000, max=$25,000,000)
      plug-in roots: 1.77 / 4.74 (smaller selected: under the mean anchor mu = ln(mean) - sigma^2/2 the (1-1/n) quantile DECREASES as sigma rises past the sigma=z_n turnover, so the larger root sits on the decreasing branch — not a physically sensible read of a max)
      exact-E[max] root: 1.54 (point read; no sampling band computed)
      plug-in sampling band @ truth 1.7: [1.42, 1.81, 2.53] (p5/p50/p95), no-root rate 5.9%, z_n ceiling 3.26 (smaller root cannot exceed it); realized root-conditional bias at this n: band median +0.11 vs truth (bias direction is n-DEPENDENT — high at large n, flipped LOW at small n by the ceiling + no-root truncation)
      observed plug-in read sits at ~p46 of the truth-1.7 band (evidential direction)
  small_300m_2b  (n=508, mean=$2,000,000, max=$108,000,000)
      plug-in roots: 2.30 / 3.46 (smaller selected: under the mean anchor mu = ln(mean) - sigma^2/2 the (1-1/n) quantile DECREASES as sigma rises past the sigma=z_n turnover, so the larger root sits on the decreasing branch — not a physically sensible read of a max)
      exact-E[max] root: 1.72 (point read; no sampling band computed)
      plug-in sampling band @ truth 1.7: [1.37, 1.80, 2.48] (p5/p50/p95), no-root rate 20.4%, z_n ceiling 2.88 (smaller root cannot exceed it); realized root-conditional bias at this n: band median +0.10 vs truth (bias direction is n-DEPENDENT — high at large n, flipped LOW at small n by the ceiling + no-root truncation)
      observed plug-in read sits at ~p89 of the truth-1.7 band (evidential direction) (near-edge)
  mid_2b_10b  (n=187, mean=$5,100,000, max=$268,000,000)
      plug-in: no root under the quantile plug-in (artifact of the plug-in, never tail-heaviness)
      exact-E[max] root: 2.09 (point read; no sampling band computed)
      plug-in sampling band @ truth 1.7: [1.26, 1.71, 2.27] (p5/p50/p95), no-root rate 43.5%, z_n ceiling 2.55 (smaller root cannot exceed it); realized root-conditional bias at this n: band median +0.01 vs truth (bias direction is n-DEPENDENT — high at large n, flipped LOW at small n by the ceiling + no-root truncation)
  large_10b_100b  (n=43, mean=$30,500,000, max=$503,500,000)
      plug-in: no root under the quantile plug-in (artifact of the plug-in, never tail-heaviness)
      exact-E[max] root: 1.85 (point read; no sampling band computed)
      plug-in sampling band @ truth 1.7: [1.02, 1.41, 1.80] (p5/p50/p95), no-root rate 82.2%, z_n ceiling 1.99 (smaller root cannot exceed it); realized root-conditional bias at this n: band median -0.29 vs truth (bias direction is n-DEPENDENT — high at large n, flipped LOW at small n by the ceiling + no-root truncation)
  mega_gt_100b  (n=4, mean=$38,300,000, max=$75,000,000)
      EXCLUDED from the sigma read: n=4 with a $10.6M minimum — a differently-truncated population
  unknown_rev  (n=2645, mean=$47,000, max=$2,700,000)
      EXCLUDED from the sigma read: no revenue conditioning, which is the entire point of the per-band read

[B-ND-CAUSE] Table 9 (p.59) — incident cost by cause of loss, SMEs
  business_email_compromise  (n=1864, mean=$98,000, max=$30,000,000)
      plug-in: no root under the quantile plug-in (artifact of the plug-in, never tail-heaviness)
      exact-E[max] root: 2.39 (point read; no sampling band computed)
      plug-in sampling band @ truth 1.7: [1.42, 1.81, 2.54] (p5/p50/p95), no-root rate 5.3%, z_n ceiling 3.27 (smaller root cannot exceed it); realized root-conditional bias at this n: band median +0.11 vs truth (bias direction is n-DEPENDENT — high at large n, flipped LOW at small n by the ceiling + no-root truncation)
  ransomware  (n=2571, mean=$631,000, max=$108,000,000)
      plug-in roots: 2.36 / 4.36 (smaller selected: under the mean anchor mu = ln(mean) - sigma^2/2 the (1-1/n) quantile DECREASES as sigma rises past the sigma=z_n turnover, so the larger root sits on the decreasing branch — not a physically sensible read of a max)
      exact-E[max] root: 1.93 (point read; no sampling band computed)
      plug-in sampling band @ truth 1.7: [1.44, 1.81, 2.53] (p5/p50/p95), no-root rate 3.8%, z_n ceiling 3.36 (smaller root cannot exceed it); realized root-conditional bias at this n: band median +0.11 vs truth (bias direction is n-DEPENDENT — high at large n, flipped LOW at small n by the ceiling + no-root truncation)
      observed plug-in read sits at ~p91 of the truth-1.7 band (evidential direction) (near-edge)
  hacker  (n=1191, mean=$135,000, max=$22,000,000)
      plug-in: no root under the quantile plug-in (artifact of the plug-in, never tail-heaviness)
      exact-E[max] root: 2.13 (point read; no sampling band computed)
      plug-in sampling band @ truth 1.7: [1.40, 1.81, 2.49] (p5/p50/p95), no-root rate 9.2%, z_n ceiling 3.14 (smaller root cannot exceed it); realized root-conditional bias at this n: band median +0.11 vs truth (bias direction is n-DEPENDENT — high at large n, flipped LOW at small n by the ceiling + no-root truncation)
  wire_transfer_fraud  (n=260, mean=$178,000, max=$3,800,000)
      plug-in roots: 1.67 / 3.66 (smaller selected: under the mean anchor mu = ln(mean) - sigma^2/2 the (1-1/n) quantile DECREASES as sigma rises past the sigma=z_n turnover, so the larger root sits on the decreasing branch — not a physically sensible read of a max)
      exact-E[max] root: 1.34 (point read; no sampling band computed)
      plug-in sampling band @ truth 1.7: [1.29, 1.75, 2.32] (p5/p50/p95), no-root rate 35.9%, z_n ceiling 2.67 (smaller root cannot exceed it); realized root-conditional bias at this n: band median +0.05 vs truth (bias direction is n-DEPENDENT — high at large n, flipped LOW at small n by the ceiling + no-root truncation)
      observed plug-in read sits at ~p39 of the truth-1.7 band (evidential direction)
  theft_of_money  (n=834, mean=$38,000, max=$500,000)
      plug-in roots: 1.02 / 5.05 (smaller selected: under the mean anchor mu = ln(mean) - sigma^2/2 the (1-1/n) quantile DECREASES as sigma rises past the sigma=z_n turnover, so the larger root sits on the decreasing branch — not a physically sensible read of a max)
      exact-E[max] root: 0.923 (point read; no sampling band computed)
      plug-in sampling band @ truth 1.7: [1.40, 1.83, 2.56] (p5/p50/p95), no-root rate 11.8%, z_n ceiling 3.04 (smaller root cannot exceed it); realized root-conditional bias at this n: band median +0.13 vs truth (bias direction is n-DEPENDENT — high at large n, flipped LOW at small n by the ceiling + no-root truncation)
      observed plug-in read sits at ~p0 of the truth-1.7 band (evidential direction) — AT/BELOW the band's LOW edge: materially inconsistent with truth 1.7 at this conditioning level, in the LOW direction

[B-ND-REF] anchor REFERENCE bands (direction NOT established:
  loss-form blindness pushes NetD BELOW FAIR loss magnitude;
  claim-reporting selection + the >=$1K filter push it ABOVE;
  Table 9 is SME-conditioned. A library per-event mean far below its
  mapped class mean prompts a DOCUMENTED REVIEW, never an automatic
  violation. NetD figures are NEVER percentile anchors.)
  business_email_compromise: SME mean $98,000 (n=1864) -> vendor/BEC mean-hold family (IC3 $123,005 anchor)
  ransomware: SME mean $631,000 (n=2571) -> intrusion/ransomware-class catastrophic entries
  wire_transfer_fraud: SME mean $178,000 (n=260) -> fraud-transfer entries

[B-ND-COND] conditioning level of every read above: cross-firm x
  cross-type WITHIN a revenue band — the within-revenue-tier
  UPPER-BOUND row of within-scenario-sigma-calibration.md §2, NOT the
  within-scenario quantity. Relation to IRIS 1.97-2.92 at that level
  is per-row and MIXED (see each row above) — NO summary direction is
  stated: several rows' z_n ceilings (e.g. small 2.88, wire 2.67) sit
  below IRIS's upper 2.92, so those rows structurally cannot read
  above the band regardless of the data. The >=$1K filter enters via
  COND-MEAN INFLATION (governed by the mass below $1K, not the mean
  share): correcting each published conditional mean to its
  untruncated equivalent at truth 1.7 shifts the plug-in read by
  <= +0.011 on included revenue bands and <= +0.052 at worst (theft_of_money; one-step correction, mildly understating — see filter_read_shift docstring)
  — conclusions unchanged (the worst-shifted read stays at/below its
  band's LOW edge).
```

## Known anomalies / errata

- Table-9 N differs from the body figures' N for the same causes (wire
  transfer fraud: 260 in Table 9 vs 438 in Figures 5 and 36) along an axis
  **the report does not state**: Table 9's claims column sums to 8,936
  (exactly Figure 9's SME N) while Figure 5 declares N=8,278, and four of
  Figure 5's five cause counts match Table 9 while wire fraud does not.
  Two genuinely different populations — but NOT the demographic-vs-≥$1K
  split. Table 9's row is self-consistent (its n pairs with its max), so
  it is the one used.
- The ≥$1K qualifier for Table 9 is stated on p.7, not in the table
  subtitle (Table 3's subtitle does carry it).
- The report contradicts itself on sector count: p.7 says "18 business
  sectors"; printed p.53 says "nineteen" and lists 19. The frontmatter
  follows p.7; recorded here per the errata discipline.
- BEC's incident-cost maximum ($30.0M) yields max/mean = 306 — no real
  root under the quantile plug-in at n=1,864; the exact-E[max] read (2.39)
  is the usable figure for that row.

## When this source informs an overlay or calibration override

None today. PR3 uses this source ONLY for the §9 validation-posture
consistency note in `within-scenario-sigma-calibration.md` and the
reference-band table above. Any future override citing NetD must satisfy the
primary-cited gate (printed page/table per figure) and restate the
conditioning register.
