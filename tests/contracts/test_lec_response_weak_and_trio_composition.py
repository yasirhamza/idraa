"""Spec3-B1 weak-AND-trio composition smoke test under post-#131 mixed-unit reality.

Plan T6 Step 10a contract test. Pure in-memory; no DB fixture. Mirrors the
style of tests/contracts/test_compute_assignment_opeff_two_branch_null_handling.py
(the adjacent contract-level test for the same code path).

Standard §3.3 specifies the LEC Response weak-AND-trio:
  LEC_RESP_EVENT_TERMINATION  (ELAPSED_TIME, τ=64d post-#131)
+ LEC_RESP_RESILIENCE         (PROBABILITY,  post-#131 reclassified — was
                               ELAPSED_TIME with disputed τ=33d "3-week
                               BCM heuristic" that lacked a primary citation)
+ LEC_RESP_LOSS_REDUCTION     (CURRENCY,     always was — no opeff)

Post-#131 the trio spans three distinct UnitTypes. Standard §3.3 states
"deficiencies in one diminish but don't inhibit entirely" — weak-AND
composition must remain non-degenerate under this mixed-unit reality:
  - finite, bounded output ∈ [0, 1]
  - zeroing one arm does NOT collapse the whole group
  - monotonic in each non-CURRENCY input

This test pins the composition primitive contract. The full engine-path
weak-AND wiring is already covered by
fair_cam/tests/risk_engine/test_compose_group_effectiveness.py
::test_scenario_f_response_group_elapsed_time_now_included (Scenario F).

See:
  docs/plans/2026-05-15-issue-131-tau-calibration-design.md §3 (τ values)
  docs/plans/2026-05-15-issue-131-tau-calibration-design.md §6 (Spec3-B1)
"""

from __future__ import annotations

import math

import pytest
from fair_cam.composition import (
    compute_assignment_opeff_two_branch,
    weak_and_compose,
)
from fair_cam.models.control import FairCamControlFunctionAssignment
from fair_cam.models.sub_function import FairCamSubFunction


def _asg(
    sub_function: FairCamSubFunction,
    capability_value: float | None,
    coverage: float = 0.9,
    reliability: float = 0.9,
) -> FairCamControlFunctionAssignment:
    return FairCamControlFunctionAssignment(
        sub_function=sub_function,
        capability_value=capability_value,
        coverage=coverage,
        reliability=reliability,
        degradation_rate=0.0,
    )


def _compose_trio(
    et_capability: float,
    resilience_capability: float,
    loss_reduction_capability: float,
) -> float | None:
    """Build the three trio arms, compute per-assignment opeffs via the
    two-branch dispatcher, drop the CURRENCY-arm None per runtime behavior,
    and weak-AND-compose what remains.

    Mirrors the actual call shape used by
    fair_cam.risk_engine.control_aware.compose_group_effectiveness for the
    LEC_RESPONSE group: CURRENCY operands return None from the per-assignment
    helper and are excluded from the weak-AND aggregate (Spec §3.2.4).
    """
    et = _asg(FairCamSubFunction.LEC_RESP_EVENT_TERMINATION, et_capability)
    res = _asg(FairCamSubFunction.LEC_RESP_RESILIENCE, resilience_capability)
    lr = _asg(FairCamSubFunction.LEC_RESP_LOSS_REDUCTION, loss_reduction_capability)

    opeffs: list[float] = []
    for a in (et, res, lr):
        v = compute_assignment_opeff_two_branch(a)
        if v is not None:  # CURRENCY arm returns None; exclude per runtime behavior
            opeffs.append(v)

    return weak_and_compose(opeffs)


# ── Spec3-B1: finite, bounded output under mixed-unit reality ──────────────


