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
2. **Mechanism:** `ransomware`, `supply_chain`, `social_engineering`, `physical_tampering`, `denial_of_service`
   (flooding or resource exhaustion; not sabotage by an insider) or `malware`.
3. **Insider misuse,** if no mechanism fits and a malicious insider abused access they already held. Accidental
   exposure is not misuse.
4. **Otherwise** `data_disclosure` or `data_tampering` if the effect is on data; if none fits, `miscellaneous`.

Existing library entries that disagree are expected findings for the next curation campaign, not errors in this
document. An accepted relabel never re-applies the new type's default share profile: the entry's loss-form profile is
re-judged per `docs/reference/loss-form-share-rubric.md`, not copied from the type default.

## Where Idraa's enum text and the seed disagree

`SUB_FUNCTION_DESCRIPTIONS` (in `src/idraa/models/enums.py`) and the control library disagree on these placements.
All are tracked in a follow-up issue.

**Decided in `criteria.json`.** These follow the decomposition rubric; the enum text still needs aligning.

| Topic | Enum text says | Seed labels | Criteria follow |
|---|---|---|---|
| Network segmentation | Resistance example | Avoidance | Avoidance: preventing a party from reaching the asset's interface |
| Avoidance scope | "remove the opportunity entirely" | Avoidance also for limiting contact (NAC, SWG, email security, firewall) | contact-frequency definition |
| Restore from backup | Loss reduction example | Data Backup and Recovery: Resilience | Resilience ("restores operations") |
| Remediation SLAs | Implementation example | (no seed control) | Defined expectations: requiring remediation is not performing it |

**Open.** Examples are left out of `criteria.json` until reconciled, so the audit surfaces the conflict.

| Topic | Enum text says | Seed labels |
|---|---|---|
| IR retainer | Loss reduction example | Incident Response: Event termination only |
| Threat-modelling workshops | Threat intelligence example | Threat Modeling: analysis + threat data (DSC) |
| Pre-merge SAST | Reduce variance probability example | SAST: Control monitoring |
| Audit reviews, policy-compliance scans | Identify misalignment examples | Compliance Audit, Security Configuration Assessment: no Identify misalignment |
| Security training programme | Ensure capability example | Security Awareness and Training: Communication + Control monitoring |
| Audit results, pen-test findings | Controls data examples | no seed control carries Controls data |

Also for the reconciliation: the patching and vulnerability-scanning examples treat asset CVEs as variance
management (VMC). Rubric §4 Example 3 routes patching an asset CVE to Resistance, and the seed carries both.
