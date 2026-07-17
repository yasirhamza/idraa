# Canonical attack-vector classes

**Status:** interim soft-check vocabulary for the attack-coverage gap-fill
epic (#529 §7.3). `scenario_library_entries.attack_vector` is a free-text
column — every archetype author has historically picked their own
short-slug value describing how the scenario's initial foothold happens.
That freedom is exactly how the epic's root-cause gap (edge-appliance,
client-side, transient-device, removable-media, and destructive/wiper
coverage silently reading zero) went undetected for four prior epics: there
was no place a reviewer could see the *marginal* across all values at once.

This doc is the interim fix: every distinct `attack_vector` value used in
`data/seed_library_entries.json` + `data/seed_library_entries_extension.json`
is catalogued below under one of the canonical classes. A soft-check test
(`tests/unit/test_attack_vector_coverage.py::test_every_attack_vector_maps_to_a_known_canonical_class`)
asserts every value in the live library appears here — so a new ad-hoc value
authored without visiting this doc fails loudly instead of silently
widening the un-audited marginal again.

**Not an enum migration.** The `attack_vector` column stays free text. A full
controlled-vocabulary migration of the column itself is a **noted follow-up**
(design doc §7.3, scope-drift-log item 8), not part of this epic — the
proportionate interim is this documented vocabulary + the soft-check test,
not a schema change.

**How to add a new value.** When authoring a new library entry, pick an
existing `attack_vector` value where it genuinely fits. If none fits, add
the new value to the correct class below (or add a new class) in the same
change that adds the entry — the soft-check test will otherwise fail your PR.

## Class index

