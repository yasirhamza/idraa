# CISA Year in Review 2024 — Reference Data

**Status:** STUB — content TBD when source is fully extracted in a future PR.

**Source:** Cybersecurity and Infrastructure Security Agency (CISA), "2024
Year in Review" annual report. Full URL and citation to be filled in when
the source is fully extracted.

**Population covered:** Threat advisories, vulnerability disclosures, and
critical-infrastructure (CI) sector incident summaries reported to or
coordinated by CISA across calendar year 2024. US-centric; aggregated at
the sector and advisory level rather than the org-loss-event level.

**Methodology summary:** Aggregates CISA's published advisories, joint
cybersecurity advisories (CSAs), and CI-sector engagement summaries. Counts
are advisory-level and engagement-level, not victim-org-level. No direct
financial-loss figures are published in the same form as IC3.

**Why this is reference-only (not calibration):** Broad threat-landscape and
advisory-volume data, not org-loss-event-focused. Population (advisories
issued, sectors engaged) does not align with FAIR's loss-event distribution
shape; cannot be used to calibrate Loss Magnitude or LEF. Use as a
qualitative cross-reference for the `critical_infrastructure` overlay
posture and for sector-prevalence sidebars.

## Headlines

| Metric | Value | Source page/table |
| --- | --- | --- |

(Populated when source is extracted.)

## Known anomalies / errata

(None recorded yet — TBD when source is extracted.)

## When this source informs an overlay or calibration override

- `STARTER_OVERLAYS["critical_infrastructure"]` cites this document. (This
  forward-reference resolves when PR β lands the
  `STARTER_OVERLAY_PROVENANCE` dict referencing this file from its
  `sources` field.)

(Update this section whenever a new overlay or override `sources` field
adds this file. Bidirectional citation rule per spec §6.6.2.)
