"""Two-branch composition tests for calculate_control_risk_adjustment (PR μ.1)."""

from __future__ import annotations

import math
from uuid import uuid4

import pytest

from fair_cam.calibration.elapsed_time_taus import get_canonical_tau
from fair_cam.controls.effectiveness import ControlEffectivenessCalculator
from fair_cam.models.control import (
    Control,
    ControlType,
    CostModel,
    FairCamControlFunctionAssignment,
)
from fair_cam.models.sub_function import FairCamSubFunction


def _asg(
    sub_function: FairCamSubFunction,
    capability_value: float | None,
    coverage: float = 0.8,
    reliability: float = 0.8,
) -> FairCamControlFunctionAssignment:
    return FairCamControlFunctionAssignment(
        sub_function=sub_function,
        capability_value=capability_value,
        coverage=coverage,
        reliability=reliability,
        degradation_rate=0.0,
    )


def _control(assignments, annual_cost: float = 100_000.0) -> Control:
    return Control(
        control_id=str(uuid4()),
        name="Test control",
        description="",
        control_type=ControlType.TECHNICAL,
        assignments=assignments,
        cost_model=CostModel(annual_cost=annual_cost),
    )


# ── ELAPSED_TIME branch ────────────────────────────────────────────────────


class TestElapsedTimeBranch:
    """VMC_CORR_IMPLEMENTATION: ELAPSED_TIME; τ=79.3 (post-issue-#131).

    #130 FULL MIGRATION re-pin. The per-control domain->node MULTIPLIER branch
    (`vuln_mult *= 1 - asn_eff·0.3`) is RETIRED — node multipliers now come from
    per-GROUP composition in the engine. `calculate_control_risk_adjustment`'s
    SURVIVING contract is the per-assignment ELAPSED_TIME opeff in the
    `breakdown` (PR μ.1b #129 §6) + identity multipliers. These tests now pin the
    breakdown opeff (the two-branch math under test) rather than the retired
    vulnerability_multiplier.

    Issue #131: switched dropped VMC_ID_CONTROL_MONITORING → kept
    VMC_CORR_IMPLEMENTATION. opeff values are τ-independent at t=0 (1.0),
    t=τ·ln(2) (0.5), large-t (≈0), and NULL (the 0.5 NULL-anchor in
    compute_assignment_part — the standalone _null_safe_default helper was
    folded into it by Slice 2 #439);
    only test_at_one_day is τ-sensitive.

    NOTE: the breakdown 'opeff' field is
    compute_assignment_opeff_two_branch(assignment) = exp(-t/τ)·coverage·
    reliability (it INCLUDES the cov·rel factor). With cov=rel=0.8, cov·rel=0.64,
    so opeff(t=0)=0.64, opeff(τ·ln2)=0.32, opeff(large)≈0. For NULL it records
    the 0.5 NULL-anchor: 0.5·cov·rel = 0.32.
    """

    SF = FairCamSubFunction.VMC_CORR_IMPLEMENTATION
    COV, REL = 0.8, 0.8

    @staticmethod
    def _opeff(adj) -> float:
        et = [b for b in adj.breakdown if b["opeff"] is not None]
        assert et, adj.breakdown
        return et[0]["opeff"]

    def test_at_zero_elapsed_full_opeff(self) -> None:
        # opeff(t=0) = exp(0)·0.8·0.8 = 0.64; multipliers identity (retired branch).
        adj = ControlEffectivenessCalculator().calculate_control_risk_adjustment(
            _control([_asg(self.SF, 0.0, self.COV, self.REL)]),
            100.0,
            0.5,
            1e6,
            5e5,
        )
        assert self._opeff(adj) == pytest.approx(0.64, abs=1e-6)
        assert adj.vulnerability_multiplier == pytest.approx(1.0)

    def test_at_median_elapsed_half_opeff(self) -> None:
        # t=τ·ln(2); opeff = 0.5·0.8·0.8 = 0.32.
        tau = get_canonical_tau(self.SF)
        adj = ControlEffectivenessCalculator().calculate_control_risk_adjustment(
            _control([_asg(self.SF, tau * math.log(2), self.COV, self.REL)]),
            100.0,
            0.5,
            1e6,
            5e5,
        )
        assert self._opeff(adj) == pytest.approx(0.32, abs=1e-6)

    def test_at_one_day_fast_org(self) -> None:
        # opeff = exp(-1/79.3)·0.8·0.8.
        tau = get_canonical_tau(self.SF)
        adj = ControlEffectivenessCalculator().calculate_control_risk_adjustment(
            _control([_asg(self.SF, 1.0, self.COV, self.REL)]),
            100.0,
            0.5,
            1e6,
            5e5,
        )
        assert self._opeff(adj) == pytest.approx(math.exp(-1.0 / tau) * 0.64, abs=1e-6)

    def test_at_large_elapsed_underflows(self) -> None:
        # Large t -> opeff ≈ 0; multipliers identity (retired branch).
        adj = ControlEffectivenessCalculator().calculate_control_risk_adjustment(
            _control([_asg(self.SF, 1000.0, self.COV, self.REL)]),
            100.0,
            0.5,
            1e6,
            5e5,
        )
        assert self._opeff(adj) == pytest.approx(0.0, abs=1e-3)
        assert adj.vulnerability_multiplier == pytest.approx(1.0, abs=1e-3)

    def test_null_capability_value_uses_null_safe_default(self) -> None:
        # NULL → the compute_assignment_part 0.5 NULL-anchor: 0.5 * coverage *
        # reliability = 0.32 (issue #131 Arch3-N1; standalone _null_safe_default
        # folded into compute_assignment_part by Slice 2 #439); recorded in the
        # breakdown opeff. Multipliers identity.
        adj = ControlEffectivenessCalculator().calculate_control_risk_adjustment(
            _control([_asg(self.SF, None, self.COV, self.REL)]),
            100.0,
            0.5,
            1e6,
            5e5,
        )
        assert self._opeff(adj) == pytest.approx(0.5 * 0.8 * 0.8, abs=1e-6)
        assert adj.vulnerability_multiplier == pytest.approx(1.0)


