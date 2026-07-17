"""Epic D-i (#497 sec7) → D-iii-a ENFORCING: the sector-flattening regression guard.

The flattening Epic D fixes is a PRIMARY-loss defect -- recon's near-zero primary
loss shared the identical curve as ransomware's full primary loss within a sector.
Post-D-iii-a (envelopexshare recalibration), **no two archetypes share an identical
primary_loss lognormal** within a sector, except a documented `_PL_ALLOWLIST` of
slug sets that GENUINELY share a modeled curve (loss-effect-identical, per plan-gate
ruling R14 -- an allowlist entry, never a nudge-to-pass).

**Secondary loss is now a within-sector distinctness axis** (2026-07-07 de-templating):
each archetype's SL is differentiated via re-tuned secondary loss-form shares (SL
recomputed as mu_s + ln(Sum secondary shares)), and genuine same-sector shares are
captured in ``_SL_ALLOWLIST``. The unified ``test_within_sector_values_distinct_across_dimensions``
hard-fails on un-allowlisted SL collisions, alongside TEF/vuln/PL.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

import pytest

# IC3 2025 BEC-specific per-complaint arithmetic mean ($3,046,598,558 / 24,768).
# Pinned here so the envelope-consistency guard fails loudly if the builder anchor drifts.
_IC3_BEC_MEAN = 123005.0

_IND2SEC = {
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

# slug sets that LEGITIMATELY share a primary_loss curve (loss-effect-identical),
# each with a one-line rationale. NOT a nudge escape hatch (plan-gate R14).
_PL_ALLOWLIST: list[frozenset[str]] = [
    # Two manufacturing IP-theft archetypes: both model only incident-response +
    # reputation (identical shares); the dominant IP/competitive-advantage loss is
    # UNMODELED for both (no defensible source, sec6 waiver) -- so the MODELED curves
    # are genuinely identical. Allowlisted, not nudged.
    frozenset({"insider-ip-theft-manufacturing", "ip-theft-by-competitor"}),
    # Attack-coverage gap-fill epic (#529 Task 1): edge-ransomware-perimeter-
    # gateway's primary_loss is genuinely identical to ransomware-on-fileshare's
    # (both professional_services, Sum(primary)=0.70, same ransomware family) --
    # the two archetypes differ in initial-access vector and TEF/vuln, not loss
    # magnitude.
    frozenset({"edge-ransomware-perimeter-gateway", "ransomware-on-fileshare"}),
]


def _load() -> list[dict]:
    entries: list[dict] = []
    for name in ("seed_library_entries.json", "seed_library_entries_extension.json"):
        entries.extend(json.loads(Path("data", name).read_text(encoding="utf-8")))
    return entries


def _sector(e: dict) -> str:
    ind = (e.get("calibration_anchor") or {}).get("industry")
    return _IND2SEC.get(ind) or _IND2SEC.get(
        (e.get("applicable_industries") or [None])[0], "technology_saas"
    )


def _envelopes() -> dict[str, dict]:
    rows = json.loads(Path("data", "loss_form_envelopes.json").read_text(encoding="utf-8"))
    return {r["sector"]: r for r in rows}


def _share_sum(profile: list[dict], kind: str) -> float:
    return sum(f["share"] for f in profile if f.get("kind") == kind and f.get("share") is not None)


def _check_node_reconstructs(
    slug: str,
    label: str,
    node: dict,
    shape: str,
    expected_mu: float,
    expected_sigma: float,
    mismatches: list[str],
) -> None:
    """One loss node must reconstruct from its envelope-derived lognormal
    (expected_mu, expected_sigma): catastrophic entries store it natively;
    capped entries store the Milestone B mechanical PERT conversion of it
    (low/high = exp(mu -/+ Z*sigma), mode == low) -- the full citation chain
    survives the conversion."""
    from tests._loss_shape_helpers import expected_pert_from_lognormal

    if shape == "catastrophic":
        if node.get("distribution") != "lognormal":
            mismatches.append(f"{slug}: {label} must stay lognormal (catastrophic)")
            return
        if node["sigma"] != pytest.approx(expected_sigma, abs=1e-9):
            mismatches.append(f"{slug}: {label} sigma {node['sigma']} != {expected_sigma}")
        if node["mean"] != pytest.approx(expected_mu, abs=1e-9):
            mismatches.append(f"{slug}: {label} mu {node['mean']} != {expected_mu}")
        return
    if node.get("distribution") != "PERT":
        mismatches.append(f"{slug}: {label} must be PERT post-Milestone-B (capped)")
        return
    exp_low, exp_high = expected_pert_from_lognormal(expected_mu, expected_sigma)
    if node["low"] != pytest.approx(exp_low, rel=1e-9) or node["high"] != pytest.approx(
        exp_high, rel=1e-9
    ):
        mismatches.append(
            f"{slug}: {label} PERT {node['low']}/{node['high']} != "
            f"envelope-derived {exp_low}/{exp_high}"
        )
    if node["mode"] != node["low"]:
        mismatches.append(f"{slug}: {label} mode {node['mode']} != low {node['low']}")


def test_loss_params_reconstruct_from_envelope_and_shares() -> None:
    """Methodology IMPORTANT-1 (plan-gate R12), re-scoped for Milestone B
    (#loss-pert-overhaul): every loss node must reconstruct EXACTLY from the
    sector envelope + its loss_form_profile shares. Catastrophic entries pin
    sigma == sigma_s and mu == mu_s + ln(Sum shares) natively (BEC vendor tier
    uses the mean-preserving IC3 parameterization); capped entries pin the
    mechanical PERT conversion of that SAME lognormal -- so the citation chain
    is preserved through the shape change.

    Without this guard a future seed edit or builder bug could set a wrong sigma
    or mu and pass the whole suite (the DB<->JSON migration test is tautological
    and the differentiation guard only checks distinctness, not correctness).
    """
    env = _envelopes()
    mismatches: list[str] = []
    for e in _load():
        slug = e["slug"]
        shape = e.get("loss_shape")
        if shape not in ("capped", "catastrophic"):
            mismatches.append(f"{slug}: missing/invalid loss_shape {shape!r}")
            continue
        pl = e.get("primary_loss") or {}
        sec = _sector(e)
        mu_s, sigma_s = env[sec]["mean"], env[sec]["sigma"]
        expected_sigma = round(sigma_s, 10)

        if e.get("loss_tier") == "vendor":
            # Beyond-envelope BEC: mean-preserving mu = ln(mean) - sigma^2/2 so E[loss] = mean.
            expected_mu = round(math.log(_IC3_BEC_MEAN) - sigma_s**2 / 2, 10)
            _check_node_reconstructs(
                slug, "primary", pl, shape, expected_mu, expected_sigma, mismatches
            )
            if e.get("secondary_loss") is not None:
                mismatches.append(f"{slug}: BEC entries must have null secondary_loss")
            continue

        # In-envelope: mu = mu_s + ln(Sum primary shares); secondary likewise when present.
        prof = e.get("loss_form_profile") or []
        sp = _share_sum(prof, "primary")
        if sp <= 0:
            mismatches.append(f"{slug}: no primary share to reconstruct mu")
            continue
        expected_pl_mu = round(mu_s + math.log(sp), 10)
        _check_node_reconstructs(
            slug, "primary", pl, shape, expected_pl_mu, expected_sigma, mismatches
        )
        sl = e.get("secondary_loss")
        ss = _share_sum(prof, "secondary")
        if sl is not None:
            expected_sl_mu = round(mu_s + math.log(ss), 10)
            _check_node_reconstructs(
                slug, "secondary", sl, shape, expected_sl_mu, expected_sigma, mismatches
            )
        elif ss > 0:
            mismatches.append(f"{slug}: has secondary shares (Sum_s={ss}) but null secondary_loss")

    assert not mismatches, "envelope/share reconstruction mismatches:\n" + "\n".join(mismatches)


def test_primary_loss_distinct_across_archetypes() -> None:
    """No two entries share an identical primary_loss lognormal within a sector
    (the anti-flattening core), except a documented _PL_ALLOWLIST set."""
    by_curve: dict[tuple[str, str], set[str]] = defaultdict(set)
    for e in _load():
        pl = e.get("primary_loss") or {}
        if pl.get("distribution") != "lognormal":
            continue
        by_curve[(_sector(e), json.dumps(pl, sort_keys=True))].add(e["slug"])
    offenders = {}
    for (sector, _curve), slugs in by_curve.items():
        if len(slugs) <= 1:
            continue
        if any(slugs <= allowed for allowed in _PL_ALLOWLIST):
            continue  # exactly a documented legitimate shared curve
        offenders[sector] = sorted(slugs)
    assert not offenders, f"primary_loss flattening -- archetypes share a PL curve: {offenders}"


# slug sets that LEGITIMATELY share BOTH an identical TEF and identical vuln
# within a sector (documented genuine duplicates). Empty at ship; mirrors
# _PL_ALLOWLIST. Single-dimension (TEF-only OR vuln-only) sharing is PERMITTED
# and never appears here -- it is legitimate tier-based calibration (D-iii-b).
_TEF_VULN_ALLOWLIST: list[frozenset[str]] = []


def test_tef_vuln_not_fully_templated_within_sector() -> None:
    """R15 (#505): no two same-sector archetypes may share BOTH an identical
    threat_event_frequency AND an identical vulnerability (the combined
    fingerprint) -- that is a copy-paste template. Single-dimension sharing is
    legitimate tier calibration and is NOT flagged here."""
    by_fp: dict[tuple[str, str], set[str]] = defaultdict(set)
    for e in _load():
        fp = (
            json.dumps(e.get("threat_event_frequency"), sort_keys=True),
            json.dumps(e.get("vulnerability"), sort_keys=True),
        )
        by_fp[(_sector(e), json.dumps(fp))].add(e["slug"])
    offenders = {}
    for (sector, _fp), slugs in by_fp.items():
        if len(slugs) <= 1:
            continue
        if any(slugs <= allowed for allowed in _TEF_VULN_ALLOWLIST):
            continue
        offenders[sector] = sorted(slugs)
    assert not offenders, f"TEF+vuln fully templated within a sector: {offenders}"


_TEF_ALLOWLIST: list[frozenset[str]] = []
_VULN_ALLOWLIST: list[frozenset[str]] = [
    frozenset(
        {"safety-system-bypass", "field-instrument-spoofing", "chemical-process-safety-attack"}
    ),
    frozenset({"grid-protective-relay-manipulation", "pipeline-scada-integrity"}),
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


@pytest.mark.parametrize("dim", sorted(_DIM_ALLOWLIST))
def test_within_sector_values_distinct_across_dimensions(dim: str) -> None:
    """#detemplating: no two same-sector archetypes share an identical value on
    any of TEF / vuln / PL / SL, except a documented per-dimension allowlist.
    Null values (absent SL) are skipped."""
    allowlist = _DIM_ALLOWLIST[dim]
    by_val: dict[tuple[str, str], set[str]] = defaultdict(set)
    for e in _load():
        node = e.get(dim)
        if node is None:
            continue
        by_val[(_sector(e), json.dumps(node, sort_keys=True))].add(e["slug"])
    offenders = {}
    for (sector, _v), slugs in by_val.items():
        if len(slugs) <= 1:
            continue
        if any(slugs <= allowed for allowed in allowlist):
            continue
        offenders.setdefault(dim, {})[sector] = sorted(slugs)
    assert not offenders, f"within-sector {dim} collisions: {offenders}"


# Cross-sector genuine-tie TEF pairs (#tef-lognormal M-3 plan-gate ruling). Kept
# identical, not nudged. These are CROSS-sector, so the within-sector guard above
# never sees them (its _TEF_ALLOWLIST stays []); this global guard owns them.
_TEF_GLOBAL_ALLOWLIST: list[frozenset[str]] = [
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


def test_tef_globally_distinct_across_library() -> None:
    """#tef-pert-revert full de-templating: no two of the 93 entries share an
    identical PERT TEF node, except the documented cross-sector genuine-tie
    pairs. Keys on the full node (distribution+low+mode+high)."""
    by_node: dict[str, set[str]] = defaultdict(set)
    for e in _load():
        by_node[json.dumps(e["threat_event_frequency"], sort_keys=True)].add(e["slug"])
    offenders = {
        node: sorted(slugs)
        for node, slugs in by_node.items()
        if len(slugs) > 1 and not any(slugs <= a for a in _TEF_GLOBAL_ALLOWLIST)
    }
    assert not offenders, f"un-allowlisted global TEF collisions: {offenders}"
