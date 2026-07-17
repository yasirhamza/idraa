# C-iii-b Rebalance Audit — Trim/Merge Decisions + Projected Composition

**Branch:** `epic/library-curation-ciiib`
**Date:** 2026-06-11
**Produced by:** Task 2 implementer (decision-only task; trims execute in T6)
**Input data:** `data/seed_library_entries.json` (31) + `data/seed_library_entries_extension.json` (13) = 44 existing entries; `data/target_archetypes.json` (38 `keep_or_new == "new"` rows).

---

## 1. Audit of All 44 Existing Entries Against §3 Trim/Merge Rule

### Trim/merge eligibility criteria (§3)

A candidate for trim or merge MUST satisfy BOTH conditions simultaneously:

1. **Weakly-cited** — `loss_tier == "anecdotal"` (TIER-3). Paginated and vendor entries are categorically ineligible regardless of any other factor.
2. **Redundant** — a near-duplicate archetype (same threat_event_type + similar attack_vector + similar threat_actor) exists in the library, such that the candidate entry adds no distinct modeling value.

Hard rules (from gated plan):
- Never trim a paginated or vendor-cited entry.
- Never trim a distinct archetype.
- The three demoted Norsk-Hydro-class entries (`ransomware-on-control-layer`, `field-instrument-spoofing`, `chemical-process-safety-attack`) are anecdotal but DISTINCT archetypes with re-verify-eligible anchors — presumption KEEP.

### Tier inventory of all 44 entries

Computed programmatically (`asset_class in {"ot_systems", "safety_systems"}` OT predicate, MB-I1):

| Tier      | Count |
|-----------|------:|
| paginated | 23    |
| vendor    | 11    |
| anecdotal | 10    |
| **Total** | **44** |

OT entries by asset_class predicate: **16 of 44 = 36.4%** (pre-rebalance; drops to 19.5% post-addition).

The 3 entries that are OT by `asset_class` but would be missed by a `threat_event_type.startswith("ot_")` predicate: `ransomware-on-historian` (ot_systems, ransomware), `it-ot-bridge-compromise` (ot_systems, malware), `nation-state-ics-supply-chain` (ot_systems, supply_chain). This confirms MB-I1's asset_class predicate requirement.

### Full entry table (all 44)

| slug | loss_tier | asset_class | threat_event_type | OT? |
|------|-----------|-------------|-------------------|-----|
| ransomware-on-ehr | paginated | systems | ransomware | no |
| ransomware-on-historian | vendor | ot_systems | ransomware | yes |
| unauthorized-plc-modification | paginated | ot_systems | ot_safety_tampering | yes |
| safety-system-bypass | paginated | safety_systems | ot_safety_tampering | yes |
| denial-of-control | vendor | ot_systems | ot_availability | yes |
| hmi-credential-compromise | vendor | ot_systems | ot_availability | yes |
| it-ot-bridge-compromise | vendor | ot_systems | malware | yes |
| nation-state-ics-supply-chain | vendor | ot_systems | supply_chain | yes |
| hacktivist-ot-disruption | vendor | ot_systems | ot_availability | yes |
| bec-fraud-financial | anecdotal | data | social_engineering | no |
| ransomware-on-virtualization-stack | paginated | systems | ransomware | no |
| insider-data-theft-financial | paginated | data | insider_misuse | no |
| insider-ip-theft-manufacturing | paginated | data | insider_misuse | no |
| cloud-account-takeover | paginated | systems | malware | no |
| api-key-leak-devops | paginated | data | data_disclosure | no |
| ddos-extortion-financial | paginated | systems | denial_of_service | no |
| solarwinds-class-supply-chain | anecdotal | systems | supply_chain | no |
| moveit-class-zero-day-mft | paginated | data | data_disclosure | no |
| session-hijack-post-mfa-bypass | paginated | data | malware | no |
| watering-hole-industry-targeted | paginated | systems | malware | no |
| s3-misconfiguration-data-exposure | paginated | data | data_disclosure | no |
| package-registry-supply-chain | anecdotal | systems | supply_chain | no |
| ddos-financial-seasonal-peak | paginated | systems | denial_of_service | no |
| phishing-ad-compromise-ransomware | paginated | systems | ransomware | no |
| ransomware-on-fileshare | paginated | data | ransomware | no |
| credential-stuffing-consumer-portal | paginated | data | malware | no |
| mfa-fatigue-prompt-bombing | paginated | systems | social_engineering | no |
| ransomware-healthcare-small-practice | anecdotal | systems | ransomware | no |
| ot-network-scanning-reconnaissance | vendor | ot_systems | ot_availability | yes |
| data-breach-notification-regulatory-tail | paginated | data | data_disclosure | no |
| generative-ai-prompt-injection | anecdotal | data | data_disclosure | no |
| ransomware-on-control-layer | anecdotal | ot_systems | ot_availability | yes |
| process-view-manipulation | vendor | ot_systems | ot_integrity | yes |
| field-instrument-spoofing | anecdotal | ot_systems | ot_integrity | yes |
| oem-remote-maintenance-abuse | vendor | ot_systems | ot_availability | yes |
| grid-protective-relay-manipulation | vendor | ot_systems | ot_availability | yes |
| pipeline-scada-integrity | vendor | ot_systems | ot_integrity | yes |
| chemical-process-safety-attack | anecdotal | safety_systems | ot_safety_tampering | yes |
| accidental-insider-exposure | anecdotal | data | insider_misuse | no |
| web-app-exploitation | paginated | data | data_disclosure | no |
| third-party-processor-breach | paginated | data | supply_chain | no |
| retail-pos-card-skimming | anecdotal | data | data_disclosure | no |
| public-sector-targeted-intrusion | paginated | systems | ransomware | no |
| logistics-disruption | paginated | systems | ransomware | no |

