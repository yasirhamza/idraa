# FFIEC Cybersecurity Advisories — Reference Data

**Status:** STUB — content TBD when source is fully extracted in a future PR.

**Source:** Federal Financial Institutions Examination Council (FFIEC)
cybersecurity guidance, advisories, and assessment-tool updates issued to
US-regulated financial institutions. Full advisory list, date windows, and
citation methodology to be filled in when the source is fully extracted.

**Population covered:** US-regulated banks, credit unions, savings
associations, and their service providers under FFIEC member-agency
oversight (FRB, FDIC, NCUA, OCC, CFPB, SLC). Each advisory addresses the
universe of FFIEC-supervised institutions; population is regulatory-scope-
defined, not victim-org-defined.

**Methodology summary:** Each advisory is a regulatory-guidance artifact
with its own scope (cybersecurity assessment tool, threat-specific
guidance, examination-procedure update). No standardized loss-figure
methodology. Trend and volume signals come from advisory-issuance cadence,
examination-finding patterns, and assessment-tool revisions, not from
victim-loss aggregation.

**Why this is reference-only (not calibration):** Advisory and guidance
data is regulator-prescriptive (what financial institutions must do), not
victim-loss-event-descriptive (what actually happened and at what cost).
Population mismatch with FAIR's loss-event distribution prevents direct
calibration use. Best used to inform `regulated_financial` overlay posture
(what controls regulators expect, what TTPs guidance highlights) and for
financial-sector compliance sidebars.

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
