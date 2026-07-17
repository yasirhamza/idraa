# Attribution — Framework → FAIR-CAM Crosswalk Seed

`data/seed_framework_crosswalk.json` records, per framework subcategory, the set
of FAIR-CAM sub-functions that subcategory fulfils — the **factual mapping
relationships** between two published frameworks. These relationships were
**referenced from** the FAIR Institute's published crosswalks and independently
expressed here as structured data.

## What this data is (and is not)

- The entries are **factual mapping relationships** (framework code → FAIR-CAM
  function), not reproductions of any source document's prose, descriptions,
  formatting, or layout. The source spreadsheets themselves are **not** included
  in this repository.
- Framework subcategory text is published by **NIST** (NIST CSF — a
  U.S.-government work, public domain) and the **Center for Internet Security**
  (CIS Controls).
- No copyright or license is claimed or implied over the factual mapping
  relationships recorded here. This NOTICE is **attribution**, not an adoption of
  any third party's license terms.

## Reference source (attribution)

The mapping relationships were referenced from crosswalks published by the
**FAIR Institute** (<https://www.fairinstitute.org/>):

1. **NIST CSF 1.1 → FAIR-CAM 1.0 Mapping** — FAIR Institute.
2. **CIS 8.0 → FAIR-CAM Mapping V1.0** — FAIR Institute.

We credit the FAIR Institute as the source we referenced for these mapping
relationships. The FAIR Controls Analytics Model (FAIR-CAM) is published by the
FAIR Institute.

## RiskFlow extension layer

Five CIS safeguard entries carry an additional RiskFlow-authored FAIR-CAM function
in `riskflow_extension_functions`, kept structurally separate from the referenced
base layer. These are **RiskFlow methodology decisions**, not part of any FAIR
Institute source; each carries its own rationale in that entry's
`citation.riskflow_extension`.
