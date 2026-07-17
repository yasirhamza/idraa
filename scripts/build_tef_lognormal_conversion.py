"""#tef-lognormal: convert library TEF PERT->lognormal + full cross-sector
de-templating (offline curation; NOT app runtime).

Fits every entry's PERT (low, high) as the lognormal p5/p95 via
fair_cam ... lognormal_from_quantiles -- byte-identical to the wizard's
_fit_lognorm_native (services/wizard_finalize.py). 36 entries across 20 groups
are re-spaced BEFORE fitting (full cross-sector de-templating; 4 genuine-tie
pairs allowlisted) so no two of the 93 share a lognormal TEF. Writes
data/seed_library_entries*.json directly, then runs fail-loud checks. See
docs/reference/tef-cross-sector-ordering.md.

Run: uv run python scripts/build_tef_lognormal_conversion.py
"""

from __future__ import annotations

import itertools
import json
import math
from collections import defaultdict
from pathlib import Path

from fair_cam.quantile_pooling._lognormal_native import (
    lognormal_from_quantiles,
    lognormal_mean,
    lognormal_quantiles,
)

_SEED = Path("data/seed_library_entries.json")
_EXT = Path("data/seed_library_entries_extension.json")

# slug -> new (low, high) applied BEFORE the fit; absent entries keep current.
# Relative-frequency ordering by multiplicative level-shift. Per-group rationale:
# docs/reference/tef-cross-sector-ordering.md.
RESPACE: dict[str, tuple[float, float]] = {
    "financial-call-center-social-eng": (1.4, 17.0),
    "session-hijack-post-mfa-bypass": (0.75, 15.0),
    "retail-store-employee-fraud": (0.42, 11.0),
    "accidental-insider-exposure": (0.8, 12.0),
    "telecom-field-cabinet-tamper": (0.7, 11.0),  # M-2: reversed below api-key
    "retail-ecommerce-checkout-ddos": (0.8, 9.5),
    "cloud-account-takeover": (0.75, 12.0),
    "retail-pos-card-skimming": (0.6, 10.0),
    "ransomware-healthcare-small-practice": (0.42, 6.8),
    "public-sector-targeted-intrusion": (0.35, 5.6),
    "logistics-tms-data-tampering": (0.3, 4.8),
    "healthcare-staff-credential-phish": (0.39, 7.8),
    "hospitality-loyalty-account-takeover": (0.33, 6.6),
    "gov-citizen-portal-ddos": (0.24, 4.8),
    "data-breach-notification-regulatory-tail": (0.25, 6.2),
    "insider-ip-theft-manufacturing": (0.16, 4.0),
    "logistics-disruption": (0.22, 3.75),
    "branch-atm-physical-tamper": (0.25, 5.0),
    "healthcare-record-alteration": (0.22, 4.4),
    "agri-equipment-physical-tamper": (0.15, 3.0),  # M-3: tie w/ education-campus
    "education-campus-facility-tamper": (0.15, 3.0),  # M-3: tie w/ agri-equipment
    "financial-transaction-tampering": (0.23, 3.45),
    "education-student-records-insider": (0.17, 2.55),
    "ransomware-on-historian": (0.12, 2.3),
    "telecom-subscriber-data-breach": (0.085, 1.7),
    "gov-employee-insider-leak": (0.058, 1.7),
    "nation-state-ics-supply-chain": (0.043, 1.3),  # M-1: Tier D rarest, lowest
    "tolling-plant-ransomware-customer-liability": (0.12, 1.8),
    "gov-records-tampering": (0.105, 1.6),
    "crop-science-ip-exfiltration": (0.085, 1.3),
    "manufacturing-facility-sabotage": (0.043, 1.0),
    "solarwinds-class-supply-chain": (0.043, 0.85),
    "denial-of-control": (0.035, 0.7),
    "datacenter-physical-breach": (0.06, 1.2),
    "energy-settlement-platform-tampering-offtaker-liability": (0.04, 0.65),
    "k12-edtech-vendor-breach": (0.043, 0.34),
}
# Genuine ties kept identical (allowlisted, NOT nudged) -- see the reference doc.
_TEF_ALLOWLIST: list[frozenset[str]] = [
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
# Per-group frequency ordering (highest first) for the directional-median guard.
_GROUP_ORDER: list[list[str]] = [
    ["telecom-ddos-core-network", "financial-call-center-social-eng"],
    [
        "credential-stuffing-consumer-portal",
        "session-hijack-post-mfa-bypass",
        "retail-store-employee-fraud",
    ],
    ["api-key-leak-devops", "accidental-insider-exposure", "telecom-field-cabinet-tamper"],
    ["bec-fraud-financial", "retail-ecommerce-checkout-ddos"],
    [
        "cloud-account-takeover",
        "retail-pos-card-skimming",
        "phishing-ad-compromise-ransomware",
        "ransomware-healthcare-small-practice",
        "public-sector-targeted-intrusion",
        "logistics-tms-data-tampering",
    ],
    [
        "healthcare-staff-credential-phish",
        "hospitality-loyalty-account-takeover",
        "third-party-processor-breach",
        "gov-citizen-portal-ddos",
    ],
    [
        "data-breach-notification-regulatory-tail",
        "hmi-credential-compromise",
        "insider-ip-theft-manufacturing",
    ],
    ["professional-payroll-bec", "logistics-disruption"],
    [
        "branch-atm-physical-tamper",
        "healthcare-record-alteration",
        "logistics-warehouse-physical-intrusion",
        "agri-equipment-physical-tamper",
    ],
    [
        "financial-transaction-tampering",
        "energy-billing-system-tamper",
        "education-student-records-insider",
    ],
    ["ransomware-on-historian", "moveit-class-zero-day-mft", "telecom-subscriber-data-breach"],
    [
        "gov-employee-insider-leak",
        "professional-office-physical-theft",
        "nation-state-ics-supply-chain",
    ],
    [
        "tolling-plant-ransomware-customer-liability",
        "gov-records-tampering",
        "oem-remote-maintenance-abuse",
        "crop-science-ip-exfiltration",
    ],
    ["telecom-bgp-route-hijack", "manufacturing-facility-sabotage"],
    ["ip-theft-by-competitor", "solarwinds-class-supply-chain", "denial-of-control"],
    ["ransomware-on-control-layer", "energy-settlement-platform-tampering-offtaker-liability"],
    ["law-enforcement-records-extortion-breach", "k12-edtech-vendor-breach"],
]


def _fit(slug: str, tef: dict) -> tuple[dict, float, float]:
    """Return (lognormal node, source_low, source_high) for an entry's TEF."""
    low, high = RESPACE.get(slug, (tef["low"], tef["high"]))
    d = lognormal_from_quantiles(low, high, 0.05, 0.95)
    node = {
        "distribution": "lognormal",
        "mean": round(d["mean"], 10),
        "sigma": round(d["sigma"], 10),
    }
    return node, low, high


def _die(msg: str) -> None:
    raise SystemExit(f"build_tef_lognormal_conversion: {msg}")


def main() -> None:
    base = json.loads(_SEED.read_text(encoding="utf-8"))
    ext = json.loads(_EXT.read_text(encoding="utf-8"))
    entries = base + ext

    fitted: dict[str, dict] = {}
    medians: dict[str, float] = {}
    for e in entries:
        tef = e["threat_event_frequency"]
        if tef.get("distribution") != "PERT":
            _die(f"{e['slug']} TEF is not PERT (already converted?): {tef}")
        pert_mean = (tef["low"] + 4 * tef["mode"] + tef["high"]) / 6.0
        node, low, high = _fit(e["slug"], tef)
        fitted[e["slug"]] = node
        p5, med, p95 = lognormal_quantiles(node["mean"], node["sigma"], [0.05, 0.5, 0.95])
        medians[e["slug"]] = med
        # round-trip: p5/p95 recover the (possibly re-spaced) source bounds
        if not (math.isclose(p5, low, rel_tol=1e-6) and math.isclose(p95, high, rel_tol=1e-6)):
            _die(f"{e['slug']} round-trip failed: p5={p5} p95={p95} vs ({low},{high})")
        # mean-band on the 57 non-re-spaced entries only
        if e["slug"] not in RESPACE:
            ratio = lognormal_mean(node["mean"], node["sigma"]) / pert_mean
            if not (0.85 <= ratio <= 1.20):
                _die(f"{e['slug']} mean ratio {ratio:.3f} outside [0.85, 1.20]")

    # directional ordering: each group's medians strictly descending (highest first)
    for grp in _GROUP_ORDER:
        for hi, lo in itertools.pairwise(grp):
            if not medians[hi] > medians[lo]:
                _die(f"order broken in group: {hi} median {medians[hi]} !> {lo} {medians[lo]}")

    # GLOBAL distinctness (full de-templating): no two of the 93 share a lognormal
    # node, except a documented _TEF_ALLOWLIST genuine-tie pair.
    by_node: dict[str, set[str]] = defaultdict(set)
    for e in entries:
        by_node[json.dumps(fitted[e["slug"]], sort_keys=True)].add(e["slug"])
    collisions = {
        k: sorted(v)
        for k, v in by_node.items()
        if len(v) > 1 and not any(v <= a for a in _TEF_ALLOWLIST)
    }
    if collisions:
        _die(f"global TEF collisions after fit (excl allowlist): {collisions}")

    # write back (mutate in place, preserve all other fields + order)
    for group, path in ((base, _SEED), (ext, _EXT)):
        for e in group:
            e["threat_event_frequency"] = fitted[e["slug"]]
        path.write_text(json.dumps(group, indent=2) + "\n", encoding="utf-8")
    n_distinct = len(by_node)
    print(
        f"converted {len(entries)} TEF nodes to lognormal; "
        f"0 global collisions (excl allowlist); {n_distinct} distinct nodes"
    )


if __name__ == "__main__":
    main()