### Per-entry trim/merge ruling (anecdotal entries only; all others are categorically ineligible)

The 10 anecdotal entries, examined against the trim/merge criteria:

---

**1. `ransomware-on-control-layer`** (anecdotal, ot_availability, ot_systems, cybercriminals, it_ot_bridge)

**Decision: KEEP**

Hard-rule protection: this is one of the three Norsk-Hydro-class entries named in the plan as distinct archetypes with re-verify-eligible anchors. The archetype models ransomware reaching the Purdue Level 1-2 DCS/process controllers and forcing a physical process trip — distinct from `ransomware-on-historian` (IT-layer/data impact, vendor tier) which models historian loss without process trip. The loss model is bimodal (near-zero if contained at IT/OT boundary vs. catastrophic if DCS reached), a characteristic documented in the entry's `canonical_fair_gap`. No existing vendor or paginated entry covers this process-layer consequence archetype. Non-redundant; presumption KEEP stands.

---

**2. `field-instrument-spoofing`** (anecdotal, ot_integrity, ot_systems, nation_state, engineering_workstation_compromise)

**Decision: KEEP**

Hard-rule protection: named Norsk-Hydro-class entry. Distinct archetype: Level-0/1 physical sensor signal spoofing (flow/pressure/temperature/level falsification at the measurement source) vs. `process-view-manipulation` (vendor tier) which covers HMI/historian display falsification above Level 1. The falsification occurs at a different layer of the Purdue model; the control loop is driven toward off-spec conditions by corrupted measurement inputs rather than by corrupted display data fed to operators. Stuxnet's centrifuge parameter manipulation at the measurement layer is the canonical reference. Distinct threat surface, distinct loss mechanism, non-redundant.

---

**3. `chemical-process-safety-attack`** (anecdotal, ot_safety_tampering, safety_systems, nation_state, engineering_workstation_compromise)

**Decision: KEEP**

Hard-rule protection: named Norsk-Hydro-class entry. Distinct archetype: deliberate defeat of a chemical/petrochemical SIS to enable an overpressure or runaway reaction with catastrophic physical consequence. Different from `safety-system-bypass` (paginated, also ot_safety_tampering / safety_systems): `safety-system-bypass` models the TRITON/TRISIS pattern of reprogramming a Triconex SIS controller using direct SIS access; `chemical-process-safety-attack` models the upstream BPCS manipulation that creates the process upset intended to propagate through a defeated SIS to physical catastrophe. These are complementary layers of the same kill-chain archetype, not duplicates — they have distinct threat entry points and distinct loss modes. Both carry re-verify-eligible anchors per the C-iii-a audit.

---

**4. `bec-fraud-financial`** (anecdotal, social_engineering, data, cybercriminals)

