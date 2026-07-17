"""#tef-pert-revert (Milestone A): convert library TEF lognormal->PERT (offline
curation; NOT app runtime). Reverses #520. (low, high) = the bounds #520 fed its
lognormal fit (so #520's de-templating is preserved byte-for-byte); mode = the
original pre-#520 mode for the 57 unchanged entries, or relative-skew re-derived
mode_frac=(old_mode-old_low)/(old_high-old_low) placed into the new (low,high) for
the 36 re-spaced entries. The resulting 93 triples are PINNED below (regenerable
from `git show 96e27dc:data/seed_library_entries*.json` + #520's RESPACE). See
docs/reference/tef-representation.md.

Run: uv run python scripts/build_tef_pert_revert.py
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

_SEED = Path("data/seed_library_entries.json")
_EXT = Path("data/seed_library_entries_extension.json")

# slug -> (low, mode, high). Pinned; see module docstring for derivation.
_PERT: dict[str, tuple[float, float, float]] = {
    "ransomware-on-ehr": (0.5, 1.5, 4.0),
    "ransomware-on-historian": (0.12, 0.578947, 2.3),
    "unauthorized-plc-modification": (0.02, 0.1, 0.5),
    "safety-system-bypass": (0.01, 0.05, 0.25),
    "denial-of-control": (0.035, 0.14, 0.7),
    "hmi-credential-compromise": (0.2, 1.0, 5.0),
    "it-ot-bridge-compromise": (0.1, 0.5, 2.5),
    "nation-state-ics-supply-chain": (0.043, 0.216379, 1.3),
    "hacktivist-ot-disruption": (0.2, 1.5, 8.0),
    "bec-fraud-financial": (1.0, 3.0, 12.0),
    "ransomware-on-virtualization-stack": (0.3, 1.0, 4.0),
    "insider-data-theft-financial": (0.4, 1.5, 6.0),
    "insider-ip-theft-manufacturing": (0.16, 0.8, 4.0),
    "cloud-account-takeover": (0.75, 3.0, 12.0),
    "api-key-leak-devops": (1.0, 4.0, 15.0),
    "ddos-extortion-financial": (0.5, 2.0, 10.0),
    "solarwinds-class-supply-chain": (0.043, 0.170421, 0.85),
    "moveit-class-zero-day-mft": (0.1, 0.5, 2.0),
    "session-hijack-post-mfa-bypass": (0.75, 3.0, 15.0),
    "watering-hole-industry-targeted": (0.1, 0.5, 3.0),
    "s3-misconfiguration-data-exposure": (0.6, 2.5, 12.0),
    "package-registry-supply-chain": (0.5, 3.0, 12.0),
    "ddos-financial-seasonal-peak": (0.6, 2.5, 9.0),
    "phishing-ad-compromise-ransomware": (0.5, 2.0, 8.0),
    "ransomware-on-fileshare": (1.0, 3.0, 10.0),
    "credential-stuffing-consumer-portal": (1, 5, 20),
    "mfa-fatigue-prompt-bombing": (0.4, 1.5, 7.0),
    "ransomware-healthcare-small-practice": (0.42, 1.696, 6.8),
    "ot-network-scanning-reconnaissance": (0.5, 3.0, 15.0),
    "data-breach-notification-regulatory-tail": (0.25, 1.241667, 6.2),
    "generative-ai-prompt-injection": (0.5, 3.0, 20.0),
    "ransomware-on-control-layer": (0.05, 0.2, 0.8),
    "process-view-manipulation": (0.01, 0.06, 0.3),
    "field-instrument-spoofing": (0.02, 0.08, 0.4),
    "oem-remote-maintenance-abuse": (0.1, 0.4, 1.5),
    "grid-protective-relay-manipulation": (0.02, 0.1, 0.45),
    "pipeline-scada-integrity": (0.02, 0.08, 0.4),
    "chemical-process-safety-attack": (0.005, 0.03, 0.15),
    "accidental-insider-exposure": (0.8, 3.2, 12.0),
    "web-app-exploitation": (0.8, 3.0, 10.0),
    "third-party-processor-breach": (0.3, 1.5, 6.0),
    "retail-pos-card-skimming": (0.6, 2.48, 10.0),
    "public-sector-targeted-intrusion": (0.35, 1.4, 5.6),
    "logistics-disruption": (0.22, 0.745745, 3.75),
    "telecom-subscriber-data-breach": (0.085, 0.34, 1.7),
    "hospitality-pos-card-skimming": (0.3, 1.0, 3.5),
    "hospitality-loyalty-account-takeover": (0.33, 1.65, 6.6),
    "hospitality-guest-data-insider": (0.15, 0.6, 2.5),
    "education-student-records-insider": (0.17, 0.68, 2.55),
    "gov-citizen-portal-ddos": (0.24, 1.2, 4.8),
    "gov-records-tampering": (0.105, 0.425357, 1.6),
    "gov-employee-insider-leak": (0.058, 0.341103, 1.7),
    "ip-theft-by-competitor": (0.05, 0.2, 1.0),
    "manufacturing-billing-fraud": (0.15, 0.6, 2.5),
    "healthcare-staff-credential-phish": (0.39, 1.95, 7.8),
    "professional-payroll-bec": (0.3, 1.2, 5.0),
    "energy-billing-system-tamper": (0.2, 0.8, 3.0),
    "telecom-ddos-core-network": (2.0, 8.0, 24.0),
    "telecom-sim-swap-fraud": (5.0, 20.0, 60.0),
    "telecom-bgp-route-hijack": (0.05, 0.3, 1.2),
    "telecom-field-cabinet-tamper": (0.7, 3.642857, 11.0),
    "food-cold-chain-ransomware": (0.2, 0.8, 2.5),
    "food-recall-data-tampering": (0.08, 0.4, 1.8),
    "agri-equipment-physical-tamper": (0.15, 0.75, 3.0),
    "agri-coop-bec-fraud": (2.0, 8.0, 20.0),
    "crop-science-ip-exfiltration": (0.085, 0.345357, 1.3),
    "hospitality-booking-ddos-peak-season": (2.0, 6.0, 18.0),
    "education-research-ip-exfiltration": (0.3, 1.0, 3.0),
    "logistics-tms-data-tampering": (0.3, 1.2, 4.8),
    "logistics-warehouse-physical-intrusion": (0.2, 1.0, 4.0),
    "competitor-trade-secret-recruit": (0.12, 0.6, 2.5),
    "datacenter-physical-breach": (0.06, 0.3, 1.2),
    "branch-atm-physical-tamper": (0.25, 1.25, 5.0),
    "financial-transaction-tampering": (0.23, 1.15, 3.45),
    "healthcare-record-alteration": (0.22, 1.1, 4.4),
    "retail-ecommerce-checkout-ddos": (0.8, 3.172727, 9.5),
    "saas-revenue-outage-sabotage": (0.08, 0.4, 1.6),
    "professional-office-physical-theft": (0.05, 0.3, 1.5),
    "retail-store-employee-fraud": (0.42, 2.647368, 11.0),
    "manufacturing-facility-sabotage": (0.043, 0.251043, 1.0),
    "financial-call-center-social-eng": (1.4, 5.654545, 17.0),
    "education-campus-facility-tamper": (0.15, 0.75, 3.0),
    "tolling-plant-ransomware-customer-liability": (0.12, 0.48, 1.8),
    "pipeline-nomination-scada-curtailment-shipper-penalty": (0.03, 0.13, 0.6),
    "energy-settlement-platform-tampering-offtaker-liability": (0.04, 0.162, 0.65),
    "physician-practice-clearinghouse-revenue-disruption": (0.05, 0.15, 0.5),
    "law-enforcement-records-extortion-breach": (0.05, 0.15, 0.4),
    "casino-ransomware-operational-disruption": (0.1, 0.25, 0.6),
    "telecom-lawful-intercept-nationstate-compromise": (0.02, 0.1, 0.3),
    "law-firm-privileged-data-ransomware-extortion": (0.1, 0.25, 0.6),
    "k12-edtech-vendor-breach": (0.043, 0.127857, 0.34),
    "higher-ed-insider-ddos": (0.05, 0.12, 0.35),
    "judiciary-court-system-ransomware": (0.08, 0.2, 0.5),
}
# Genuine-tie pairs (identical PERT triples), from #520's allowlist.
_ALLOWLIST: list[frozenset[str]] = [
    frozenset({"field-instrument-spoofing", "pipeline-scada-integrity"}),
    frozenset(
        {
            "casino-ransomware-operational-disruption",
            "law-firm-privileged-data-ransomware-extortion",
        }
    ),
    frozenset({"manufacturing-billing-fraud", "hospitality-guest-data-insider"}),
    frozenset({"agri-equipment-physical-tamper", "education-campus-facility-tamper"}),
]


def _die(msg: str) -> None:
    raise SystemExit(f"build_tef_pert_revert: {msg}")


def main() -> None:
    base = json.loads(_SEED.read_text(encoding="utf-8"))
    ext = json.loads(_EXT.read_text(encoding="utf-8"))
    entries = base + ext

    slugs = {e["slug"] for e in entries}
    if set(_PERT) != slugs:
        _die(f"_PERT slug set mismatch: missing={slugs - set(_PERT)} extra={set(_PERT) - slugs}")

    nodes: dict[str, dict] = {}
    for e in entries:
        tef = e["threat_event_frequency"]
        if tef.get("distribution") != "lognormal":
            _die(f"{e['slug']} TEF is not lognormal (already reverted?): {tef}")
        low, mode, high = _PERT[e["slug"]]
        if not (low < mode < high):
            _die(f"{e['slug']} invalid PERT: low={low} mode={mode} high={high}")
        nodes[e["slug"]] = {"distribution": "PERT", "low": low, "mode": mode, "high": high}

    # global distinctness (full de-templating), excl the 4 allowlisted genuine-tie pairs
    by_node: dict[str, set[str]] = defaultdict(set)
    for e in entries:
        by_node[json.dumps(nodes[e["slug"]], sort_keys=True)].add(e["slug"])
    collisions = {
        k: sorted(v)
        for k, v in by_node.items()
        if len(v) > 1 and not any(v <= a for a in _ALLOWLIST)
    }
    if collisions:
        _die(f"un-allowlisted PERT TEF collisions: {collisions}")
    n_distinct = len(by_node)

    for group, path in ((base, _SEED), (ext, _EXT)):
        for e in group:
            e["threat_event_frequency"] = nodes[e["slug"]]
        path.write_text(json.dumps(group, indent=2) + "\n", encoding="utf-8")
    print(f"reverted {len(entries)} TEF nodes to PERT; {n_distinct} distinct nodes (excl 4 ties)")


if __name__ == "__main__":
    main()
