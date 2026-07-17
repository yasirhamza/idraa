# SEC Cybersecurity Disclosures — Reference Data

**Status:** STUB — content TBD when source is fully extracted in a future PR.

**Source:** US Securities and Exchange Commission (SEC) cybersecurity
disclosures filed under the 2023 cyber-disclosure rule (Form 8-K Item 1.05
"Material Cybersecurity Incidents" and Form 10-K cybersecurity governance
disclosures). Full filing-window, sample size, and citation methodology to
be filled in when the source is fully extracted.

**Population covered:** US-listed public companies that filed
cyber-incident or governance disclosures under the SEC rule. Skews toward
larger, regulated, publicly traded firms — i.e., the financial,
energy/utility, and healthcare-with-public-listings tier. Excludes private
companies, smaller firms below the SEC reporting threshold, and the long
tail of unreported incidents.

**Methodology summary:** Each filing is voluntarily-structured by the
filer (no standardized loss-figure schema). Incident materiality is
filer-asserted, not auditor-validated. Comparing incidents across filers
requires normalizing for company size, business model, and how broadly
"material" is interpreted. Some filers disclose dollar figures, most do
not.

**Why this is reference-only (not calibration):** Disclosure population is
biased toward large publicly traded firms; financial-impact field is
inconsistently populated and definitionally varied (some quote remediation
cost, some quote business-interruption, some give no number). Cannot
directly feed FAIR loss-event distributions. Best used to inform
`regulated_financial` overlay posture (which incident-pattern types and
disclosure timelines dominate among regulated financials) and for
report sidebars on disclosure-tier impact.

## Headlines

| Metric | Value | Source page/table |
| --- | --- | --- |

(Populated when source is extracted.)

## Known anomalies / errata

(None recorded yet — TBD when source is extracted.)

## When this source informs an overlay or calibration override

- `STARTER_OVERLAYS["regulated_financial"]` cites this document. (This
  forward-reference resolves when PR β lands the
  `STARTER_OVERLAY_PROVENANCE` dict referencing this file from its
  `sources` field.)

(Update this section whenever a new overlay or override `sources` field
adds this file. Bidirectional citation rule per spec §6.6.2.)
