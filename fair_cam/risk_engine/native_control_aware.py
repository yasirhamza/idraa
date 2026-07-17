# fair_cam/risk_engine/native_control_aware.py
"""Native (pyfair-free) FAIR-CAM control-aware calculator. (pyfair dependency
removed in epic #324.) Samples
through the native FAIREngine; reuses compose_groups + _group_comp_to_node_multipliers
verbatim; returns the same ControlEnhancedRisk dataclass so run_executor's payload +
simulation_results shape are unchanged. See spec
docs/superpowers/specs/2026-06-08-native-fair-engine-design.md §4.4."""

from __future__ import annotations

from typing import Any

import numpy as np

from fair_cam.controls.effectiveness import ControlEffectivenessCalculator
from fair_cam.models.control import Control, ControlRegistry
from fair_cam.models.risk_enhanced import ConfidenceMetrics, ControlEnhancedRisk, FAIRRisk
from fair_cam.risk_engine.control_attribution import build_control_adjustment, representative_value

# NB: _group_comp_to_node_multipliers lives in control_aware.py (pyfair-free fn);
# AggregateEnhancedRisk (real home control_aware.py:179, NOT models.risk_enhanced)
# is consumed by the AGGREGATE path (Task 6).
from fair_cam.risk_engine.control_aware import (
    AggregateEnhancedRisk,
    _group_comp_to_node_multipliers,
)
from fair_cam.risk_engine.fair_core import FAIREngine, FAIRParameters
from fair_cam.risk_engine.group_composition import compose_groups

__all__ = ["NativeControlAwareRiskCalculator"]


def _fair_risk_from_engine(result: dict[str, Any], n_simulations: int) -> FAIRRisk:
    return FAIRRisk(
        loss_event_frequency=float(np.mean(result["lef_distribution"])),
        loss_magnitude=float(np.mean(result["loss_magnitude_distribution"])),
        annualized_loss_expectancy=float(result["ale_mean"]),
        mean=float(result["ale_mean"]),
        median=float(result["ale_median"]),
        mode=0.0,
        std_deviation=float(result["ale_std"]),
        var_95=float(result["ale_p95"]),
        var_99=float(result["ale_p99"]),
        simulation_results=result["risk_distribution"],
        n_simulations=n_simulations,
    )


def _agg_fair_risk(risk_samples: np.ndarray, n_simulations: int) -> FAIRRisk:
    # LEF/LM not meaningful at aggregate level (matches _extract_fair_results_from_metamodel:695-696).
    # Finite guard on the ROLLUP path (Sec-R2-I1): per-scenario arrays are each
    # finite-guarded in the engine, but their accumulated sum could overflow; guard
    # symmetrically so a non-finite aggregate can never reach stored JSON.
    if not np.all(np.isfinite(risk_samples)):
        raise ValueError("aggregate rollup produced non-finite risk samples; refusing to store")
    return FAIRRisk(
        loss_event_frequency=0.0,
        loss_magnitude=0.0,
        annualized_loss_expectancy=float(np.mean(risk_samples)),
        mean=float(np.mean(risk_samples)),
        median=float(np.median(risk_samples)),
        mode=0.0,
        std_deviation=float(np.std(risk_samples)),
        var_95=float(np.percentile(risk_samples, 95)),
        var_99=float(np.percentile(risk_samples, 99)),
        simulation_results=risk_samples,
        n_simulations=n_simulations,
    )


