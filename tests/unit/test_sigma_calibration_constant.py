"""tests/unit/test_sigma_calibration_constant.py — the within-scenario
loss-dispersion default and its IRIS bounds argument (derivation record:
docs/reference/within-scenario-sigma-calibration.md)."""

from __future__ import annotations

import math

from fair_cam.data.iris_2025 import LOSS_BY_EVENT_TYPE_TREND, LOSS_BY_REVENUE_TIER_2024
from scipy.stats import norm

from idraa.services.calibration import SIGMA_WARN_THRESHOLD, WITHIN_SCENARIO_SIGMA_DEFAULT

Z_0_90 = 1.2815515655446004
Z_0_95 = 1.6448536269514722


def _sig(p50: float, phigh: float, z: float) -> float:
    return math.log(phigh / p50) / z


def test_z_constants_match_scipy() -> None:
    # 1-ulp tolerance: the pinned Z_0_95 literal differs from scipy's ppf in
    # the last bit; strict equality is a known false failure.
    assert math.isclose(Z_0_90, float(norm.ppf(0.90)), rel_tol=1e-12)
    assert math.isclose(Z_0_95, float(norm.ppf(0.95)), rel_tol=1e-12)


def test_default_is_the_pinned_value() -> None:
    assert WITHIN_SCENARIO_SIGMA_DEFAULT == 1.7


def test_type_conditioned_reads_rederive() -> None:
    t = LOSS_BY_EVENT_TYPE_TREND
    assert (
        round(_sig(t["system_intrusion"]["p50_2024"], t["system_intrusion"]["p90_2024"], Z_0_90), 4)
        == 1.3570
    )
    assert (
        round(_sig(t["ransomware"]["p50_2024"], t["ransomware"]["p90_2024"], Z_0_90), 4) == 1.6813
    )


def test_accidental_disclosure_exclusion_is_deliberate() -> None:
    """The third 2024 read is EXCLUDED from the anchor set (mixed cross-type
    bucket); assert its value so the exclusion can never read as an oversight."""
    t = LOSS_BY_EVENT_TYPE_TREND["accidental_disclosure_insider_misuse"]
    assert round(_sig(t["p50_2024"], t["p90_2024"], Z_0_90), 4) == 4.2497


def test_bound_argument() -> None:
    t = LOSS_BY_EVENT_TYPE_TREND["ransomware"]
    assert _sig(t["p50_2024"], t["p90_2024"], Z_0_90) <= WITHIN_SCENARIO_SIGMA_DEFAULT
    tier_min = min(_sig(v["p50"], v["p95"], Z_0_95) for v in LOSS_BY_REVENUE_TIER_2024.values())
    assert tier_min > WITHIN_SCENARIO_SIGMA_DEFAULT
    assert round(tier_min, 4) == 1.9687


def test_mode_clamp_precondition() -> None:
    """sigma_default must exceed z_0.95 or the capped-PERT collapse gains an
    interior mode (shape-regime change -- a new design, not a constant edit)."""
    assert WITHIN_SCENARIO_SIGMA_DEFAULT > Z_0_95


def test_warn_threshold() -> None:
    assert SIGMA_WARN_THRESHOLD == 2.2
    assert SIGMA_WARN_THRESHOLD > WITHIN_SCENARIO_SIGMA_DEFAULT
