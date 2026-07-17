"""Epic D-iii-b new-entries builder (offline curation; NOT app runtime).

Authors 8 new attested vertical library entries (one+ per under-represented non-OT
vertical) on the Epic D envelope x share loss model, APPENDING them to
data/seed_library_entries_extension.json (idempotent: skips a slug already present).

primary_loss/secondary_loss = envelope x Sum(analyst-judged loss-form shares),
sigma = sector envelope sigma (data/loss_form_envelopes.json). loss_tier=paginated.
source_citations = the IRIS envelope cite FIRST, then the attestation incident cite
(the incident attests the archetype; the envelope supplies the magnitude). TEF +
vulnerability stay per-entry PERT (tier-based, not templated). Engine UNCHANGED.

Run: uv run python scripts/build_d_iii_b_new_entries.py
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

ENV = {r["sector"]: r for r in json.loads(Path("data/loss_form_envelopes.json").read_text())}
IRIS_CITE = "IRIS 2025 Figure A3, p. 35 (sector loss envelope; Epic D-iii envelopexshare model)"
_EXT = Path("data/seed_library_entries_extension.json")

IND2SEC = {
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

VULN_POSTURE = "inherent (control-naive) per fair-cam-methodology 'Vulnerability anchor'"
_MB = "envelope-share (analyst-judged, vulnerability-grade; per loss-form-share-rubric.md)"


def sector_of(e: dict) -> str:
    ind = (e.get("calibration_anchor") or {}).get("industry")
    return IND2SEC.get(ind) or IND2SEC.get(
        (e.get("applicable_industries") or [None])[0], "technology_saas"
    )


def lognormal(mu: float, sigma: float) -> dict:
    return {"distribution": "lognormal", "mean": round(mu, 10), "sigma": round(sigma, 10)}


def build_profile(shares: list[tuple[str, float, str]]) -> list[dict]:
    dom = max((s for _, s, _ in shares), default=0)
    return [
        {
            "form": form,
            "kind": "primary" if kind == "P" else "secondary",
            "magnitude_basis": _MB,
            "citations": [],
            "verified": False,
            "composition_role": "dominant" if share == dom else "contributing",
            "share": round(share, 4),
        }
        for form, share, kind in shares
    ]


# Each entry: authored scalar fields + _shares [(form, share, P|S)] + _tef/_vuln PERT + _attest cite.
NEW_ENTRIES: list[dict] = [
    {
        "slug": "physician-practice-clearinghouse-revenue-disruption",
        "name": "Physician-Practice Revenue-Cycle Disruption - National Claims-Clearinghouse Outage",
        "status": "published",
        "threat_event_type": "supply_chain",
        "threat_actor_type": "cybercriminals",
        "asset_class": "business_process_third_party_revenue",
        "attack_vector": "third_party_dependency",
        "tags": [
            "healthcare",
            "third_party",
            "revenue_cycle",
            "clearinghouse",
            "business_interruption",
        ],
        "description": (
            "A national medical-claims clearinghouse that a physician practice depends on for "
            "eligibility checks, claim submission, and remittance is taken offline by a ransomware "
            "attack on the clearinghouse operator. The practice's own systems are untouched, but the "
            "severed claims-and-payment channel halts revenue-cycle operations: claims cannot be "
            "submitted or adjudicated, cash flow stops, and staff divert to manual workarounds for weeks."
        ),
        "example_incidents": (
            "Change Healthcare (UnitedHealth/Optum), Feb 2024: an ALPHV/BlackCat ransomware attack on "
            "the largest US claims clearinghouse disrupted claim submission and payment for providers "
            "nationwide; an American Medical Association survey in April 2024 found most practices lost "
            "claim-submission and payment revenue and diverted staff to recovery."
        ),
        "canonical_fair_gap": (
            "FAIR has no archetype for a business-continuity loss that originates entirely in a third "
            "party the organization depends on for revenue, with no breach of the organization's own "
            "data. The loss is a cash-flow/productivity interruption on a business_process_third_party_"
            "revenue asset, not a data-liability event, so notification/fines channels are near-zero and "
            "the magnitude tracks revenue-cycle downtime, not record count."
        ),
        "applicable_industries": ["healthcare"],
        "applicable_sub_sectors": ["physician_practice", "health_system"],
        "applicable_org_sizes": ["small", "medium"],
        "suggested_control_ids": [
            "business-continuity-disaster-recovery",
            "incident-response",
            "cyber-insurance",
        ],
        "calibration_anchor": {"industry": "healthcare", "revenue_tier": "10m_to_100m"},
        "_shares": [("productivity", 0.40, "P"), ("response", 0.18, "P")],
        "_tef": {"low": 0.05, "mode": 0.15, "high": 0.5},
        "_vuln": {"low": 0.5, "mode": 0.8, "high": 0.95},
        "_attest": (
            "Change Healthcare (UnitedHealth/Optum) ransomware outage, 2024-02-21 (ALPHV/BlackCat); AMA "
            "physician-impact survey Apr 2024 - ama-assn.org/about/leadership/hard-lessons-learned-change-healthcare-breach"
        ),
    },
    {
        "slug": "law-enforcement-records-extortion-breach",
        "name": "Law-Enforcement Records Breach - CJIS-Adjacent Data Extortion",
        "status": "published",
        "threat_event_type": "data_disclosure",
        "threat_actor_type": "cybercriminals",
        "asset_class": "data",
        "attack_vector": "network_intrusion",
        "tags": ["government", "law_enforcement", "extortion", "cjis_adjacent", "data_leak"],
        "description": (
            "An external cybercriminal group breaches a state or local law-enforcement agency's records "
            "systems and, after encryption or exfiltration, extorts the agency by threatening to leak the "
            "stolen data. The exposed material - gang-intelligence databases, informant identities, "
            "internal-affairs files, officer PII - creates a physical-safety and litigation exposure "
            "distinct from an ordinary data breach."
        ),
        "example_incidents": (
            "DC Metropolitan Police Department, Apr-May 2021: the Babuk group exfiltrated and leaked about "
            "250GB including gang-intelligence and officer files after ransom negotiations broke down. City "
            "of Oakland, Feb-Apr 2023: the Play group (and a later LockBit claim) leaked 600+GB including "
            "police internal-affairs and whistleblower-identity files."
        ),
        "canonical_fair_gap": (
            "FAIR's generic data-disclosure archetype does not capture the officer/informant physical-"
            "safety exposure and compromised-active-investigation harm that dominate a law-enforcement "
            "records breach, nor the outsized litigation/fines secondary tail from exposing statutorily-"
            "confidential records. Framed as CJIS-adjacent (state/local records that interface with federal "
            "CJIS data), not a breach of the federal CJIS system itself."
        ),
        "applicable_industries": ["public"],
        "applicable_sub_sectors": ["law_enforcement"],
        "applicable_org_sizes": ["medium", "large"],
        "suggested_control_ids": [
            "incident-response",
            "endpoint-detection-response",
            "data-loss-prevention",
        ],
        "calibration_anchor": {"industry": "public", "revenue_tier": "100m_to_1b"},
        "_shares": [("response", 0.30, "P"), ("fines", 0.12, "S"), ("reputation", 0.15, "S")],
        "_tef": {"low": 0.05, "mode": 0.15, "high": 0.4},
        "_vuln": {"low": 0.3, "mode": 0.5, "high": 0.75},
        "_attest": (
            "DC Metropolitan Police Dept / Babuk ransomware data-leak, Apr-May 2021 (washingtonpost.com "
            "2021-05-13); City of Oakland / Play ransomware police-records leak, Feb-Apr 2023 (oaklandside.org 2023-04-05)"
        ),
    },
    {
        "slug": "casino-ransomware-operational-disruption",
        "name": "Integrated Resort-Casino Ransomware - Guest-Facing and Gaming-System Shutdown",
        "status": "published",
        "threat_event_type": "ransomware",
        "threat_actor_type": "cybercriminals",
        "asset_class": "business_process_revenue",
        "attack_vector": "social_engineering",
        "tags": [
            "hospitality",
            "casino",
            "ransomware",
            "scattered_spider",
            "operational_disruption",
        ],
        "description": (
            "A financially-motivated extortion group gains initial access to an integrated resort-casino "
            "through help-desk social engineering (voice phishing / MFA reset), then deploys ransomware "
            "that disables guest-facing and gaming systems - slot floors, digital room keys, property-"
            "management and payment systems - forcing a multi-day operational shutdown and large revenue loss."
        ),
        "example_incidents": (
            "MGM Resorts, Sep 2023: Scattered Spider used help-desk social engineering to obtain access and "
            "deploy ransomware; MGM's Q3-2023 SEC 8-K disclosed a roughly $100M Adjusted Property EBITDAR "
            "impact. Caesars Entertainment, Aug-Sep 2023: a parallel Scattered Spider intrusion resolved "
            "with a negotiated ransom (SEC 8-K, Oct 2023)."
        ),
        "canonical_fair_gap": (
            "Existing hospitality archetypes cover card-data theft (POS skimming) and booking-platform "
            "DDoS, but not ransomware that disables physical guest-facing and gaming operations. The "
            "dominant loss is a productivity/revenue interruption from the operational shutdown, not data "
            "liability. The extortion payment, where paid, maps to the response form (recovery cost); it is "
            "not modeled as a separate loss form."
        ),
        "applicable_industries": ["hospitality"],
        "applicable_sub_sectors": ["casino", "resort"],
        "applicable_org_sizes": ["large", "enterprise"],
        "suggested_control_ids": [
            "incident-response",
            "endpoint-detection-response",
            "data-backup-recovery",
            "email-security-protection",
        ],
        "calibration_anchor": {"industry": "hospitality", "revenue_tier": "10b_to_100b"},
        "_shares": [
            ("productivity", 0.55, "P"),
            ("response", 0.12, "P"),
            ("replacement", 0.05, "P"),
            ("reputation", 0.12, "S"),
        ],
        "_tef": {"low": 0.1, "mode": 0.25, "high": 0.6},
        "_vuln": {"low": 0.3, "mode": 0.55, "high": 0.8},
        "_attest": (
            "MGM Resorts ransomware (Scattered Spider), Sep 2023 - MGM Q3-2023 SEC 8-K disclosed ~$100M "
            "Adjusted Property EBITDAR impact; Caesars Entertainment ransom, Aug-Sep 2023 (SEC 8-K Oct 2023)"
        ),
    },
    {
        "slug": "telecom-lawful-intercept-nationstate-compromise",
        "name": "Telecom Lawful-Intercept / CALEA System Compromise - Nation-State Espionage",
        "status": "published",
        "threat_event_type": "data_disclosure",
        "threat_actor_type": "nation_state",
        "asset_class": "data",
        "attack_vector": "network_intrusion",
        "tags": [
            "telecom",
            "nation_state",
            "lawful_intercept",
            "calea",
            "salt_typhoon",
            "espionage",
        ],
        "description": (
            "A nation-state actor gains persistent, credentialed access to a telecommunications carrier's "
            "core and lawful-intercept (CALEA) infrastructure - backbone and provider-edge routers and the "
            "case-management systems that hold court-ordered interception data - to exfiltrate call content "
            "and metadata for espionage. Remediation requires hardening or replacing compromised network "
            "equipment and reviewing lawful-intercept access."
        ),
        "example_incidents": (
            "Salt Typhoon (PRC state-sponsored), disclosed 2024: an FBI/CISA joint statement (Nov 2024) "
            "confirmed compromise of multiple US carriers to steal call records and data subject to lawful-"
            "intercept court orders; a CISA/NSA/FBI advisory (AA25-239A, Aug 2025) detailed the router-"
            "exploitation tradecraft."
        ),
        "canonical_fair_gap": (
            "Distinct from a criminal subscriber-data breach (PII monetization) and from BGP route hijacking "
            "(routing-control-plane tampering): the target is interception infrastructure, the actor is a "
            "nation-state, and the dominant loss is national-security incident response plus rip-and-replace "
            "equipment remediation, with no publicly disclosed per-org loss figure - magnitude is taken from "
            "the sector envelope."
        ),
        "applicable_industries": ["information"],
        "applicable_sub_sectors": ["wireless_carrier", "broadband_provider"],
        "applicable_org_sizes": ["large", "enterprise"],
        "suggested_control_ids": [
            "network-segmentation",
            "incident-response",
            "security-information-event-management",
            "endpoint-detection-response",
        ],
        "calibration_anchor": {"industry": "information", "revenue_tier": "10b_to_100b"},
        "_shares": [("response", 0.30, "P"), ("replacement", 0.15, "P"), ("reputation", 0.10, "S")],
        "_tef": {"low": 0.02, "mode": 0.1, "high": 0.3},
        "_vuln": {"low": 0.4, "mode": 0.65, "high": 0.9},
        "_attest": (
            "Salt Typhoon PRC state-sponsored telecom compromise - FBI/CISA Joint Statement 2024-11-13; "
            "CISA/NSA/FBI Advisory AA25-239A 2025-08-27 (fbi.gov, cisa.gov)"
        ),
    },
    {
        "slug": "law-firm-privileged-data-ransomware-extortion",
        "name": "Law-Firm Privileged-Data Ransomware - Double-Extortion",
        "status": "published",
        "threat_event_type": "ransomware",
        "threat_actor_type": "cybercriminals",
        "asset_class": "data",
        "attack_vector": "phishing",
        "tags": [
            "professional_services",
            "law_firm",
            "ransomware",
            "double_extortion",
            "privilege",
        ],
        "description": (
            "A cybercriminal group compromises a law firm and deploys ransomware with double-extortion, "
            "exfiltrating privileged client material before encryption. Beyond forensics and downtime, the "
            "breach of attorney-client privilege creates a distinct secondary exposure: bar-disciplinary "
            "risk, client conflicts and disqualification, and malpractice liability."
        ),
        "example_incidents": (
            "IC3 2025 Annual Report (p.16) reports Legal Services as the single largest non-critical-"
            "infrastructure sector for ransomware complaints (18%). Grubman Shire Meiselas and Sacks, May "
            "2020 (REvil): 756GB of privileged client data exfiltrated with a large extortion demand. DLA "
            "Piper, Jun 2017 (NotPetya): a firm-wide outage with a lengthy rebuild."
        ),
        "canonical_fair_gap": (
            "Generic ransomware and MFT-zero-day archetypes do not capture the attorney-client-privilege "
            "loss channel - bar-disciplinary exposure, client-conflict/disqualification fallout, and "
            "malpractice liability - which drives a law firm's secondary loss. These are modeled inside the "
            "fines and reputation forms; no new loss form is introduced."
        ),
        "applicable_industries": ["professional"],
        "applicable_sub_sectors": ["law_firm"],
        "applicable_org_sizes": ["medium", "large"],
        "suggested_control_ids": [
            "endpoint-detection-response",
            "incident-response",
            "email-security-protection",
            "data-backup-recovery",
        ],
        "calibration_anchor": {"industry": "professional", "revenue_tier": "100m_to_1b"},
        "_shares": [
            ("response", 0.30, "P"),
            ("productivity", 0.15, "P"),
            ("reputation", 0.20, "S"),
            ("fines", 0.08, "S"),
        ],
        "_tef": {"low": 0.1, "mode": 0.25, "high": 0.6},
        "_vuln": {"low": 0.3, "mode": 0.5, "high": 0.75},
        "_attest": (
            "IC3 2025 Annual Report p.16 (Legal Services 18% of non-critical ransomware complaints); "
            "Grubman Shire Meiselas and Sacks / REvil, May 2020 (infosecurity-magazine.com); DLA Piper / "
            "NotPetya, Jun 2017 (SC Media 2019 retrospective)"
        ),
    },
    {
        "slug": "k12-edtech-vendor-breach",
        "name": "K-12 Student-Information-System Vendor Breach - Downstream District Extortion",
        "status": "published",
        "threat_event_type": "supply_chain",
        "threat_actor_type": "cybercriminals",
        "asset_class": "data",
        "attack_vector": "third_party_credential_compromise",
        "tags": ["education", "k12", "edtech", "supply_chain", "ferpa", "extortion"],
        "description": (
            "A cybercriminal group breaches a K-12 student-information-system (SIS) or edtech vendor and "
            "exfiltrates student and staff PII across the vendor's customer base, then extorts the vendor "
            "and, separately, the downstream school districts. The districts' own systems are not disrupted, "
            "but FERPA-covered breach liability and notification costs accrue to them."
        ),
        "example_incidents": (
            "PowerSchool, Dec 2024: a compromised support-portal credential enabled exfiltration of student "
            "and teacher records across the vendor's K-12 customer base (tens of millions of records "
            "reported); the vendor paid an extortion demand and districts subsequently faced separate "
            "extortion using the same data. A criminal prosecution followed."
        ),
        "canonical_fair_gap": (
            "Distinct from an insider abusing district SIS access and from ransomware on a district's own "
            "network: the loss originates in a third-party vendor, liability accrues to the district, and "
            "the magnitude sits at education's own severity scale (not the financial-sector scale of the "
            "generic third-party-processor archetype)."
        ),
        "applicable_industries": ["education"],
        "applicable_sub_sectors": ["k12"],
        "applicable_org_sizes": ["medium", "large"],
        "suggested_control_ids": ["incident-response", "data-loss-prevention", "cyber-insurance"],
        "calibration_anchor": {"industry": "education", "revenue_tier": "10m_to_100m"},
        "_shares": [("response", 0.35, "P"), ("reputation", 0.12, "S"), ("fines", 0.10, "S")],
        "_tef": {"low": 0.05, "mode": 0.15, "high": 0.4},
        "_vuln": {"low": 0.4, "mode": 0.7, "high": 0.9},
        "_attest": (
            "PowerSchool K-12 SIS vendor breach, Dec 2024 (compromised support-portal credential; tens of "
            "millions of student/teacher records; vendor extortion paid; downstream district re-extortion "
            "2025; criminal prosecution). Figures reported as a range across outlets."
        ),
    },
    {
        "slug": "higher-ed-insider-ddos",
        "name": "Higher-Education Insider-Motivated Botnet DDoS Against Campus Network",
        "status": "published",
        "threat_event_type": "denial_of_service",
        "threat_actor_type": "insider_malicious",
        "asset_class": "systems",
        "attack_vector": "volumetric_ddos_botnet",
        "tags": ["education", "higher_education", "ddos", "insider", "availability"],
        "description": (
            "An enrolled affiliate (a student), motivated by personal grudge, directs an external botnet to "
            "launch volumetric distributed denial-of-service attacks against a university's own network - "
            "central authentication, registration, and coursework systems - causing repeated availability "
            "outages during high-load periods. The attacker is an insider by trust/affiliation, but the "
            "DDoS is external and distributed, not conducted through granted network access."
        ),
        "example_incidents": (
            "Paras Jha, a Rutgers University student, ran a botnet DDoS campaign against Rutgers' central "
            "authentication and registration systems from Nov 2014 to Sep 2016; he pleaded guilty in the "
            "District of New Jersey and was ordered to pay $8.6M restitution (DOJ USAO-NJ, 2018)."
        ),
        "canonical_fair_gap": (
            "The only insider-motivated denial-of-service archetype in the library. Distinct from external-"
            "extortion and seasonal-revenue DDoS entries (different motive, no extortion) and from physical "
            "campus tampering. Single well-documented exemplar (Rutgers/Jha); framed narrowly as insider-"
            "motivated external botnet DDoS against campus availability."
        ),
        "applicable_industries": ["education"],
        "applicable_sub_sectors": ["higher_education"],
        "applicable_org_sizes": ["large", "enterprise"],
        "suggested_control_ids": [
            "ddos-protection",
            "incident-response",
            "background-verification",
        ],
        "calibration_anchor": {"industry": "education", "revenue_tier": "100m_to_1b"},
        "_shares": [("productivity", 0.28, "P"), ("response", 0.10, "P")],
        "_tef": {"low": 0.05, "mode": 0.12, "high": 0.35},
        "_vuln": {"low": 0.3, "mode": 0.5, "high": 0.75},
        "_attest": (
            "Paras Jha botnet DDoS campaign against Rutgers University central authentication/registration, "
            "Nov 2014-Sep 2016; DOJ USAO-NJ guilty plea, $8.6M restitution (CyberScoop 2018-10-26 reporting "
            "the DOJ announcement)"
        ),
    },
    {
        "slug": "judiciary-court-system-ransomware",
        "name": "Judiciary Court-System Ransomware - Case-Management Disruption and Confidential-Record Exposure",
        "status": "published",
        "threat_event_type": "ransomware",
        "threat_actor_type": "cybercriminals",
        "asset_class": "systems",
        "attack_vector": "network_intrusion",
        "tags": ["government", "judiciary", "ransomware", "double_extortion", "case_management"],
        "description": (
            "A cybercriminal group deploys ransomware against a court system's own independently-administered "
            "case-management and e-filing infrastructure (a separate branch of government, not a shared "
            "municipal network), with double-extortion of confidential case records. Operations halt - "
            "docket backlog, missed statutory deadlines - and sealed or confidential records (juvenile, "
            "domestic-violence, sealed cases) may be exposed."
        ),
        "example_incidents": (
            "Kansas Judicial Branch, Oct 2023: a statewide ransomware attack took court systems offline for "
            "5+ weeks with confirmed theft of case records. Los Angeles County Superior Court, Jul 2024: "
            "ransomware forced a multi-day full closure of the largest US trial court."
        ),
        "canonical_fair_gap": (
            "Distinct from generic municipal-ransomware (courts as one collaterally-affected service): the "
            "judiciary runs its own case-management/e-filing systems, and the harm includes a due-process/"
            "backlog productivity loss plus a fines channel from exposing statutorily-confidential records. "
            "Also distinct from insider records-tampering (external ransomware vs an insider integrity attack)."
        ),
        "applicable_industries": ["public"],
        "applicable_sub_sectors": ["judiciary"],
        "applicable_org_sizes": ["medium", "large", "enterprise"],
        "suggested_control_ids": [
            "incident-response",
            "data-backup-recovery",
            "endpoint-detection-response",
            "business-continuity-disaster-recovery",
        ],
        "calibration_anchor": {"industry": "public", "revenue_tier": "100m_to_1b"},
        "_shares": [
            ("response", 0.25, "P"),
            ("productivity", 0.20, "P"),
            ("fines", 0.10, "S"),
            ("reputation", 0.12, "S"),
        ],
        "_tef": {"low": 0.08, "mode": 0.2, "high": 0.5},
        "_vuln": {"low": 0.3, "mode": 0.55, "high": 0.8},
        "_attest": (
            "Kansas Judicial Branch statewide ransomware, Oct 2023 (5+ week outage, confirmed case-record "
            "theft; bleepingcomputer.com); Los Angeles County Superior Court ransomware, Jul 2024 (5-day "
            "full closure; cnn.com 2024-07-22)"
        ),
    },
]


def realize(entry: dict) -> dict:
    """Turn an authored NEW_ENTRIES dict into a full seed entry (compute loss nodes)."""
    e = {k: v for k, v in entry.items() if not k.startswith("_")}
    shares = entry["_shares"]
    sec = sector_of(e)
    mu_s, sigma_s = ENV[sec]["mean"], ENV[sec]["sigma"]
    sp = sum(s for _, s, k in shares if k == "P")
    ss = sum(s for _, s, k in shares if k == "S")
    e["threat_event_frequency"] = {"distribution": "PERT", **entry["_tef"]}
    e["vulnerability"] = {"distribution": "PERT", **entry["_vuln"]}
    e["primary_loss"] = lognormal(mu_s + math.log(sp), sigma_s)
    e["secondary_loss"] = lognormal(mu_s + math.log(ss), sigma_s) if ss > 0 else None
    e["loss_tier"] = "paginated"
    e["source_citations"] = [IRIS_CITE, entry["_attest"]]
    e["loss_form_profile"] = build_profile(shares)
    ca = dict(entry["calibration_anchor"])
    ca.setdefault("vuln_posture", VULN_POSTURE)
    ca.setdefault(
        "loss_anchor", f"IRIS 2025 Figure A3 p.35 {sec} envelope; Epic D-iii envelopexshare"
    )
    e["calibration_anchor"] = ca
    e.setdefault("standards_references", None)
    return e


def main() -> None:
    rows = json.loads(_EXT.read_text())
    have = {r["slug"] for r in rows}
    realized = [realize(e) for e in NEW_ENTRIES]

    # per-resolved-sector primary_loss collision check vs the WHOLE library.
    all_e = (
        json.loads(Path("data/seed_library_entries.json").read_text())
        + rows
        + [r for r in realized if r["slug"] not in have]
    )
    by = defaultdict(lambda: defaultdict(list))
    for e in all_e:
        pl = e.get("primary_loss") or {}
        if pl.get("distribution") == "lognormal":
            by[sector_of(e)][round(pl["mean"], 6)].append(e["slug"])
    coll = {(s, m): sl for s, mm in by.items() for m, sl in mm.items() if len(sl) > 1}
    if coll:
        print("PRIMARY-LOSS COLLISIONS (same sector, same curve):")
        for (s, m), sl in coll.items():
            print(f"  PL ({s!r}, {m}) {sl}")

    appended = 0
    for r in realized:
        if r["slug"] in have:
            print(f"skip (present): {r['slug']}")
            continue
        rows.append(r)
        appended += 1
    _EXT.write_text(json.dumps(rows, indent=2) + "\n")
    print(f"appended {appended} entries -> {_EXT} (now {len(rows)})")


if __name__ == "__main__":
    main()