def test_weak_and_trio_composition_under_mixed_units_is_finite_and_bounded() -> None:
    """Spec3-B1: Standard §3.3 weak-AND-trio remains finite and bounded ∈ [0, 1]
    under the post-#131 mixed-UnitType inputs (ELAPSED_TIME + PROBABILITY +
    CURRENCY-excluded).

    Hand-math (cov=rel=0.9 on every arm):
      ET (cap=64d, τ=64):  exp(-64/64) * 0.9 * 0.9 = exp(-1) * 0.81 ≈ 0.297982
      Resilience (cap=0.8): 0.8 * 0.9 * 0.9 = 0.648
      LossRed (CURRENCY):  None → excluded from weak-AND
      weak_and([0.297982, 0.648]) (equal weights 0.5 each)
        = (0.297982 + 0.648) / 2 ≈ 0.472991
    """
    g_eff = _compose_trio(
        et_capability=64.0,
        resilience_capability=0.8,
        loss_reduction_capability=50_000.0,
    )

    assert g_eff is not None, "Trio must yield a real float; only CURRENCY was excluded"
    assert 0.0 <= g_eff <= 1.0, f"weak-AND output must be bounded in [0, 1]; got {g_eff}"
    assert math.isfinite(g_eff), f"weak-AND output must be finite; got {g_eff}"
    assert g_eff == pytest.approx(0.472991, abs=1e-5)


# ── Spec3-B1: zeroing one arm does NOT collapse the trio (§3.3) ────────────


def test_weak_and_trio_does_not_collapse_when_elapsed_time_arm_zeroed() -> None:
    """§3.3 "deficiencies in one diminish but don't inhibit entirely": zeroing
    the ELAPSED_TIME arm (very long elapsed time → opeff ≈ 0) must NOT
    collapse the trio output to 0. The PROBABILITY arm still contributes.

    Hand-math (cov=rel=0.9):
      ET (cap=10000d, τ=64):   exp(-10000/64) * 0.81 ≈ 1.12e-68 (effectively 0)
      Resilience (cap=0.8):    0.8 * 0.9 * 0.9 = 0.648
      weak_and([~0, 0.648]) ≈ (0 + 0.648) / 2 = 0.324
    """
    g_eff = _compose_trio(
        et_capability=10_000.0,  # opeff ≈ 1e-68 — effectively zero
        resilience_capability=0.8,
        loss_reduction_capability=50_000.0,
    )

    assert g_eff is not None
    assert g_eff > 0.0, (
        f"Zeroing the ET arm collapsed the weak-AND trio to {g_eff}; §3.3 "
        "requires non-degeneracy when only one arm is deficient"
    )
    assert g_eff == pytest.approx(0.324, abs=1e-3)


def test_weak_and_trio_does_not_collapse_when_probability_arm_zeroed() -> None:
    """§3.3 "deficiencies in one diminish but don't inhibit entirely": zeroing
    the PROBABILITY arm (capability_value=0.0) must NOT collapse the trio
    output to 0. The ELAPSED_TIME arm still contributes.

    Hand-math (cov=rel=0.9):
      ET (cap=64d, τ=64):      exp(-1) * 0.81 ≈ 0.297982
      Resilience (cap=0.0):    0.0 * 0.9 * 0.9 = 0.0
      weak_and([0.297982, 0]) = 0.297982 / 2 ≈ 0.148991
    """
    g_eff = _compose_trio(
        et_capability=64.0,
        resilience_capability=0.0,
        loss_reduction_capability=50_000.0,
    )

    assert g_eff is not None
    assert g_eff > 0.0, (
        f"Zeroing the PROBABILITY arm collapsed the weak-AND trio to {g_eff}; "
        "§3.3 requires non-degeneracy when only one arm is deficient"
    )
    assert g_eff == pytest.approx(0.148991, abs=1e-5)


# ── Spec3-B1: monotonicity in each non-CURRENCY input ──────────────────────


