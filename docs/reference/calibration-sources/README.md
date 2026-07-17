# Calibration Source Reference Shelf

Reference material for cyber-loss datasets that v3 analysts and report
readers may consult. **None of these sources are imported by `fair_cam`
calibration code** — that is reserved for IRIS (see `fair_cam/data/`).

These sources may inform:

- **Overlay multipliers** (`fair_cam/parameters/overlays.py::STARTER_OVERLAY_PROVENANCE`),
  which v3 seeds into the `OverlayDefinition` table on first migration.
- **Calibration override entries** that organizations enter via CRUD, citing
  one or more sources here.
- **Report sidebars** that surface "what other studies say" alongside FAIR results.

## Sources

| Source | Year | File | Primary use |
| --- | --- | --- | --- |
| FBI IC3 Annual Report | 2025 | [ic3_2025.md](ic3_2025.md) | `critical_infrastructure` overlay; fraud-context sidebars |
| CISA Year in Review (stub) | 2024 | [cisa_year_in_review_2024.md](cisa_year_in_review_2024.md) | `critical_infrastructure` overlay |
| CISA DIB Advisories (stub) | — | [cisa_dib_advisories.md](cisa_dib_advisories.md) | `defense_industrial_base` overlay |
| SEC Cybersecurity Disclosures (stub) | — | [sec_cyber_disclosures.md](sec_cyber_disclosures.md) | `regulated_financial` overlay |
| FFIEC Advisories (stub) | — | [ffiec_advisories.md](ffiec_advisories.md) | `regulated_financial` overlay |
| IBM Cost of Data Breach | 2024 | [ibm_codb_2024.md](ibm_codb_2024.md) | Canonical τ for LEC sub-functions (MTTI 194d / MTTC 64d, p10 Fig 4); per-vector + maturity-tier + storage-location benchmark cells |
| Verizon DBIR | 2024 | [dbir_2024.md](dbir_2024.md) | Canonical τ for VMC_CORR_IMPLEMENTATION (CISA KEV 55d median, p21 Fig 19); CISA KEV scan-latency overlay candidate |
| SANS 2024 D&R Survey + 2023 IR Survey | 2024 / 2023 | [sans_ir_2024.md](sans_ir_2024.md) | Reference-only — IR timing anchors only; treatment-selection + resilience-recovery medians not extractable |
| VERIS Community Database (VCDB) | continuously updated | [veris_dataset.md](veris_dataset.md) | Schema-of-record for VERIS timeline fields; no maintained per-class medians at WebFetch scope (deferred for future per-org override calibration) |
| FAIR Institute IRIS Ransomware Sub-Study | 2024 | [iris_2024.md](iris_2024.md) | Reference-only — paywalled; zero τ-relevant temporal metrics extractable; ransomware-share / sector-mix context only |

## Reconciliations

| Topic | File |
| --- | --- |
| IRIS vs IC3 — when to use which | [iris-vs-ic3-reconciliation.md](iris-vs-ic3-reconciliation.md) |

## Adding a new source

1. Copy `_template.md` to `<source_lower>_<YYYY>.md` (e.g., `dbir_2026.md`).
2. Fill in headlines + structured tables; cite page/figure for every value.
3. If the source informs an overlay or override, list the relevant entries
   in the "When this source informs..." section.
4. Add a row to this README's source table.
5. Submit PR.
