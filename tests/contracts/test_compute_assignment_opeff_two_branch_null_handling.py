"""NULL-capability_value handling in `compute_assignment_opeff_two_branch` (issue #131).

Pure in-memory contract tests — no DB fixture required. Constructs
FairCamControlFunctionAssignment directly. Mirrors the style of
tests/contracts/test_two_branch_adapter.py (which is the canonical
adjacent-area contract test).

Coverage matrix (plan T2 Step 8.4):
  (a) PROBABILITY + NULL → _null_safe_default (0.5 * cov * rel).
      Exercises the new PROBABILITY NULL guard introduced in issue #131.
      Uses VMC_ID_THREAT_INTELLIGENCE — a sub-function reclassified from
      ELAPSED_TIME to PROBABILITY in T2.
  (b) PROBABILITY + non-NULL → cap * cov * rel (existing path, regression).
  (c) PERCENT_REDUCTION + non-NULL → cap * cov * rel (existing path).
  (d) ELAPSED_TIME + NULL → _null_safe_default (regression guard on the
      pre-existing fallback path; algebraic identity).
  (e) ELAPSED_TIME + NULL numerically equivalent to the pre-issue-#131
      `elapsed_time_to_opeff(τ·ln(2), τ) * cov * rel` formulation, within
      ``math.isclose``. This catches future regressions of the refactor's
      algebraic identity.
  (f) CURRENCY → None (existing path, regression).

Plan-gate finding Arch3-N1 (issue #131): `_null_safe_default` factored
from both NULL paths so they share one source of truth. These tests pin
the identity-equivalence.
"""

from __future__ import annotations

import math

import pytest
from fair_cam.composition import compute_assignment_opeff_two_branch
from fair_cam.models.control import FairCamControlFunctionAssignment
from fair_cam.models.sub_function import FairCamSubFunction
from fair_cam.normalization import elapsed_time_to_opeff


def _asg(
    sub_function: FairCamSubFunction,
    capability_value: float | None,
    coverage: float = 0.8,
    reliability: float = 0.9,
) -> FairCamControlFunctionAssignment:
    return FairCamControlFunctionAssignment(
        sub_function=sub_function,
        capability_value=capability_value,
        coverage=coverage,
        reliability=reliability,
        degradation_rate=0.0,
    )


# (a) PROBABILITY + NULL → _null_safe_default
def test_probability_null_capability_returns_null_safe_default() -> None:
    """Issue #131: PROBABILITY-branch NULL guard returns 0.5 * cov * rel.

    Uses VMC_ID_THREAT_INTELLIGENCE — one of the six sub-functions
    reclassified ELAPSED_TIME → PROBABILITY in T2. Before issue #131 the
    sub-function routed through the ELAPSED_TIME branch's τ·ln(2)
    fallback; now it routes through the PROBABILITY branch's
    _null_safe_default. Both produce the same constant.
    """
    asg = _asg(FairCamSubFunction.VMC_ID_THREAT_INTELLIGENCE, None, coverage=0.8, reliability=0.9)
    result = compute_assignment_opeff_two_branch(asg)
    expected = 0.5 * 0.8 * 0.9
    assert result == pytest.approx(expected, abs=1e-12)


# (b) PROBABILITY + non-NULL → cap * cov * rel (regression)
def test_probability_non_null_capability_unchanged() -> None:
    """PROBABILITY-branch non-NULL path is unchanged by issue #131."""
    asg = _asg(FairCamSubFunction.VMC_ID_THREAT_INTELLIGENCE, 0.7, coverage=0.8, reliability=0.9)
    result = compute_assignment_opeff_two_branch(asg)
    expected = 0.7 * 0.8 * 0.9
    assert result == pytest.approx(expected, abs=1e-12)


# (c) PERCENT_REDUCTION + non-NULL → cap * cov * rel (regression)
def test_percent_reduction_non_null_capability_unchanged() -> None:
    """PERCENT_REDUCTION-branch non-NULL path is unchanged by issue #131
    (shares Layer-1 multiplicative formula with PROBABILITY)."""
    asg = _asg(
        FairCamSubFunction.VMC_PREV_REDUCE_CHANGE_FREQ,
        0.5,
        coverage=0.85,
        reliability=0.85,
    )
    result = compute_assignment_opeff_two_branch(asg)
    expected = 0.5 * 0.85 * 0.85
    assert result == pytest.approx(expected, abs=1e-12)