def test_weak_and_trio_monotonic_in_elapsed_time_arm() -> None:
    """Better ET capability (LOWER elapsed time → HIGHER opeff) must yield
    a HIGHER trio output. Monotonicity is a key property for an
    investment-decision tool: improving any arm should never decrease the
    composed effectiveness (spec Q4 / FAIR-CAM §3.3).

    Hand-math (cov=rel=0.9, Resilience cap=0.8 → opeff=0.648 fixed):
      et=32d (BETTER): exp(-32/64) * 0.81 = 0.491290 → weak_and ≈ 0.569645
      et=64d (BASE):   exp(-64/64) * 0.81 = 0.297982 → weak_and ≈ 0.472991
      et=128d (WORSE): exp(-128/64) * 0.81 = 0.109622 → weak_and ≈ 0.378811
    """
    g_eff_better = _compose_trio(32.0, 0.8, 50_000.0)
    g_eff_base = _compose_trio(64.0, 0.8, 50_000.0)
    g_eff_worse = _compose_trio(128.0, 0.8, 50_000.0)

    assert g_eff_better is not None and g_eff_base is not None and g_eff_worse is not None
    assert g_eff_better > g_eff_base > g_eff_worse, (
        f"weak-AND must be monotonic in the ET arm; got "
        f"better={g_eff_better} base={g_eff_base} worse={g_eff_worse}"
    )
    assert g_eff_better == pytest.approx(0.569645, abs=1e-5)
    assert g_eff_base == pytest.approx(0.472991, abs=1e-5)
    assert g_eff_worse == pytest.approx(0.378811, abs=1e-5)


def test_weak_and_trio_monotonic_in_probability_arm() -> None:
    """Better Resilience capability (HIGHER PROBABILITY value) must yield
    a HIGHER trio output. Same monotonicity property — exercises the
    PROBABILITY branch.

    Hand-math (cov=rel=0.9, ET cap=64d → opeff≈0.297982 fixed):
      res=0.2: 0.2 * 0.81 = 0.162   → weak_and ≈ 0.229991
      res=0.5: 0.5 * 0.81 = 0.405   → weak_and ≈ 0.351491
      res=0.9: 0.9 * 0.81 = 0.729   → weak_and ≈ 0.513491
    """
    g_eff_low = _compose_trio(64.0, 0.2, 50_000.0)
    g_eff_mid = _compose_trio(64.0, 0.5, 50_000.0)
    g_eff_high = _compose_trio(64.0, 0.9, 50_000.0)

    assert g_eff_low is not None and g_eff_mid is not None and g_eff_high is not None
    assert g_eff_high > g_eff_mid > g_eff_low, (
        f"weak-AND must be monotonic in the PROBABILITY arm; got "
        f"high={g_eff_high} mid={g_eff_mid} low={g_eff_low}"
    )
    assert g_eff_low == pytest.approx(0.229991, abs=1e-5)
    assert g_eff_mid == pytest.approx(0.351491, abs=1e-5)
    assert g_eff_high == pytest.approx(0.513491, abs=1e-5)


# ── Spec3-B1: CURRENCY arm value is irrelevant to opeff composition ────────


def test_weak_and_trio_currency_arm_value_does_not_affect_opeff_composition() -> None:
    """LEC_RESP_LOSS_REDUCTION (CURRENCY) has no opeff semantic — its
    capability_value (a dollar amount) feeds the separate
    loss_reduction_per_event accumulator, NOT the weak-AND. Verify the
    weak-AND output is invariant to the CURRENCY-arm value.
    """
    g_eff_low_dollars = _compose_trio(64.0, 0.8, 1.0)
    g_eff_mid_dollars = _compose_trio(64.0, 0.8, 50_000.0)
    g_eff_high_dollars = _compose_trio(64.0, 0.8, 1_000_000_000.0)

    assert g_eff_low_dollars is not None
    assert g_eff_mid_dollars is not None
    assert g_eff_high_dollars is not None
    assert g_eff_low_dollars == g_eff_mid_dollars == g_eff_high_dollars, (
        "weak-AND output must be invariant to CURRENCY-arm capability_value; "
        f"got low={g_eff_low_dollars}, mid={g_eff_mid_dollars}, high={g_eff_high_dollars}"
    )
