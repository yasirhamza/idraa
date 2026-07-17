"""Milestone B (#loss-pert-overhaul): library loss representation guard.

91 capped entries: PL/SL are bounded PERT with mode == low < high (the
analytic-mode clamp fires for every entry, sigma range 1.838-3.472 > 1.645).
11 catastrophic entries: PL/SL stay uncapped lognormal, byte-unchanged (the
attack-coverage gap-fill epic, #529, added the 11th -- W1
destructive-wiper-nationstate). Every entry carries an explicit loss_shape.
Spot pins anchor the mechanical conversion low/high = exp(mu -/+
1.6448536269514722*sigma) and the untouched catastrophic params."""

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

# Mechanical-conversion spot pins (plan pinned table): (pl_low, pl_high).
_SPOT_PERT = {
    "ransomware-on-ehr": (15955.6628554057, 10080000.000343738),
    "web-app-exploitation": (1134.0206187144, 42679999.99965714),
    # Honest wide-sigma outliers (methodology-flagged, accepted):
    "telecom-sim-swap-fraud": (0.9810565461, 89611.6799463846),
    "bec-fraud-financial": (3.7573034111, 141409.8711597068),
}
# Catastrophic byte-unchanged spot pins: (pl_mean, pl_sigma).
_SPOT_LOGNORMAL = {
    "chemical-process-safety-attack": (13.6876771865, 2.2723417799),
    "nation-state-ics-supply-chain": (11.4605789846, 1.8377081683),
    "solarwinds-class-supply-chain": (13.1275499041, 3.4721527617),
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
