"""Milestone B (#loss-pert-overhaul) one-shot seed conversion.

Converts the 83 capped entries' PL/SL lognormal {mean, sigma} to bounded PERT
low = exp(mu - Z*sigma), high = exp(mu + Z*sigma), mode = low (the analytic
mode exp(mu - sigma^2) is below p5 for every entry -- ASSERTED, not assumed),
and stamps loss_shape on all 93 (capped default, catastrophic shortlist per
spec 2026-07-09 §3). Catastrophic entries' loss nodes are byte-unchanged.

One-shot by design: re-running against converted seed dies on the
loss_shape-already-present precondition. Run from the repo root:
    uv run python scripts/build_loss_pert_conversion.py
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

_PROJ = Path(__file__).resolve().parent.parent
for p in (_PROJ / "src",):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from idraa.services.seed_library_loader import LibraryEntrySeed  # noqa: E402

Z = 1.6448536269514722
_SEEDS = (
    _PROJ / "data" / "seed_library_entries.json",
    _PROJ / "data" / "seed_library_entries_extension.json",
)
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
    }
)


def _die(msg: str) -> None:
    raise SystemExit(f"FATAL: {msg}")


def _convert(node: dict) -> dict:
    if node.get("distribution") != "lognormal":
        _die(f"expected lognormal loss node, got {node!r}")
    mu, sigma = node["mean"], node["sigma"]
    if sigma <= Z:
        _die(f"sigma={sigma} <= Z -- analytic mode would NOT clamp; plan premise broken")
    low = round(math.exp(mu - Z * sigma), 10)
    high = round(math.exp(mu + Z * sigma), 10)
    if not (0 < low < high):
        _die(f"bad bounds low={low} high={high}")
    return {"distribution": "PERT", "low": low, "mode": low, "high": high}


def main() -> None:
    total, converted, cat_seen = 0, 0, set()
    for path in _SEEDS:
        entries = json.loads(path.read_text(encoding="utf-8"))
        for e in entries:
            total += 1
            if "loss_shape" in e:
                _die(f"{e['slug']}: loss_shape already present -- builder is one-shot")
            if e["slug"] in _CATASTROPHIC:
                e["loss_shape"] = "catastrophic"
                cat_seen.add(e["slug"])
                if e["primary_loss"].get("distribution") != "lognormal":
                    _die(f"{e['slug']}: catastrophic entry not lognormal")
                continue
            e["loss_shape"] = "capped"
            e["primary_loss"] = _convert(e["primary_loss"])
            if e.get("secondary_loss"):
                e["secondary_loss"] = _convert(e["secondary_loss"])
            converted += 1
            LibraryEntrySeed.model_validate(e)  # fail-loud schema check
        path.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
    if total != 93 or converted != 83 or cat_seen != _CATASTROPHIC:
        _die(f"counts off: total={total} converted={converted} cat={sorted(cat_seen)}")
    print(f"converted {converted} capped entries; {len(cat_seen)} catastrophic unchanged")


if __name__ == "__main__":
    main()