**Decision: KEEP**

The 38 new archetypes include `agri-coop-bec-fraud` (food_agriculture, cash_or_equivalent) and `professional-payroll-bec` (professional_services, cash_or_equivalent). These are different sectors and different asset classes from `bec-fraud-financial` (financial_services, data). The existing entry models financial-sector wire fraud and executive impersonation with the primary asset at risk being financial data/records; the new entries model BEC against agricultural cooperatives and professional-services payroll. Sector, asset class, and loss profile all differ. Non-redundant. KEEP.

---

**5. `solarwinds-class-supply-chain`** (anecdotal, supply_chain, systems, nation_state)

**Decision: KEEP**

Although `package-registry-supply-chain` is also anecdotal + supply_chain + systems + nation_state, these are distinct archetypes with fundamentally different attack models. SolarWinds-class = compromised vendor software build pipeline delivering a backdoor via a signed update to a vetted enterprise software tool, with highly targeted follow-on exploitation; package-registry = public open-source registry publication (npm/PyPI) open to any actor, blast radius determined by download count and transitive dependency depth. The threat actor access model, victim targeting mechanism, and blast-radius determinants differ materially. Both satisfy distinct modeling needs for organizations with different threat profiles. Non-redundant. KEEP.

---

**6. `package-registry-supply-chain`** (anecdotal, supply_chain, systems, nation_state)

**Decision: KEEP**

Addressed above (item 5). KEEP.

---

**7. `ransomware-healthcare-small-practice`** (anecdotal, ransomware, systems, healthcare, cybercriminals)

**Decision: KEEP**

The paginated `ransomware-on-ehr` entry targets large/enterprise healthcare systems (calibrated to IRIS 2025 healthcare pair p50=$557k / p95=$14M). `ransomware-healthcare-small-practice` targets small/medium healthcare practices (dental, physician, behavioral health) with a categorically different vulnerability profile (no dedicated IT, legacy EHR, minimal backups), loss structure (practice downtime rather than ransom + PHI regulatory tail dominating), and org-size calibration (less_than_10m revenue tier). The spec §1b mandates inherent-posture calibration: an entry calibrated to large hospital systems misrepresents vulnerability for small practices. This is a distinct, required archetype for the small-practice user segment. Non-redundant. KEEP.

---

**8. `generative-ai-prompt-injection`** (anecdotal, data_disclosure, data, cybercriminals)

**Decision: KEEP**

No similar entry exists in the 44 or the 38 new archetypes. This is the only AI-adversarial archetype in the library; its `canonical_fair_gap` explicitly documents it as a genuine taxonomy gap (no FAIR standard threat category fits). Emerging threat with distinct vulnerability factors (LLM architecture decisions, RAG retrieval scope, tool permission model). Wholly distinct archetype. KEEP.

---

**9. `accidental-insider-exposure`** (anecdotal, insider_misuse, data, insider_accidental)

**Decision: KEEP**

The existing `insider-data-theft-financial` (paginated, insider_misuse, data) is a malicious insider archetype. `accidental-insider-exposure` is a non-malicious/error archetype (misdelivery, misconfiguration, wrong-recipient email). These have fundamentally different threat actor motivation terms, different frequency drivers (error rate vs. malicious intent), and different loss models (regulatory notification tail without an adversary loss-magnitude component vs. malicious exfiltration with competitive-advantage / litigation tail). The FAIR taxonomy distinction between negligent/accidental and malicious insider is architecturally relevant to the engine's vulnerability modeling. Non-redundant. KEEP.

---

**10. `retail-pos-card-skimming`** (anecdotal, data_disclosure, data, retail, cybercriminals)

**Decision: KEEP**

The paginated `data-breach-notification-regulatory-tail` (data_disclosure, data, retail) focuses on the GDPR/CCPA regulatory fine and class-action secondary loss tail — the loss driver is the regulatory framework and record volume. `retail-pos-card-skimming` focuses on PCI-DSS loss: card-brand assessments, PCI forensic remediation, PCI-DSS downgrade/fine, and the payment-card asset class rather than PII records. Different loss instruments (PCI card-brand assessments vs. GDPR/CCPA statutory fines), different compliance obligations (PCI-DSS remediation mandates vs. notification law obligations), and different primary loss mechanisms (cardholder data resale vs. breach-notification cost). Non-redundant. KEEP.

