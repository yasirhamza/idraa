"""Pin canonical qualitative band values to spec §2.2 (methodology-gated).

Any change here is a calibration change: it requires a spec §2.2 edit and a
methodology re-review, never a casual re-pin.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import idraa

SEED = Path(idraa.__file__).resolve().parent.parent.parent / "data" / "seed_qualitative_bands.json"

EXPECTED_FREQUENCY = {  # label: (low, mode, high)
    "very_low": (0.01, 0.032, 0.1),
    "low": (0.1, 0.32, 1),
    "moderate": (1, 3.2, 10),
    "high": (10, 32, 100),
    "very_high": (100, 160, 250),
}
EXPECTED_MAGNITUDE = {
    "very_low": (1_000, 3_200, 10_000),
    "low": (10_000, 32_000, 100_000),
    "moderate": (100_000, 320_000, 1_000_000),
    "high": (1_000_000, 3_200_000, 10_000_000),
    "very_high": (10_000_000, 100_000_000, 1_000_000_000),
}


def _bands():
    return json.loads(SEED.read_text(encoding="utf-8"))


def test_exactly_ten_bands_five_per_kind():
    bands = _bands()
    assert len(bands) == 10
    assert sum(1 for b in bands if b["kind"] == "frequency") == 5
    assert sum(1 for b in bands if b["kind"] == "magnitude") == 5


def test_frequency_values_pinned():
    got = {
        b["label"]: (b["low"], b["mode"], b["high"]) for b in _bands() if b["kind"] == "frequency"
    }
    assert got == EXPECTED_FREQUENCY


def test_magnitude_values_pinned():
    got = {
        b["label"]: (b["low"], b["mode"], b["high"]) for b in _bands() if b["kind"] == "magnitude"
    }
    assert got == EXPECTED_MAGNITUDE


def test_modes_are_2sf_geometric_midpoints():
    for b in _bands():
        gm = math.sqrt(b["low"] * b["high"])
        # spec §2.3: mode = geometric midpoint rounded to EXACTLY 2 significant
        # figures, uniformly (plan-gate M1). These log-decade midpoints are all
        # √10-pattern (mantissa 3.16 or 1.58), so 2sf rounding deviates ≤1.19%;
        # bound at 3.5% rejects 1sf roundings (5.13% off) with margin.
        assert abs(b["mode"] - gm) / gm < 0.035, (b["label"], b["kind"], gm)


def test_derivations_carry_provenance():
    for b in _bands():
        d = b["derivation"]
        if b["kind"] == "magnitude":
            assert "O-RA" in d and "Table 1" in d and "§6.6" in d and "p.33" in d, b["label"]
            # the two spec-§2.2 honest caveats (M3): example-scale/management
            # approval + input-ward direction-of-use vs §6.5
            assert "example" in d.lower() and "§6.5" in d, b["label"]
            if b["label"] == "very_high":
                assert "p99.9" in d, "M2 cap rationale must be pinned"
        else:
            assert "convention" in d and "O-RA" in d, b["label"]  # names the absence
            assert "priors" in d.lower(), b["label"]  # epistemic label (N1)
