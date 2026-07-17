# CISA Defense Industrial Base (DIB) Advisories — Reference Data

**Status:** STUB — content TBD when source is fully extracted in a future PR.

**Source:** Cybersecurity and Infrastructure Security Agency (CISA), Defense
Industrial Base (DIB) sector advisories and joint cybersecurity advisories
(CSAs) tagged to the DIB. Full citation list, advisory IDs, and date
windows to be filled in when the source is fully extracted.

**Population covered:** Cybersecurity advisories targeting or describing
attacks on US Defense Industrial Base contractors and subcontractors. Mixes
APT-attribution advisories, vulnerability disclosures, and incident
post-mortems. Sample is the universe of DIB-tagged advisories CISA has
published, not a victim-org census.

**Methodology summary:** Each advisory is a discrete artifact with its own
methodology (threat-actor attribution, IOC list, MITRE ATT&CK mapping,
recommended mitigations). No published financial-loss aggregation across
advisories. Volume and trend signals come from advisory-issuance counts,
not victim-tier loss data.

**Why this is reference-only (not calibration):** Advisory population is
adversary-and-vulnerability-centric, not loss-event-centric. Population
mismatch with FAIR's victim-org loss distribution prevents direct
calibration use. Best used to inform `defense_industrial_base` overlay
posture (which controls fail more often, what TTPs dominate) and for
sector-targeted threat sidebars in reports.

## Headlines

| Metric | Value | Source page/table |
| --- | --- | --- |

(Populated when source is extracted.)

## Known anomalies / errata

(None recorded yet — TBD when source is extracted.)

## When this source informs an overlay or calibration override

- `STARTER_OVERLAYS["defense_industrial_base"]` cites this document. (This
  forward-reference resolves when PR β lands the
  `STARTER_OVERLAY_PROVENANCE` dict referencing this file from its
  `sources` field.)

(Update this section whenever a new overlay or override `sources` field
adds this file. Bidirectional citation rule per spec §6.6.2.)