---

### Summary decision table

| slug | tier | OT? | Decision | Rationale (one-line) |
|------|------|-----|----------|----------------------|
| ransomware-on-control-layer | anecdotal | yes | **KEEP** | Named Norsk-Hydro entry; distinct DCS/process-trip archetype vs. historian-only |
| field-instrument-spoofing | anecdotal | yes | **KEEP** | Named Norsk-Hydro entry; Level-0 sensor spoofing distinct from Level-2 HMI falsification |
| chemical-process-safety-attack | anecdotal | yes | **KEEP** | Named Norsk-Hydro entry; BPCS-manipulation + SIS defeat distinct from direct SIS reprogramming |
| bec-fraud-financial | anecdotal | no | **KEEP** | Different sector/asset class from new BEC entries; financial-sector wire-fraud archetype |
| solarwinds-class-supply-chain | anecdotal | no | **KEEP** | Distinct from package-registry: signed vendor update vs. open-source registry |
| package-registry-supply-chain | anecdotal | no | **KEEP** | Distinct from SolarWinds: public registry / transitive dependency model |
| ransomware-healthcare-small-practice | anecdotal | no | **KEEP** | Small-practice org profile categorically distinct from enterprise healthcare EHR entry |
| generative-ai-prompt-injection | anecdotal | no | **KEEP** | Only AI-adversarial entry; genuine FAIR taxonomy gap; no near-duplicate exists |
| accidental-insider-exposure | anecdotal | no | **KEEP** | Accidental/error actor categorically distinct from malicious insider |
| retail-pos-card-skimming | anecdotal | no | **KEEP** | PCI-DSS loss instruments distinct from GDPR/CCPA regulatory-tail entry |

**Total trims: 0. Total merges: 0.**

All 44 existing entries are retained as published. The audit found no entry that satisfies both criteria (anecdotal AND redundant) simultaneously. All 10 anecdotal entries represent distinct archetypes with non-substitutable modeling value.

---

## 2. Projected Final Composition Table

Post-rebalance: **44 existing (0 trims) + 38 new = 82 entries total**.

Verified: total 82 ∈ [70, 90] ✓

### Composition by sector × OT/IT

| Sector | Total | OT | non-OT | Threat Types (≥2 required) |
|--------|------:|---:|-------:|---------------------------|
| education | 4 | 0 | 4 | data_disclosure, insider_misuse, physical_tampering, ransomware |
| energy_utilities | 13 | 11 | 2 | data_tampering, malware, ot_availability, ot_integrity, ransomware, supply_chain |
| financial_services | 9 | 0 | 9 | data_disclosure, data_tampering, denial_of_service, insider_misuse, physical_tampering, social_engineering, supply_chain |
| food_agriculture | 5 | 0 | 5 | data_disclosure, data_tampering, physical_tampering, ransomware, social_engineering |
| government_public | 4 | 0 | 4 | data_tampering, denial_of_service, insider_misuse, ransomware |
| healthcare | 5 | 0 | 5 | data_tampering, insider_misuse, ransomware, social_engineering |
| hospitality | 4 | 0 | 4 | data_disclosure, denial_of_service, insider_misuse, social_engineering |
| manufacturing | 10 | 5 | 5 | insider_misuse, ot_availability, ot_integrity, ot_safety_tampering, physical_tampering, ransomware |
| professional_services | 4 | 0 | 4 | data_disclosure, physical_tampering, ransomware, social_engineering |
| retail_ecommerce | 5 | 0 | 5 | data_disclosure, denial_of_service, insider_misuse, malware |
| technology_saas | 11 | 0 | 11 | data_disclosure, insider_misuse, malware, physical_tampering, social_engineering, supply_chain |
| telecom | 5 | 0 | 5 | data_disclosure, data_tampering, denial_of_service, physical_tampering, social_engineering |
| transportation_logistics | 3 | 0 | 3 | data_tampering, physical_tampering, ransomware |
| **TOTAL** | **82** | **16** | **66** | |

**OT share: 16/82 = 19.5%** — well within the ≤32% gate (spec §3 target ~25–30%, §3 cap 32% per plan SCB-6 with 2pp headroom; the 38 non-OT additions drive the share down from the pre-rebalance 36.4%).

