# Scenario and control labelling conventions

The canonical option wording for library labels lives in `data/curation/criteria.json`. The dev-only curation
checker (`scripts/curation_check/`) asks its judge exactly these questions, so changing that file changes what the
audit measures. Changes need methodology review.

## Control functions: map per behaviour

Follows `docs/reference/control-function-decomposition-rubric.md` ("Classification is per behavior").
- Map each behaviour the control itself performs.
- A policy or standard that only *requires* a safeguard performs **Defined expectations** (plus **Communication** if
  it conveys the requirement to people).
- It performs the safeguard's own function only when the control itself performs or *technically* enforces it,
  meaning the system blocks the non-compliant action. Enforcement by sanctions is **Incentives**. Requiring or
  listing mechanisms is not performing them.
- A mandated safeguard that is its own library control keeps its own function there. Crediting it again on the
  policy would count it twice.
- *Correct misaligned decisions* (`dsc_corr_misaligned`) is virtual and is never offered.

## Threat event type: precedence (owner decision, 2026-09-28)

The enum mixes mechanisms (ransomware, malware, supply chain), effects (data disclosure, the OT types) and one actor
class (insider misuse), so a scenario can fit several values. Pick by this precedence:
1. **OT effect first.** If an OT or safety system is the target and the scenario describes an OT effect, choose the
   OT type whatever the mechanism:
   - `ot_safety_tampering` when safety functions are defeated or altered;
   - otherwise, by the primary loss effect: `ot_availability` when the process stops or control or view is lost,
     `ot_integrity` when the process keeps running in a manipulated state or the operator's view is falsified.

   If no OT effect is described, continue.
2. **Mechanism** (not ranked; the option texts separate them): `ransomware`, `supply_chain`, `social_engineering`,
   `physical_tampering`, `denial_of_service` or `malware`.
3. **Insider misuse,** if no mechanism fits and a malicious insider abused access they already held. Accidental
   exposure is not misuse.
4. **Otherwise** `data_disclosure` or `data_tampering` if the effect is on data; if none fits, `miscellaneous`.

Existing library entries that disagree are expected findings for the next curation campaign, not errors in this
document. An accepted relabel never re-applies the new type's default share profile: the entry's loss-form profile is
re-judged per `docs/reference/loss-form-share-rubric.md`, not copied from the type default.

## Placements settled in #192 (methodology review 2026-09-29)

`SUB_FUNCTION_DESCRIPTIONS` (`src/idraa/models/enums.py`), the control library and `criteria.json` now agree on these.
Every #192 item is decided; none remains open. Two placements are kept at low confidence (their contested wording
stays out of `criteria.json`, so the audit keeps surfacing them).

| Topic | Placement | Seed | Criteria |
|---|---|---|---|
| Network segmentation | Avoidance (prevents reaching the interface) | Avoidance | example, anchored to Network Segmentation |
| Avoidance scope | reduces contact frequency: removes exposure or blocks reaching the interface | NAC, SWG, email security, firewall: Avoidance | definition |
| Restore from backup | Resilience (restores operations) | Data Backup and Recovery: Resilience | "Tested restore from backups" |
| Remediation SLAs | Defined expectations (requiring is not performing) | none | none |
| IR retainer | Event termination (retained responders contain the event) — low confidence; alternative Loss reduction, which would need a cited currency value | Incident Response: Event termination | kept out |
| Threat-modelling workshops | Analysis (+ Threat data), DSC | Threat Modeling | example |
| SAST, DAST, dependency scanning | Control monitoring when the scan reports (as SAST/DAST are seeded); a scan or test that blocks the merge is Reduce variance probability (gating vs reporting; A8 uses the same hinge) — low confidence | SAST, DAST: Control monitoring | kept out |
| Policy-compliance scans | Control monitoring (drifted configurations) | Security Configuration Assessment | example |
| Audit | of decisions, behaviour or policy adherence (non-compliance): Identify misalignment; of control gaps and configurations: Control monitoring | Compliance Audit: Control monitoring + Reporting (its policy-adherence review is Identify misalignment, not claimable: no genuine grounding tag; score-neutral) | "Periodic review of management decisions…" |
| Security training | Ensure capability (skills); policy induction is Communication | Security Awareness and Training: Communication + Ensure capability + Control monitoring | example |
| Audit results, pen-test findings | Control monitoring outputs; Controls data only when compiled into a decision-grade record (GRC register, scorecard) | Penetration Testing: Control monitoring | kept out |
| Mandatory code review, IaC review gates | Reduce variance probability | none (Secure Coding Practices only lists reviews) | "Mandatory peer code review" (no library control) |
| Secure build baselines, locked-down configurations | Hardening is Resistance / Avoidance; only golden-image pipelines (variance probability) or technical change-locks (change frequency) are VMC | Hardened OS / Cloud / SaaS: Resistance + Avoidance | kept out |
| Risk-based patch prioritisation | Treatment selection ("Treatment Selection and Prioritization", §4.3.1) | none; whether Patch Management performs it is low confidence | kept out |

**Patching and scanning (per behaviour).** The asset's own resistance (its patch level and hardened configuration) is
the LEC control. A newly disclosed CVE is variance in it:
- finding the CVE (vulnerability scanning, penetration testing, SAST/DAST) is Control monitoring;
- deploying the fix is Implementation;
- the patched state is Resistance (rubric §4 Example 3).

A control that does both, such as Patch Management, carries both. The channels are distinct in the engine (effect level
vs reliability via κ), but not independent in meaning: the Resistance assignment's authored reliability and the VMC
pair's uplift both reflect patch cadence. Accepted as the seed's status quo (#437 T1); revisit if Patch Management's r0
is ever re-anchored on patch-latency data.

## Asset class `people` (owner decision 2026-09-29, #192)

`people` means the persons themselves as the asset at risk (e.g. their safety, health or personal security). Staff who
are deceived, recruited or who misuse access are the threat vector or actor, not the asset, so label the asset actually
lost (data, cash_or_equivalent, …). Employee records are `data`. When persons are harmed because a safety function was
defeated, the targeted asset (`safety_systems`) is the label.

The six former `people` entries were relabelled: education-student-records-insider, gov-employee-insider-leak,
healthcare-staff-credential-phish and competitor-trade-secret-recruit → `data`; financial-call-center-social-eng and
telecom-sim-swap-fraud → `cash_or_equivalent` (telecom at low confidence: its only calibrated loss form is the
transferred funds; `business_process_cost` is the alternative if a loss-form re-judgement leaves process remediation as
the primary loss). That leaves none, and the `people ≥ 1` coverage floor was removed because only mislabels satisfied
it. `AssetClass.PEOPLE` is allowlisted in `test_library_taxonomy_coverage.py` (zero published entries by design).