| Class | ATT&CK correlation | Distinct values | Entries |
|---|---|---:|---:|
| [phishing](#phishing) | T1566 Phishing | 5 | 14 |
| [social-engineering](#social-engineering) | T1566 (voice/BEC variants) | 2 | 5 |
| [credential-abuse](#credential-abuse) | T1078.004, T1621, T1552, T1539 | 6 | 7 |
| [insider-misuse](#insider-misuse) | T1078 (insider abuse of granted access) | 3 | 15 |
| [supply-chain](#supply-chain) | T1195 Supply Chain Compromise | 5 | 5 |
| [edge-appliance-exploitation](#edge-appliance-exploitation) | T1190 Exploit Public-Facing Application | 2 | 3 |
| [remote-access-exploitation](#remote-access-exploitation) | T1133 External Remote Services | 1 | 3 |
| [network-exploitation](#network-exploitation) | T1190 / T1210 / general recon | 6 | 23 |
| [client-exploitation](#client-exploitation) | T1203 Exploitation for Client Execution | 1 | 1 |
| [drive-by](#drive-by) | T1189 Drive-by Compromise | 2 | 2 |
| [removable-media](#removable-media) | T0847 Replication Through Removable Media (ICS) | 1 | 1 |
| [wireless](#wireless) | T0860 Wireless Compromise (ICS) / T1669 | 1 | 1 |
| [physical](#physical) | T0883 Internet Accessible Device / physical intrusion | 1 | 7 |
| [transient-device](#transient-device) | T0864 Transient Cyber Asset (ICS) | 1 | 1 |
| [ot-engineering-access](#ot-engineering-access) | ICS engineering-workstation / IT-OT bridge | 2 | 7 |
| [denial-of-service](#denial-of-service) | T1498 Network Denial of Service | 2 | 4 |
| [destructive-malware](#destructive-malware) | T1485 Data Destruction / T1486 Data Encrypted for Impact | 1 | 1 |
| [cloud-misconfiguration](#cloud-misconfiguration) | Miscellaneous Errors (not adversarial IA) | 1 | 1 |
| [ai-manipulation](#ai-manipulation) | ATLAS (out of ATT&CK scope; #482 exempt) | 1 | 1 |

44 distinct values across 102 entries as of #529 (machine-verified — see
`tests/unit/test_attack_vector_coverage.py`).

## phishing

Deceptive email/message delivers a malicious link, attachment, or
credential-harvest page; the intrusion proceeds from the resulting
compromise or from lateral movement off the phished endpoint.

- `email_phishing`
- `phishing`
- `phishing_credential_harvest_then_ad_lateral_movement`
- `spearphishing_followed_by_lateral_movement`
- `smb_propagation_from_phished_endpoint` — worm-like SMB propagation
  originating from a phished endpoint; grouped here rather than as its own
  class because the initial-access step is phishing, not the propagation
  mechanism.

## social-engineering

Human-targeted manipulation that is not centered on a malicious
link/attachment — voice/SIM-swap pretexting, business-email-compromise
impersonation, in-person recruitment.

- `social_engineering`
- `email_social_engineering`

## credential-abuse

Theft, stuffing, replay, or bypass of authentication material through a
technical or automated mechanism (distinct from the phishing lure that
sometimes precedes it, and from insiders misusing access they were actually
granted).

- `automated_credential_stuffing_from_breach_databases`
- `credential_exploitation`
- `infostealer_malware_session_token_theft`
- `stolen_credentials_or_mfa_bypass`
- `mfa_push_notification_bombing` — MFA-fatigue defeat of an
  already-stolen-credential login (ATT&CK T1621).
- `secrets_in_source_control_or_ci` — leaked API keys/secrets abused as
  credential material.

## insider-misuse

An actor who already holds legitimate access (employee, contractor, or
privileged vendor) abuses it — the FAIR-CAM insider-threat pattern, distinct
from an external party stealing or forging credentials.

- `insider_access_abuse`
- `insider_privileged_access`
- `privileged_access_misuse`

## supply-chain

Compromise reaches the target through a third-party vendor, dependency, or
software-update channel rather than directly.

- `malicious_open_source_package`
- `trojanized_software_update`
- `trusted_vendor_software_update`
- `third_party_dependency` — an upstream vendor's own outage/incident
  cascades onto the entry's organization (no code/credential path required).
- `third_party_credential_compromise` — a vendor's own systems/credentials
  are breached and downstream customer data is exposed.

## edge-appliance-exploitation

A vulnerability is exploited directly in an internet-facing perimeter/edge
device (VPN concentrator, firewall, remote-access gateway, EOL router).
Maps to the §6.1 ICS-twin T0817 (Drive-by Compromise, ICS) discussion only
by contrast — this class is the *exploited*, not *browsed-to*, pattern.

- `edge_appliance_exploitation`
- `edge_device_orb_repurposing` — the compromised edge device is further
  repurposed as an operational-relay-box (ORB) proxy; the initial-access
  step is still exploiting the edge device.

## remote-access-exploitation

A legitimate remote-access or remote-maintenance channel (VPN session, HMI
remote-support link, OEM vendor support tunnel) is abused rather than a
software vulnerability exploited — the ATT&CK T1133 (External Remote
Services) pattern, kept distinct from T1190 edge-appliance-exploitation per
the design doc §6.1 ICS-twin analysis (`oem-remote-maintenance-abuse` maps
to T1133, not T1190).

- `remote_access_exploitation`

## network-exploitation

Direct exploitation of, or intrusion into, an internet- or network-exposed
application/service/protocol that isn't a perimeter-security edge appliance
in the sense above (application servers, exposed hypervisor management
interfaces, OT protocol stacks, general network intrusion, reconnaissance).

- `external_network_exploitation`
- `network_intrusion`
- `protocol_exploitation`
- `passive_network_scanning_and_active_probing`
- `zero_day_exploitation` — zero-day in an internet-exposed application
  server (e.g. MOVEit-class MFT), not a perimeter security appliance.
- `vmware_esxi_exploitation` — exposed hypervisor management interface
  exploited directly.

## client-exploitation

End-user client software (browser, email client) is exploited directly,
with no intervening compromised third-party website.

- `client_zeroclick_exploitation`

## drive-by

Compromise via a legitimate-but-compromised website or watering hole —
ATT&CK T1189 (Drive-by Compromise).

- `drive_by_client_exploitation`
- `compromised_industry_website` — the §6.1 ICS-twin faithful mapping
  (`watering-hole-industry-targeted` → T0817).

## removable-media

Physical removable media (USB) crosses an air gap or bypasses network
controls — ATT&CK T0847 (ICS) / T1091.

- `removable_media`

## wireless

Compromise via a wireless protocol or network — ATT&CK T0860 (ICS Wireless
Compromise) / T1669 (enterprise Wi-Fi, out of scope for this epic's OT
field-wireless entry, see the marginal report's deferred-techniques note).

- `wireless_compromise`

## physical

Physical intrusion, tampering, or theft (facility break-in, device theft,
field-cabinet tamper).

- `physical_access`

## transient-device

A temporarily-connected device (contractor laptop, field technician asset)
introduces the compromise — ATT&CK T0864 (ICS Transient Cyber Asset).

- `transient_device_compromise`

## ot-engineering-access

Compromise of an OT engineering workstation or the IT/OT bridge layer that
connects enterprise IT to the control-system network.

- `engineering_workstation_compromise`
- `it_ot_bridge`

## denial-of-service

Volumetric or resource-exhaustion availability attack — ATT&CK T1498
(Network Denial of Service).

- `volumetric_ddos_botnet`
- `volumetric_ddos_with_ransom_demand`

## destructive-malware

Wiper or otherwise irreversibly-destructive payload deployed for impact
rather than encryption-for-ransom — ATT&CK T1485 (Data Destruction), the
epic's impact-axis addition (W1).

- `destructive_malware_deployment`

## cloud-misconfiguration

Unintentionally exposed cloud resource — the FAIR-CAM Miscellaneous Errors
pattern (design doc §6.3), not an adversarial initial-access technique.

- `misconfigured_cloud_storage_public_access`

## ai-manipulation

Manipulation of an AI/LLM system's input or tool-use context (prompt
injection). Out of ATT&CK's classic-IA scope; this is the one entry the
completeness guard (`test_completeness_every_published_entry_mapped_or_ai_exempt`)
deliberately leaves unmapped, per issue #482.

- `indirect_prompt_injection_via_rag_or_tool_use`
