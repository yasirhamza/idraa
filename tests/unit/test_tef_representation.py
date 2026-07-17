"""TEF representation guard (#tef-pert-revert / Milestone A): every library TEF
is bounded PERT {low, mode, high}. Reverses #520 (TEF lognormal). vuln stays PERT;
PL/SL untouched (Milestone B)."""

from __future__ import annotations

import json
from pathlib import Path


def _load() -> list[dict]:
    entries: list[dict] = []
    for name in ("seed_library_entries.json", "seed_library_entries_extension.json"):
        entries.extend(json.loads(Path("data", name).read_text(encoding="utf-8")))
    return entries


def test_all_seed_tef_is_pert() -> None:
    """No lognormal TEF remains: every threat_event_frequency is PERT with
    low<mode<high and no mean/sigma keys."""
    offenders = {}
    for e in _load():
        tef = e["threat_event_frequency"]
        ok = (
            tef.get("distribution") == "PERT"
            and {"low", "mode", "high"} <= set(tef)
            and not ({"mean", "sigma"} & set(tef))
            and tef["low"] < tef["mode"] < tef["high"]
        )
        if not ok:
            offenders[e["slug"]] = tef
    assert not offenders, f"non-PERT / malformed TEF: {offenders}"


def test_tef_bounds_match_pre520_fed_bounds() -> None:
    """The PERT (low, high) equal the bounds #520 fed its fit — i.e. the
    de-templating carried through. Representative unchanged entry ransomware-on-ehr
    -> (0.5, 4.0); representative re-spaced entry nation-state-ics-supply-chain
    -> (0.043, 1.3)."""
    by = {e["slug"]: e["threat_event_frequency"] for e in _load()}
    assert (by["ransomware-on-ehr"]["low"], by["ransomware-on-ehr"]["high"]) == (0.5, 4.0)
    assert (
        by["nation-state-ics-supply-chain"]["low"],
        by["nation-state-ics-supply-chain"]["high"],
    ) == (0.043, 1.3)
