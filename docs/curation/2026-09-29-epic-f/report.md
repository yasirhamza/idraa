# Curation check: epic-f (2026-09-29)

Judge `jev` · model `jev-1.13.0` · commit `25b33ace29` · top 15 per check

Scores order the queues and select the unreviewed closest-match list. They are not calibrated probabilities: in the System One trial the judge's yes/no answers on control functions had an expected calibration error (ECE) of 0.14, and 13 of 55 asset-class answers given a score of 0.99 or higher were wrong.

Mark each row's **Disposition** as `accepted`, `rejected` or `deferred`, with a one-line **Reason**. Flags are candidates for review, not verdicts.

**Dispositions:** made by Claude Fable 5.1 acting as curator on the owner's instruction (2026-09-29), with an adversarial verification pass; not by a human curator. Hit rates from this report therefore measure agreement between Jev's flags and Fable's judgment. Changed-entries rows are dispositioned only where the judge scored 0.50 or more (it disagrees with the curated value); lower rows are left blank because the judge agrees. `responses.jsonl` (904 KB, over the 500 KB repo limit) is archived outside the repo, sha256 `baa106cbed2e36e69aa95533053f625f9cdb7976d2f528fa7b7c8c9acf7de3a8`.

## Scenario label audit

297 scored rows from 99 items.

Top 15; 16 more scored within 0.10 below the cut.

