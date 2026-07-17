"""Epic D-iii-a recalibration builder (offline curation; NOT app runtime).

Applies the loss-form share rubric to all 85 seed entries: computes
primary_loss/secondary_loss = envelope x Sum(shares), populates loss_form_profile,
and sets loss_tier + source_citations -- writing back into
data/seed_library_entries*.json DIRECTLY (Epic C 3d7b9e357d52 pattern;
plan-gate ruling R1). Per-slug share profiles are loss-effect-justified analyst
judgments (vulnerability-grade) chosen to be DISTINCT within a sector (R14).

TEF + vulnerability are deliberately NOT touched here -- de-templating those
(plus the R15 TEF/vuln bucket-cap differentiation guard) is a separate tracked
follow-on (GH issue #505). This builder only reshapes the loss nodes.

Beyond-envelope (plan-gate R7-R11):
  BEC/wire-fraud -> own IC3 lognormal (BEC-specific $123,005 mean; sigma borrowed
    from the sector envelope, disclosed). loss_tier=vendor, IC3 cite.
  IP/trade-secret -> in-envelope response+reputation shares; competitive-advantage
    magnitude UNMODELED with a loud disclosure + sec6 waiver (no defensible source).

Run: uv run python scripts/build_d_iii_a_recalibration.py
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

ENV = {r["sector"]: r for r in json.loads(Path("data/loss_form_envelopes.json").read_text())}
IRIS_CITE = "IRIS 2025 Figure A3, p. 35 (sector loss envelope; Epic D-iii envelopexshare model)"
IC3_BEC_MEAN = (
    123005.0  # $3,046,598,558 / 24,768 BEC complaints, ic3_2025.md:132,108 (BEC-specific)
)
IC3_CITE = (
    "IC3 2025 Annual Report p.7-8, p.132 (BEC per-complaint mean $123,005; "
    "downward-biased -- mixes consumer/SMB with corporate)"
)

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


def sector_of(e: dict) -> str:
    ind = (e.get("calibration_anchor") or {}).get("industry")
    return IND2SEC.get(ind) or IND2SEC.get(
        (e.get("applicable_industries") or [None])[0], "technology_saas"
    )


# -- Per-slug share profiles: {slug: [(form, share, kind), ...]} --------------
# Loss-effect-justified, DISTINCT Sumprimary within each sector (R14). P=primary S=secondary.
PROFILES: dict[str, list[tuple[str, float, str]]] = {
    # === energy_utilities (15) -- distinct Sumprimary ===
    "grid-protective-relay-manipulation": [
        ("replacement", 0.40, "P"),
        ("productivity", 0.40, "P"),
        ("response", 0.12, "P"),
    ],  # 0.92 relay damage
    "denial-of-control": [
        ("productivity", 0.55, "P"),
        ("response", 0.15, "P"),
        ("replacement", 0.10, "P"),
    ],  # 0.80 full halt
    "oem-remote-maintenance-abuse": [
        ("productivity", 0.50, "P"),
        ("replacement", 0.12, "P"),
        ("response", 0.13, "P"),
    ],  # 0.75 vendor-access sustained
    "ransomware-on-historian": [
        ("productivity", 0.40, "P"),
        ("response", 0.25, "P"),
        ("replacement", 0.05, "P"),
        ("reputation", 0.15, "S"),
        ("fines", 0.10, "S"),
    ],  # 0.70/0.25
    "nation-state-ics-supply-chain": [
        ("productivity", 0.35, "P"),
        ("response", 0.30, "P"),
        ("reputation", 0.15, "S"),
    ],  # 0.65 broad
    "it-ot-bridge-compromise": [
        ("productivity", 0.38, "P"),
        ("response", 0.28, "P"),
        ("reputation", 0.15, "S"),
    ],  # 0.66 IT->OT pivot
    "process-view-manipulation": [
        ("productivity", 0.43, "P"),
        ("response", 0.16, "P"),
        ("replacement", 0.05, "P"),
    ],  # 0.64 bad view rework
    "hmi-credential-compromise": [
        ("productivity", 0.45, "P"),
        ("response", 0.18, "P"),
    ],  # 0.63 operator lockout
    "pipeline-scada-integrity": [
        ("productivity", 0.40, "P"),
        ("response", 0.15, "P"),
        ("replacement", 0.06, "P"),
    ],  # 0.61 integrity
    "pipeline-nomination-scada-curtailment-shipper-penalty": [
        ("productivity", 0.48, "P"),
        ("response", 0.10, "P"),
        ("fines", 0.22, "S"),
    ],  # 0.58/0.22 curtailment+penalty
    "watering-hole-industry-targeted": [
        ("productivity", 0.30, "P"),
        ("response", 0.22, "P"),
        ("reputation", 0.12, "S"),
    ],  # 0.52 targeted malware
    "energy-settlement-platform-tampering-offtaker-liability": [
        ("productivity", 0.30, "P"),
        ("response", 0.18, "P"),
        ("fines", 0.15, "S"),
    ],  # 0.48/0.15 settlement+offtaker
    "energy-billing-system-tamper": [
        ("productivity", 0.26, "P"),
        ("response", 0.20, "P"),
        ("reputation", 0.10, "S"),
    ],  # 0.46 billing rework
    "hacktivist-ot-disruption": [
        ("productivity", 0.35, "P"),
        ("response", 0.10, "P"),
    ],  # 0.45 short outage
    "ot-network-scanning-reconnaissance": [("response", 0.03, "P")],  # 0.03 recon (flagship)
    # === technology_saas (16) -- distinct Sumprimary ===
    "solarwinds-class-supply-chain": [
        ("productivity", 0.40, "P"),
        ("response", 0.30, "P"),
        ("reputation", 0.15, "S"),
    ],  # 0.70 nation-state supply chain
    "session-hijack-post-mfa-bypass": [
        ("productivity", 0.33, "P"),
        ("response", 0.30, "P"),
        ("reputation", 0.15, "S"),
    ],  # 0.63
    "cloud-account-takeover": [
        ("productivity", 0.30, "P"),
        ("response", 0.28, "P"),
        ("reputation", 0.15, "S"),
    ],  # 0.58
    "package-registry-supply-chain": [
        ("productivity", 0.32, "P"),
        ("response", 0.24, "P"),
        ("reputation", 0.12, "S"),
    ],  # 0.56 narrower supply chain
    "saas-revenue-outage-sabotage": [
        ("productivity", 0.40, "P"),
        ("response", 0.13, "P"),
        ("reputation", 0.15, "S"),
    ],  # 0.53 insider outage
    "telecom-bgp-route-hijack": [
        ("productivity", 0.35, "P"),
        ("response", 0.16, "P"),
        ("reputation", 0.10, "S"),
    ],  # 0.51 route hijack
    "telecom-ddos-core-network": [
        ("productivity", 0.46, "P"),
        ("response", 0.03, "P"),
    ],  # 0.49 core downtime
    "datacenter-physical-breach": [
        ("replacement", 0.30, "P"),
        ("productivity", 0.13, "P"),
        ("response", 0.04, "P"),
    ],  # 0.47 physical
    "telecom-field-cabinet-tamper": [
        ("replacement", 0.30, "P"),
        ("productivity", 0.10, "P"),
        ("response", 0.03, "P"),
    ],  # 0.43 field cabinet
    "s3-misconfiguration-data-exposure": [
        ("response", 0.22, "P"),
        ("response", 0.14, "S"),
        ("reputation", 0.24, "S"),
        ("fines", 0.15, "S"),
    ],  # 0.22/0.53 breach
    "telecom-subscriber-data-breach": [
        ("response", 0.20, "P"),
        ("response", 0.15, "S"),
        ("reputation", 0.25, "S"),
        ("fines", 0.16, "S"),
    ],  # 0.20/0.56
    "api-key-leak-devops": [
        ("response", 0.19, "P"),
        ("response", 0.13, "S"),
        ("reputation", 0.20, "S"),
        ("fines", 0.12, "S"),
    ],  # 0.19/0.45
    "generative-ai-prompt-injection": [
        ("response", 0.21, "P"),
        ("reputation", 0.18, "S"),
        ("fines", 0.10, "S"),
    ],  # 0.21/0.28 novel; broad exfil path -> more response
    "mfa-fatigue-prompt-bombing": [
        ("response", 0.24, "P"),
        ("reputation", 0.12, "S"),
    ],  # 0.24 cred phish -> breach-lite
    # telecom-sim-swap-fraud -> BEC beyond-envelope
    # competitor-trade-secret-recruit -> IP beyond-envelope
    # === manufacturing (13) -- distinct Sumprimary ===
    "chemical-process-safety-attack": [
        ("productivity", 0.45, "P"),
        ("replacement", 0.28, "P"),
        ("response", 0.15, "P"),
        ("fines", 0.10, "S"),
    ],  # 0.88/0.10 severe safety
    "safety-system-bypass": [
        ("productivity", 0.45, "P"),
        ("replacement", 0.25, "P"),
        ("response", 0.15, "P"),
        ("fines", 0.10, "S"),
    ],  # 0.85/0.10
    "unauthorized-plc-modification": [
        ("productivity", 0.42, "P"),
        ("replacement", 0.22, "P"),
        ("response", 0.16, "P"),
        ("fines", 0.08, "S"),
    ],  # 0.80/0.08
    "ransomware-on-control-layer": [
        ("productivity", 0.50, "P"),
        ("replacement", 0.12, "P"),
        ("response", 0.16, "P"),
    ],  # 0.78 OT ransomware
    "tolling-plant-ransomware-customer-liability": [
        ("productivity", 0.40, "P"),
        ("response", 0.25, "P"),
        ("replacement", 0.05, "P"),
        ("fines", 0.15, "S"),
        ("reputation", 0.10, "S"),
    ],  # 0.70/0.25 + customer liability
    "manufacturing-facility-sabotage": [
        ("replacement", 0.42, "P"),
        ("productivity", 0.20, "P"),
        ("response", 0.05, "P"),
    ],  # 0.67 physical
    "ransomware-on-virtualization-stack": [
        ("productivity", 0.38, "P"),
        ("response", 0.25, "P"),
        ("replacement", 0.05, "P"),
        ("reputation", 0.12, "S"),
    ],  # 0.68 IT ransomware
    "field-instrument-spoofing": [
        ("productivity", 0.40, "P"),
        ("response", 0.15, "P"),
        ("replacement", 0.05, "P"),
    ],  # 0.60 integrity
    "food-cold-chain-ransomware": [
        ("productivity", 0.35, "P"),
        ("response", 0.22, "P"),
        ("replacement", 0.06, "P"),
        ("reputation", 0.12, "S"),
    ],  # 0.63 spoilage
    "food-recall-data-tampering": [
        ("productivity", 0.28, "P"),
        ("response", 0.20, "P"),
        ("reputation", 0.15, "S"),
        ("fines", 0.10, "S"),
    ],  # 0.48/0.25 recall
    # insider-ip-theft-manufacturing, ip-theft-by-competitor -> IP beyond-envelope
    # manufacturing-billing-fraud -> BEC beyond-envelope
    # === financial_services (9) ===
    "third-party-processor-breach": [
        ("productivity", 0.32, "P"),
        ("response", 0.30, "P"),
        ("reputation", 0.18, "S"),
    ],  # 0.62 supply chain
    "web-app-exploitation": [
        ("response", 0.22, "P"),
        ("response", 0.15, "S"),
        ("reputation", 0.25, "S"),
        ("fines", 0.18, "S"),
    ],  # 0.22/0.58 breach
    "insider-data-theft-financial": [
        ("response", 0.20, "P"),
        ("reputation", 0.18, "S"),
        ("fines", 0.12, "S"),
    ],  # 0.20 insider
    "financial-transaction-tampering": [
        ("productivity", 0.28, "P"),
        ("response", 0.22, "P"),
        ("reputation", 0.14, "S"),
        ("fines", 0.12, "S"),
    ],  # 0.50 integrity
    "ddos-extortion-financial": [
        ("productivity", 0.30, "P"),
        ("response", 0.05, "P"),
        ("reputation", 0.08, "S"),
    ],  # 0.35 ddos+extortion
    "ddos-financial-seasonal-peak": [
        ("productivity", 0.28, "P"),
        ("response", 0.02, "P"),
    ],  # 0.30 pure downtime
    "branch-atm-physical-tamper": [
        ("replacement", 0.30, "P"),
        ("productivity", 0.10, "P"),
        ("response", 0.05, "P"),
    ],  # 0.45 physical
    "financial-call-center-social-eng": [
        ("response", 0.24, "P"),
        ("reputation", 0.14, "S"),
    ],  # 0.24 social->breach
    # bec-fraud-financial -> BEC beyond-envelope
    # === healthcare (5) ===
    "ransomware-on-ehr": [
        ("productivity", 0.42, "P"),
        ("response", 0.25, "P"),
        ("replacement", 0.05, "P"),
        ("reputation", 0.15, "S"),
        ("fines", 0.10, "S"),
    ],  # 0.72/0.25 large hospital
    "ransomware-healthcare-small-practice": [
        ("productivity", 0.35, "P"),
        ("response", 0.22, "P"),
        ("replacement", 0.05, "P"),
        ("reputation", 0.12, "S"),
        ("fines", 0.08, "S"),
    ],  # 0.62/0.20 small practice
    "healthcare-record-alteration": [
        ("productivity", 0.25, "P"),
        ("response", 0.22, "P"),
        ("reputation", 0.14, "S"),
        ("fines", 0.12, "S"),
    ],  # 0.47 integrity
    "healthcare-staff-credential-phish": [
        ("response", 0.26, "P"),
        ("reputation", 0.15, "S"),
        ("fines", 0.12, "S"),
    ],  # 0.26 phish->breach
    "accidental-insider-exposure": [
        ("response", 0.18, "P"),
        ("reputation", 0.15, "S"),
        ("fines", 0.12, "S"),
    ],  # 0.18 accidental
    # === retail_ecommerce (5) ===
    "credential-stuffing-consumer-portal": [
        ("productivity", 0.28, "P"),
        ("response", 0.24, "P"),
        ("reputation", 0.15, "S"),
    ],  # 0.52 malware/ato
    "retail-pos-card-skimming": [
        ("response", 0.22, "P"),
        ("response", 0.14, "S"),
        ("reputation", 0.24, "S"),
        ("fines", 0.18, "S"),
    ],  # 0.22/0.56 card breach
    "data-breach-notification-regulatory-tail": [
        ("response", 0.20, "P"),
        ("reputation", 0.22, "S"),
        ("fines", 0.20, "S"),
    ],  # 0.20/0.42 reg tail
    "retail-ecommerce-checkout-ddos": [
        ("productivity", 0.32, "P"),
        ("response", 0.03, "P"),
    ],  # 0.35 peak downtime
    "retail-store-employee-fraud": [
        ("response", 0.10, "P"),
        ("reputation", 0.08, "S"),
    ],  # 0.10 small insider fraud (in-envelope, minor)
    # === professional_services (4) ===
    "moveit-class-zero-day-mft": [
        ("response", 0.24, "P"),
        ("response", 0.16, "S"),
        ("reputation", 0.25, "S"),
        ("fines", 0.18, "S"),
    ],  # 0.24/0.59 mass breach
    "ransomware-on-fileshare": [
        ("productivity", 0.40, "P"),
        ("response", 0.25, "P"),
        ("replacement", 0.05, "P"),
        ("reputation", 0.15, "S"),
    ],  # 0.70 ransomware
    "professional-office-physical-theft": [
        ("replacement", 0.28, "P"),
        ("response", 0.06, "P"),
        ("reputation", 0.10, "S"),
    ],  # 0.34 physical
    # professional-payroll-bec -> BEC beyond-envelope
    # === education (4) ===
    "phishing-ad-compromise-ransomware": [
        ("productivity", 0.40, "P"),
        ("response", 0.25, "P"),
        ("replacement", 0.05, "P"),
        ("reputation", 0.12, "S"),
    ],  # 0.70 ransomware
    "education-student-records-insider": [
        ("response", 0.20, "P"),
        ("reputation", 0.16, "S"),
        ("fines", 0.12, "S"),
    ],  # 0.20 insider
    "education-campus-facility-tamper": [
        ("replacement", 0.28, "P"),
        ("productivity", 0.10, "P"),
        ("response", 0.05, "P"),
    ],  # 0.43 physical
    # education-research-ip-exfiltration -> IP beyond-envelope
    # === government_public (4) ===
    "public-sector-targeted-intrusion": [
        ("productivity", 0.40, "P"),
        ("response", 0.25, "P"),
        ("replacement", 0.05, "P"),
        ("reputation", 0.12, "S"),
    ],  # 0.70 ransomware/intrusion
    "gov-records-tampering": [
        ("productivity", 0.25, "P"),
        ("response", 0.22, "P"),
        ("reputation", 0.14, "S"),
    ],  # 0.47 integrity
    "gov-citizen-portal-ddos": [
        ("productivity", 0.30, "P"),
        ("response", 0.03, "P"),
    ],  # 0.33 downtime
    "gov-employee-insider-leak": [
        ("response", 0.18, "P"),
        ("reputation", 0.16, "S"),
    ],  # 0.18 insider leak
    # === hospitality (4) ===
    "hospitality-pos-card-skimming": [
        ("response", 0.22, "P"),
        ("response", 0.14, "S"),
        ("reputation", 0.25, "S"),
        ("fines", 0.16, "S"),
    ],  # 0.22/0.55 card breach
    "hospitality-loyalty-account-takeover": [
        ("response", 0.18, "P"),
        ("reputation", 0.18, "S"),
    ],  # 0.18 loyalty ato
    "hospitality-booking-ddos-peak-season": [
        ("productivity", 0.30, "P"),
        ("response", 0.03, "P"),
    ],  # 0.33 peak downtime
    "hospitality-guest-data-insider": [
        ("response", 0.15, "P"),
        ("reputation", 0.16, "S"),
    ],  # 0.15 insider
    # === transportation_logistics (3) ===
    "logistics-disruption": [
        ("productivity", 0.42, "P"),
        ("response", 0.25, "P"),
        ("replacement", 0.05, "P"),
        ("reputation", 0.12, "S"),
    ],  # 0.72 ransomware
    "logistics-tms-data-tampering": [
        ("productivity", 0.28, "P"),
        ("response", 0.20, "P"),
        ("reputation", 0.12, "S"),
    ],  # 0.48 integrity
    "logistics-warehouse-physical-intrusion": [
        ("replacement", 0.28, "P"),
        ("productivity", 0.12, "P"),
        ("response", 0.05, "P"),
    ],  # 0.45 physical
    # === food_agriculture (3) ===
    "agri-equipment-physical-tamper": [
        ("replacement", 0.30, "P"),
        ("productivity", 0.15, "P"),
        ("response", 0.05, "P"),
    ],  # 0.50 physical
    # agri-coop-bec-fraud -> BEC beyond-envelope
    # crop-science-ip-exfiltration -> IP beyond-envelope
}

# Beyond-envelope classification (plan-gate R7-R11)
BEC_SLUGS = {
    "telecom-sim-swap-fraud",
    "manufacturing-billing-fraud",
    "bec-fraud-financial",
    "professional-payroll-bec",
    "agri-coop-bec-fraud",
}
IP_SLUGS = {
    "competitor-trade-secret-recruit",
    "insider-ip-theft-manufacturing",
    "ip-theft-by-competitor",
    "education-research-ip-exfiltration",
    "crop-science-ip-exfiltration",
}
# IP entries model incident-response + reputation via envelope shares; competitive-advantage UNMODELED.
IP_PROFILE = [("response", 0.18, "P"), ("reputation", 0.15, "S")]  # Sump 0.18, Sums 0.15


def lognormal(mu: float, sigma: float) -> dict:
    return {"distribution": "lognormal", "mean": round(mu, 10), "sigma": round(sigma, 10)}


def build_profile(shares: list[tuple[str, float, str]], beyond: str | None) -> list[dict]:
    prof = []
    dom = max((s for _, s, _ in shares), default=0)
    for form, share, kind in shares:
        prof.append(
            {
                "form": form,
                "kind": "primary" if kind == "P" else "secondary",
                "magnitude_basis": "envelope-share (analyst-judged, vulnerability-grade; per loss-form-share-rubric.md)",
                "citations": [],
                "verified": False,
                "composition_role": "dominant" if share == dom else "contributing",
                "share": round(share, 4),
            }
        )
    return prof


def recalibrate(e: dict) -> None:
    slug = e["slug"]
    sector = sector_of(e)
    env = ENV[sector]
    mu_s, sigma_s = env["mean"], env["sigma"]

    if slug in BEC_SLUGS:
        # R7/R8/R9: own IC3 lognormal, sigma borrowed from envelope (disclosed), no response share.
        # IC3_BEC_MEAN is an ARITHMETIC MEAN (total losses / complaint count), NOT a median.
        # numpy rng.lognormal(mu, sigma) samples exp(N(mu, sigma)), whose median is exp(mu) and
        # whose mean is exp(mu + sigma^2/2). Placing ln(mean) in the mu slot would make the mean
        # the MEDIAN and inflate E[loss] to mean*exp(sigma^2/2) (up to ~$51M at sigma=3.47) --
        # the issue #131 mean/median conflation. Mean-preserving parameterization mu = ln(mean)
        # - sigma^2/2 gives E[loss] = mean = $123,005 exactly for every BEC entry regardless of
        # the borrowed sigma (family-consistent expected loss; median = mean*exp(-sigma^2/2)).
        e["primary_loss"] = lognormal(math.log(IC3_BEC_MEAN) - sigma_s**2 / 2, sigma_s)
        e["secondary_loss"] = None
        e["loss_tier"] = "vendor"
        e["source_citations"] = [IC3_CITE]
        e["loss_form_profile"] = [
            {
                "form": "replacement",
                "kind": "primary",
                "magnitude_basis": f"IC3 2025 BEC-specific per-complaint mean ${IC3_BEC_MEAN:,.0f} (beyond-envelope own distribution; funds-transfer loss). Mean-preserving lognormal: mu = ln(mean) - sigma^2/2 so E[loss] = mean exactly; median = mean*exp(-sigma^2/2). sigma borrowed from the {sector} envelope (disclosed shape assumption, A1 parity).",
                "citations": [IC3_CITE],
                "verified": True,
                "composition_role": "dominant",
                "share": None,
            }
        ]
        return

    if slug in IP_SLUGS:
        # R11: envelope response+reputation shares; competitive-advantage UNMODELED (loud disclosure + sec6 waiver).
        sp = sum(s for _, s, k in IP_PROFILE if k == "P")
        ss = sum(s for _, s, k in IP_PROFILE if k == "S")
        e["primary_loss"] = lognormal(mu_s + math.log(sp), sigma_s)
        e["secondary_loss"] = lognormal(mu_s + math.log(ss), sigma_s)
        e["loss_tier"] = "paginated"
        e["source_citations"] = [
            IRIS_CITE,
            "LOSS UNDERSTATED: the dominant competitive-advantage / IP-theft loss channel is UNMODELED -- no defensible public magnitude source (adjudicated-damages sweep found none; the entry's prior IP cites are the gap-report mis-cites). Only incident-response + reputation are modeled here. Explicit sec6-materiality-bar waiver (plan-gate R11).",
        ]
        prof = build_profile(IP_PROFILE, "ip")
        prof.append(
            {
                "form": "competitive_advantage",
                "kind": "primary",
                "magnitude_basis": "UNMODELED -- dominant IP/trade-secret loss, no defensible public magnitude source (beyond-envelope, sec6 waiver).",
                "citations": [],
                "verified": False,
                "composition_role": "provenance_only",
                "share": None,
            }
        )
        e["loss_form_profile"] = prof
        return

    # In-envelope entry.
    shares = PROFILES.get(slug)
    if shares is None:
        raise SystemExit(f"NO PROFILE for {slug} ({sector}/{e['threat_event_type']})")
    sp = sum(s for _, s, k in shares if k == "P")
    ss = sum(s for _, s, k in shares if k == "S")
    e["primary_loss"] = lognormal(mu_s + math.log(sp), sigma_s)
    e["secondary_loss"] = lognormal(mu_s + math.log(ss), sigma_s) if ss > 0 else None
    e["loss_tier"] = "paginated"
    e["source_citations"] = [IRIS_CITE]
    e["loss_form_profile"] = build_profile(shares, None)


def main() -> None:
    files = ["data/seed_library_entries.json", "data/seed_library_entries_extension.json"]
    all_e = {}
    per_file = {}
    for f in files:
        rows = json.loads(Path(f).read_text())
        per_file[f] = rows
        for e in rows:
            all_e[e["slug"]] = e
    for e in all_e.values():
        recalibrate(e)

    # per-sector PL + SL distinctness check (R14). A collision that is a GENUINE
    # shared modeled profile (e.g. two IP-theft entries) is a legitimate allowlist
    # entry, NOT a nudge; a coincidental one gets real loss-effect differentiation.
    def _coll(node: str) -> dict:
        by = defaultdict(lambda: defaultdict(list))
        for e in all_e.values():
            n = e.get(node)
            if isinstance(n, dict) and n.get("distribution") == "lognormal":
                by[sector_of(e)][round(n["mean"], 6)].append(e["slug"])
        return {(s, m): sl for s, mm in by.items() for m, sl in mm.items() if len(sl) > 1}

    pl_c, sl_c = _coll("primary_loss"), _coll("secondary_loss")
    if pl_c or sl_c:
        print("COLLISIONS (same sector, same curve):")
        for k, v in pl_c.items():
            print("  PL", k, v)
        for k, v in sl_c.items():
            print("  SL", k, v)
    else:
        print("OK: all primary_loss + secondary_loss lognormals distinct within each sector.")
    for f, rows in per_file.items():
        Path(f).write_text(json.dumps(rows, indent=2) + "\n")
    print(f"recalibrated {len(all_e)} entries -> seed JSON")


if __name__ == "__main__":
    main()
