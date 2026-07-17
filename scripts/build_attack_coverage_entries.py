"""Attack-coverage gap-fill epic (#529) Task 1: builder for 9 new
scenario-library entries closing a vector-marginal gap (edge-appliance,
transient-device, client-exploitation, OT-wireless/removable-media, and
destructive-wiper archetypes) that no prior epic's crosswalk audit caught.

Milestone B (#524, `dc6836f`) storage-shape split governs how the computed
loss nodes are STORED: 8 entries are `loss_shape="capped"` (the mechanical
PERT conversion of the envelope x share lognormal); W1 alone is
`loss_shape="catastrophic"` (raw lognormal) -- owner-approved 2026-07-09 per
the design doc S10 C2 ruling (nation-state, self-propagating wiper, unbounded
blast radius; NotPetya's Maersk loss dwarfs the transportation_logistics p95).

primary_loss / secondary_loss MAGNITUDE is always Epic D Amendment A1's
envelope x share model: mean = mu_s + ln(Sum shares), sigma = sigma_s, from
the full-precision sector envelope (data/loss_form_envelopes.json). The
intermediate mean/sigma are rounded to 10dp BEFORE any PERT conversion
(round-mean-first -- converting from full precision is off by ~1e-6 and fails
tests/integration/test_library_loss_differentiation.py).

APPENDS the 9 absent entries to data/seed_library_entries_extension.json
(idempotent: skips a slug already present). Run from the repo root:
    uv run python scripts/build_attack_coverage_entries.py
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

_PROJ = Path(__file__).resolve().parent.parent
_EXTENSION_PATH = _PROJ / "data" / "seed_library_entries_extension.json"
_ENVELOPES_PATH = _PROJ / "data" / "loss_form_envelopes.json"

# Milestone B mechanical PERT conversion (scripts/build_loss_pert_conversion.py):
# low/high = exp(mean -/+ Z*sigma), mode = low.
Z = 1.6448536269514722

# Verbatim copy of tests/integration/test_library_loss_differentiation._IND2SEC
# (Epic D-iii-a drift guard -- tests/unit/test_attack_coverage_entries.py
# asserts this copy equals the source so the two never silently diverge).
IND2SEC: dict[str, str] = {
    "agriculture": "food_agriculture",
    "education": "education",
    "education_services": "education",
    "finance_and_insurance": "financial_services",
    "financial": "financial_services",
    "health_care_and_social_assistance": "healthcare",
    "healthcare": "healthcare",
    "information": "technology_saas",
    "manufacturing": "manufacturing",
    "professional": "professional_services",
    "professional_and_business_services": "professional_services",
    "public": "government_public",
    "retail": "retail_ecommerce",
    "retail_trade": "retail_ecommerce",
    "transportation": "transportation_logistics",
    "transportation_and_warehousing": "transportation_logistics",
    "utilities": "energy_utilities",
    "hospitality": "hospitality",
}

_IRIS_CITE = "IRIS 2025 Figure A3, p. 35 (sector loss envelope; Epic D-iii envelopexshare model)"
_MAGNITUDE_BASIS = (
    "envelope-share (analyst-judged, vulnerability-grade; per loss-form-share-rubric.md)"
)
_VULN_POSTURE = "inherent (control-naive) per fair-cam-methodology 'Vulnerability anchor'"
_REVENUE_TIER = "100m_to_1b"
_ORG_SIZES = ["medium", "large", "enterprise"]


def _die(msg: str) -> None:
    raise SystemExit(f"FATAL: {msg}")


def _envelopes() -> dict[str, dict[str, Any]]:
    rows = json.loads(_ENVELOPES_PATH.read_text(encoding="utf-8"))
    return {r["sector"]: r for r in rows}


def _pert_from_lognormal(mean: float, sigma: float) -> dict[str, Any]:
    """Mechanical PERT conversion of an ALREADY-10dp-rounded lognormal
    (mean, sigma): low/high = exp(mean -/+ Z*sigma), mode = low. The analytic
    mode exp(mean - sigma**2) must stay below low -- checked, never assumed."""
    low = round(math.exp(mean - Z * sigma), 10)
    high = round(math.exp(mean + Z * sigma), 10)
    analytic_mode = math.exp(mean - sigma**2)
    if not analytic_mode < low:
        _die(f"analytic mode {analytic_mode} does not clamp below low {low}")
    if not 0 < low < high:
        _die(f"bad PERT bounds low={low} high={high}")
    return {"distribution": "PERT", "low": low, "mode": low, "high": high}


# Each entry: hand-authored scalar fields + loss_form share profile ("shares":
# list of (form, kind, share)) + TEF/vuln PERT tuples + a single attestation
# citation string. `industry` drives sector resolution via IND2SEC; `shares`
# and `loss_shape` drive the computed loss nodes (see recalc()).
NEW_ENTRIES: list[dict[str, Any]] = [
    {
        "slug": "edge-ransomware-perimeter-gateway",
        "name": "Edge VPN/Gateway CVE Exploitation - Ransomware",
        "threat_event_type": "ransomware",
        "threat_actor_type": "cybercriminals",
        "asset_class": "systems",
        "attack_vector": "edge_appliance_exploitation",
        "industry": "professional",
        "applicable_industries": [
            "professional",
            "financial",
            "healthcare",
            "manufacturing",
            "retail",
            "information",
            "public",
            "transportation",
        ],
        "tags": [
            "edge_device",
            "vpn_gateway",
            "cve_exploitation",
            "ransomware",
            "double_extortion",
        ],
        "tef": (0.3, 0.9, 3.5),
        "vuln": (0.15, 0.40, 0.70),
        "shares": [
            ("productivity", "primary", 0.40),
            ("response", "primary", 0.25),
            ("replacement", "primary", 0.05),
            ("reputation", "secondary", 0.14),
            ("fines", "secondary", 0.06),
        ],
        "loss_shape": "capped",
        "description": (
            "Cybercriminal mass-exploitation of a known CVE in an internet-facing edge appliance "
            "(VPN concentrator, remote-access gateway, perimeter firewall) as the initial access for "
            "ransomware deployment, requiring no user interaction. Distinct from the phishing/"
            "credential-derived ransomware archetypes that dominate the library."
        ),
        "example_incidents": (
            "Citrix Bleed (CVE-2023-4966) was mass-exploited in 2023 to gain initial access and "
            "deploy ransomware at organizations including Boeing, ICBC, and Comcast-Xfinity. Ivanti "
            "Connect Secure vulnerabilities (CVE-2023-46805, CVE-2024-21887, subject of CISA Emergency "
            "Directive 24-01 in Jan 2024) and CVE-2025-0282 were mass-exploited to deploy the SPAWN "
            "malware family (Verizon DBIR 2026 p.108)."
        ),
        "canonical_fair_gap": (
            "FAIR's MALWARE/ransomware category does not segment ransomware by initial-access "
            "surface. Edge-appliance-exploited ransomware has a distinct frequency (DBIR 2026's #1 "
            "and fastest-growing initial-access vector) and inherent-vulnerability profile (an "
            "internet-facing unpatched appliance is exploitable with no user interaction) versus "
            "phishing/credential-derived ransomware. Its per-incident loss magnitude equals a typical "
            "ransomware incident - hence deliberately loss-effect-identical to ransomware-on-fileshare "
            "(see _PL_ALLOWLIST); the distinctness lives in the vector and frequency (TEF/vuln), not "
            "the loss curve."
        ),
        "attestation": (
            "Citrix Bleed (CVE-2023-4966) enabling ransomware at Boeing / ICBC / Comcast-Xfinity, "
            "2023; Ivanti Connect Secure (CVE-2023-46805, CVE-2024-21887; CISA Emergency Directive "
            "24-01, Jan 2024) and CVE-2025-0282 mass-exploited to deploy SPAWN (Verizon DBIR 2026 "
            "p.108); DBIR 2026 p.98 (29% of ransomware initial access via unpatched edge-device "
            "vulnerabilities), p.15 (exploitation of vulnerabilities is the #1 initial-access vector "
            "at 31%)"
        ),
        "suggested_control_ids": [
            "patch-management",
            "vulnerability-assessment",
            "network-segmentation",
            "endpoint-detection-response",
            "secure-remote-access",
        ],
    },
    {
        "slug": "edge-espionage-nationstate",
        "name": "Edge Appliance CVE Exploitation - Nation-State Espionage",
        "threat_event_type": "data_disclosure",
        "threat_actor_type": "nation_state",
        "asset_class": "data",
        "attack_vector": "edge_appliance_exploitation",
        "industry": "information",
        "applicable_industries": [
            "information",
            "professional",
            "public",
            "manufacturing",
            "financial",
            "transportation",
        ],
        "tags": [
            "edge_device",
            "cve_exploitation",
            "espionage",
            "nation_state",
            "persistent_access",
        ],
        "tef": (0.04, 0.18, 0.9),
        "vuln": (0.10, 0.28, 0.55),
        "shares": [
            ("response", "primary", 0.28),
            ("replacement", "primary", 0.12),
            ("reputation", "secondary", 0.12),
        ],
        "loss_shape": "capped",
        "description": (
            "Nation-state exploitation of a known edge-appliance CVE to establish persistent access "
            "and exfiltrate data/secrets, with no ransom demanded. Distinct actor and effect from the "
            "edge-ransomware archetype: the loss profile is incident-response plus rip-and-replace "
            "hardware (replacement), with no productivity/business-interruption loss and no extortion."
        ),
        "example_incidents": (
            "Volt Typhoon, a PRC state-sponsored actor, compromised edge routers and appliances "
            "across multiple U.S. critical-infrastructure sectors for persistent access (CISA "
            "AA24-038A, 2024-02-07). Palo Alto Networks PAN-OS GlobalProtect (CVE-2024-3400) and "
            "Fortinet FortiOS vulnerabilities were mass-exploited, and UNC5221 conducted espionage via "
            "Ivanti appliance vulnerabilities."
        ),
        "canonical_fair_gap": (
            "FAIR does not distinguish nation-state edge-appliance espionage (persistent access plus "
            "secrets exfiltration, replacement-heavy) from criminal edge exploitation. This is a "
            "sector-agnostic archetype anchored to technology_saas only as a representative envelope, "
            "applicable across sectors."
        ),
        "attestation": (
            "Volt Typhoon PRC state-sponsored compromise of edge routers/appliances across multiple "
            "U.S. critical-infrastructure sectors - CISA AA24-038A (2024-02-07); Palo Alto PAN-OS "
            "GlobalProtect CVE-2024-3400 (2024); Fortinet FortiOS mass exploitation; UNC5221 Ivanti "
            "espionage. Verizon DBIR 2026 p.15 (exploitation #1 initial-access vector). NOTE: cite "
            "CISA advisory IDs; CISA URLs 403 on automated fetch."
        ),
        "suggested_control_ids": [
            "patch-management",
            "network-detection-response",
            "network-segmentation",
            "threat-intelligence-program",
            "vulnerability-assessment",
        ],
    },
    {
        "slug": "edge-device-orb-foothold",
        "name": "Internet-Edge Device Compromise - Foothold / ORB Repurposing",
        "threat_event_type": "malware",
        "threat_actor_type": "nation_state",
        "asset_class": "systems",
        "attack_vector": "edge_device_orb_repurposing",
        "industry": "information",
        "applicable_industries": ["information", "transportation", "manufacturing", "public"],
        "tags": ["telecom", "edge_device", "orb", "router", "foothold", "eol_device"],
        "tef": (0.15, 0.5, 1.8),
        "vuln": (0.20, 0.45, 0.75),
        "shares": [
            ("response", "primary", 0.18),
            ("productivity", "primary", 0.12),
            ("replacement", "primary", 0.08),
        ],
        "loss_shape": "capped",
        "description": (
            "Compromise of an internet-facing edge device (EOL router/modem, VPN concentrator) as an "
            "unauthorized-access foothold and operational-relay-box (ORB) repurposing target. Direct "
            "loss is incident-response plus device replacement (modest); the downstream consequence "
            "of the access is modeled by separate scenarios. Edge routers are ISP/telecom "
            "infrastructure, so this archetype is anchored via telecom."
        ),
        "example_incidents": (
            "The J-Magic campaign compromised Juniper edge routers via the cd00r 'magic packet' "
            "backdoor (Verizon DBIR 2026 p.108). The FBI and CISA disrupted the PRC-linked KV-botnet "
            "of SOHO/edge routers on 2024-01-31. DBIR 2026 (p.34) reports 45,000-50,000 EOL "
            "internet-facing cellular modems with exposed management interfaces repurposed as "
            "operational relay boxes."
        ),
        "canonical_fair_gap": (
            "FAIR has no archetype for an edge device compromised as a proxy/foothold rather than as "
            "the end target - modest direct loss, high strategic value as staging. Distinct from the "
            "OT-consequence scenarios."
        ),
        "attestation": (
            "J-Magic campaign against Juniper edge routers via the cd00r 'magic packet' backdoor "
            "(Verizon DBIR 2026 p.108); FBI/CISA disruption of the PRC-linked KV-botnet of SOHO/edge "
            "routers (2024-01-31); DBIR 2026 p.34 (45,000-50,000 EOL internet-facing cellular modems "
            "with exposed management interfaces repurposed as operational relay boxes)"
        ),
        "suggested_control_ids": [
            "patch-management",
            "network-detection-response",
            "network-segmentation",
            "vulnerability-assessment",
        ],
    },
    {
        "slug": "transient-cyber-asset-ot-intrusion",
        "name": "Transient Cyber Asset - Contractor/Vendor Device OT Intrusion",
        "threat_event_type": "malware",
        "threat_actor_type": "cybercriminals",
        "asset_class": "ot_systems",
        "attack_vector": "transient_device_compromise",
        "industry": "manufacturing",
        "applicable_industries": ["manufacturing", "transportation", "agriculture", "public"],
        "tags": ["ot", "transient_cyber_asset", "contractor_laptop", "it_ot_boundary", "ics"],
        "tef": (0.09, 0.45, 1.7),
        "vuln": (0.12, 0.35, 0.65),
        "shares": [
            ("productivity", "primary", 0.30),
            ("response", "primary", 0.20),
            ("replacement", "primary", 0.05),
            ("reputation", "secondary", 0.08),
        ],
        "loss_shape": "capped",
        "description": (
            "A temporarily-connected engineering/maintenance/vendor laptop or removable engineering "
            "workstation bridging malware across the IT/OT boundary - the transient device itself is "
            "the vector. Sum of primary shares = 0.55 (not the ot_availability 0.90 default) reflects "
            "an access/staging event with partial disruption and no equipment destruction. Distinct "
            "from it-ot-bridge-compromise (network bridge via spearphishing)."
        ),
        "example_incidents": (
            "This archetype is pattern-attested rather than tied to a single named breach: MITRE "
            "ATT&CK for ICS documents T0864 Transient Cyber Asset as a recognized top-tier ICS "
            "initial-access technique, and NIST SP 800-82r3 Section 6 addresses transient-device "
            "controls; the pattern of contractor/vendor-laptop OT intrusions recurs across CISA ICS "
            "advisories."
        ),
        "canonical_fair_gap": (
            "The single most-cited OT initial-access vector - a transient/contractor device crossing "
            "into OT - has no library archetype; it carries a distinct inherent-vulnerability and "
            "frequency profile from network-bridge OT intrusions."
        ),
        "attestation": (
            "MITRE ATT&CK for ICS T0864 Transient Cyber Asset (recognized top-tier ICS initial-access "
            "technique); NIST SP 800-82r3 Sec 6 (transient-device controls); pattern documented "
            "across CISA ICS advisories"
        ),
        "suggested_control_ids": [
            "mobile-device-management",
            "network-segmentation",
            "endpoint-detection-response",
            "data-loss-prevention",
            "user-access-control",
        ],
    },
    {
        "slug": "browser-zeroday-driveby",
        "name": "Browser / Client-Software Zero-Day - Drive-By Endpoint Compromise",
        "threat_event_type": "malware",
        "threat_actor_type": "cybercriminals",
        "asset_class": "systems",
        "attack_vector": "drive_by_client_exploitation",
        "industry": "retail",
        "applicable_industries": [
            "retail",
            "information",
            "professional",
            "financial",
            "hospitality",
            "public",
        ],
        "tags": ["client_exploitation", "browser_zeroday", "drive_by", "malvertising", "t1203"],
        "tef": (0.3, 1.0, 5.0),
        "vuln": (0.05, 0.20, 0.50),
        "shares": [
            ("productivity", "primary", 0.26),
            ("response", "primary", 0.18),
            ("reputation", "secondary", 0.10),
        ],
        "loss_shape": "capped",
        "description": (
            "Opportunistic exploitation of a browser or client-software zero-day via a "
            "drive-by/malicious-ad delivery, compromising the endpoint (MITRE ATT&CK T1203, entirely "
            "unmapped in the library). Distinct from watering-hole-industry-targeted (nation-state, "
            "targeted site): this is opportunistic, cross-sector, and endpoint-first."
        ),
        "example_incidents": (
            "CVE-2023-4863, a heap-buffer-overflow vulnerability in the libwebp image library, was "
            "mass-exploited client-side across Chrome and other browsers in the wild starting "
            "September 2023. Google's Threat Analysis Group tracks browser zero-days exploited in the "
            "wild annually."
        ),
        "canonical_fair_gap": (
            "FAIR has no archetype for client-software (browser/document reader) zero-day "
            "exploitation as an initial-access vector - a distinct inherent-vulnerability profile "
            "(browser patch state plus browsing exposure) from phishing."
        ),
        "attestation": (
            "CVE-2023-4863 (libwebp) mass client-side exploitation across Chrome and other browsers, "
            "exploited in the wild Sep 2023; Google TAG 'in-the-wild 0-days' annual tracking of "
            "browser zero-days"
        ),
        "suggested_control_ids": [
            "endpoint-detection-response",
            "patch-management",
            "secure-web-gateway",
            "security-awareness-training",
            "network-detection-response",
        ],
    },
    {
        "slug": "email-client-zeroclick-espionage",
        "name": "Email-Client Zero-Click Exploitation - Nation-State",
        "threat_event_type": "data_disclosure",
        "threat_actor_type": "nation_state",
        "asset_class": "data",
        "attack_vector": "client_zeroclick_exploitation",
        "industry": "public",
        "applicable_industries": ["public", "information", "professional", "manufacturing"],
        "tags": ["client_exploitation", "zero_click", "email_client", "nation_state", "espionage"],
        "tef": (0.03, 0.15, 0.6),
        "vuln": (0.08, 0.28, 0.58),
        "shares": [
            ("response", "primary", 0.23),
            ("replacement", "primary", 0.05),
            ("reputation", "secondary", 0.10),
        ],
        "loss_shape": "capped",
        "description": (
            "Zero-click exploitation of an email/mail-client vulnerability (no user interaction) - a "
            "distinct vulnerability profile from every phishing archetype, which require a click - "
            "used by a nation-state actor for espionage."
        ),
        "example_incidents": (
            "Microsoft Outlook CVE-2023-23397, a zero-click NTLM-relay vulnerability, was exploited by "
            "APT28/Fancy Bear against European government, military, and other-sector targets "
            "(Microsoft MSTIC and CERT-EU advisory SA2023-018, March 2023)."
        ),
        "canonical_fair_gap": (
            "FAIR does not distinguish zero-click client exploitation (no user interaction) from "
            "click-dependent phishing - a materially different inherent vulnerability."
        ),
        "attestation": (
            "Microsoft Outlook CVE-2023-23397 zero-click NTLM-relay vulnerability, exploited by "
            "APT28/Fancy Bear against European government, military, and other-sector targets "
            "(Microsoft MSTIC + CERT-EU SA2023-018, Mar 2023)"
        ),
        "suggested_control_ids": [
            "patch-management",
            "email-security-protection",
            "endpoint-detection-response",
            "network-detection-response",
            "threat-intelligence-program",
        ],
    },
    {
        "slug": "removable-media-airgap-ot",
        "name": "Removable Media - Air-Gapped OT Compromise",
        "threat_event_type": "malware",
        "threat_actor_type": "nation_state",
        "asset_class": "ot_systems",
        "attack_vector": "removable_media",
        "industry": "manufacturing",
        "applicable_industries": ["manufacturing", "transportation", "agriculture", "public"],
        "tags": ["ot", "removable_media", "usb", "air_gap", "ics", "stuxnet_class"],
        "tef": (0.015, 0.08, 0.4),
        "vuln": (0.04, 0.13, 0.35),
        "shares": [
            ("productivity", "primary", 0.36),
            ("response", "primary", 0.18),
            ("replacement", "primary", 0.10),
        ],
        "loss_shape": "capped",
        "description": (
            "Delivery of malware across an air gap into OT via removable media (USB) - the Stuxnet "
            "class. Low inherent vulnerability (requires physical media plus an air-gap crossing), "
            "high consequence. The two existing 'USB' corpus entries are physical-tamper/ransomware, "
            "not this archetype."
        ),
        "example_incidents": (
            "Stuxnet (W32.Stuxnet, 2010) compromised air-gapped PLCs via USB media, per the Symantec "
            "W32.Stuxnet Dossier. The Raspberry Robin USB worm (tracked since 2021 by Red Canary and "
            "Microsoft) is a continuing example of the same delivery pattern."
        ),
        "canonical_fair_gap": (
            "FAIR has no archetype for removable-media delivery across an air gap into OT - a "
            "distinct low-frequency, high-consequence vector."
        ),
        "attestation": (
            "Stuxnet (W32.Stuxnet, 2010) - air-gapped PLCs compromised via USB, per the Symantec "
            "W32.Stuxnet Dossier; Raspberry Robin USB worm (2021-present, Red Canary / Microsoft)"
        ),
        "suggested_control_ids": [
            "data-loss-prevention",
            "mobile-device-management",
            "network-segmentation",
            "user-access-control",
        ],
    },
    {
        "slug": "ot-wireless-field-network-compromise",
        "name": "OT Wireless / Rogue-AP - Field-Network Process Disruption",
        "threat_event_type": "ot_availability",
        "threat_actor_type": "cybercriminals",
        "asset_class": "ot_systems",
        "attack_vector": "wireless_compromise",
        "industry": "transportation",
        "applicable_industries": ["transportation", "manufacturing", "agriculture", "public"],
        "tags": ["ot", "wireless", "rogue_ap", "field_network", "ics", "t0860"],
        "tef": (0.06, 0.22, 0.85),
        "vuln": (0.05, 0.20, 0.45),
        "shares": [
            ("productivity", "primary", 0.34),
            ("response", "primary", 0.11),
            ("replacement", "primary", 0.05),
        ],
        "loss_shape": "capped",
        "description": (
            "Compromise via OT field-wireless (WirelessHART/ISA100, plant Wi-Fi, rogue AP, cellular "
            "field networks) causing availability/process disruption - the T0860 class, absent from "
            "the library. Sum of primary shares = 0.50 (not the ot_availability 0.90 default) "
            "reflects partial disruption via a field-network foothold, with no equipment destruction."
        ),
        "example_incidents": (
            "Vitek Boden's Maroochy Shire SCADA radio attack (2000) is the canonical wireless-OT "
            "case, documented in the MITRE ATT&CK for ICS T0860 procedure examples and the "
            "Abrams/Weiss NIST case study. The archetype spans rail, port, manufacturing, and water "
            "OT-wireless deployments."
        ),
        "canonical_fair_gap": (
            "FAIR has no OT-wireless initial-access archetype; it is a distinct vector from wired "
            "IT/OT bridges."
        ),
        "attestation": (
            "Maroochy Shire SCADA radio attack (Vitek Boden, 2000) - the canonical wireless-OT case, "
            "in the MITRE ATT&CK for ICS T0860 procedure examples and the Abrams/Weiss NIST case study"
        ),
        "suggested_control_ids": [
            "wireless-access-authentication-encryption",
            "network-segmentation",
            "network-detection-response",
            "network-access-control",
        ],
    },
    {
        "slug": "destructive-wiper-nationstate",
        "name": "Destructive Wiper / Pseudo-Ransomware - No-Recovery Sabotage",
        "threat_event_type": "malware",
        "threat_actor_type": "nation_state",
        "asset_class": "systems",
        "attack_vector": "destructive_malware_deployment",
        "industry": "transportation",
        "applicable_industries": [
            "transportation",
            "manufacturing",
            "healthcare",
            "financial",
            "information",
            "public",
            "professional",
            "retail",
        ],
        "tags": [
            "wiper",
            "destructive_malware",
            "pseudo_ransomware",
            "nation_state",
            "no_recovery",
            "notpetya_class",
        ],
        "tef": (0.02, 0.12, 0.55),
        "vuln": (0.10, 0.30, 0.60),
        "shares": [
            ("productivity", "primary", 0.45),
            ("replacement", "primary", 0.25),
            ("response", "primary", 0.15),
            ("reputation", "secondary", 0.10),
        ],
        "loss_shape": "catastrophic",
        "description": (
            "Deployment of destructive wiper / pseudo-ransomware malware with no ransom and no "
            "recovery path - the loss is extended downtime plus total-estate rebuild. Actor may be "
            "nation-state, hacktivist, or a false-flag pseudo-ransomware operation. An impact-axis "
            "archetype distinct from all 17 encryption-with-recovery (T1486) ransomware entries in "
            "the library."
        ),
        "example_incidents": (
            "NotPetya (2017, attributed to Sandworm/GRU) was pushed via a trojanized M.E.Doc software "
            "update and spread through EternalBlue/EternalRomance plus Mimikatz, striking Maersk, "
            "Merck, Mondelez, and FedEx/TNT. Shamoon/Disttrack wiped roughly 30,000 workstations at "
            "Saudi Aramco in 2012 and returned in a 2016-17 revival. Olympic Destroyer targeted the "
            "2018 PyeongChang Winter Olympics. HermeticWiper and WhisperGate struck Ukraine in 2022."
        ),
        "canonical_fair_gap": (
            "FAIR's MALWARE/ransomware archetypes assume an extortion transaction with a recovery "
            "path (pay, then decrypt). A destructive wiper has no ransom and no recovery - it is "
            "replacement-heavy (total rebuild) with no fines/ransom channel, a distinct actor and "
            "loss-form profile from encryption ransomware. Typed malware (destructive-malware) "
            "because a wiper is an availability loss, not integrity corruption (data_tampering)."
        ),
        "attestation": (
            "NotPetya (2017, Sandworm/GRU) - pushed via a trojanized M.E.Doc software update, "
            "spreading through EternalBlue/EternalRomance + Mimikatz (Maersk, Merck, Mondelez, "
            "FedEx/TNT); Shamoon/Disttrack (2012 Saudi Aramco, ~30,000 workstations wiped; 2016-17 "
            "revival); Olympic Destroyer (2018 PyeongChang); HermeticWiper/WhisperGate (2022 Ukraine)"
        ),
        "suggested_control_ids": [
            "data-backup-recovery",
            "endpoint-detection-response",
            "network-segmentation",
            "patch-management",
            "incident-response",
        ],
    },
]


def recalc(e: dict[str, Any]) -> dict[str, Any]:
    """Turn one NEW_ENTRIES dict into a full seed entry: resolves the sector,
    computes primary_loss/secondary_loss (envelope x share, round-mean-first),
    finalizes loss_form_profile (magnitude_basis + composition_role), and
    assembles calibration_anchor + ordered source_citations."""
    sector = IND2SEC.get(e["industry"])
    if sector is None:
        _die(f"{e['slug']}: industry {e['industry']!r} not in IND2SEC")
    env = _envelopes()[sector]
    mu_s, sigma_s = env["mean"], env["sigma"]
    sigma = round(sigma_s, 10)

    shares: list[tuple[str, str, float]] = e["shares"]
    sum_p = round(sum(s for _, kind, s in shares if kind == "primary"), 10)
    sum_s = round(sum(s for _, kind, s in shares if kind == "secondary"), 10)
    total = round(sum_p + sum_s, 10)
    if not 0 < total <= 1:
        _die(f"{e['slug']}: Sum(shares)={total} out of (0, 1]")
    if sum_p <= 0:
        _die(f"{e['slug']}: no primary share to derive primary_loss")

    mean_p = round(mu_s + math.log(sum_p), 10)
    mean_s = round(mu_s + math.log(sum_s), 10) if sum_s > 0 else None

    if e["loss_shape"] == "catastrophic":
        primary_loss: dict[str, Any] = {"distribution": "lognormal", "mean": mean_p, "sigma": sigma}
        secondary_loss: dict[str, Any] | None = (
            {"distribution": "lognormal", "mean": mean_s, "sigma": sigma}
            if mean_s is not None
            else None
        )
    elif e["loss_shape"] == "capped":
        primary_loss = _pert_from_lognormal(mean_p, sigma)
        secondary_loss = _pert_from_lognormal(mean_s, sigma) if mean_s is not None else None
    else:
        _die(f"{e['slug']}: unknown loss_shape {e['loss_shape']!r}")
        raise AssertionError("unreachable")  # for the type checker; _die never returns

    max_share = max(s for _, _, s in shares)
    loss_form_profile = [
        {
            "form": form,
            "kind": kind,
            "magnitude_basis": _MAGNITUDE_BASIS,
            "citations": [],
            "verified": False,
            "composition_role": "dominant" if share == max_share else "contributing",
            "share": share,
        }
        for form, kind, share in shares
    ]

    return {
        "slug": e["slug"],
        "name": e["name"],
        "status": "published",
        "threat_event_type": e["threat_event_type"],
        "threat_actor_type": e["threat_actor_type"],
        "asset_class": e["asset_class"],
        "attack_vector": e["attack_vector"],
        "tags": e["tags"],
        "description": e["description"],
        "example_incidents": e["example_incidents"],
        "canonical_fair_gap": e["canonical_fair_gap"],
        "applicable_industries": e["applicable_industries"],
        "applicable_sub_sectors": None,
        "applicable_org_sizes": _ORG_SIZES,
        "suggested_control_ids": e["suggested_control_ids"],
        "calibration_anchor": {
            "industry": e["industry"],
            "revenue_tier": _REVENUE_TIER,
            "vuln_posture": _VULN_POSTURE,
            "loss_anchor": f"IRIS 2025 Figure A3 p.35 {sector} envelope; Epic D-iii envelopexshare",
        },
        "threat_event_frequency": {
            "distribution": "PERT",
            "low": e["tef"][0],
            "mode": e["tef"][1],
            "high": e["tef"][2],
        },
        "vulnerability": {
            "distribution": "PERT",
            "low": e["vuln"][0],
            "mode": e["vuln"][1],
            "high": e["vuln"][2],
        },
        "primary_loss": primary_loss,
        "secondary_loss": secondary_loss,
        "loss_tier": "paginated",
        "source_citations": [_IRIS_CITE, e["attestation"]],
        "loss_form_profile": loss_form_profile,
        "standards_references": None,
        "loss_shape": e["loss_shape"],
    }


def main() -> None:
    existing = json.loads(_EXTENSION_PATH.read_text(encoding="utf-8"))
    existing_slugs = {row["slug"] for row in existing}
    built = [recalc(e) for e in NEW_ENTRIES]

    # Collision check: no two of the 9 new entries share a (sector, primary_loss)
    # pair (the anti-flattening check, scoped to just this batch -- the full
    # 102-entry cross-check is tests/integration/test_library_loss_differentiation.py).
    by_sector_pl: dict[tuple[str, str], list[str]] = {}
    for row in built:
        sector = IND2SEC[row["calibration_anchor"]["industry"]]
        key = (sector, json.dumps(row["primary_loss"], sort_keys=True))
        by_sector_pl.setdefault(key, []).append(row["slug"])
    collisions = {k: v for k, v in by_sector_pl.items() if len(v) > 1}
    if collisions:
        print(f"WARNING: within-batch primary_loss collisions: {collisions}")
    else:
        print("collision check: no within-batch primary_loss collisions across the 9 new entries")

    appended = 0
    for row in built:
        if row["slug"] in existing_slugs:
            print(f"skip (already present): {row['slug']}")
            continue
        existing.append(row)
        appended += 1
        print(f"appended: {row['slug']}")

    _EXTENSION_PATH.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    print(f"done: {appended} entries appended, extension file now has {len(existing)} entries")


if __name__ == "__main__":
    main()