# ── CURRENCY branch ────────────────────────────────────────────────────────


class TestCurrencyBranch:
    """LEC_RESP_LOSS_REDUCTION: CURRENCY; subtractor bypasses multiplier branch."""

    SF = FairCamSubFunction.LEC_RESP_LOSS_REDUCTION

    def test_currency_assignment_does_not_affect_multipliers(self) -> None:
        adj = ControlEffectivenessCalculator().calculate_control_risk_adjustment(
            _control([_asg(self.SF, 250_000.0, 0.9, 0.95)]),
            100.0,
            0.5,
            1e6,
            5e5,
        )
        assert adj.threat_event_frequency_multiplier == 1.0
        assert adj.vulnerability_multiplier == 1.0
        assert adj.primary_loss_multiplier == 1.0
        assert adj.secondary_loss_multiplier == 1.0

    def test_currency_assignment_sets_loss_reduction_per_event(self) -> None:
        # 250000 * 0.9 * 0.95 = 213750
        adj = ControlEffectivenessCalculator().calculate_control_risk_adjustment(
            _control([_asg(self.SF, 250_000.0, 0.9, 0.95)]),
            100.0,
            0.5,
            1e6,
            5e5,
        )
        assert adj.loss_reduction_per_event == pytest.approx(213_750.0, abs=1.0)

    def test_null_capability_value_currency_contributes_zero(self) -> None:
        adj = ControlEffectivenessCalculator().calculate_control_risk_adjustment(
            _control([_asg(self.SF, None, 0.9, 0.95)]),
            100.0,
            0.5,
            1e6,
            5e5,
        )
        assert adj.loss_reduction_per_event == 0.0


# ── Mixed-unit composition ─────────────────────────────────────────────────


