---
title: "<Source name>"
year: <YYYY>
url: <full URL>
accessed: YYYY-MM-DD          # Sec2-N1/Spec2-N3 tamper-evidence — required for non-paginated sources
permalink: <commit-hash URL or N/A>  # for non-paginated primary sources; N/A if paginated PDF
methodology_summary: "<sample size, data-collection method per source>"
---

# <Source Title> <Year> — Reference Data

**Source:** <publisher, exact report name, URL, date published>
**Population covered:** <one paragraph: who/what is in the dataset>
**Methodology summary:** <one paragraph: how data was collected, key caveats,
inflation adjustment, sample size, classification scheme>
**Why this is reference-only (not calibration):** <one paragraph: why
`fair_cam` does not import this — population mismatch, methodology coarseness,
licensing, etc.>

## Headlines

| Metric | Value | Source page/table |
| --- | --- | --- |

## <Topic A>

(structured table with explicit page or figure citation per row)

## <Topic B>

(another structured table)

## Known anomalies / errata

(documented quirks of the report — typos, truncated figures, methodology
shifts that affect comparability with prior years)

## When this source informs an overlay or calibration override

(bidirectional citation: lists overlays/overrides whose `sources` field
references this file. Reverse-traceable. Update this section whenever an
overlay or override entry adds this file to its `sources`.)