**OT predicate used (MB-I1 — exact):** `asset_class in {"ot_systems", "safety_systems"}`. The `threat_event_type.startswith("ot_")` predicate is prohibited; it would undercount by 3 (`ransomware-on-historian`, `it-ot-bridge-compromise`, `nation-state-ics-supply-chain` are OT by asset_class with generic threat types).

### Loss-tier breakdown

> **pre-T2b caveat — the tier column below is subject to revision by T2b anchor upgrades; see the T2b commit for the final tier split. The coverage-matrix and OT-share outputs are tier-agnostic and unaffected.**

| Source | paginated | vendor | anecdotal | Total |
|--------|----------:|-------:|----------:|------:|
| Existing 44 | 23 | 11 | 10 | 44 |
| New 38 (pre-T2b) | 13 | — | 25 | 38 |
| **Total (pre-T2b)** | **36** | **11** | **35** | **82** |

New entries: 13 quantile_pair-anchored archetypes → paginated lognormal (T3 batch); 25 none-anchored archetypes → anecdotal PERT (T4/T5 batches). T2b (NAIC + VERIS/VCDB evaluation) may upgrade some of the 25 none-anchored rows; T4/T5 recount against the post-T2b anchor state at execution (per plan).

---

## 3. §3 Coverage-Matrix Check

### Core sector coverage (each ≥2 entries spanning ≥2 threat types)

All 13 core sectors checked programmatically:

| Sector | Entries | Threat types | Status |
|--------|--------:|-------------:|--------|
| manufacturing | 10 | 6 | ✓ |
| energy_utilities | 13 | 6 | ✓ |
| healthcare | 5 | 4 | ✓ |
| financial_services | 9 | 7 | ✓ |
| retail_ecommerce | 5 | 4 | ✓ |
| technology_saas | 11 | 6 | ✓ |
| government_public | 4 | 4 | ✓ |
| education | 4 | 4 | ✓ |
| professional_services | 4 | 4 | ✓ |
| transportation_logistics | 3 | 3 | ✓ |
| telecom | 5 | 5 | ✓ (satisfied by new entries — 5 new archetypes) |
| hospitality | 4 | 4 | ✓ (satisfied by new entries — 4 new archetypes) |
| food_agriculture | 5 | 5 | ✓ (satisfied by new entries — 5 new archetypes) |

**All 13 core sectors: SATISFIED.**

Telecom, hospitality, and food_agriculture are previously-unrepresented sectors; their coverage is entirely from new entries. Cross-check confirms the 38 new archetypes in `target_archetypes.json` carry the required sector/threat-type combinations.

### Named non-OT threat type coverage (each ≥2 entries)

| Threat type | Entries | Status |
|-------------|--------:|--------|
| data_tampering | 7 | ✓ |
| physical_tampering | 8 | ✓ |
| denial_of_service | 6 | ✓ |
| social_engineering | 9 | ✓ |
| insider_misuse | 10 | ✓ |

**All 5 named threat types: SATISFIED.**

`data_tampering` and `physical_tampering` were 0-entry gaps in the pre-rebalance library; both are entirely satisfied by new entries. Cross-check against `target_archetypes.json`: `data_tampering` new entries are `telecom-bgp-route-hijack`, `food-recall-data-tampering`, `gov-records-tampering`, `logistics-tms-data-tampering`, `healthcare-record-alteration`, `energy-billing-system-tamper`, `financial-transaction-tampering` (7 total, combining existing 0 + new 7). `physical_tampering` new entries are `agri-equipment-physical-tamper`, `logistics-warehouse-physical-intrusion`, `datacenter-physical-breach`, `professional-office-physical-theft`, `manufacturing-facility-sabotage`, `education-campus-facility-tamper`, `telecom-field-cabinet-tamper`, `branch-atm-physical-tamper` (8 total, combining existing 0 + new 8).

### Underused asset class coverage (≥1 entry each)