# (d) ELAPSED_TIME + NULL → _null_safe_default (regression)
def test_elapsed_time_null_capability_returns_null_safe_default() -> None:
    """ELAPSED_TIME-branch NULL fallback is now `_null_safe_default(assignment)`
    instead of the prior `elapsed_time_to_opeff(τ·ln(2), τ)`.

    Algebraic identity preserves the numeric value byte-for-byte:
      elapsed_time_to_opeff(τ·ln(2), τ) = exp(-τ·ln(2) / τ) = exp(-ln(2)) = 0.5
    Uses LEC_DET_MONITORING (KEPT ELAPSED_TIME survivor).
    """
    asg = _asg(FairCamSubFunction.LEC_DET_MONITORING, None, coverage=0.8, reliability=0.9)
    result = compute_assignment_opeff_two_branch(asg)
    expected = 0.5 * 0.8 * 0.9
    assert result == pytest.approx(expected, abs=1e-12)


# (e) Parametrized algebraic-identity equivalence
@pytest.mark.parametrize(
    "sub_function",
    [
        FairCamSubFunction.LEC_DET_MONITORING,
        FairCamSubFunction.LEC_RESP_EVENT_TERMINATION,
        FairCamSubFunction.VMC_CORR_IMPLEMENTATION,
    ],
)
@pytest.mark.parametrize(
    "coverage,reliability",
    [(0.5, 0.5), (0.7, 0.9), (1.0, 1.0), (0.0, 0.9), (0.9, 0.0)],
)
def test_elapsed_time_null_equivalent_to_prior_tau_ln2_formulation(
    sub_function: FairCamSubFunction,
    coverage: float,
    reliability: float,
) -> None:
    """ELAPSED_TIME NULL path: `_null_safe_default(assignment)` is numerically
    equivalent to the pre-issue-#131 `elapsed_time_to_opeff(τ·ln(2), τ) * cov * rel`
    formulation. Catches future regressions of the algebraic identity
    factored at Arch3-N1.

    Parametrized across all 3 KEPT ELAPSED_TIME sub-functions × 5
    (coverage, reliability) combinations (including boundary 0/1 values)
    — 15 sub-cases.
    """
    from fair_cam.calibration.elapsed_time_taus import get_canonical_tau

    asg = _asg(sub_function, None, coverage=coverage, reliability=reliability)
    new_result = compute_assignment_opeff_two_branch(asg)
    assert new_result is not None  # type-narrowing for mypy
    # Prior formulation: elapsed_time_to_opeff(τ·ln(2), τ) * cov * rel.
    tau = get_canonical_tau(sub_function)
    old_opeff = elapsed_time_to_opeff(tau * math.log(2), tau)
    old_result = old_opeff * coverage * reliability
    assert math.isclose(new_result, old_result, abs_tol=1e-12), (
        f"Algebraic identity broke for {sub_function.value} "
        f"(cov={coverage}, rel={reliability}): new={new_result}, old={old_result}"
    )


# (f) CURRENCY → None (regression)
def test_currency_returns_none_regardless_of_capability_value() -> None:
    """CURRENCY assignments do NOT have an opeff; helper returns None.

    Regression guard: issue #131 did not touch CURRENCY semantics.
    LEC_RESP_LOSS_REDUCTION is the sole CURRENCY sub-function.
    """
    asg_non_null = _asg(FairCamSubFunction.LEC_RESP_LOSS_REDUCTION, 100_000.0)
    asg_null = _asg(FairCamSubFunction.LEC_RESP_LOSS_REDUCTION, None)
    assert compute_assignment_opeff_two_branch(asg_non_null) is None
    assert compute_assignment_opeff_two_branch(asg_null) is None


# Helper-level identity test (lives here for proximity to the issue-#131
# Arch3-N1 algebraic identity that the NULL fallback encodes).
def test_null_fallback_independent_of_sub_function() -> None:
    """The NULL fallback depends only on coverage * reliability, not on the
    sub-function. Confirms the fallback is a pure function of the
    assignment's (coverage, reliability) fields — the algebraic identity
    is sub-function-independent.

    Slice 2 (#439): the standalone ``_null_safe_default`` helper was deleted;
    the 0.5-anchor NULL fallback now lives inline in
    ``compute_assignment_part`` (reliability-free) and is re-multiplied by
    reliability in ``compute_assignment_opeff_two_branch``. This test pins the
    same sub-function-independent identity via the public two-branch wrapper.
    """
    asg1 = _asg(FairCamSubFunction.LEC_DET_MONITORING, None, coverage=0.7, reliability=0.6)
    asg2 = _asg(FairCamSubFunction.VMC_ID_THREAT_INTELLIGENCE, None, coverage=0.7, reliability=0.6)
    assert compute_assignment_opeff_two_branch(asg1) == compute_assignment_opeff_two_branch(asg2)
    assert compute_assignment_opeff_two_branch(asg1) == pytest.approx(0.5 * 0.7 * 0.6, abs=1e-12)