def test_mixed_units_on_one_control() -> None:
    """One control with ELAPSED_TIME + PROBABILITY + CURRENCY assignments.

    #130 FULL MIGRATION re-pin: the per-control domain->node multipliers are
    retired (now identity); node composition is per-GROUP in the engine. The
    SURVIVING contract from `calculate_control_risk_adjustment` is the breakdown
    (one entry per assignment, with the ELAPSED_TIME opeff + CURRENCY
    loss_reduction_per_event) and the CURRENCY subtractor total. Issue #131:
    VMC_CORR_IMPLEMENTATION (τ=79.3).
    """
    assignments = [
        _asg(FairCamSubFunction.VMC_CORR_IMPLEMENTATION, 7.0, 0.8, 0.8),  # ELAPSED_TIME
        _asg(FairCamSubFunction.LEC_PREV_RESISTANCE, 0.85, 0.9, 0.9),  # PROBABILITY
        _asg(FairCamSubFunction.LEC_RESP_LOSS_REDUCTION, 100_000.0, 1.0, 1.0),  # CURRENCY
    ]
    adj = ControlEffectivenessCalculator().calculate_control_risk_adjustment(
        _control(assignments),
        100.0,
        0.5,
        1e6,
        5e5,
    )
    # Multipliers are identity (retired per-control branch).
    assert adj.threat_event_frequency_multiplier == pytest.approx(1.0)
    assert adj.vulnerability_multiplier == pytest.approx(1.0)
    # CURRENCY subtractor still accumulated (100k × 1.0 × 1.0).
    assert adj.loss_reduction_per_event == pytest.approx(100_000.0, abs=1.0)
    # Breakdown still records one entry per assignment with the ELAPSED_TIME
    # opeff exp(-7/τ) and the CURRENCY per-event subtractor.
    assert len(adj.breakdown) == 3
    et = [b for b in adj.breakdown if b["opeff"] is not None]
    tau = get_canonical_tau(FairCamSubFunction.VMC_CORR_IMPLEMENTATION)
    # opeff includes cov·rel = 0.8·0.8 = 0.64.
    assert et[0]["opeff"] == pytest.approx(math.exp(-7.0 / tau) * 0.64, abs=1e-6)
    cur = [b for b in adj.breakdown if b["loss_reduction_per_event"] is not None]
    assert cur[0]["loss_reduction_per_event"] == pytest.approx(100_000.0, abs=1.0)


# ── risk_reduction_value uses multipliers only (Arch-B2) ───────────────────


def test_currency_branch_rejects_non_finite_capability_value() -> None:
    """Defense-in-depth: even if DTO validator is bypassed (e.g., direct
    FairCamControlFunctionAssignment construction in a fixture), the
    calculator catches NaN before propagating to ALE."""
    asg = _asg(FairCamSubFunction.LEC_RESP_LOSS_REDUCTION, float("nan"), 0.9, 0.95)
    with pytest.raises(ValueError, match="loss_reduction_per_event non-finite"):
        ControlEffectivenessCalculator().calculate_control_risk_adjustment(
            _control([asg]),
            100.0,
            0.5,
            1e6,
            5e5,
        )


def test_risk_reduction_value_excludes_subtractor() -> None:
    """ControlAdjustment.risk_reduction_value uses multipliers only;
    subtractor is applied separately in _apply_control_adjustments
    (Arch-B2 fix — avoid double-counting)."""
    base_tef, base_vuln, base_pl, base_sl = 100.0, 0.5, 1e6, 5e5
    # Currency-only control: no multipliers change; subtractor present.
    adj = ControlEffectivenessCalculator().calculate_control_risk_adjustment(
        _control([_asg(FairCamSubFunction.LEC_RESP_LOSS_REDUCTION, 100_000.0, 1.0, 1.0)]),
        base_tef,
        base_vuln,
        base_pl,
        base_sl,
    )
    # Since no multipliers shifted, adjusted_ale (multipliers-only) == original_ale.
    # risk_reduction_value should be exactly 0.0 (NOT 100_000 — that would be
    # the subtractor effect, which lives in _apply_control_adjustments).
    assert adj.risk_reduction_value == pytest.approx(0.0, abs=1.0)
    assert adj.loss_reduction_per_event == 100_000.0
