"""Milestone B (#loss-pert-overhaul): library loss representation guard.

91 capped entries: PL/SL are bounded PERT with mode == low < high (the
analytic-mode clamp fires for every entry). 11 catastrophic entries: PL/SL
stay uncapped lognormal (the attack-coverage gap-fill epic, #529, added the
11th -- W1 destructive-wiper-nationstate). Every entry carries an explicit
loss_shape. Spot pins anchor the mechanical conversion low/high = exp(mu -/+
1.6448536269514722*sigma) and the catastrophic params.

#sigma-recalibration (PR1 Task 2): sigma is now the within-scenario default
(1.7) everywhere -- the "sigma range 1.838-3.472 > 1.645" mode-clamp margin
and the "byte-unchanged" catastrophic-param claim are pre-recalibration
history; mu/mean stays held (median-anchored) except the two Task-0-overridden
wiper fields (docs/reference/within-scenario-sigma-calibration.md adjustment
(d)) -- none of this file's spot pins are the wiper, so every mean/mu below is
unchanged from the pre-recalibration value, only sigma/low/high moved."""

from __future__ import annotations

import json
from pathlib import Path

_SEEDS = ("data/seed_library_entries.json", "data/seed_library_entries_extension.json")

_CATASTROPHIC = frozenset(
    {
        "chemical-process-safety-attack",
        "safety-system-bypass",
        "unauthorized-plc-modification",
        "field-instrument-spoofing",
        "grid-protective-relay-manipulation",
        "denial-of-control",
        "pipeline-scada-integrity",
        "nation-state-ics-supply-chain",
        "solarwinds-class-supply-chain",
        "telecom-lawful-intercept-nationstate-compromise",
        # Attack-coverage gap-fill epic (#529 Task 1): W1, owner-approved
        # 2026-07-09 (C2 -- nation-state, self-propagating wiper, unbounded
        # blast radius).
        "destructive-wiper-nationstate",
    }
)

# Mechanical-conversion spot pins, re-pinned post-#sigma-recalibration (PR1
# Task 2): mu/mean held (median-anchored for the non-vendor pair; IC3
# mean-anchored for the vendor pair -- both vendor entries land on the SAME
# curve, the documented shared-curve consequence of the mean-anchor, D11'),
# sigma -> WITHIN_SCENARIO_SIGMA_DEFAULT (1.7): (pl_low, pl_high).
_SPOT_PERT = {
    "ransomware-on-ehr": (24478.8629170978, 6570284.009215002),
    "web-app-exploitation": (13428.4606082079, 3604285.064272992),
    # Vendor mean-anchor (IC3 $123,005): identical curve on both slugs.
    "telecom-sim-swap-fraud": (1769.9898978414, 475076.6553840482),
    "bec-fraud-financial": (1769.9898978414, 475076.6553840482),
}
# Catastrophic spot pins, re-pinned post-#sigma-recalibration: mean/mu held
# (none of these three is the destructive-wiper-nationstate override), sigma
# -> WITHIN_SCENARIO_SIGMA_DEFAULT (1.7).
_SPOT_LOGNORMAL = {
    "chemical-process-safety-attack": (13.6876771865, 1.7),
    "nation-state-ics-supply-chain": (11.4605789846, 1.7),
    "solarwinds-class-supply-chain": (13.1275499041, 1.7),
}


def _entries() -> list[dict]:
    out: list[dict] = []
    for p in _SEEDS:
        out.extend(json.loads(Path(p).read_text(encoding="utf-8")))
    return out


def test_loss_shape_and_distribution_shape_by_class() -> None:
    entries = _entries()
    assert len(entries) == 102
    seen_cat: set[str] = set()
    for e in entries:
        shape = e.get("loss_shape")
        assert shape in ("capped", "catastrophic"), f"{e['slug']}: missing/invalid loss_shape"
        nodes = [e["primary_loss"]] + ([e["secondary_loss"]] if e.get("secondary_loss") else [])
        if shape == "catastrophic":
            seen_cat.add(e["slug"])
            for n in nodes:
                assert n["distribution"] == "lognormal", (e["slug"], n)
                assert n["sigma"] > 0
        else:
            for n in nodes:
                assert n["distribution"] == "PERT", (e["slug"], n)
                assert n["low"] == n["mode"] < n["high"], (e["slug"], n)
                assert "mean" not in n and "sigma" not in n
    assert seen_cat == set(_CATASTROPHIC)


def test_conversion_spot_pins() -> None:
    by_slug = {e["slug"]: e for e in _entries()}
    for slug, (low, high) in _SPOT_PERT.items():
        pl = by_slug[slug]["primary_loss"]
        assert (pl["low"], pl["high"]) == (low, high), slug
    for slug, (mean, sigma) in _SPOT_LOGNORMAL.items():
        pl = by_slug[slug]["primary_loss"]
        assert (pl["mean"], pl["sigma"]) == (mean, sigma), slug