| # | Score | Subject | Finding | Disposition | Reason |
|---|---|---|---|---|---|
| 1 | 1.00 | cloud-account-takeover · threat_event_type | curated **malware** score 0.00; judge prefers **social_engineering** (0.77) | accepted | accepted in pilot; held under owner decision O7 with its loss-form re-judgement, tracked in #198 |
| 2 | 1.00 | food-recall-data-tampering · asset_class | curated **business_process_cost** score 0.00; judge prefers **data** (0.94) | rejected | business_process_cost is right: the loss is recall, enforcement and litigation from a defeated QA/lot-release process, not the value of the records as an information asset (pilot reason reused) |
| 3 | 1.00 | hmi-credential-compromise · threat_event_type | curated **ot_availability** score 0.00; judge prefers **ot_integrity** (1.00) | accepted | relabel ot_availability → ot_integrity: the described effect is setpoint manipulation while the process keeps running (Oldsmar NaOH dosing), no stop or loss of control/view; re-judge loss forms per rubric |
| 4 | 1.00 | hospitality-pos-card-skimming · asset_class | curated **cash_or_equivalent** score 0.00; judge prefers **data** (0.99) | accepted | relabel cash_or_equivalent → data: the asset lost is cardholder data sold on carding forums; the org's loss is PCI forensics, card-brand assessments and fines, no funds are taken from it; matches retail-pos-card-skimming |
| 5 | 1.00 | manufacturing-billing-fraud · asset_class | curated **business_process_cost** score 0.00; judge prefers **cash_or_equivalent** (1.00) | accepted | relabel business_process_cost → cash_or_equivalent: the insider diverts payments to accounts they control and 'loss is direct financial theft'; same call as bec-fraud-financial (pilot row 2) |
| 6 | 1.00 | ot-network-scanning-reconnaissance · threat_event_type | curated **ot_availability** score 0.00; judge prefers **social_engineering** (0.46) | accepted | relabel ot_availability → data_disclosure (not social_engineering): no OT effect is described at the recon stage, spearphishing is one route beside scanning; the loss is disclosure of OT network intelligence (step 4) |
| 7 | 1.00 | unauthorized-plc-modification · threat_event_type | curated **ot_safety_tampering** score 0.00; judge prefers **ot_integrity** (0.99) | accepted | relabel ot_safety_tampering → ot_integrity: the entry alters basic-process PLC logic (asset ot_systems) while masking the HMI; safety functions are not the target (that is safety-system-bypass); move Triton example there |
| 8 | 0.99 | edge-device-orb-foothold · threat_actor_type | judge's top score is 'not enough information' (0.87); curated **nation_state** score 0.01; next: **cybercriminals** 0.12 | accepted | sharpen description: name the state-sponsored ORB operators (its KV-botnet/PRC example); as written 'compromise of an internet-facing edge device' fits any actor, so nation_state is unsupported (pilot row 8 precedent) |
| 9 | 0.99 | food-recall-data-tampering · threat_event_type | curated **data_tampering** score 0.01; judge prefers **insider_misuse** (0.99) | accepted | relabel data_tampering → insider_misuse: no mechanism fits and a malicious insider abuses ERP/LIMS/QA access they already hold (step 3 precedes step 4), as pilot rows 10/14/15; re-judge loss forms per rubric (#198 class) |
| 10 | 0.99 | grid-protective-relay-manipulation · threat_event_type | curated **ot_availability** score 0.01; judge prefers **ot_safety_tampering** (0.92) | accepted | relabel ot_availability → ot_safety_tampering: the sharpened entry is protection defeat (relays disabled so faults are not cleared, equipment damaged), a safety function altered; also review asset → safety_systems |
| 11 | 0.99 | it-ot-bridge-compromise · threat_event_type | curated **malware** score 0.01; judge prefers **ransomware** (0.45) | accepted | relabel malware → ot_availability (not ransomware): OT assets are the target and the described effect is an OT shutdown (productivity-dominant); the mechanism is phishing/stuffing then pivot, not code; sharpen the effect |
| 12 | 0.99 | law-enforcement-records-extortion-breach · threat_event_type | curated **data_disclosure** score 0.01; judge prefers **ransomware** (0.99) | accepted | sharpen description: drop 'encryption or' so the entry is exfiltration-and-leak extortion, which is data_disclosure (no encryption/lock, no outage loss form); the encryption variant would be ransomware under the criteria |
| 13 | 0.99 | ransomware-on-historian · threat_event_type | curated **ransomware** score 0.01; judge prefers **ot_availability** (0.99) | accepted | relabel ransomware → ot_availability: an OT system (historian) is the target and the described effect is loss of view forcing process shutdown, so precedence step 1 applies whatever the mechanism; re-judge loss forms |
| 14 | 0.98 | field-instrument-spoofing · threat_actor_type | judge's top score is 'not enough information' (0.97); curated **nation_state** score 0.02; next: **insider_malicious** 0.01 | accepted | sharpen description: say a nation-state actor (its Stuxnet example; L0 signal spoofing needs state-grade access and process engineering); as written 'An adversary' supports no actor label (pilot row 8 precedent) |
| 15 | 0.98 | hospitality-pos-card-skimming · threat_event_type | curated **data_disclosure** score 0.02; judge prefers **malware** (0.98) | accepted | relabel data_disclosure → malware: both described routes are malicious code on victim systems (POS memory scraper, injected JS skimmer), so mechanism (step 2) precedes step 4; apply to retail-pos-card-skimming too |

## Control function audit

1556 scored rows from 63 items; 19 suppressed as deliberately dropped claims (seed `_meta.claim_drops`).

### Possibly missing

Top 8; 29 more scored within 0.10 below the cut.

| # | Score | Subject | Finding | Disposition | Reason |
|---|---|---|---|---|---|
| 1 | 0.90 | cloud-security-posture-management · Monitoring | not labelled **Monitoring**; judge's yes-score 0.90 | rejected | CSPM's 'monitoring for abnormal behavior' clause is undecomposed boilerplate: rubric §7.1 lists CSPM's behaviours with posture drift as VMC Control monitoring and no LEC detection member; no DE.CM-1/4/7 tag grounds one |
| 2 | 0.88 | patch-management · Reduce variance probability | not labelled **Reduce variance probability**; judge's yes-score 0.88 | rejected | The criteria's staging-test example is for control-degrading changes (rubric I5); PM's patch testing guards compatibility/downtime of the patched asset; PM's channels are the #437 T1 status quo (a third double-counts) |
| 3 | 0.88 | security-configuration-assessment · Reporting | not labelled **Reporting**; judge's yes-score 0.88 | rejected | SCA's reports are a by-product of the assessment (findings, compliance evidence for auditors): Control-monitoring outputs per #192, not risk analysis delivered to decision-makers; Reporting only where it is the purpose |
| 4 | 0.86 | email-security-protection · Recognition | not labelled **Recognition**; judge's yes-score 0.86 | rejected | Scanning for phishing and malware indicators is the decision logic of the inline block before inbox delivery (Avoidance per the #192 table), not loss-event detection; SWG is labelled the same way (pilot reason reused) |
| 5 | 0.86 | external-attack-surface-management · Visibility | not labelled **Visibility**; judge's yes-score 0.86 | rejected | EASM observes the org's own exposure state (open services, unknown assets), i.e. gaps in other controls = VMC Control monitoring, already its sole label; Visibility captures threat activity, which EASM does not |
| 6 | 0.85 | secure-web-gateway · Recognition | not labelled **Recognition**; judge's yes-score 0.85 | rejected | SWG's signature/heuristic scanning is the decision logic of the inline block (Avoidance per the #192 table), not classification of a loss event under way; same call as email-security-protection Recognition (pilot row 8) |
| 7 | 0.84 | application-performance-monitoring · Recognition | not labelled **Recognition**; judge's yes-score 0.84 | accepted | add lec_det_recognition (low capability): APM's baseline-deviation thresholds classify anomalies as DoS, exfiltration or unauthorised access (criteria 'tuned alert thresholds'); ground with DE.AE-1/DE.AE-5 as FIM is |
| 8 | 0.84 | cloud-security-posture-management · Reporting | not labelled **Reporting**; judge's yes-score 0.84 | rejected | CSPM's posture dashboard is already carried as dsc_prev_sa_analysis (rubric §7.1 item 5) and its compliance reports for audits are Control-monitoring outputs (#192); Reporting is reserved for controls whose purpose it is |

### Possibly wrong

Top 7; 10 more scored within 0.10 below the cut.

| # | Score | Subject | Finding | Disposition | Reason |
|---|---|---|---|---|---|
| 1 | 0.89 | host-intrusion-detection-prevention · Resilience | labelled **Resilience**; judge's yes-score 0.11 | accepted | accepted in pilot; application deferred by owner decision O6, tracked in #197 |
| 2 | 0.89 | third-party-risk-management · Threat data | labelled **Threat data**; judge's yes-score 0.11 | rejected | keep: vendor risk assessment and ongoing monitoring produce the org's data on third parties as a supply-chain threat source and how likely they are to bring breaches in, which feeds sourcing decisions (pilot reason) |
| 3 | 0.88 | network-detection-response · Resilience | labelled **Resilience**; judge's yes-score 0.12 | accepted | accepted in pilot; application deferred by owner decision O6, tracked in #197 |
| 4 | 0.87 | endpoint-detection-response · Resilience | labelled **Resilience**; judge's yes-score 0.13 | accepted | accepted in pilot; application deferred by owner decision O6, tracked in #197 |
| 5 | 0.86 | user-access-control · Implementation | labelled **Implementation**; judge's yes-score 0.14 | rejected | keep: access reviews and de-provisioning revoke the excess or orphaned entitlements they find, restoring a drifted least-privilege state (the correction step of the id+corr pair retained in #437 T3) (pilot reason reused) |
| 6 | 0.84 | third-party-risk-management · Incentives | labelled **Incentives**; judge's yes-score 0.16 | rejected | keep: contractual security clauses and SLAs attach consequences to the vendor's security choices, enforcement by sanction (Incentives) not a technical safeguard; ID.SC-3 is the one genuine home for the tag (pilot reason) |
| 7 | 0.83 | secure-remote-access · Avoidance | labelled **Avoidance**; judge's yes-score 0.17 | accepted | sharpen description: say SRA gates remote entry (VPN/ZTNA broker) so internal services are not internet-exposed; only tunnel encryption is attested (now Resistance), so Avoidance is text-orphaned; PR.AC-5 grounds it |

<details><summary>Suppressed: deliberately dropped claims (seed <code>_meta.claim_drops</code>)</summary>

- security-rating-service · Reporting: yes-score 0.95; dropped because dsc_prev_sa_reporting dropped: only RS.CO-*/DE.DP-4/RC.CO-3 ground it; an external security rating service is an analysis/monitoring input (ID.SC-4, RS.AN-2), not an internal reporting control.
- secure-web-gateway · Reporting: yes-score 0.74; dropped because dsc_prev_sa_reporting dropped: only NIST reporting/comms subcategories (RS.CO-*, DE.DP-4, RC.CO-3) ground it; an SWG is an inline web-filtering gateway, not an internal reporting control — it would not carry those tags independently.
- acceptable-use-policy · Incentives: yes-score 0.62; dropped because dsc_prev_incentives is only groundable by NIST ID.SC-3 (supply-chain contracts) and lec_prev_deterrence only by PR.DS-5 (DLP) — neither tag is one an Acceptable Use Policy would carry independently (D2 / Meth-B1/B2). Contrived tags avoided.
- cyber-risk-quantification-management · Control monitoring: yes-score 0.58; dropped because vmc_id_control_monitoring dropped: CRQM quantifies/reports risk and informs control-improvement decisions (PR.IP-7 grounds vmc_corr_implementation) but does not itself monitor control state; DE.CM-8/ID.SC-4 are scanning/supplier-assessment controls CRQM would not carry. No defensible tag.
- data-in-transit-encryption · Control monitoring: yes-score 0.50; dropped because vmc_id_control_monitoring + vmc_corr_implementation dropped (#437 rollout T3, non-faithful VMC-on-self boilerplate): same rationale as DRE — encryption monitoring/auditing is self-operation, not VMC identification/correction of a separate control (rubric §4-I1). Partial pair (vmc_corr_implementation null-capability) scored $0. DTE still SCORES via the genuine lec_prev_resistance.
- security-conscious-personnel · Reduce variance probability: yes-score 0.42; dropped because vmc_prev_reduce_variance_prob dropped: an awareness/culture control does not reduce a control's change/variance frequency (B-METH-1). lec_prev_resistance was PREVIOUSLY dropped here because the tags then considered (PR.AC-1/PR.IP-3/CIS 16.10) were contrived for an awareness posture; it is RE-ADDED in #437 rollout T2 via the GENUINE home CIS 14.2 (Train Workforce Members to Recognize Social Engineering Attacks) + a reviewed crosswalk-seed extension 14.2 -&gt; lec_prev_resistance, so no contrived tag is resurrected.
- acceptable-use-policy · Deterrence: yes-score 0.39; dropped because dsc_prev_incentives is only groundable by NIST ID.SC-3 (supply-chain contracts) and lec_prev_deterrence only by PR.DS-5 (DLP) — neither tag is one an Acceptable Use Policy would carry independently (D2 / Meth-B1/B2). Contrived tags avoided.
- data-at-rest-encryption · Control monitoring: yes-score 0.39; dropped because vmc_id_control_monitoring + vmc_corr_implementation dropped (#437 rollout T3, non-faithful VMC-on-self boilerplate): the 'monitoring/auditing' in the description is self-operation on the encryption control itself, not identification/correction of a SEPARATE degraded control, which is what VMC models (rubric §4-I1). The pair was also partial (vmc_corr_implementation null-capability) so it scored $0. dsc_prev_defined_expectations dropped: encryption is a technical resistance control, not a decision-support capability (§2.7 do-not-author-DSC-chasing-value); it was a 1-of-9 label-only DSC that added no score. DRE still SCORES via the genuine lec_prev_resistance; lec_resp_loss_reduction (encrypted-data safe-harbor loss-magnitude reduction, null-capability pending a T4 cited figure) is retained as a GENUINE channel.
- user-access-control · Reduce variance probability: yes-score 0.28; dropped because vmc_prev_reduce_variance_prob dropped (#437 rollout T3, double-scorer, borderline rubric I5): the 'entitlement drift' UAC governs is UAC's OWN entitlements (the ASSET), not a SEPARATE control's change-variance frequency (VMC-Prevention); it double-counts the access-restriction mechanism the kept lec_prev_resistance already captures. vmc_id_control_monitoring + vmc_corr_implementation are RETAINED (genuine partial meta pair, $0, faithful) and dsc_prev_defined_expectations is retained (harmless label-only). UAC still SCORES via lec_prev_resistance.
- third-party-risk-management · Controls data: yes-score 0.26; dropped because lec_prev_resistance dropped (TPRM is a governance/assessment program, not a technical resistance control — PR.AC-*/PR.DS-* would be contrived); dsc_prev_sa_data_controls dropped (that function covers INTERNAL control-state inventories e.g. CIS 5.1/6.6, whereas TPRM tracks third-party-risk data — no defensible tag).
- network-access-control · Reduce variance probability: yes-score 0.21; dropped because vmc_prev_reduce_variance_prob dropped (#437 rollout T3, double-scorer): NAC posture-gates ACCESS — that behavior is lec_prev_avoidance (already assigned + scoring, removing-contact per rubric §4-I1). NAC does not reduce the frequency/probability of changes that introduce a SEPARATE control's variance (VMC-Prevention); the variance channel double-counts the same access-gating mechanism the kept avoidance channel already captures. No distinct change-frequency-reduction home in the crosswalk. NAC still SCORES via lec_prev_avoidance.
- data-at-rest-encryption · Defined expectations: yes-score 0.19; dropped because vmc_id_control_monitoring + vmc_corr_implementation dropped (#437 rollout T3, non-faithful VMC-on-self boilerplate): the 'monitoring/auditing' in the description is self-operation on the encryption control itself, not identification/correction of a SEPARATE degraded control, which is what VMC models (rubric §4-I1). The pair was also partial (vmc_corr_implementation null-capability) so it scored $0. dsc_prev_defined_expectations dropped: encryption is a technical resistance control, not a decision-support capability (§2.7 do-not-author-DSC-chasing-value); it was a 1-of-9 label-only DSC that added no score. DRE still SCORES via the genuine lec_prev_resistance; lec_resp_loss_reduction (encrypted-data safe-harbor loss-magnitude reduction, null-capability pending a T4 cited figure) is retained as a GENUINE channel.
- data-in-transit-encryption · Implementation: yes-score 0.18; dropped because vmc_id_control_monitoring + vmc_corr_implementation dropped (#437 rollout T3, non-faithful VMC-on-self boilerplate): same rationale as DRE — encryption monitoring/auditing is self-operation, not VMC identification/correction of a separate control (rubric §4-I1). Partial pair (vmc_corr_implementation null-capability) scored $0. DTE still SCORES via the genuine lec_prev_resistance.
- secure-coding-practices · Implementation: yes-score 0.17; dropped because vmc_corr_implementation dropped (#437 rollout T2, audit mis-channel): SCP reduces the exploitable-defect density of its OWN code (the software application is the ASSET) = LEC Resistance per rubric §4-I1 Example-3; it does not sustain or correct a SEPARATE degraded control, which is what VMC-Correction models (§4-I1 Example-2). SCP now scores via lec_prev_resistance; dsc_prev_defined_expectations (coding standards) is retained label-only.
- cyber-risk-quantification-management · Implementation: yes-score 0.15; dropped because vmc_corr_implementation dropped (#437 rollout T3, mis-channel): CRQM SELECTS/PRIORITIZES treatments (decision-support = DSC), it does not IMPLEMENT corrections — vmc_corr_implementation models the ELAPSED_TIME time-to-restore-a-degraded-control, which is a remediation-execution capability CRQM does not carry. The DSC triad (dsc_prev_sa_reporting/dsc_prev_sa_analysis/dsc_prev_defined_expectations) is retained as the genuine decision-support channel. CRQM stays NON-SCORING (genuinely-meta residual, disposition B, pending a direct channel in #439); this is a faithfulness cleanup, not a scoring change.
- data-at-rest-encryption · Implementation: yes-score 0.14; dropped because vmc_id_control_monitoring + vmc_corr_implementation dropped (#437 rollout T3, non-faithful VMC-on-self boilerplate): the 'monitoring/auditing' in the description is self-operation on the encryption control itself, not identification/correction of a SEPARATE degraded control, which is what VMC models (rubric §4-I1). The pair was also partial (vmc_corr_implementation null-capability) so it scored $0. dsc_prev_defined_expectations dropped: encryption is a technical resistance control, not a decision-support capability (§2.7 do-not-author-DSC-chasing-value); it was a 1-of-9 label-only DSC that added no score. DRE still SCORES via the genuine lec_prev_resistance; lec_resp_loss_reduction (encrypted-data safe-harbor loss-magnitude reduction, null-capability pending a T4 cited figure) is retained as a GENUINE channel.
- security-conscious-personnel · Reporting: yes-score 0.14; dropped because dsc_prev_sa_reporting dropped: only reporting/comms subcategories ground it; a 'security conscious personnel' posture is an awareness/culture outcome, not a reporting capability — no defensible reporting tag.
- third-party-risk-management · Resistance: yes-score 0.13; dropped because lec_prev_resistance dropped (TPRM is a governance/assessment program, not a technical resistance control — PR.AC-*/PR.DS-* would be contrived); dsc_prev_sa_data_controls dropped (that function covers INTERNAL control-state inventories e.g. CIS 5.1/6.6, whereas TPRM tracks third-party-risk data — no defensible tag).
- asset-management · Incentives: yes-score 0.04; dropped because dsc_prev_incentives is only groundable by NIST ID.SC-3 (supply-chain contracts), which Asset Management would not carry independently of the function (D2 / Meth-B1).

</details>

## Scenario overlap

74 scored rows from 99 items.

Top 15; 50 more scored within 0.10 below the cut.

| # | Score | Subject | Finding | Disposition | Reason |
|---|---|---|---|---|---|
| 1 | 1.00 | agri-coop-bec-fraud ↔ bec-fraud-financial | from agri-coop-bec-fraud: judge's top score is **Business Email Compromise — Financial Sector Wire Fraud** (0.91); distinct-score 0.00; also **Professional Services Payroll BEC — Direct-Deposit Fraud** (0.09); reverse: bec-fraud-financial's score for this pair 0.61: merge, sharpen, or keep | rejected | Justified sector variant: same BEC mechanism, but agri-coop targets seasonal grain/input payments with its own TEF (mode 8 vs 3) and no industry overlap (agriculture vs financial/professional/real_estate) (pilot reason) |
| 2 | 1.00 | competitor-trade-secret-recruit ↔ ip-theft-by-competitor | from competitor-trade-secret-recruit: judge's top score is **Manufacturing Competitor-Driven IP Theft — Trade Secret Exfiltration** (0.80); distinct-score 0.00; also **Insider IP Theft — Manufacturing and Semiconductor** (0.19); reverse: ip-theft-by-competitor's score for this pair 0.39: merge, sharpen, or keep | rejected | Distinct sector, asset and method: SaaS IP via recruitment (information) vs manufacturing IP via an insider, no industry overlap; overlap #12 merges ip-theft away; D7 labels tracked in #200. Side: 'technology' non-enum |
| 3 | 1.00 | credential-stuffing-consumer-portal ↔ hospitality-loyalty-account-takeover | from credential-stuffing-consumer-portal: judge's top score is **Hospitality Loyalty Program Account Takeover — Points Fraud** (1.00); distinct-score 0.00; reverse: hospitality-loyalty-account-takeover's score for this pair 0.98: merge, sharpen, or keep | rejected | The pilot's sharpen is applied (industries now retail/financial vs hospitality/transportation): justified sector variant with own calibration; the data vs cash_or_equivalent asset split (D4) is tracked in #200 |
| 4 | 1.00 | ddos-financial-seasonal-peak ↔ retail-ecommerce-checkout-ddos | from ddos-financial-seasonal-peak: judge's top score is **Retail E-Commerce Checkout DDoS — Revenue-Disruption Attack During Peak Season** (0.92); distinct-score 0.00; also **Hospitality Booking Platform DDoS — Peak-Season Revenue Disruption** (0.08); reverse: retail-ecommerce-checkout-ddos's score for this pair 0.60: merge, sharpen, or keep | rejected | The pilot's sharpen is applied (retail checkout and retail industry removed from the financial entry, retail_trade → retail): justified sector variant, disjoint industries, banking/payment processor vs checkout |
| 5 | 1.00 | education-student-records-insider ↔ hospitality-guest-data-insider | from education-student-records-insider: judge's top score is **Hospitality Guest Data Exfiltration — Malicious Insider PMS Breach** (0.49); distinct-score 0.00; also **Insider Data Theft — Financial Records and PII Exfiltration** (0.46): merge, sharpen, or keep | rejected | Justified sector variant: student SIS records under FERPA vs guest PMS profiles and card data under PCI/GDPR, different sectors and loss paths (pilot reason reused; the asset_class split is now settled, both data) |
| 6 | 1.00 | financial-transaction-tampering ↔ manufacturing-billing-fraud | from financial-transaction-tampering: judge's top score is **Manufacturing Insider Billing Fraud — Fictitious Vendor AP Manipulation** (0.94); distinct-score 0.00; reverse: manufacturing-billing-fraud's score for this pair 0.98: merge, sharpen, or keep | rejected | Justified sector variant: bank core-banking/wire/trading fraud with regulatory penalties vs manufacturing AP fictitious-vendor fraud, no industry overlap, separate calibration. Side: 'finance_and_insurance' is non-enum |
| 7 | 1.00 | food-recall-data-tampering ↔ healthcare-record-alteration | from food-recall-data-tampering: judge's top score is **Healthcare EHR Record Alteration — Malicious Insider Data Integrity Tampering** (0.69); distinct-score 0.00; also **Government Records Tampering — Malicious Insider Data Corruption** (0.28); reverse: healthcare-record-alteration's score for this pair 0.49: merge, sharpen, or keep | rejected | Different asset and loss path: food QA/lot-release records defeated → recall, FDA enforcement (business_process_cost) vs EHR altered → fraudulent billing, malpractice, HIPAA (data); disjoint sectors |
| 8 | 1.00 | hmi-credential-compromise ↔ oem-remote-maintenance-abuse | from hmi-credential-compromise: judge's top score is **Abused OEM / Integrator Remote-Maintenance Access** (0.88); distinct-score 0.00; also **Hacktivist OT Disruption — HMI Defacement or Process Interference** (0.10); reverse: oem-remote-maintenance-abuse's score for this pair 0.67: merge, sharpen, or keep | rejected | Different method and effect: operator's own exposed HMI remote access with setpoint manipulation (integrity) vs standing vendor pathway with disruptive commands or outage; side: drop the Oldsmar example from OEM |
| 9 | 1.00 | hospitality-booking-ddos-peak-season ↔ retail-ecommerce-checkout-ddos | from hospitality-booking-ddos-peak-season: judge's top score is **Retail E-Commerce Checkout DDoS — Revenue-Disruption Attack During Peak Season** (0.94); distinct-score 0.00; also **DDoS at Financial Seasonal Peak — Payment and Banking** (0.06): merge, sharpen, or keep | rejected | Justified sector variant: booking/PMS platform with OTA partner penalties vs e-commerce checkout with cart abandonment, disjoint industries (hospitality vs retail), own TEF and loss calibration |
| 10 | 1.00 | hospitality-guest-data-insider ↔ insider-data-theft-financial | from hospitality-guest-data-insider: judge's top score is **Insider Data Theft — Financial Records and PII Exfiltration** (0.96); distinct-score 0.00; reverse: insider-data-theft-financial's score for this pair 0.93: merge, sharpen, or keep | rejected | Justified sector variant: PMS guest profiles and card data (PCI + GDPR) vs financial records/PII at scale (Capital One pattern), disjoint industries and own calibration (TEF 0.6 vs 1.5) |
| 11 | 1.00 | hospitality-pos-card-skimming ↔ retail-pos-card-skimming | from hospitality-pos-card-skimming: judge's top score is **Retail POS / E-commerce Card Skimming (Magecart)** (1.00); distinct-score 0.00; reverse: retail-pos-card-skimming's score for this pair 1.00: merge, sharpen, or keep | accepted | sharpen: same threat, method, effect and loss forms; make industries disjoint (drop hospitality from retail-pos, retail from hospitality-pos) so each sector sees one entry; keep hospitality variant for its hotel anchors |
| 12 | 1.00 | insider-ip-theft-manufacturing ↔ ip-theft-by-competitor | from ip-theft-by-competitor: judge's top score is **Competitor Trade-Secret Theft via Employee Recruitment**, **Insider IP Theft — Manufacturing and Semiconductor** (0.39, tied); distinct-score 0.00; also **Crop-Science IP Exfiltration — Competitor-Sponsored Theft of Seed Genetics and Agrochemical IP** (0.22); reverse: insider-ip-theft-manufacturing's score for this pair 0.82: merge, sharpen, or keep | accepted | merge ip-theft-by-competitor into insider-ip-theft-manufacturing: same insider exfiltration of manufacturing trade secrets, same sectors, identical PL and loss forms; the competitor motive is already in the insider entry |
| 13 | 1.00 | it-ot-bridge-compromise ↔ ransomware-on-historian | from it-ot-bridge-compromise: judge's top score is **Ransomware Propagation to OT Historian** (0.91); distinct-score 0.00; also **Abused OEM / Integrator Remote-Maintenance Access** (0.07): merge, sharpen, or keep | accepted | sharpen: scope it-ot-bridge-compromise to a non-ransomware, attacker-directed OT pivot and move the Colonial ransomware wording to ransomware-on-historian; today both model a ransomware-forced OT shutdown |
| 14 | 1.00 | k12-edtech-vendor-breach ↔ third-party-processor-breach | from k12-edtech-vendor-breach: judge's top score is **Third-Party / MSP Data-Processor Breach** (0.92); distinct-score 0.00; also **Zero-Day Exploitation of Managed File Transfer (MFT) Platform** (0.06); reverse: third-party-processor-breach's score for this pair 0.64: merge, sharpen, or keep | rejected | Justified sector variant: K-12 SIS vendor with FERPA liability and PowerSchool anchor vs generic MSP/SaaS/payroll processor; disjoint industries and very different calibration (TEF 0.13 vs 1.5, PL high 0.94M vs 10.2M) |
| 15 | 1.00 | nation-state-ics-supply-chain ↔ solarwinds-class-supply-chain | from solarwinds-class-supply-chain: judge's top score is **Nation-State ICS Supply Chain Pre-positioning** (0.54); distinct-score 0.00; also **Open Source Package Registry Compromise** (0.45); reverse: nation-state-ics-supply-chain's score for this pair 0.72: merge, sharpen, or keep | rejected | Differs in FAIR scope: OT asset (ICS vendor updates, Havex, energy OT pre-positioning) vs IT systems (IT management build pipeline, follow-on exploitation); disjoint industries (utilities/mining vs IT/finance/public) |

## Changed entries

Every entry whose seed record differs from merge base `8ccac47ee4`. Selected by what changed, not by score — it includes rows where the judge agrees. It is not a sample of the queues, so do not compute hit rates from it. Its dispositions here are optional and never tallied; the ranked queue above is the row that must carry the disposition (see its `see <queue> #n` cross-reference where one applies).

### Scenario label audit

| # | Score | Subject | Finding | Disposition | Reason |
|---|---|---|---|---|---|
| 1 | 0.99 | grid-protective-relay-manipulation · threat_event_type | curated **ot_availability** score 0.01; judge prefers **ot_safety_tampering** (0.92) | see Scenario label audit #10 |  |
| 2 | 0.95 | ddos-financial-seasonal-peak · asset_class | curated **systems** score 0.05; judge prefers **business_process_revenue** (0.86) | accepted | relabel asset_class systems → business_process_revenue: the loss is framed as transaction revenue foregone (productivity-dominant), exactly as its sharpened sibling retail-ecommerce-checkout-ddos carries |
| 3 | 0.88 | credential-stuffing-consumer-portal · asset_class | curated **data** score 0.12; judge prefers **cash_or_equivalent** (0.77) | deferred | tracked in #200 (D4): data vs sibling hospitality-loyalty-account-takeover's cash_or_equivalent is filed for the next campaign; the entry mixes payment-instrument fraud with credential resale |
| 4 | 0.82 | telecom-sim-swap-fraud · asset_class | curated **cash_or_equivalent** score 0.18; judge prefers **systems** (0.43) | deferred | tracked in #200 (D5/D6): the low-confidence cash_or_equivalent label follows the loss-form re-judgement (business_process_cost alternative); the judge's systems is no candidate, carrier systems are the vector |
| 5 | 0.81 | competitor-trade-secret-recruit · threat_event_type | curated **social_engineering** score 0.19; judge prefers **data_disclosure** (0.47) | deferred | tracked in #200 (D7): social_engineering vs sibling ip-theft-by-competitor's insider_misuse, plus the actor question, is filed; the judge's data_disclosure is step-4 fallback only if no mechanism fits |
| 6 | 0.54 | credential-stuffing-consumer-portal · threat_event_type | judge's top score agrees with curated **data_tampering** (0.46); next: **data_disclosure** 0.26 | rejected | keep data_tampering (the judge's own top pick): no mechanism fits (tools run on attacker infrastructure, nobody deceived), no insider; effect is fraudulent account changes and transactions (step 4); pilot reason reused |
| 7 | 0.34 | safety-system-bypass · threat_actor_type | judge's top score agrees with curated **nation_state** (0.66); next: **insider_malicious** 0.04 |  |  |
| 8 | 0.13 | grid-protective-relay-manipulation · asset_class | judge's top score agrees with curated **ot_systems** (0.87); next: **facilities** 0.11 |  |  |
| 9 | 0.12 | gov-citizen-portal-ddos · asset_class | judge's top score agrees with curated **systems** (0.88); next: **business_process_cost** 0.08 |  |  |
| 10 | 0.08 | energy-billing-system-tamper · asset_class | judge's top score agrees with curated **business_process_revenue** (0.92); next: **systems** 0.04 |  |  |
| 11 | 0.03 | energy-billing-system-tamper · threat_event_type | judge's top score agrees with curated **data_tampering** (0.97); next: **ot_integrity** 0.03 |  |  |
| 12 | 0.02 | competitor-trade-secret-recruit · threat_actor_type | judge's top score agrees with curated **competitors** (0.98); next: **insider_malicious** 0.02 |  |  |
| 13 | 0.01 | financial-call-center-social-eng · asset_class | judge's top score agrees with curated **cash_or_equivalent** (0.99); next: **data** 0.01 |  |  |
| 14 | 0.00 | accidental-insider-exposure · asset_class | judge's top score agrees with curated **data** (1.00); next: **business_process_cost** 0.00 |  |  |
| 15 | 0.00 | accidental-insider-exposure · threat_actor_type | judge's top score agrees with curated **insider_accidental** (1.00); next: **competitors** 0.00 |  |  |
| 16 | 0.00 | accidental-insider-exposure · threat_event_type | judge's top score agrees with curated **data_disclosure** (1.00); next: **data_tampering** 0.00 |  |  |
| 17 | 0.00 | bec-fraud-financial · asset_class | judge's top score agrees with curated **cash_or_equivalent** (1.00); next: **business_process_cost** 0.00 |  |  |
| 18 | 0.00 | bec-fraud-financial · threat_actor_type | judge's top score agrees with curated **cybercriminals** (1.00); next: **competitors** 0.00 |  |  |
| 19 | 0.00 | bec-fraud-financial · threat_event_type | judge's top score agrees with curated **social_engineering** (1.00); next: **data_disclosure** 0.00 |  |  |
| 20 | 0.00 | competitor-trade-secret-recruit · asset_class | judge's top score agrees with curated **data** (1.00); next: **business_process_cost** 0.00 |  |  |
| 21 | 0.00 | credential-stuffing-consumer-portal · threat_actor_type | judge's top score agrees with curated **cybercriminals** (1.00); next: **competitors** 0.00 |  |  |
| 22 | 0.00 | ddos-financial-seasonal-peak · threat_actor_type | judge's top score agrees with curated **cybercriminals** (1.00); next: **competitors** 0.00 |  |  |
| 23 | 0.00 | ddos-financial-seasonal-peak · threat_event_type | judge's top score agrees with curated **denial_of_service** (1.00); next: **data_disclosure** 0.00 |  |  |
| 24 | 0.00 | education-student-records-insider · asset_class | judge's top score agrees with curated **data** (1.00); next: **business_process_cost** 0.00 |  |  |
| 25 | 0.00 | education-student-records-insider · threat_actor_type | judge's top score agrees with curated **insider_malicious** (1.00); next: **competitors** 0.00 |  |  |
| 26 | 0.00 | education-student-records-insider · threat_event_type | judge's top score agrees with curated **insider_misuse** (1.00); next: **data_disclosure** 0.00 |  |  |
| 27 | 0.00 | energy-billing-system-tamper · threat_actor_type | judge's top score agrees with curated **cybercriminals** (1.00); next: **competitors** 0.00 |  |  |
| 28 | 0.00 | energy-settlement-platform-tampering-offtaker-liability · asset_class | judge's top score agrees with curated **business_process_third_party_revenue** (1.00); next: **business_process_cost** 0.00 |  |  |
| 29 | 0.00 | energy-settlement-platform-tampering-offtaker-liability · threat_actor_type | judge's top score agrees with curated **nation_state** (1.00); next: **competitors** 0.00 |  |  |
| 30 | 0.00 | energy-settlement-platform-tampering-offtaker-liability · threat_event_type | judge's top score agrees with curated **data_tampering** (1.00); next: **data_disclosure** 0.00 |  |  |
| 31 | 0.00 | financial-call-center-social-eng · threat_actor_type | judge's top score agrees with curated **cybercriminals** (1.00); next: **competitors** 0.00 |  |  |
| 32 | 0.00 | financial-call-center-social-eng · threat_event_type | judge's top score agrees with curated **social_engineering** (1.00); next: **data_disclosure** 0.00 |  |  |
| 33 | 0.00 | financial-transaction-tampering · asset_class | judge's top score agrees with curated **cash_or_equivalent** (1.00); next: **business_process_cost** 0.00 |  |  |
| 34 | 0.00 | financial-transaction-tampering · threat_actor_type | judge's top score agrees with curated **insider_malicious** (1.00); next: **competitors** 0.00 |  |  |
| 35 | 0.00 | financial-transaction-tampering · threat_event_type | judge's top score agrees with curated **insider_misuse** (1.00); next: **data_disclosure** 0.00 |  |  |
| 36 | 0.00 | gov-citizen-portal-ddos · threat_actor_type | judge's top score agrees with curated **hacktivists** (1.00); next: **competitors** 0.00 |  |  |
| 37 | 0.00 | gov-citizen-portal-ddos · threat_event_type | judge's top score agrees with curated **denial_of_service** (1.00); next: **data_disclosure** 0.00 |  |  |
| 38 | 0.00 | gov-employee-insider-leak · asset_class | judge's top score agrees with curated **data** (1.00); next: **business_process_cost** 0.00 |  |  |
| 39 | 0.00 | gov-employee-insider-leak · threat_actor_type | judge's top score agrees with curated **insider_malicious** (1.00); next: **competitors** 0.00 |  |  |
| 40 | 0.00 | gov-employee-insider-leak · threat_event_type | judge's top score agrees with curated **insider_misuse** (1.00); next: **data_disclosure** 0.00 |  |  |
| 41 | 0.00 | gov-records-tampering · asset_class | judge's top score agrees with curated **data** (1.00); next: **business_process_cost** 0.00 |  |  |
| 42 | 0.00 | gov-records-tampering · threat_actor_type | judge's top score agrees with curated **insider_malicious** (1.00); next: **competitors** 0.00 |  |  |
| 43 | 0.00 | gov-records-tampering · threat_event_type | judge's top score agrees with curated **insider_misuse** (1.00); next: **data_disclosure** 0.00 |  |  |
| 44 | 0.00 | grid-protective-relay-manipulation · threat_actor_type | judge's top score agrees with curated **nation_state** (1.00); next: **competitors** 0.00 |  |  |
| 45 | 0.00 | healthcare-record-alteration · asset_class | judge's top score agrees with curated **data** (1.00); next: **business_process_cost** 0.00 |  |  |
| 46 | 0.00 | healthcare-record-alteration · threat_actor_type | judge's top score agrees with curated **insider_malicious** (1.00); next: **competitors** 0.00 |  |  |
| 47 | 0.00 | healthcare-record-alteration · threat_event_type | judge's top score agrees with curated **insider_misuse** (1.00); next: **data_disclosure** 0.00 |  |  |
| 48 | 0.00 | healthcare-staff-credential-phish · asset_class | judge's top score agrees with curated **data** (1.00); next: **business_process_cost** 0.00 |  |  |
| 49 | 0.00 | healthcare-staff-credential-phish · threat_actor_type | judge's top score agrees with curated **cybercriminals** (1.00); next: **competitors** 0.00 |  |  |
| 50 | 0.00 | healthcare-staff-credential-phish · threat_event_type | judge's top score agrees with curated **social_engineering** (1.00); next: **data_disclosure** 0.00 |  |  |
| 51 | 0.00 | professional-payroll-bec · asset_class | judge's top score agrees with curated **cash_or_equivalent** (1.00); next: **business_process_cost** 0.00 |  |  |
| 52 | 0.00 | professional-payroll-bec · threat_actor_type | judge's top score agrees with curated **cybercriminals** (1.00); next: **competitors** 0.00 |  |  |
| 53 | 0.00 | professional-payroll-bec · threat_event_type | judge's top score agrees with curated **social_engineering** (1.00); next: **data_disclosure** 0.00 |  |  |
| 54 | 0.00 | retail-ecommerce-checkout-ddos · asset_class | judge's top score agrees with curated **business_process_revenue** (1.00); next: **business_process_cost** 0.00 |  |  |
| 55 | 0.00 | retail-ecommerce-checkout-ddos · threat_actor_type | judge's top score agrees with curated **cybercriminals** (1.00); next: **competitors** 0.00 |  |  |
| 56 | 0.00 | retail-ecommerce-checkout-ddos · threat_event_type | judge's top score agrees with curated **denial_of_service** (1.00); next: **data_disclosure** 0.00 |  |  |
| 57 | 0.00 | safety-system-bypass · asset_class | judge's top score agrees with curated **safety_systems** (1.00); next: **business_process_cost** 0.00 |  |  |
| 58 | 0.00 | safety-system-bypass · threat_event_type | judge's top score agrees with curated **ot_safety_tampering** (1.00); next: **data_disclosure** 0.00 |  |  |
| 59 | 0.00 | telecom-sim-swap-fraud · threat_actor_type | judge's top score agrees with curated **cybercriminals** (1.00); next: **competitors** 0.00 |  |  |
| 60 | 0.00 | telecom-sim-swap-fraud · threat_event_type | judge's top score agrees with curated **social_engineering** (1.00); next: **data_disclosure** 0.00 |  |  |
| 61 | 0.00 | web-app-exploitation · asset_class | judge's top score agrees with curated **data** (1.00); next: **business_process_cost** 0.00 |  |  |
| 62 | 0.00 | web-app-exploitation · threat_actor_type | judge's top score agrees with curated **cybercriminals** (1.00); next: **competitors** 0.00 |  |  |
| 63 | 0.00 | web-app-exploitation · threat_event_type | judge's top score agrees with curated **data_disclosure** (1.00); next: **data_tampering** 0.00 |  |  |

### Control function audit

| # | Score | Subject | Finding | Disposition | Reason |
|---|---|---|---|---|---|
| 1 | 0.83 | secure-remote-access · Avoidance | labelled **Avoidance**; judge's yes-score 0.17 | see Control function audit Possibly wrong #7 |  |
| 2 | 0.82 | security-awareness-training · Control monitoring | labelled **Control monitoring**; judge's yes-score 0.18 | rejected | keep: settled in #192 (SAT = Communication + Ensure capability + Control monitoring); simulated phishing periodically tests the separate human control (Security Conscious Personnel, LEC Resistance); CIS 14.1 grounds it |
| 3 | 0.79 | saas-security-posture-management · Reporting | not labelled **Reporting**; judge's yes-score 0.79 | rejected | keep unlabelled: SSPM compliance reports/audit trails are control-monitoring outputs, not risk analysis delivered to decision-makers; no carried tag grounds Reporting (RS.CO-*/DE.DP-4 contrived, as in the SWG/SRS drops) |
| 4 | 0.76 | security-information-event-management · Reporting | not labelled **Reporting**; judge's yes-score 0.76 | rejected | keep unlabelled: SIEM compliance reports/dashboards demonstrate compliance and alert analysts; management risk reporting is not attested and no carried tag grounds Reporting (DE.DP-4 sits on Compliance Audit) |
| 5 | 0.73 | saas-security-posture-management · Analysis | not labelled **Analysis**; judge's yes-score 0.73 | rejected | keep unlabelled: SSPM 'security assessment' finds misconfigurations/compliance gaps (Control monitoring, carried); remediation recommendations are Treatment selection; no risk model attested, no tag (CSPM's is ID.RA-5) |
| 6 | 0.71 | saas-security-posture-management · Avoidance | labelled **Avoidance**; judge's yes-score 0.29 | rejected | keep: SSPM enforces data-sharing settings and auto-remediates misconfigurations, re-privatising publicly shared SaaS data (removes contact); rubric §4 Example 1 / §7 CSPM precedent, grounded by CIS 3.3 and ATT&CK M1037 |
| 7 | 0.65 | saas-security-posture-management · Recognition | not labelled **Recognition**; judge's yes-score 0.65 | rejected | keep unlabelled: 'activity monitoring rules' are org-defined policies SSPM enforces (Avoidance/Resistance/Monitoring, carried); item 6 only 'helps organizations detect'; no tool classification attested, no DE.AE tag |
| 8 | 0.64 | security-information-event-management · Threat data | not labelled **Threat data**; judge's yes-score 0.64 | rejected | keep unlabelled: SIEM's retained logs are raw detection/forensic inputs, not the compiled 'internal incident history' of the criteria example (IR/GRC); CIS 8.2/8.10 could ground it, but no decision use is attested (§6.4) |
| 9 | 0.57 | security-information-event-management · Analysis | not labelled **Analysis**; judge's yes-score 0.57 | rejected | keep unlabelled: SIEM 'event correlation and analysis' is detection-time classification of live events (Recognition, carried via DE.AE-3), not risk insight from analysis models; no carried tag grounds Analysis |
| 10 | 0.55 | saas-security-posture-management · Reduce variance probability | not labelled **Reduce variance probability**; judge's yes-score 0.55 | rejected | keep unlabelled: deliberately removed as an I5 assign-to-score (see Avoidance citation); posture enforcement hardens the asset's own config (Avoidance/Resistance, carried). Record the drop in _meta.claim_drops |
| 11 | 0.54 | file-integrity-monitoring · Control monitoring | not labelled **Control monitoring**; judge's yes-score 0.54 | accepted | add vmc_id_control_monitoring (score-neutral partial pair): FIM's baseline comparison of config files/registry detects drifted security-control configuration ('Configuration-drift detection'); PR.DS-6 crosswalks to it |
| 12 | 0.49 | saas-security-posture-management · Treatment selection | not labelled **Treatment selection**; judge's yes-score 0.49 |  |  |
| 13 | 0.49 | security-awareness-training · Reduce variance probability | not labelled **Reduce variance probability**; judge's yes-score 0.49 |  |  |
| 14 | 0.47 | saas-security-posture-management · Identify misalignment | not labelled **Identify misalignment**; judge's yes-score 0.47 |  |  |
| 15 | 0.46 | saas-security-posture-management · Defined expectations | not labelled **Defined expectations**; judge's yes-score 0.46 |  |  |
| 16 | 0.42 | security-information-event-management · Control monitoring | labelled **Control monitoring**; judge's yes-score 0.58 |  |  |
| 17 | 0.36 | saas-security-posture-management · Asset data | not labelled **Asset data**; judge's yes-score 0.36 |  |  |
| 18 | 0.34 | saas-security-posture-management · Controls data | not labelled **Controls data**; judge's yes-score 0.34 |  |  |
| 19 | 0.32 | security-information-event-management · Threat intelligence | not labelled **Threat intelligence**; judge's yes-score 0.32 |  |  |
| 20 | 0.30 | saas-security-posture-management · Event termination | not labelled **Event termination**; judge's yes-score 0.30 |  |  |
| 21 | 0.30 | security-information-event-management · Identify misalignment | not labelled **Identify misalignment**; judge's yes-score 0.30 |  |  |
| 22 | 0.28 | security-awareness-training · Recognition | not labelled **Recognition**; judge's yes-score 0.28 |  |  |
| 23 | 0.27 | file-integrity-monitoring · Recognition | labelled **Recognition**; judge's yes-score 0.73 |  |  |
| 24 | 0.26 | saas-security-posture-management · Resistance | labelled **Resistance**; judge's yes-score 0.74 |  |  |
| 25 | 0.26 | security-information-event-management · Controls data | not labelled **Controls data**; judge's yes-score 0.26 |  |  |
| 26 | 0.24 | file-integrity-monitoring · Identify misalignment | not labelled **Identify misalignment**; judge's yes-score 0.24 |  |  |
| 27 | 0.24 | security-information-event-management · Loss reduction | not labelled **Loss reduction**; judge's yes-score 0.24 |  |  |
| 28 | 0.23 | security-awareness-training · Deterrence | not labelled **Deterrence**; judge's yes-score 0.23 |  |  |
| 29 | 0.22 | security-awareness-training · Defined expectations | not labelled **Defined expectations**; judge's yes-score 0.22 |  |  |
| 30 | 0.19 | file-integrity-monitoring · Reporting | not labelled **Reporting**; judge's yes-score 0.19 |  |  |
| 31 | 0.19 | security-awareness-training · Resistance | not labelled **Resistance**; judge's yes-score 0.19 |  |  |
| 32 | 0.19 | saas-security-posture-management · Implementation | labelled **Implementation**; judge's yes-score 0.81 |  |  |
| 33 | 0.17 | saas-security-posture-management · Deterrence | not labelled **Deterrence**; judge's yes-score 0.17 |  |  |
| 34 | 0.15 | security-awareness-training · Ensure capability | labelled **Ensure capability**; judge's yes-score 0.85 |  |  |
| 35 | 0.15 | file-integrity-monitoring · Controls data | not labelled **Controls data**; judge's yes-score 0.15 |  |  |
| 36 | 0.15 | security-awareness-training · Loss reduction | not labelled **Loss reduction**; judge's yes-score 0.15 |  |  |
| 37 | 0.15 | security-awareness-training · Threat data | not labelled **Threat data**; judge's yes-score 0.15 |  |  |
| 38 | 0.14 | file-integrity-monitoring · Deterrence | not labelled **Deterrence**; judge's yes-score 0.14 |  |  |
| 39 | 0.14 | file-integrity-monitoring · Loss reduction | not labelled **Loss reduction**; judge's yes-score 0.14 |  |  |
| 40 | 0.14 | file-integrity-monitoring · Monitoring | labelled **Monitoring**; judge's yes-score 0.86 |  |  |
| 41 | 0.13 | security-awareness-training · Identify misalignment | not labelled **Identify misalignment**; judge's yes-score 0.13 |  |  |
| 42 | 0.13 | security-information-event-management · Event termination | not labelled **Event termination**; judge's yes-score 0.13 |  |  |
| 43 | 0.12 | file-integrity-monitoring · Analysis | not labelled **Analysis**; judge's yes-score 0.12 |  |  |
| 44 | 0.12 | file-integrity-monitoring · Defined expectations | not labelled **Defined expectations**; judge's yes-score 0.12 |  |  |
| 45 | 0.12 | saas-security-posture-management · Loss reduction | not labelled **Loss reduction**; judge's yes-score 0.12 |  |  |
| 46 | 0.12 | saas-security-posture-management · Threat data | not labelled **Threat data**; judge's yes-score 0.12 |  |  |
| 47 | 0.12 | security-information-event-management · Deterrence | not labelled **Deterrence**; judge's yes-score 0.12 |  |  |
| 48 | 0.12 | security-information-event-management · Treatment selection | not labelled **Treatment selection**; judge's yes-score 0.12 |  |  |
| 49 | 0.11 | file-integrity-monitoring · Threat data | not labelled **Threat data**; judge's yes-score 0.11 |  |  |
| 50 | 0.11 | saas-security-posture-management · Communication | not labelled **Communication**; judge's yes-score 0.11 |  |  |
| 51 | 0.11 | security-awareness-training · Threat intelligence | not labelled **Threat intelligence**; judge's yes-score 0.11 |  |  |
| 52 | 0.11 | security-information-event-management · Communication | not labelled **Communication**; judge's yes-score 0.11 |  |  |
| 53 | 0.10 | file-integrity-monitoring · Asset data | not labelled **Asset data**; judge's yes-score 0.10 |  |  |
| 54 | 0.10 | security-awareness-training · Resilience | not labelled **Resilience**; judge's yes-score 0.10 |  |  |
| 55 | 0.10 | security-information-event-management · Reduce variance probability | not labelled **Reduce variance probability**; judge's yes-score 0.10 |  |  |
| 56 | 0.10 | saas-security-posture-management · Visibility | labelled **Visibility**; judge's yes-score 0.90 |  |  |
| 57 | 0.10 | secure-remote-access · Resistance | labelled **Resistance**; judge's yes-score 0.90 |  |  |
| 58 | 0.09 | file-integrity-monitoring · Reduce variance probability | not labelled **Reduce variance probability**; judge's yes-score 0.09 |  |  |
| 59 | 0.09 | security-awareness-training · Reporting | not labelled **Reporting**; judge's yes-score 0.09 |  |  |
| 60 | 0.09 | security-information-event-management · Resilience | not labelled **Resilience**; judge's yes-score 0.09 |  |  |
| 61 | 0.09 | security-information-event-management · Recognition | labelled **Recognition**; judge's yes-score 0.91 |  |  |
| 62 | 0.08 | secure-remote-access · Implementation | not labelled **Implementation**; judge's yes-score 0.08 |  |  |
| 63 | 0.08 | secure-remote-access · Loss reduction | not labelled **Loss reduction**; judge's yes-score 0.08 |  |  |
| 64 | 0.08 | secure-remote-access · Reduce variance probability | not labelled **Reduce variance probability**; judge's yes-score 0.08 |  |  |
| 65 | 0.08 | security-awareness-training · Avoidance | not labelled **Avoidance**; judge's yes-score 0.08 |  |  |
| 66 | 0.08 | security-awareness-training · Controls data | not labelled **Controls data**; judge's yes-score 0.08 |  |  |
| 67 | 0.08 | security-awareness-training · Implementation | not labelled **Implementation**; judge's yes-score 0.08 |  |  |
| 68 | 0.08 | security-information-event-management · Asset data | not labelled **Asset data**; judge's yes-score 0.08 |  |  |
| 69 | 0.08 | security-information-event-management · Resistance | not labelled **Resistance**; judge's yes-score 0.08 |  |  |
| 70 | 0.08 | saas-security-posture-management · Monitoring | labelled **Monitoring**; judge's yes-score 0.92 |  |  |
| 71 | 0.08 | security-awareness-training · Communication | labelled **Communication**; judge's yes-score 0.92 |  |  |
| 72 | 0.07 | file-integrity-monitoring · Communication | not labelled **Communication**; judge's yes-score 0.07 |  |  |
| 73 | 0.07 | file-integrity-monitoring · Resilience | not labelled **Resilience**; judge's yes-score 0.07 |  |  |
| 74 | 0.07 | file-integrity-monitoring · Resistance | not labelled **Resistance**; judge's yes-score 0.07 |  |  |
| 75 | 0.07 | saas-security-posture-management · Reduce change frequency | not labelled **Reduce change frequency**; judge's yes-score 0.07 |  |  |
| 76 | 0.07 | saas-security-posture-management · Resilience | not labelled **Resilience**; judge's yes-score 0.07 |  |  |
| 77 | 0.07 | saas-security-posture-management · Threat intelligence | not labelled **Threat intelligence**; judge's yes-score 0.07 |  |  |
| 78 | 0.07 | secure-remote-access · Deterrence | not labelled **Deterrence**; judge's yes-score 0.07 |  |  |
| 79 | 0.07 | secure-remote-access · Resilience | not labelled **Resilience**; judge's yes-score 0.07 |  |  |
| 80 | 0.07 | secure-remote-access · Visibility | not labelled **Visibility**; judge's yes-score 0.07 |  |  |
| 81 | 0.07 | security-awareness-training · Incentives | not labelled **Incentives**; judge's yes-score 0.07 |  |  |
| 82 | 0.07 | security-information-event-management · Defined expectations | not labelled **Defined expectations**; judge's yes-score 0.07 |  |  |
| 83 | 0.06 | file-integrity-monitoring · Visibility | labelled **Visibility**; judge's yes-score 0.94 |  |  |
| 84 | 0.06 | file-integrity-monitoring · Event termination | not labelled **Event termination**; judge's yes-score 0.06 |  |  |
| 85 | 0.06 | saas-security-posture-management · Ensure capability | not labelled **Ensure capability**; judge's yes-score 0.06 |  |  |
| 86 | 0.06 | secure-remote-access · Communication | not labelled **Communication**; judge's yes-score 0.06 |  |  |
| 87 | 0.06 | secure-remote-access · Defined expectations | not labelled **Defined expectations**; judge's yes-score 0.06 |  |  |
| 88 | 0.06 | security-awareness-training · Analysis | not labelled **Analysis**; judge's yes-score 0.06 |  |  |
| 89 | 0.06 | security-awareness-training · Monitoring | not labelled **Monitoring**; judge's yes-score 0.06 |  |  |
| 90 | 0.06 | security-awareness-training · Visibility | not labelled **Visibility**; judge's yes-score 0.06 |  |  |
| 91 | 0.06 | security-information-event-management · Avoidance | not labelled **Avoidance**; judge's yes-score 0.06 |  |  |
| 92 | 0.06 | security-information-event-management · Ensure capability | not labelled **Ensure capability**; judge's yes-score 0.06 |  |  |
| 93 | 0.06 | security-information-event-management · Implementation | not labelled **Implementation**; judge's yes-score 0.06 |  |  |
| 94 | 0.05 | saas-security-posture-management · Control monitoring | labelled **Control monitoring**; judge's yes-score 0.95 |  |  |
| 95 | 0.05 | security-information-event-management · Visibility | labelled **Visibility**; judge's yes-score 0.95 |  |  |
| 96 | 0.05 | file-integrity-monitoring · Implementation | not labelled **Implementation**; judge's yes-score 0.05 |  |  |
| 97 | 0.05 | file-integrity-monitoring · Reduce change frequency | not labelled **Reduce change frequency**; judge's yes-score 0.05 |  |  |
| 98 | 0.05 | file-integrity-monitoring · Treatment selection | not labelled **Treatment selection**; judge's yes-score 0.05 |  |  |
| 99 | 0.05 | saas-security-posture-management · Incentives | not labelled **Incentives**; judge's yes-score 0.05 |  |  |
| 100 | 0.05 | secure-remote-access · Controls data | not labelled **Controls data**; judge's yes-score 0.05 |  |  |
| 101 | 0.05 | secure-remote-access · Event termination | not labelled **Event termination**; judge's yes-score 0.05 |  |  |
| 102 | 0.05 | security-awareness-training · Event termination | not labelled **Event termination**; judge's yes-score 0.05 |  |  |
| 103 | 0.05 | security-awareness-training · Treatment selection | not labelled **Treatment selection**; judge's yes-score 0.05 |  |  |
| 104 | 0.04 | file-integrity-monitoring · Avoidance | not labelled **Avoidance**; judge's yes-score 0.04 |  |  |
| 105 | 0.04 | file-integrity-monitoring · Ensure capability | not labelled **Ensure capability**; judge's yes-score 0.04 |  |  |
| 106 | 0.04 | file-integrity-monitoring · Threat intelligence | not labelled **Threat intelligence**; judge's yes-score 0.04 |  |  |
| 107 | 0.04 | secure-remote-access · Asset data | not labelled **Asset data**; judge's yes-score 0.04 |  |  |
| 108 | 0.04 | secure-remote-access · Control monitoring | not labelled **Control monitoring**; judge's yes-score 0.04 |  |  |
| 109 | 0.04 | secure-remote-access · Ensure capability | not labelled **Ensure capability**; judge's yes-score 0.04 |  |  |
| 110 | 0.04 | secure-remote-access · Monitoring | not labelled **Monitoring**; judge's yes-score 0.04 |  |  |
| 111 | 0.04 | secure-remote-access · Recognition | not labelled **Recognition**; judge's yes-score 0.04 |  |  |
| 112 | 0.04 | security-awareness-training · Asset data | not labelled **Asset data**; judge's yes-score 0.04 |  |  |
| 113 | 0.03 | security-information-event-management · Monitoring | labelled **Monitoring**; judge's yes-score 0.97 |  |  |
| 114 | 0.03 | file-integrity-monitoring · Incentives | not labelled **Incentives**; judge's yes-score 0.03 |  |  |
| 115 | 0.03 | secure-remote-access · Analysis | not labelled **Analysis**; judge's yes-score 0.03 |  |  |
| 116 | 0.03 | secure-remote-access · Identify misalignment | not labelled **Identify misalignment**; judge's yes-score 0.03 |  |  |
| 117 | 0.03 | secure-remote-access · Incentives | not labelled **Incentives**; judge's yes-score 0.03 |  |  |
| 118 | 0.03 | secure-remote-access · Reduce change frequency | not labelled **Reduce change frequency**; judge's yes-score 0.03 |  |  |
| 119 | 0.03 | secure-remote-access · Reporting | not labelled **Reporting**; judge's yes-score 0.03 |  |  |
| 120 | 0.03 | secure-remote-access · Threat data | not labelled **Threat data**; judge's yes-score 0.03 |  |  |
| 121 | 0.03 | secure-remote-access · Threat intelligence | not labelled **Threat intelligence**; judge's yes-score 0.03 |  |  |
| 122 | 0.03 | secure-remote-access · Treatment selection | not labelled **Treatment selection**; judge's yes-score 0.03 |  |  |
| 123 | 0.03 | security-awareness-training · Reduce change frequency | not labelled **Reduce change frequency**; judge's yes-score 0.03 |  |  |
| 124 | 0.03 | security-information-event-management · Incentives | not labelled **Incentives**; judge's yes-score 0.03 |  |  |
| 125 | 0.03 | security-information-event-management · Reduce change frequency | not labelled **Reduce change frequency**; judge's yes-score 0.03 |  |  |

### Scenario overlap

| # | Score | Subject | Finding | Disposition | Reason |
|---|---|---|---|---|---|
| 1 | 1.00 | agri-coop-bec-fraud ↔ bec-fraud-financial | from agri-coop-bec-fraud: judge's top score is **Business Email Compromise — Financial Sector Wire Fraud** (0.91); distinct-score 0.00; also **Professional Services Payroll BEC — Direct-Deposit Fraud** (0.09); reverse: bec-fraud-financial's score for this pair 0.61: merge, sharpen, or keep | see Scenario overlap #1 |  |
| 2 | 1.00 | competitor-trade-secret-recruit ↔ ip-theft-by-competitor | from competitor-trade-secret-recruit: judge's top score is **Manufacturing Competitor-Driven IP Theft — Trade Secret Exfiltration** (0.80); distinct-score 0.00; also **Insider IP Theft — Manufacturing and Semiconductor** (0.19); reverse: ip-theft-by-competitor's score for this pair 0.39: merge, sharpen, or keep | see Scenario overlap #2 |  |
| 3 | 1.00 | credential-stuffing-consumer-portal ↔ hospitality-loyalty-account-takeover | from credential-stuffing-consumer-portal: judge's top score is **Hospitality Loyalty Program Account Takeover — Points Fraud** (1.00); distinct-score 0.00; reverse: hospitality-loyalty-account-takeover's score for this pair 0.98: merge, sharpen, or keep | see Scenario overlap #3 |  |
| 4 | 1.00 | ddos-financial-seasonal-peak ↔ retail-ecommerce-checkout-ddos | from ddos-financial-seasonal-peak: judge's top score is **Retail E-Commerce Checkout DDoS — Revenue-Disruption Attack During Peak Season** (0.92); distinct-score 0.00; also **Hospitality Booking Platform DDoS — Peak-Season Revenue Disruption** (0.08); reverse: retail-ecommerce-checkout-ddos's score for this pair 0.60: merge, sharpen, or keep | see Scenario overlap #4 |  |
| 5 | 1.00 | education-student-records-insider ↔ hospitality-guest-data-insider | from education-student-records-insider: judge's top score is **Hospitality Guest Data Exfiltration — Malicious Insider PMS Breach** (0.49); distinct-score 0.00; also **Insider Data Theft — Financial Records and PII Exfiltration** (0.46): merge, sharpen, or keep | see Scenario overlap #5 |  |
| 6 | 1.00 | financial-transaction-tampering ↔ manufacturing-billing-fraud | from financial-transaction-tampering: judge's top score is **Manufacturing Insider Billing Fraud — Fictitious Vendor AP Manipulation** (0.94); distinct-score 0.00; reverse: manufacturing-billing-fraud's score for this pair 0.98: merge, sharpen, or keep | see Scenario overlap #6 |  |
| 7 | 1.00 | food-recall-data-tampering ↔ healthcare-record-alteration | from food-recall-data-tampering: judge's top score is **Healthcare EHR Record Alteration — Malicious Insider Data Integrity Tampering** (0.69); distinct-score 0.00; also **Government Records Tampering — Malicious Insider Data Corruption** (0.28); reverse: healthcare-record-alteration's score for this pair 0.49: merge, sharpen, or keep | see Scenario overlap #7 |  |
| 8 | 1.00 | hospitality-booking-ddos-peak-season ↔ retail-ecommerce-checkout-ddos | from hospitality-booking-ddos-peak-season: judge's top score is **Retail E-Commerce Checkout DDoS — Revenue-Disruption Attack During Peak Season** (0.94); distinct-score 0.00; also **DDoS at Financial Seasonal Peak — Payment and Banking** (0.06): merge, sharpen, or keep | see Scenario overlap #9 |  |
| 9 | 0.99 | accidental-insider-exposure ↔ s3-misconfiguration-data-exposure | from accidental-insider-exposure: judge's top score is **Cloud Storage Misconfiguration — Mass S3/Blob Data Exposure** (0.98); distinct-score 0.01; reverse: s3-misconfiguration-data-exposure's score for this pair 0.83: merge, sharpen, or keep | rejected | Different threat: non-malicious insider error with no adversary (misdelivery, wrong attachment, mis-set share) vs cybercriminals discovering and mass-exfiltrating a public bucket; actor, vulnerability (0.2 vs 0.7) differ |
| 10 | 0.99 | bec-fraud-financial ↔ professional-payroll-bec | from professional-payroll-bec: judge's top score is **Business Email Compromise — Financial Sector Wire Fraud** (0.87); distinct-score 0.01; also **Agricultural Cooperative BEC Fraud — Grain-Transfer and Commodity Payment Diversion** (0.12): merge, sharpen, or keep | rejected | Justified variant, pilot sharpen applied: the vendor-wire clause left payroll-bec, so the entries split by method and loss path (HR impersonation diverting employee direct deposits vs vendor wire redirect) |
| 11 | 0.99 | ddos-financial-seasonal-peak ↔ gov-citizen-portal-ddos | from gov-citizen-portal-ddos: judge's top score is **DDoS at Financial Seasonal Peak — Payment and Banking** (0.58); distinct-score 0.01; also **Retail E-Commerce Checkout DDoS — Revenue-Disruption Attack During Peak Season** (0.20): merge, sharpen, or keep | rejected | Different threat and effect: cybercriminals timing outages to peak transaction revenue vs hacktivists disrupting public services for political signalling with response/productivity losses; disjoint industries |
| 12 | 0.99 | ddos-financial-seasonal-peak ↔ telecom-ddos-core-network | from telecom-ddos-core-network: judge's top score is **DDoS at Financial Seasonal Peak — Payment and Banking** (0.53); distinct-score 0.01; also **Retail E-Commerce Checkout DDoS — Revenue-Disruption Attack During Peak Season** (0.18): merge, sharpen, or keep | rejected | Different asset and loss path: payment/banking portals with transaction revenue foregone vs a carrier's core network (routing, DNS, signalling) with wholesale SLA penalties to downstream operators; disjoint industries |
| 13 | 0.99 | energy-settlement-platform-tampering-offtaker-liability ↔ pipeline-nomination-scada-curtailment-shipper-penalty | from energy-settlement-platform-tampering-offtaker-liability: judge's top score is **Pipeline / Midstream Nomination-SCADA Disruption — Shipper Revenue Penalties** (0.72); distinct-score 0.01; also **Tolling / Contract-Manufacturing Plant Ransomware — Customer Revenue Liability** (0.17); reverse: pipeline-nomination-scada-curtailment-shipper-penalty's score for this pair 0.36: merge, sharpen, or keep | rejected | Different effect (pilot reason reused): the settlement attack leaves energy delivered but unsettled; the pipeline SCADA/nomination outage physically curtails throughput (ot_availability); only the asset is shared |
| 14 | 0.99 | gov-employee-insider-leak ↔ insider-data-theft-financial | from gov-employee-insider-leak: judge's top score is **Insider Data Theft — Financial Records and PII Exfiltration** (0.69); distinct-score 0.01; also **Education Student Records Insider Misuse — FERPA Breach** (0.20): merge, sharpen, or keep | rejected | Justified sector variant: classified/personnel/law-enforcement records leaked to media or foreign actors with IG proceedings vs financial PII sold or used for fraud; disjoint industries, own IRIS anchors, TEF 0.34 vs 1.5 |
| 15 | 0.99 | gov-records-tampering ↔ healthcare-record-alteration | from gov-records-tampering: judge's top score is **Healthcare EHR Record Alteration — Malicious Insider Data Integrity Tampering** (0.73); distinct-score 0.01; also **Food Safety Record Tampering — Malicious Insider ERP/QA Data Manipulation** (0.24): merge, sharpen, or keep | rejected | Justified sector variant: official records (voter rolls, court dockets) corrupted with reconstruction, IG and citizen litigation vs EHR alteration with patient harm, malpractice and HIPAA penalties; disjoint industries |
| 16 | 0.99 | grid-protective-relay-manipulation ↔ safety-system-bypass | from safety-system-bypass: judge's top score is **Power-Grid Protective Relay / IEC 61850 Manipulation**, **Unauthorized PLC Logic Modification** (0.35, tied); distinct-score 0.01; also **Pipeline SCADA Pressure/Flow Integrity Attack** (0.26); reverse: grid-protective-relay-manipulation's score for this pair 0.43: merge, sharpen, or keep | rejected | Different method, effect and sector: protocol exploitation of grid relays so faults are not cleared (equipment damage) vs engineering-workstation reprogramming of a process SIS so an upset ends in explosion/toxic release |
| 17 | 0.99 | safety-system-bypass ↔ unauthorized-plc-modification | from safety-system-bypass: judge's top score is **Power-Grid Protective Relay / IEC 61850 Manipulation**, **Unauthorized PLC Logic Modification** (0.35, tied); distinct-score 0.01; also **Pipeline SCADA Pressure/Flow Integrity Attack** (0.26); reverse: unauthorized-plc-modification's score for this pair 0.82: merge, sharpen, or keep | rejected | Different asset and effect: SIS/ESD safety functions defeated so a separate process upset goes unprotected vs basic-process-control PLC logic altered to plant latent failure conditions; same actor, distinct target layer |
| 18 | 0.99 | telecom-subscriber-data-breach ↔ web-app-exploitation | from telecom-subscriber-data-breach: judge's top score is **Web Application Exploitation — SQLi / API Abuse to Exfiltration** (0.93); distinct-score 0.01; reverse: web-app-exploitation's score for this pair 0.61: merge, sharpen, or keep | rejected | Justified sector variant: carrier subscriber data (call records, device IDs) with FCC/FTC tail, carrier sub-sectors and Information calibration at TEF 0.34, vs the cross-sector generic web/API exploitation entry |
| 19 | 0.97 | ddos-financial-seasonal-peak ↔ higher-ed-insider-ddos | from higher-ed-insider-ddos: judge's top score is **DDoS at Financial Seasonal Peak — Payment and Banking** (0.38); distinct-score 0.03; also **Retail E-Commerce Checkout DDoS — Revenue-Disruption Attack During Peak Season** (0.36): merge, sharpen, or keep | rejected | Different threat and sector: a student with a grudge renting a botnet against campus systems (insider_malicious, education) vs financially motivated cybercriminals timing outages to payment peaks (financial, revenue) |
| 20 | 0.97 | denial-of-control ↔ grid-protective-relay-manipulation | from denial-of-control: judge's top score is **Power-Grid Protective Relay / IEC 61850 Manipulation** (0.42); distinct-score 0.03; also **Pipeline SCADA Pressure/Flow Integrity Attack** (0.26): merge, sharpen, or keep | rejected | Justified variant, pilot sharpen applied: the relay entry is scoped to protection defeat (faults not cleared, equipment damage, replacement 0.4) and hands breaker-open load loss to denial-of-control (productivity 0.55) |
| 21 | 0.97 | financial-call-center-social-eng ↔ telecom-sim-swap-fraud | from financial-call-center-social-eng: judge's top score is **Telecom SIM-Swap Fraud — Carrier-Liability Account Takeover** (0.68); distinct-score 0.03; also **Business Email Compromise — Financial Sector Wire Fraud** (0.17): merge, sharpen, or keep | rejected | Different attacked org, method and loss path: vishing a bank's agents into authorising transfers (bank bears reversals) vs deceiving a carrier's CSRs into a SIM port-out defeating subscribers' MFA (carrier liability) |
| 22 | 0.97 | hospitality-loyalty-account-takeover ↔ telecom-sim-swap-fraud | from telecom-sim-swap-fraud: judge's top score is **Hospitality Loyalty Program Account Takeover — Points Fraud** (0.60); distinct-score 0.03; also **Financial Institution Call Center Social Engineering — Vishing and Pretexting** (0.23): merge, sharpen, or keep | rejected | Different attacked org and loss path: loyalty accounts drained via stuffing, phishing or SIM-swap (redemption write-offs at the hotel/airline) vs the carrier's own liability for the port-out; SIM-swap is one route of 3 |
| 23 | 0.96 | financial-transaction-tampering ↔ logistics-tms-data-tampering | from logistics-tms-data-tampering: judge's top score is **Financial Institution Transaction Tampering — Insider Unauthorized Value Transfer** (0.33); distinct-score 0.04; also **Energy Utility Billing System Tampering — AMI/Meter Data Fraud** (0.20): merge, sharpen, or keep | rejected | Different threat, asset and sector: privileged bank insider diverting funds (insider_misuse, cash_or_equivalent) vs cybercriminals with stolen TMS credentials falsifying manifests to divert cargo (data_tampering) |
| 24 | 0.96 | healthcare-staff-credential-phish ↔ ransomware-on-ehr | from healthcare-staff-credential-phish: judge's top score is **Ransomware on EHR Cluster** (0.58); distinct-score 0.04; also **Phishing-Derived Active Directory Compromise Leading to Ransomware** (0.11): merge, sharpen, or keep | rejected | Different effect and asset despite the shared phishing entry: stolen staff credentials used to exfiltrate PHI or run BEC (social_engineering, data) vs ransomware encrypting the EHR cluster, clinical disruption dominant |
| 25 | 0.93 | energy-billing-system-tamper ↔ energy-settlement-platform-tampering-offtaker-liability | from energy-billing-system-tamper: judge's top score is **Energy-Trading Settlement Platform Tampering — Offtaker / PPA Counterparty Revenue Liability** (0.28); distinct-score 0.07; also **Healthcare EHR Record Alteration — Malicious Insider Data Integrity Tampering** (0.16): merge, sharpen, or keep | rejected | Different threat, asset and loss path: cybercriminals manipulating AMI/meter data for under-billing (the utility's own revenue) vs a state actor corrupting wholesale/PPA settlement so the org owes make-whole liability |
| 26 | 0.27 | generative-ai-prompt-injection ↔ web-app-exploitation | from generative-ai-prompt-injection: judge's top score is 'distinct' (0.73); closest: **Web Application Exploitation — SQLi / API Abuse to Exfiltration** (0.10); also **Open Source Package Registry Compromise** (0.05): merge, sharpen, or keep |  |  |

### Not checked (not published)

Deprecated (or otherwise not published, as of the working tree) and changed since the merge base; never judged, under any check.

- `chemical-process-safety-attack`
- `data-breach-notification-regulatory-tail`
- `ddos-extortion-financial`

## Before / after

**Scenario label audit:** 13 left the queue (fixed, outranked or renamed), 2 still flagged, 13 new.

- left the queue: `scenario-labels:accidental-insider-exposure:threat_event_type`
- left the queue: `scenario-labels:bec-fraud-financial:asset_class`
- left the queue: `scenario-labels:competitor-trade-secret-recruit:asset_class`
- left the queue: `scenario-labels:credential-stuffing-consumer-portal:threat_event_type`
- left the queue: `scenario-labels:education-student-records-insider:asset_class`
- left the queue: `scenario-labels:energy-billing-system-tamper:asset_class`
- left the queue: `scenario-labels:energy-settlement-platform-tampering-offtaker-liability:threat_actor_type`
- left the queue: `scenario-labels:financial-call-center-social-eng:asset_class`
- left the queue: `scenario-labels:financial-transaction-tampering:threat_event_type`
- left the queue: `scenario-labels:gov-citizen-portal-ddos:asset_class`
- left the queue: `scenario-labels:gov-employee-insider-leak:asset_class`
- left the queue: `scenario-labels:gov-records-tampering:threat_event_type`
- left the queue: `scenario-labels:healthcare-record-alteration:threat_event_type`
- still flagged: `scenario-labels:cloud-account-takeover:threat_event_type (now #1)`
- still flagged: `scenario-labels:food-recall-data-tampering:asset_class (now #2)`
- new: `scenario-labels:hmi-credential-compromise:threat_event_type`
- new: `scenario-labels:hospitality-pos-card-skimming:asset_class`
- new: `scenario-labels:manufacturing-billing-fraud:asset_class`
- new: `scenario-labels:ot-network-scanning-reconnaissance:threat_event_type`
- new: `scenario-labels:unauthorized-plc-modification:threat_event_type`
- new: `scenario-labels:edge-device-orb-foothold:threat_actor_type`
- new: `scenario-labels:food-recall-data-tampering:threat_event_type`
- new: `scenario-labels:grid-protective-relay-manipulation:threat_event_type`
- new: `scenario-labels:it-ot-bridge-compromise:threat_event_type`
- new: `scenario-labels:law-enforcement-records-extortion-breach:threat_event_type`
- new: `scenario-labels:ransomware-on-historian:threat_event_type`
- new: `scenario-labels:field-instrument-spoofing:threat_actor_type`
- new: `scenario-labels:hospitality-pos-card-skimming:threat_event_type`

**Control function audit:** 4 left the queue (fixed, outranked or renamed), 11 still flagged, 4 new.

- left the queue: `control-functions:security-information-event-management:lec_det_visibility`
- left the queue: `control-functions:file-integrity-monitoring:lec_det_visibility`
- left the queue: `control-functions:saas-security-posture-management:lec_det_visibility`
- left the queue: `control-functions:secure-remote-access:lec_prev_resistance`
- still flagged: `control-functions:cloud-security-posture-management:lec_det_monitoring (now Possibly missing #1)`
- still flagged: `control-functions:patch-management:vmc_prev_reduce_variance_prob (now Possibly missing #2)`
- still flagged: `control-functions:security-configuration-assessment:dsc_prev_sa_reporting (now Possibly missing #3)`
- still flagged: `control-functions:email-security-protection:lec_det_recognition (now Possibly missing #4)`
- still flagged: `control-functions:host-intrusion-detection-prevention:lec_resp_resilience (now Possibly wrong #1)`
- still flagged: `control-functions:third-party-risk-management:dsc_prev_sa_data_threat (now Possibly wrong #2)`
- still flagged: `control-functions:network-detection-response:lec_resp_resilience (now Possibly wrong #3)`
- still flagged: `control-functions:endpoint-detection-response:lec_resp_resilience (now Possibly wrong #4)`
- still flagged: `control-functions:secure-remote-access:lec_prev_avoidance (now Possibly wrong #7)`
- still flagged: `control-functions:third-party-risk-management:dsc_prev_incentives (now Possibly wrong #6)`
- still flagged: `control-functions:user-access-control:vmc_corr_implementation (now Possibly wrong #5)`
- new: `control-functions:external-attack-surface-management:lec_det_visibility`
- new: `control-functions:secure-web-gateway:lec_det_recognition`
- new: `control-functions:application-performance-monitoring:lec_det_recognition`
- new: `control-functions:cloud-security-posture-management:dsc_prev_sa_reporting`

**Scenario overlap:** 8 left the queue (fixed, outranked or renamed), 7 still flagged, 8 new.

- left the queue: `overlap:bec-fraud-financial:professional-payroll-bec`
- left the queue: `overlap:chemical-process-safety-attack:safety-system-bypass`
- left the queue: `overlap:crop-science-ip-exfiltration:ip-theft-by-competitor`
- left the queue: `overlap:data-breach-notification-regulatory-tail:web-app-exploitation`
- left the queue: `overlap:ddos-extortion-financial:ddos-financial-seasonal-peak`
- left the queue: `overlap:denial-of-control:grid-protective-relay-manipulation`
- left the queue: `overlap:energy-settlement-platform-tampering-offtaker-liability:pipeline-nomination-scada-curtailment-shipper-penalty`
- left the queue: `overlap:food-cold-chain-ransomware:tolling-plant-ransomware-customer-liability`
- still flagged: `overlap:agri-coop-bec-fraud:bec-fraud-financial (now #1)`
- still flagged: `overlap:competitor-trade-secret-recruit:ip-theft-by-competitor (now #2)`
- still flagged: `overlap:credential-stuffing-consumer-portal:hospitality-loyalty-account-takeover (now #3)`
- still flagged: `overlap:ddos-financial-seasonal-peak:retail-ecommerce-checkout-ddos (now #4)`
- still flagged: `overlap:education-student-records-insider:hospitality-guest-data-insider (now #5)`
- still flagged: `overlap:financial-transaction-tampering:manufacturing-billing-fraud (now #6)`
- still flagged: `overlap:hmi-credential-compromise:oem-remote-maintenance-abuse (now #8)`
- new: `overlap:food-recall-data-tampering:healthcare-record-alteration`
- new: `overlap:hospitality-booking-ddos-peak-season:retail-ecommerce-checkout-ddos`
- new: `overlap:hospitality-guest-data-insider:insider-data-theft-financial`
- new: `overlap:hospitality-pos-card-skimming:retail-pos-card-skimming`
- new: `overlap:insider-ip-theft-manufacturing:ip-theft-by-competitor`
- new: `overlap:it-ot-bridge-compromise:ransomware-on-historian`
- new: `overlap:k12-edtech-vendor-breach:third-party-processor-breach`
- new: `overlap:nation-state-ics-supply-chain:solarwinds-class-supply-chain`
