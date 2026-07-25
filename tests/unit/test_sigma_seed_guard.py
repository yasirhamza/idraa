"""Armed guard: seed loss dispersion may never exceed the within-scenario
default, and the vendor mean-anchor must be preserved exactly."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from idraa.services.calibration import WITHIN_SCENARIO_SIGMA_DEFAULT

_ROOT = Path(__file__).resolve().parents[2]
_Z95 = 1.6448536269514722
_IC3 = 123005.0


def _entries():
    out = []
    for n in ("seed_library_entries.json", "seed_library_entries_extension.json"):
        out.extend(json.loads((_ROOT / "data" / n).read_text(encoding="utf-8")))
    return out


def test_no_catastrophic_sigma_off_default() -> None:
    seen = 0
    for e in _entries():
        for f in ("primary_loss", "secondary_loss"):
            d = e.get(f) or {}
            if d.get("distribution") == "lognormal":
                seen += 1
                assert d["sigma"] == pytest.approx(WITHIN_SCENARIO_SIGMA_DEFAULT), (e["slug"], f)
    assert seen == 18


def test_no_capped_implied_sigma_off_default() -> None:
    seen = 0
    for e in _entries():
        for f in ("primary_loss", "secondary_loss"):
            d = e.get(f) or {}
            if d.get("distribution") == "PERT":
                seen += 1
                implied = math.log(d["high"] / d["low"]) / (2 * _Z95)
                assert implied == pytest.approx(WITHIN_SCENARIO_SIGMA_DEFAULT), (e["slug"], f)
                assert d["mode"] == d["low"], (e["slug"], f)
    assert seen == 154


def test_vendor_mean_anchor_preserved_exactly() -> None:
    """The one external, citation-traced check: E[loss] == the IC3 anchor."""
    slugs = []
    for e in _entries():
        if e.get("loss_tier") != "vendor":
            continue
        slugs.append(e["slug"])
        d = e["primary_loss"]
        mu = math.log(math.sqrt(d["low"] * d["high"]))
        assert math.exp(mu + WITHIN_SCENARIO_SIGMA_DEFAULT**2 / 2) == pytest.approx(_IC3, rel=1e-9)
    assert sorted(slugs) == [
        "agri-coop-bec-fraud",
        "bec-fraud-financial",
        "manufacturing-billing-fraud",
        "professional-payroll-bec",
        "telecom-sim-swap-fraud",
    ]


def test_no_provenance_string_contradicts_the_data() -> None:
    """No provenance text may assert a sigma the distributions no longer carry.

    Regex notes (gate rounds 1-2): the capture is `(\\d+\\.\\d+)` with NO
    lookahead -- a greedy `[0-9.]+` class absorbs sentence-final periods and
    crashes float() on 36 pre-change strings, but the structured pattern
    cannot (the trailing period is not \\d), and a `(?![.\\d])` lookahead
    would make those same 36 tokens unmatchable, silently blinding this
    guard to a future stale sentence-terminal token. The builder's
    superseded-value embed is NOT sigma=-prefixed, so every surviving token
    must be the default. A second assertion kills the narrative form the
    token regex cannot see.
    """
    import re

    tok = re.compile(r"sigma=(\d+\.\d+)")
    narrative = re.compile(r"sigma borrowed from the \w+ envelope")
    for e in _entries():
        blobs = [str((e.get("calibration_anchor") or {}).get("loss_anchor", ""))]
        blobs += [str(lf.get("magnitude_basis", "")) for lf in (e.get("loss_form_profile") or [])]
        for blob in blobs:
            assert not narrative.search(blob), (e["slug"], "stale envelope-borrowed narrative")
            for m in tok.finditer(blob):
                assert float(m.group(1)) == pytest.approx(WITHIN_SCENARIO_SIGMA_DEFAULT), (
                    e["slug"],
                    m.group(0),
                )


def test_no_seed_tef_or_vuln_is_lognormal() -> None:
    """D12: lognormal is strictly a loss distribution. TEF and vulnerability
    are PERT-only in v3 storage; the library must never reintroduce the
    Epic-B-era lognormal TEF shape (102/102 PERT as of the tef-pert-revert)."""
    for e in _entries():
        for field in ("threat_event_frequency", "vulnerability"):
            d = e.get(field) or {}
            kind = str(d.get("distribution", "pert")).lower()
            assert kind not in ("lognormal", "lognormal_mixture"), (e["slug"], field, kind)