class NativeControlAwareRiskCalculator:
    def __init__(
        self,
        controls: ControlRegistry | list[Control] | None = None,
        *,
        n_simulations: int = 10_000,
        random_seed: int | None = None,
    ):
        self.n_simulations = n_simulations
        # SeedSequence root: spawn() yields INDEPENDENT children per scenario so
        # the aggregate rollup sums independent (not comonotone) scenario streams
        # — fixes the identical-seed bug. SeedSequence(None) seeds from entropy.
        self._seed_seq = np.random.SeedSequence(random_seed)
        self._effectiveness = ControlEffectivenessCalculator()
        if isinstance(controls, ControlRegistry):
            self.control_registry = controls
        elif isinstance(controls, list):
            self.control_registry = ControlRegistry()
            for c in controls:
                self.control_registry.register_control(c)
        else:
            self.control_registry = ControlRegistry()

    def _run_engine(
        self,
        params: FAIRParameters,
        *,
        seed: np.random.SeedSequence,
        vuln_mult: float = 1.0,
        subtractor: float = 0.0,
    ) -> FAIRRisk:
        engine = FAIREngine(iterations=self.n_simulations, random_seed=seed)
        result = engine.calculate_risk(
            params, secondary_loss_subtractor=subtractor, vulnerability_multiplier=vuln_mult
        )
        return _fair_risk_from_engine(result, self.n_simulations)

    def calculate_control_enhanced_risk(
        self,
        risk_params: FAIRParameters,
        active_control_ids: list[str],
        scenario_name: str = "Control Enhanced Risk",
        *,
        availability_self_detection: bool = False,
    ) -> ControlEnhancedRisk:
        # One spawned seed for THIS scenario; base + residual SHARE it (common
        # random numbers) so the control-value delta is variance-reduced. The
        # residual MARGINAL is unaffected (harness layer-2 safe).
        seed = self._seed_seq.spawn(1)[0]
        spawn_key: tuple[int, ...] = seed.spawn_key
        base_risk = self._run_engine(risk_params, seed=seed)

        active_controls = [
            c for cid in active_control_ids if (c := self.control_registry.get_control(cid))
        ]

        b_tef = representative_value(risk_params.threat_event_frequency)
        b_vuln = representative_value(risk_params.vulnerability)
        b_pl = representative_value(risk_params.primary_loss)
        b_sl = representative_value(risk_params.secondary_loss)
        control_adjustments = [
            build_control_adjustment(
                c,
                self._effectiveness,
                b_tef,
                b_vuln,
                b_pl,
                b_sl,
                availability_self_detection=availability_self_detection,
            )
            for c in active_controls
        ]

        group_comp = compose_groups(active_controls)
        node_mults = _group_comp_to_node_multipliers(
            group_comp, availability_self_detection=availability_self_detection
        )
        adjusted_params, vuln_mult = risk_params.apply_node_multipliers(node_mults)
        residual_risk = self._run_engine(
            adjusted_params,
            seed=seed,  # CRN with base
            vuln_mult=vuln_mult,
            subtractor=group_comp.currency_subtractor_total,
        )

        return ControlEnhancedRisk(
            base_risk=base_risk,
            residual_risk=residual_risk,
            control_adjustments=control_adjustments,
            confidence_intervals=ConfidenceMetrics(),  # inert (control_aware.py:1142)
            scenario_name=scenario_name,
            spawn_key=spawn_key,
        )

    def _single(
        self,
        scenario_id: str,
        scenario_name: str,
        risk_params: FAIRParameters,
        active_control_ids: list[str],
        *,
        availability_self_detection: bool = False,
    ) -> ControlEnhancedRisk:
        # Each call to calculate_control_enhanced_risk spawns its OWN child seed
        # from self._seed_seq, so every scenario gets an INDEPENDENT stream (the
        # aggregate sums independent, not comonotone, arrays). scenario_id is
        # populated here (the aggregate-path provenance per ControlEnhancedRisk).
        out = self.calculate_control_enhanced_risk(
            risk_params,
            active_control_ids,
            scenario_name,
            availability_self_detection=availability_self_detection,
        )
        out.scenario_id = scenario_id
        return out

    def calculate_aggregate_enhanced_risk(
        self,
        per_scenario_risk_params: list[tuple[str, str, FAIRParameters]],
        active_control_ids: list[str],
        per_scenario_active_control_ids: dict[str, list[str]] | None = None,
        *,
        per_scenario_availability: dict[str, bool] | None = None,
    ) -> AggregateEnhancedRisk:
        if len(per_scenario_risk_params) < 2:
            raise ValueError("AGGREGATE requires at least 2 scenarios")

        # --- #89 coupling validations (ALL FOUR, ported from control_aware.py:527-554) ---
        # (1) every active control id is registered.
        for cid in active_control_ids:
            if self.control_registry.get_control(cid) is None:
                raise ValueError(f"active control id {cid!r} not in the registry")
        if per_scenario_active_control_ids is not None:
            universe = set(active_control_ids)
            scenario_ids = {sid for sid, _, _ in per_scenario_risk_params}
            # (2) per-scenario keys == the scenario-id set.
            if set(per_scenario_active_control_ids) != scenario_ids:
                raise ValueError(
                    "per_scenario_active_control_ids keys do not match the scenario ids"
                )
            union_of_values: set[str] = set()
            for sid, ids in per_scenario_active_control_ids.items():
                # (3) each scenario's controls subset-of the universe.
                if not set(ids) <= universe:
                    raise ValueError(
                        f"scenario {sid} controls are outside the universe {sorted(universe)}"
                    )
                union_of_values |= set(ids)
            # (4) union-equality: every declared universe control must actually be
            # applied to some scenario (no orphan in active_control_ids).
            if union_of_values != universe:
                raise ValueError(
                    f"active_control_ids {sorted(universe)} must equal the union of "
                    f"per-scenario controls {sorted(union_of_values)}"
                )
        # The n_simulations-identity check is N/A natively (single self.n_simulations,
        # shared across all scenarios) -- structurally guaranteed, no set to compare.

        n = self.n_simulations
        per_scenario: list[ControlEnhancedRisk] = []
        base_acc = np.zeros(n)
        residual_acc = np.zeros(n)
        for scenario_id, scenario_name, rp in per_scenario_risk_params:
            ctrl_ids = (
                per_scenario_active_control_ids.get(scenario_id, [])
                if per_scenario_active_control_ids is not None
                else active_control_ids
            )
            avail = (
                per_scenario_availability.get(scenario_id, False)
                if per_scenario_availability is not None
                else False
            )
            ce = self._single(
                scenario_id, scenario_name, rp, ctrl_ids, availability_self_detection=avail
            )
            per_scenario.append(ce)
            base_samples = ce.base_risk.simulation_results
            residual_samples = ce.residual_risk.simulation_results
            if base_samples is None or residual_samples is None:
                raise AssertionError(
                    "per-scenario simulation_results must be populated at aggregate time"
                )
            base_acc += base_samples  # accumulator (Arch-N1)
            residual_acc += residual_samples

        agg_without = _agg_fair_risk(base_acc, n)
        agg_with = _agg_fair_risk(residual_acc, n)
        cv_dollars = agg_without.annualized_loss_expectancy - agg_with.annualized_loss_expectancy
        cv_percent = (
            cv_dollars / agg_without.annualized_loss_expectancy * 100
            if agg_without.annualized_loss_expectancy > 0
            else 0.0
        )
        return AggregateEnhancedRisk(
            per_scenario=per_scenario,
            aggregate_without_controls=agg_without,
            aggregate_with_controls=agg_with,
            control_value_dollars=cv_dollars,
            control_value_percent=cv_percent,
            confidence_intervals=ConfidenceMetrics(),
            n_scenarios=len(per_scenario),
            n_simulations=n,
        )
