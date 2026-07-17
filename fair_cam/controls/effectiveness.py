"""
Control effectiveness calculation engine

This module provides sophisticated algorithms for calculating
and modeling control effectiveness in FAIR risk scenarios.
"""

import math
from typing import Any

import numpy as np

from fair_cam.calibration.elapsed_time_taus import get_canonical_tau

from ..composition import compute_assignment_opeff_two_branch
from ..models.control import (
    Control,
)
from ..models.risk_enhanced import ControlAdjustment
from ..models.sub_function import SUB_FUNCTION_UNITS, UnitType


class ControlEffectivenessCalculator:
    """Advanced calculator for control effectiveness metrics"""

    def __init__(self, simulation_iterations: int = 10000):
        self.simulation_iterations = simulation_iterations
        self.random_state = np.random.RandomState(42)  # For reproducible results

    def calculate_control_risk_adjustment(
        self,
        control: Control,
        base_threat_frequency: float,
        base_vulnerability: float,
        base_primary_loss: float,
        base_secondary_loss: float,
    ) -> ControlAdjustment:
        """PR μ.1 two-branch composition — per-assignment breakdown + CURRENCY.

        #130 FULL MIGRATION: the per-control domain->node MULTIPLIER branch
        (the old `if domain == LOSS_EVENT/VMC/DSC: *_multiplier *= 1 - eff·w`)
        is RETIRED. The engine ALE path now composes PER BOOLEAN GROUP via the
        shared `compose_groups` routine + `GROUP_NODE_MAPPING`
        (`risk_engine/control_aware.py`), so per-control multiplicative
        accumulation is no longer the source of truth (it was strict-AND-like —
        the #130 bug for Response, and algebra drift for every other group).

        This method's SURVIVING responsibility is two-fold and unchanged:
          1. the per-assignment `breakdown` (PR μ.1b #129 §6 snapshot
             debuggability — consumed by run_executor payload + reports), and
          2. the CURRENCY (Loss-Reduction) `loss_reduction_per_event`
             accumulation (#258 / D3).

        The returned multiplier fields stay at IDENTITY (1.0) and
        `risk_reduction_value` at 0.0; the engine's
        `ControlAwareRiskCalculator._build_control_adjustment` OVERRIDES them
        with group-composed values (D9) before the `ControlAdjustment` reaches
        any consumer. Per Standard §2.3 composition is implementation-defined;
        audit §8 makes no recommendation.

        Spec: docs/plans/2026-05-15-pr-mu-1-elapsed-time-design.md; #130 D1/D2.
        """
        loss_reduction_per_event = 0.0  # PR μ.1 currency-branch accumulator
        breakdown_entries: list[dict[str, Any]] = []  # PR μ.1b #129 §6 accumulator

        for assignment in control.assignments:
            asn_eff_or_none = compute_assignment_opeff_two_branch(assignment)

            # PR μ.1b #129 §6 — per-assignment breakdown for snapshot debuggability.
            # Formulas (per CLAUDE.md "Citation-to-derivation traceability"):
            #   opeff = elapsed_time_to_opeff(t=capability_value, τ) = exp(-t/τ)
            #     cite: fair_cam/normalization.py:36 elapsed_time_to_opeff
            #     (ln(2) is the evaluation-point factor where opeff=0.5 occurs at
            #     t=τ·ln(2), NOT a factor inside the formula.)
            #   loss_reduction_per_event = capability_value × coverage × reliability
            #     cite: PR μ.1 commit 07b345a, design §3.3.3 Loss Magnitude subtractor
            #   tau_canonical = get_canonical_tau(sub_function)
            #     cite: fair_cam canonical τ table at
            #     fair_cam/calibration/elapsed_time_taus.py
            unit = SUB_FUNCTION_UNITS[assignment.sub_function]
            breakdown_entries.append(
                {
                    "sub_function": assignment.sub_function.value,
                    "unit": unit.value,
                    "capability_value_in": assignment.capability_value,
                    "tau_canonical": (
                        get_canonical_tau(assignment.sub_function)
                        if unit == UnitType.ELAPSED_TIME
                        else None
                    ),
                    "t_used": (
                        assignment.capability_value
                        if unit == UnitType.ELAPSED_TIME and assignment.capability_value is not None
                        else None
                    ),
                    "capability_was_null": assignment.capability_value is None,
                    "opeff": (asn_eff_or_none if unit == UnitType.ELAPSED_TIME else None),
                    "loss_reduction_per_event": (
                        assignment.capability_value * assignment.coverage * assignment.reliability
                        if unit == UnitType.CURRENCY and assignment.capability_value is not None
                        else None
                    ),
                }
            )

            if asn_eff_or_none is None:
                # CURRENCY branch: FAIR §3.3.3 Loss Magnitude subtractor, not opeff
                # (audit §8.4). Only LEC_RESP_LOSS_REDUCTION takes this branch.
                if assignment.capability_value is not None:
                    loss_reduction_per_event += (
                        assignment.capability_value * assignment.coverage * assignment.reliability
                    )
                continue

            # #130 FULL MIGRATION: the per-domain node multiplier branch that used
            # to live here (LOSS_EVENT -> TEF×0.8/Vuln×0.9; VMC -> Vuln×0.3; DSC ->
            # secondary×0.5/primary×0.2) is RETIRED. Node multipliers now come from
            # per-GROUP composition (compose_groups + GROUP_NODE_MAPPING) in the
            # engine. opeff is still computed above (it feeds the breakdown); no
            # per-control multiplier is accumulated.

        if not math.isfinite(loss_reduction_per_event):
            raise ValueError(
                f"loss_reduction_per_event non-finite for control "
                f"{control.control_id}: {loss_reduction_per_event}"
            )

        # Multiplier fields stay at IDENTITY and risk_reduction_value at 0.0:
        # the engine's _build_control_adjustment OVERRIDES them with the
        # group-composed node multipliers + closed-form ALE delta (D9). The
        # base_* args are retained for call-site compatibility but are unused now
        # that the multiplier path is retired.
        return ControlAdjustment(
            control_id=control.control_id,
            control_name=control.name,
            threat_event_frequency_multiplier=1.0,
            vulnerability_multiplier=1.0,
            primary_loss_multiplier=1.0,
            secondary_loss_multiplier=1.0,
            control_effectiveness=control.calculate_risk_reduction_factor(),
            confidence_level=0.95,
            risk_reduction_value=0.0,
            control_cost=control.cost_model.annual_cost,
            loss_reduction_per_event=loss_reduction_per_event,
            breakdown=breakdown_entries,  # PR μ.1b #129 §6
        )
