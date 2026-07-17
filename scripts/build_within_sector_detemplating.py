"""#detemplating: within-sector de-templating builder (offline curation; NOT app runtime).

Differentiates templated within-sector TEF / vulnerability values and re-tunes
secondary-loss shares (-> secondary_loss recomputed) across the 93 library
entries in data/seed_library_entries*.json, per
docs/superpowers/plans/2026-07-07-within-sector-detemplating.md ("The
differentiated values"). PRIMARY loss (primary_loss + primary-kind
loss_form_profile forms) is UNCHANGED -- only secondary-kind forms + the
derived secondary_loss lognormal are re-tuned. Sources of truth are the
JSON files; this script writes them directly and then runs a fail-loud
within-sector collision check (mirroring
tests/integration/test_library_loss_differentiation.py's
test_within_sector_values_distinct_across_dimensions) over ALL 93 entries on
all 4 dimensions, honoring the same per-dimension allowlists.

Run: uv run python scripts/build_within_sector_detemplating.py
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

ENV = {r["sector"]: r for r in json.loads(Path("data/loss_form_envelopes.json").read_text())}
_SEED = Path("data/seed_library_entries.json")
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


# ---------------------------------------------------------------------------
# The differentiated values (transcribed verbatim from the plan's manifest
# tables -- see docs/superpowers/plans/2026-07-07-within-sector-detemplating.md
# "## The differentiated values").
# ---------------------------------------------------------------------------

TEF_NEW: dict[str, tuple[float, float, float]] = {
    "process-view-manipulation": (0.01, 0.06, 0.3),
    "pipeline-nomination-scada-curtailment-shipper-penalty": (0.03, 0.13, 0.6),
    "web-app-exploitation": (0.8, 3.0, 10.0),
    "ddos-financial-seasonal-peak": (0.6, 2.5, 9.0),
    "insider-data-theft-financial": (0.4, 1.5, 6.0),
    "branch-atm-physical-tamper": (0.2, 1.0, 4.0),
    "hospitality-pos-card-skimming": (0.3, 1.0, 3.5),
    "hospitality-guest-data-insider": (0.15, 0.6, 2.5),
    "manufacturing-billing-fraud": (0.15, 0.6, 2.5),
    "food-recall-data-tampering": (0.08, 0.4, 1.8),
    "manufacturing-facility-sabotage": (0.05, 0.3, 1.2),
    "professional-payroll-bec": (0.3, 1.2, 5.0),
    "professional-office-physical-theft": (0.05, 0.3, 1.5),
    "s3-misconfiguration-data-exposure": (0.6, 2.5, 12.0),
    "mfa-fatigue-prompt-bombing": (0.4, 1.5, 7.0),
    "competitor-trade-secret-recruit": (0.12, 0.6, 2.5),
    "saas-revenue-outage-sabotage": (0.08, 0.4, 1.6),
    "telecom-bgp-route-hijack": (0.05, 0.3, 1.2),
}

VULN_NEW: dict[str, tuple[float, float, float]] = {
    "ransomware-on-historian": (0.05, 0.25, 0.5),
    "process-view-manipulation": (0.05, 0.15, 0.35),
    "hmi-credential-compromise": (0.1, 0.4, 0.7),
    "pipeline-nomination-scada-curtailment-shipper-penalty": (0.1, 0.32, 0.62),
    "energy-settlement-platform-tampering-offtaker-liability": (0.12, 0.45, 0.72),
    "it-ot-bridge-compromise": (0.1, 0.3, 0.6),
    "nation-state-ics-supply-chain": (0.1, 0.35, 0.65),
    "oem-remote-maintenance-abuse": (0.08, 0.28, 0.55),
    "hacktivist-ot-disruption": (0.15, 0.38, 0.68),
    "energy-billing-system-tamper": (0.2, 0.5, 0.8),
    "watering-hole-industry-targeted": (0.05, 0.2, 0.45),
    "insider-data-theft-financial": (0.05, 0.22, 0.48),
    "public-sector-targeted-intrusion": (0.1, 0.32, 0.6),
    "gov-employee-insider-leak": (0.1, 0.4, 0.7),
    "unauthorized-plc-modification": (0.05, 0.12, 0.3),
    # NOTE the IP swap vs. the pre-existing (identical) values: insider access
    # is a HIGHER inherent susceptibility than an external competitor who must
    # recruit/compromise to gain access.
    "insider-ip-theft-manufacturing": (0.08, 0.25, 0.5),
    "ransomware-on-control-layer": (0.1, 0.3, 0.6),
    "ip-theft-by-competitor": (0.05, 0.2, 0.45),
    "manufacturing-billing-fraud": (0.1, 0.35, 0.65),
    "food-recall-data-tampering": (0.15, 0.4, 0.7),
    "tolling-plant-ransomware-customer-liability": (0.15, 0.48, 0.78),
    "data-breach-notification-regulatory-tail": (0.1, 0.28, 0.55),
    "retail-store-employee-fraud": (0.2, 0.5, 0.8),
    "api-key-leak-devops": (0.1, 0.3, 0.6),
    "session-hijack-post-mfa-bypass": (0.08, 0.22, 0.5),
    "solarwinds-class-supply-chain": (0.12, 0.35, 0.7),
    "datacenter-physical-breach": (0.05, 0.18, 0.45),
}

SL_SECONDARY: dict[str, list[tuple[str, float]]] = {
    "it-ot-bridge-compromise": [("reputation", 0.13)],
    "nation-state-ics-supply-chain": [("reputation", 0.18)],
    "energy-settlement-platform-tampering-offtaker-liability": [("fines", 0.15)],
    "accidental-insider-exposure": [("reputation", 0.12), ("fines", 0.10)],
    "healthcare-staff-credential-phish": [("reputation", 0.15), ("fines", 0.12)],
    "safety-system-bypass": [("fines", 0.09)],
    "chemical-process-safety-attack": [("fines", 0.10)],
    "ransomware-on-virtualization-stack": [("reputation", 0.14)],
    "food-cold-chain-ransomware": [("reputation", 0.11)],
    "food-recall-data-tampering": [("reputation", 0.13), ("fines", 0.09)],
    "tolling-plant-ransomware-customer-liability": [("fines", 0.16), ("reputation", 0.12)],
    "cloud-account-takeover": [("reputation", 0.15)],
    "solarwinds-class-supply-chain": [("reputation", 0.20)],
    "session-hijack-post-mfa-bypass": [("reputation", 0.13)],
    "competitor-trade-secret-recruit": [("reputation", 0.11)],
    "saas-revenue-outage-sabotage": [("reputation", 0.17)],
    "package-registry-supply-chain": [("reputation", 0.16)],
    "mfa-fatigue-prompt-bombing": [("reputation", 0.10)],
    "telecom-bgp-route-hijack": [("reputation", 0.09)],
    "telecom-lawful-intercept-nationstate-compromise": [("reputation", 0.12)],
    "logistics-tms-data-tampering": [("reputation", 0.15)],
}

# ---------------------------------------------------------------------------
# Within-sector distinctness allowlists (mirrors
# tests/integration/test_library_loss_differentiation.py Task 1).
# ---------------------------------------------------------------------------

_TEF_ALLOWLIST: list[frozenset[str]] = []
_VULN_ALLOWLIST: list[frozenset[str]] = [
    frozenset(
        {"safety-system-bypass", "field-instrument-spoofing", "chemical-process-safety-attack"}
    ),
    frozenset({"grid-protective-relay-manipulation", "pipeline-scada-integrity"}),
]
_PL_ALLOWLIST: list[frozenset[str]] = [
    frozenset({"insider-ip-theft-manufacturing", "ip-theft-by-competitor"}),
]
_SL_ALLOWLIST: list[frozenset[str]] = [
    frozenset({"insider-ip-theft-manufacturing", "ip-theft-by-competitor"}),
]

_DIM_ALLOWLIST = {
    "threat_event_frequency": _TEF_ALLOWLIST,
    "vulnerability": _VULN_ALLOWLIST,
    "primary_loss": _PL_ALLOWLIST,
    "secondary_loss": _SL_ALLOWLIST,
}


def _share_sum(profile: list[dict], kind: str) -> float:
    return sum(f["share"] for f in profile if f.get("kind") == kind and f.get("share") is not None)


def _recompute_profile(profile: list[dict], new_secondary: list[tuple[str, float]]) -> list[dict]:
    """Keep PRIMARY forms byte-identical; replace ONLY the secondary-kind forms
    with the new (form, share) list; recompute composition_role across ALL
    forms (primary + secondary) whose share is not None."""
    kept = [dict(f) for f in profile if f.get("kind") != "secondary"]
    new_forms = [
        {
            "form": form,
            "kind": "secondary",
            "magnitude_basis": _MB,
            "citations": [],
            "verified": False,
            "composition_role": "contributing",
            "share": round(share, 4),
        }
        for form, share in new_secondary
    ]
    combined = kept + new_forms
    scored_shares = [f["share"] for f in combined if f.get("share") is not None]
    dom = max(scored_shares) if scored_shares else None
    for f in combined:
        if f.get("share") is None:
            continue
        f["composition_role"] = "dominant" if f["share"] == dom else "contributing"
    return combined


def _apply(entries: list[dict]) -> int:
    touched = 0
    for e in entries:
        slug = e["slug"]
        changed = False
        if slug in TEF_NEW:
            low, mode, high = TEF_NEW[slug]
            e["threat_event_frequency"] = {
                "distribution": "PERT",
                "low": low,
                "mode": mode,
                "high": high,
            }
            changed = True
        if slug in VULN_NEW:
            low, mode, high = VULN_NEW[slug]
            e["vulnerability"] = {
                "distribution": "PERT",
                "low": low,
                "mode": mode,
                "high": high,
            }
            changed = True
        if slug in SL_SECONDARY:
            sec = sector_of(e)
            mu_s, sigma_s = ENV[sec]["mean"], ENV[sec]["sigma"]
            profile = e.get("loss_form_profile") or []
            new_profile = _recompute_profile(profile, SL_SECONDARY[slug])
            e["loss_form_profile"] = new_profile
            ss = _share_sum(new_profile, "secondary")
            e["secondary_loss"] = {
                "distribution": "lognormal",
                "mean": round(mu_s + math.log(ss), 10),
                "sigma": round(sigma_s, 10),
            }
            changed = True
        if changed:
            touched += 1
    return touched


def _collision_check(entries: list[dict]) -> int:
    """Mirror test_within_sector_values_distinct_across_dimensions over ALL 93
    entries on all 4 dimensions, honoring the allowlists. Returns the count of
    un-allowlisted collisions found (0 == clean)."""
    offenders: dict[str, dict[str, list[str]]] = {}
    for dim, allowlist in _DIM_ALLOWLIST.items():
        by_val: dict[tuple[str, str], set[str]] = defaultdict(set)
        for e in entries:
            node = e.get(dim)
            if node is None:
                continue
            by_val[(sector_of(e), json.dumps(node, sort_keys=True))].add(e["slug"])
        for (sector, _v), slugs in by_val.items():
            if len(slugs) <= 1:
                continue
            if any(slugs <= allowed for allowed in allowlist):
                continue
            offenders.setdefault(dim, {})[sector] = sorted(slugs)

    n = sum(len(sectors) for sectors in offenders.values())
    if offenders:
        print("UN-ALLOWLISTED WITHIN-SECTOR COLLISIONS:")
        for dim, sectors in offenders.items():
            for sector, slugs in sectors.items():
                print(f"  {dim} ({sector!r}) {slugs}")
    return n


def _share_budget_check(entries: list[dict]) -> list[str]:
    problems = []
    for e in entries:
        profile = e.get("loss_form_profile") or []
        total = sum(f["share"] for f in profile if f.get("share") is not None)
        if total > 1.0 + 1e-9:
            problems.append(f"{e['slug']}: Sum(all shares)={total} > 1")
    return problems


def main() -> None:
    seed_rows = json.loads(_SEED.read_text(encoding="utf-8"))
    ext_rows = json.loads(_EXT.read_text(encoding="utf-8"))

    n_seed = _apply(seed_rows)
    n_ext = _apply(ext_rows)
    print(f"touched {n_seed} entries in {_SEED}, {n_ext} entries in {_EXT}")

    _SEED.write_text(json.dumps(seed_rows, indent=2) + "\n", encoding="utf-8")
    _EXT.write_text(json.dumps(ext_rows, indent=2) + "\n", encoding="utf-8")

    all_entries = seed_rows + ext_rows
    if len(all_entries) != 93:
        raise SystemExit(f"expected 93 entries total, got {len(all_entries)}")

    budget_problems = _share_budget_check(all_entries)
    if budget_problems:
        print("SHARE BUDGET VIOLATIONS:")
        for p in budget_problems:
            print(f"  {p}")
        raise SystemExit(f"{len(budget_problems)} share-budget violations -- aborting")

    n_collisions = _collision_check(all_entries)
    if n_collisions:
        raise SystemExit(f"{n_collisions} un-allowlisted collisions -- aborting")
    print("0 un-allowlisted collisions")


if __name__ == "__main__":
    main()
