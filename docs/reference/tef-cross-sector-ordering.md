# TEF cross-sector differentiation — frequency-ordering rationale

Companion to the TEF de-templating milestone. This differentiation exercise
was originally performed under the (since-reversed) TEF PERT→lognormal
representation, where the lognormal fit drops the PERT `mode` and the
distinctness key was `(low, high)` (the p5/p95 the fit consumes). 20 groups
(59 entries) shared a `(low, high)` pair. Each shared group was differentiated
by a **relative threat-frequency ordering** (a multiplicative level-shift of
the group's `(low, high)`, preserving the spread ratio → distinct anchors with
ordered means/medians). One group was a **genuine tie → allowlisted** (not
nudged).

**Since the distribution-model reversal (lognormal → PERT), TEF is stored as a
PERT triple `(low, mode, high)` again** — the `(low, high)` anchors from this
exercise are unchanged, but `mode` now also participates in the distinctness
key. See the Verification section below for the current, PERT-triple-framed
count.

## Epistemic status (carry-over from #518 plan-gate C)

The differentiation encodes an **ORDERING driven by relative threat frequency,
NOT calibrated precision**. TEF is the one FAIR dimension EXEMPT from the
coarse-scale caveat — it legitimately spans orders of magnitude, so a directional
level-shift is well within its resolution. Genuine ties are allowlisted, never
nudged to fake a difference.

## Frequency rubric (ordering signal, high → low)