| Asset class | Entries | Satisfying slugs | Status |
|-------------|--------:|-----------------|--------|
| people | 6 | telecom-sim-swap-fraud, education-student-records-insider, competitor-trade-secret-recruit, healthcare-staff-credential-phish, financial-call-center-social-eng, gov-employee-insider-leak | ✓ |
| facilities | 7 | agri-equipment-physical-tamper, logistics-warehouse-physical-intrusion, datacenter-physical-breach, professional-office-physical-theft, manufacturing-facility-sabotage, education-campus-facility-tamper, telecom-field-cabinet-tamper | ✓ |
| cash_or_equivalent | 7 | hospitality-pos-card-skimming, hospitality-loyalty-account-takeover, agri-coop-bec-fraud, branch-atm-physical-tamper, financial-transaction-tampering, professional-payroll-bec, retail-store-employee-fraud | ✓ |
| business_process_revenue | 6 | telecom-ddos-core-network, hospitality-booking-ddos-peak-season, food-cold-chain-ransomware, gov-citizen-portal-ddos, retail-ecommerce-checkout-ddos, saas-revenue-outage-sabotage | ✓ |
| business_process_cost | 4 | food-recall-data-tampering, logistics-tms-data-tampering, manufacturing-billing-fraud, energy-billing-system-tamper | ✓ |

**All 5 underused asset classes: SATISFIED.** All entries satisfying these cells come from new archetypes (the existing 44 had zero entries in any of these asset classes).

### Competitors threat actor coverage (≥1 entry)

| Actor | Entries | Satisfying slugs |
|-------|--------:|-----------------|
| competitors | 3 | crop-science-ip-exfiltration (food_agriculture), ip-theft-by-competitor (manufacturing), competitor-trade-secret-recruit (technology_saas) |

**SATISFIED.** Pre-rebalance there were 0 `competitors` actor entries; all 3 are new entries from `target_archetypes.json`.

---

## 4. Matrix Check Summary

| Matrix cell | Pre-rebalance | Post-rebalance | Status |
|-------------|:-------------:|:--------------:|--------|
| All 13 core sectors ≥2 entries + ≥2 threat_types | FAIL (3 sectors absent) | OK | ✓ |
| data_tampering ≥2 entries | FAIL (0) | 7 entries | ✓ |
| physical_tampering ≥2 entries | FAIL (0) | 8 entries | ✓ |
| denial_of_service ≥2 entries | OK | 6 entries | ✓ |
| social_engineering ≥2 entries | OK | 9 entries | ✓ |
| insider_misuse ≥2 entries | OK | 10 entries | ✓ |
| people asset class ≥1 entry | FAIL (0) | 6 entries | ✓ |
| facilities asset class ≥1 entry | FAIL (0) | 7 entries | ✓ |
| business_process_revenue ≥1 entry | FAIL (0) | 6 entries | ✓ |
| business_process_cost ≥1 entry | FAIL (0) | 4 entries | ✓ |
| cash_or_equivalent ≥1 entry | FAIL (0) | 7 entries | ✓ |
| competitors actor ≥1 entry | FAIL (0) | 3 entries | ✓ |
| OT share ≤32% | FAIL (36.4%) | 19.5% | ✓ |
| Total entries ∈ [70, 90] | OK (44) | 82 | ✓ |

**All §3 coverage-matrix cells: SATISFIED. No NEEDS_CONTEXT gaps.**

Every previously-failing cell is satisfied by new entries in `target_archetypes.json`. No archetype invention was required or performed; all satisfying entries are drawn from the committed `target_archetypes.json` file.

---

## 5. Open items for downstream tasks

- **T2b (NAIC + VERIS/VCDB evaluation):** The tier projection table above is pre-T2b. If T2b upgrades any of the 25 none-anchored archetypes from anecdotal to vendor/paginated, T4/T5 batch counts will be revised. The matrix checks and OT-share figure are not affected (they are tier-agnostic).
- **T6 execution:** This task produces decisions only. The 0-trim ruling means T6's Step 1 (`status = "deprecated"` updates) is a no-op for existing entries. T6 still needs to author the insert-if-absent migration for the 38 new entries and write the balance-assertion tests.
- **energy_utilities OT concentration:** 11 of 13 energy_utilities entries are OT (84.6% within-sector). This is by design — energy/utilities is the primary OT sector — but the within-sector concentration is high. The 2 non-OT energy entries (`watering-hole-industry-targeted`, `energy-billing-system-tamper`) provide some balance. Future curation (Epic D+) may expand non-OT energy archetypes. No action required in C-iii-b scope.
