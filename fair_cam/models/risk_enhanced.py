"""
Enhanced risk models that incorporate control effectiveness

This module extends traditional FAIR risk calculations with
control-aware risk assessment capabilities.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np


@dataclass
class ControlAdjustment:
    """Represents how a control adjusts risk factors"""

    control_id: str
    control_name: str

    # Risk factor adjustments (multiplicative factors)
    threat_event_frequency_multiplier: float = 1.0
    vulnerability_multiplier: float = 1.0
    primary_loss_multiplier: float = 1.0
    secondary_loss_multiplier: float = 1.0

    # Control-specific metrics
    control_effectiveness: float = 0.0
    confidence_level: float = 0.95

    # Financial impact
    risk_reduction_value: float = 0.0  # Dollar value of risk reduced
    control_cost: float = 0.0  # Annual cost of control

    # PR μ.1: Loss-Magnitude subtractor per event (currency).
    # Per Standard §3.3.3 / audit §8.4, LEC_RESP_LOSS_REDUCTION reduces
    # lost economic value in $. NOTE §3.3.3/§8.4 ground the EXISTENCE of a
    # currency subtractor on secondary loss only; applying it as a per-bound
    # subtraction on the PERT support is a v3 implementation choice, not a
    # Standard-derived prescription (§8 "no recommendation made").
    # Accumulated across CURRENCY-unit assignments into
    # `GroupComposition.currency_subtractor_total` and applied SAMPLE-level by
    # the native engine: max(0, max(0, SL_sample) - total) per draw
    # (fair_core; issue #258's param-level PERT-support shift was retired with
    # the legacy calculator in #328 — the double-floor semantics are
    # unchanged, just applied per sample instead of per bound).
    loss_reduction_per_event: float = 0.0

    # PR μ.1b (issue #129 §6) — snapshot debuggability.
    # Per-assignment derivation breakdown. Populated by the calculator
    # at composition time so post-tau-bump audits can reconstruct the
    # original opeff from the snapshot rather than re-deriving with
    # current canonical values. One dict per assignment with the
    # unit-conditional schema documented in design §6.
    breakdown: list[dict[str, Any]] = field(default_factory=list)

    def calculate_net_benefit(self) -> float:
        """Calculate net benefit of control (risk reduction - cost)"""
        return self.risk_reduction_value - self.control_cost

    def calculate_roi(self) -> float:
        """Calculate return on investment for control"""
        if self.control_cost <= 0:
            return float("inf") if self.risk_reduction_value > 0 else 0.0
        return (self.risk_reduction_value - self.control_cost) / self.control_cost


@dataclass
class ConfidenceMetrics:
    """Statistical confidence measures for risk calculations"""

    confidence_level: float = 0.95
    lower_bound: float = 0.0
    upper_bound: float = 0.0
    standard_error: float = 0.0
    sample_size: int = 10000

    def get_confidence_interval(self) -> tuple[float, float]:
        """Get confidence interval tuple"""
        return (self.lower_bound, self.upper_bound)


@dataclass
class FAIRRisk:
    """Traditional FAIR risk calculation results"""

    loss_event_frequency: float = 0.0
    loss_magnitude: float = 0.0
    annualized_loss_expectancy: float = 0.0

    # Distribution statistics
    mean: float = 0.0
    median: float = 0.0
    mode: float = 0.0
    std_deviation: float = 0.0

    # Risk percentiles
    var_95: float = 0.0  # 95% Value at Risk
    var_99: float = 0.0  # 99% Value at Risk

    # Simulation data
    simulation_results: np.ndarray | None = None
    n_simulations: int = 10000


@dataclass
class ControlEnhancedRisk:
    """Enhanced risk model incorporating control effectiveness"""

    # Base FAIR calculations (without controls)
    base_risk: FAIRRisk = field(default_factory=FAIRRisk)

    # Control adjustments applied
    control_adjustments: list[ControlAdjustment] = field(default_factory=list)

    # Residual risk after controls
    residual_risk: FAIRRisk = field(default_factory=FAIRRisk)

    # Statistical confidence
    confidence_intervals: ConfidenceMetrics = field(default_factory=ConfidenceMetrics)

    # Metadata
    calculation_date: datetime = field(default_factory=datetime.now)
    scenario_name: str = "Default Scenario"
    scenario_id: str | None = None  # PR xi: populated when produced by aggregate path
    spawn_key: tuple[int, ...] | None = (
        None  # child SeedSequence.spawn_key — persisted for exact reproducibility
    )

    def add_control_adjustment(self, adjustment: ControlAdjustment) -> None:
        """Add a control adjustment to the risk model"""
        self.control_adjustments.append(adjustment)

    def calculate_total_risk_reduction(self) -> float:
        """Calculate total risk reduction from all controls"""
        base_ale = self.base_risk.annualized_loss_expectancy
        residual_ale = self.residual_risk.annualized_loss_expectancy
        return max(0.0, base_ale - residual_ale)

    def calculate_total_control_cost(self) -> float:
        """Calculate total annual cost of all controls"""
        return sum(adj.control_cost for adj in self.control_adjustments)

    def calculate_aggregate_roi(self) -> float:
        """Calculate aggregate ROI for all controls"""
        total_cost = self.calculate_total_control_cost()
        total_reduction = self.calculate_total_risk_reduction()

        if total_cost <= 0:
            return float("inf") if total_reduction > 0 else 0.0

        return ((total_reduction - total_cost) / total_cost) * 100