- **Tier A (high):** automated / high-volume — credential-stuffing & malware
  campaigns, DDoS on revenue systems, high-volume social-engineering/BEC,
  account-takeover. (POS card skimming was historically Tier A but post-EMV its
  frequency has fallen sharply per DBIR — M-6; treated as high-B / low-A, which
  is why `retail-pos-card-skimming` sits at #2, not #1, in its group.)
- **Tier B (medium):** ransomware, data disclosure via common vectors, insider
  misuse of broadly-held data.
- **Tier C (lower):** data tampering, targeted intrusion, supply-chain
  compromise, physical tampering at exposed sites.
- **Tier D (rare):** nation-state OT (availability / integrity), targeted
  sabotage.
- **Asset-exposure modifier:** broad revenue/data attack surface → higher; niche
  OT / facilities → lower.

## Per-group ordering (higher frequency first; `keep` = anchor at base `(low,high)`)

| Group `(low,high)` | Ordering (→ new `(low,high)`) |
|---|---|
| (2.0, 24.0) | telecom-ddos-core-network `keep` > financial-call-center-social-eng (1.4, 17.0) |
| (1.0, 20.0) | credential-stuffing-consumer-portal `keep` (pinned campaign) > session-hijack-post-mfa-bypass (0.75, 15.0) > retail-store-employee-fraud (0.42, 11.0) |
| (1.0, 15.0) | api-key-leak-devops `keep` > accidental-insider-exposure (0.8, 12.0) > telecom-field-cabinet-tamper (0.7, 11.0) — **M-2:** api-key (CI/CD secret sprawl, Tier B) ranks above physical cabinet tampering (Tier C); reversed from the #518 within-sector direction, which was distinctness-only |
| (1.0, 12.0) | bec-fraud-financial `keep` > retail-ecommerce-checkout-ddos (0.8, 9.5) |
| (0.5, 8.0) | cloud-account-takeover (0.75, 12.0) > retail-pos-card-skimming (0.6, 10.0) > phishing-ad-compromise-ransomware `keep` > ransomware-healthcare-small-practice (0.42, 6.8) > public-sector-targeted-intrusion (0.35, 5.6) > logistics-tms-data-tampering (0.3, 4.8) |
| (0.3, 6.0) | healthcare-staff-credential-phish (0.39, 7.8) > hospitality-loyalty-account-takeover (0.33, 6.6) > third-party-processor-breach `keep` > gov-citizen-portal-ddos (0.24, 4.8) |
| (0.2, 5.0) | data-breach-notification-regulatory-tail (0.25, 6.2) > hmi-credential-compromise `keep` > insider-ip-theft-manufacturing (0.16, 4.0) |
| (0.3, 5.0) | professional-payroll-bec `keep` > logistics-disruption (0.22, 3.75) |
| (0.2, 4.0) | branch-atm-physical-tamper (0.25, 5.0) > healthcare-record-alteration (0.22, 4.4) > logistics-warehouse-physical-intrusion `keep` > **agri-equipment-physical-tamper ≡ education-campus-facility-tamper (0.15, 3.0) — ALLOWLIST (M-3:** both hacktivist physical tampering at niche exposed sites, equivalent rare frequency, no distinguishing signal) |
| (0.2, 3.0) | financial-transaction-tampering (0.23, 3.45) > energy-billing-system-tamper `keep` > education-student-records-insider (0.17, 2.55) |
| (0.15, 2.5) | manufacturing-billing-fraud ≡ hospitality-guest-data-insider `keep` (0.15, 2.5) — **ALLOWLIST (M-3:** both generic insider-misuse across two sectors, no frequency-distinguishing feature) |
| (0.1, 2.0) | ransomware-on-historian (0.12, 2.3) > moveit-class-zero-day-mft `keep` > telecom-subscriber-data-breach (0.085, 1.7) |
| (0.05, 1.5) | gov-employee-insider-leak (0.058, 1.7) > professional-office-physical-theft `keep` (0.05, 1.5) > nation-state-ics-supply-chain (0.043, 1.3) — **M-1:** nation-state ICS supply-chain (Tier D, rarest class in the library) ranks LOWEST; common office physical theft (Tier C) above it |
| (0.1, 1.5) | tolling-plant-ransomware-customer-liability (0.12, 1.8) > gov-records-tampering (0.105, 1.6) > oem-remote-maintenance-abuse `keep` > crop-science-ip-exfiltration (0.085, 1.3) |
| (0.05, 1.2) | telecom-bgp-route-hijack `keep` > manufacturing-facility-sabotage (0.043, 1.0) |
| (0.05, 1.0) | ip-theft-by-competitor `keep` > solarwinds-class-supply-chain (0.043, 0.85) > denial-of-control (0.035, 0.7); datacenter-physical-breach (0.06, 1.2) [within-sector, split up] |
| (0.05, 0.8) | ransomware-on-control-layer `keep` > energy-settlement-platform-tampering-offtaker-liability (0.04, 0.65) |
| (0.1, 0.6) | casino-ransomware-operational-disruption ≡ law-firm-privileged-data-ransomware-extortion `keep` (0.1, 0.6) — **ALLOWLIST (M-3:** both sector-targeted double-extortion ransomware; if anything law firms are hit ≥ casinos, so no defensible direction) |
| (0.05, 0.4) | law-enforcement-records-extortion-breach `keep` > k12-edtech-vendor-breach (0.043, 0.34) |
| (0.02, 0.4) | **ALLOWLIST** — field-instrument-spoofing ≡ pipeline-scada-integrity (both nation-state OT-integrity, same rare frequency; genuine tie, not nudged) |

## Allowlist (genuine ties — 4 pairs, kept identical, not nudged)

Per M-3 (methodology plan-gate), differentiation is applied only where a real
directional signal exists; genuine ties are allowlisted:
1. `field-instrument-spoofing` ≡ `pipeline-scada-integrity` — both nation-state OT-integrity.
2. `casino-ransomware-operational-disruption` ≡ `law-firm-privileged-data-ransomware-extortion` — sector-targeted double-extortion ransomware.
3. `manufacturing-billing-fraud` ≡ `hospitality-guest-data-insider` — generic insider-misuse.
4. `agri-equipment-physical-tamper` ≡ `education-campus-facility-tamper` — hacktivist physical tampering at niche sites.

## Ordering caveats (M-4/M-5 — documented, not reversed)

- **(0.3, 6.0) group:** `gov-citizen-portal-ddos` is placed lowest despite DoS
  being Tier A — because the Tier-A qualifier is "DDoS on **revenue** systems",
  and a government citizen portal is not a revenue system (hacktivist portal
  DDoS is disruptive but lower-frequency than the group's phishing/ATO/breach
  threats). Documented per M-4.
- **(0.05, 0.4) group:** `law-enforcement-records-extortion-breach` >
  `k12-edtech-vendor-breach` is rubric-consistent (data-disclosure B >
  supply-chain C); noted (M-5) that K12 edtech-vendor breaches are empirically a
  rampant category — the direction is defensible but not strong.

## Verification (against current seed, PERT triples)

Re-derived directly from the current seed data
(`data/seed_library_entries.json` + `data/seed_library_entries_extension.json`,
102 entries total) reading each entry's `threat_event_frequency` PERT
`(low, mode, high)` triple:

- **98 distinct `(low, mode, high)` triples across 102 entries.**
- **7 groups share a `(low, high)` pair** (14 entries); of those:
  - **3 are legitimate (low, high) collisions distinguished by `mode`** —
    `pipeline-nomination-scada-curtailment-shipper-penalty` (mode 0.13) vs.
    `email-client-zeroclick-espionage` (mode 0.15); `hospitality-pos-card-skimming`
    (mode 1.0) vs. `edge-ransomware-perimeter-gateway` (mode 0.9);
    `professional-payroll-bec` (mode 1.2) vs. `browser-zeroday-driveby`
    (mode 1.0). These are genuinely distinct PERT triples even though the
    `(low, high)` anchors match.
  - **4 are the genuine ties already allowlisted above** (`field-instrument-spoofing`
    ≡ `pipeline-scada-integrity`; `casino-ransomware-operational-disruption` ≡
    `law-firm-privileged-data-ransomware-extortion`; `manufacturing-billing-fraud`
    ≡ `hospitality-guest-data-insider`; `agri-equipment-physical-tamper` ≡
    `education-campus-facility-tamper`) — same `low`, `mode`, AND `high`, kept
    identical by design, not nudged.
