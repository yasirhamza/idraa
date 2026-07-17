"""
Control modeling for FAIR CAM

This module provides core classes for modeling cybersecurity controls
and their effectiveness within the FAIR risk analysis framework.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from .sub_function import TIME_UNIT_EXCLUDED, FairCamSubFunction


@dataclass
class FairCamControlFunctionAssignment:
    """Per-assignment effectiveness — mirrors v3's ControlFunctionAssignment ORM.

    Capability/coverage/reliability are sub-function-specific per Standard §2.4.
    Coverage and reliability are bounded to [0, 1]; capability_value is unbounded
    here because ELAPSED_TIME and CURRENCY sub-functions store natural-unit values
    (PR μ activates time-unit normalization).
    """

    sub_function: FairCamSubFunction
    capability_value: float | None
    coverage: float
    reliability: float
    measured_at: datetime | None = None
    confirmed_by_user_at: datetime | None = None
    degradation_rate: float = 0.0  # daily decay; PR μ supplies normative defaults

    def __post_init__(self) -> None:
        if not (0.0 <= self.coverage <= 1.0):
            raise ValueError(
                f"FairCamControlFunctionAssignment.coverage must be in [0, 1]; got {self.coverage}"
            )
        if not (0.0 <= self.reliability <= 1.0):
            raise ValueError(
                f"FairCamControlFunctionAssignment.reliability must be in [0, 1]; "
                f"got {self.reliability}"
            )


class ControlDomain(Enum):
    """FAIR CAM Control Domains - LEC/VMC/DSC Classification"""

    # Loss Event Controls (LEC) - Directly reduce loss event frequency or magnitude
    LOSS_EVENT = "loss_event"  # Prevent/mitigate threat events and their impacts

    # Variance Management Controls (VMC) - Manage performance variation in other controls
    VARIANCE_MANAGEMENT = "variance"  # Ensure reliable operation of other controls

    # Decision Support Controls (DSC) - Improve decision-making capabilities
    DECISION_SUPPORT = "decision"  # Enhance management visibility and decisions


def subfunction_to_domain(subfn: FairCamSubFunction) -> ControlDomain:
    """Map a sub-function to its FAIR-CAM domain.

    FAIR-CAM standard §2.2 (page 5): each sub-function belongs to exactly
    one of LEC / VMC / DSC.

    Internal helper for Layer 3 per-sub-function multiplier branching
    (issue #90). NOT re-exported from fair_cam/__init__.py.
    """
    name = subfn.value
    if name.startswith("lec_"):
        return ControlDomain.LOSS_EVENT
    elif name.startswith("vmc_"):
        return ControlDomain.VARIANCE_MANAGEMENT
    elif name.startswith("dsc_"):
        return ControlDomain.DECISION_SUPPORT
    raise ValueError(f"Unknown sub-function domain prefix for {subfn.value!r}")


class ControlFunction(Enum):
    """Granular control functions within each domain"""

    # Loss Event Control Functions
    THREAT_PREVENTION = "threat_prevention"  # Block threat actors
    VULNERABILITY_REDUCTION = "vulnerability_reduction"  # Reduce attack surface
    IMPACT_MITIGATION = "impact_mitigation"  # Limit damage when events occur

    # Variance Management Control Functions
    PERFORMANCE_MONITORING = "performance_monitoring"  # Monitor control effectiveness
    CONFIGURATION_MANAGEMENT = "configuration_management"  # Maintain control settings
    MAINTENANCE_SCHEDULING = "maintenance_scheduling"  # Ensure controls stay operational

    # Decision Support Control Functions
    RISK_VISIBILITY = "risk_visibility"  # Provide risk awareness
    COMPLIANCE_REPORTING = "compliance_reporting"  # Support regulatory decisions
    STRATEGIC_PLANNING = "strategic_planning"  # Enable risk-informed planning


class FairCamMapping(Enum):
    """FAIR-CAM ontological mappings for controls"""

    # FAIR frequency factors affected by controls
    CONTACT_FREQUENCY = "contact_frequency"  # How often threat actor attempts contact
    PROBABILITY_OF_ACTION = "probability_of_action"  # Likelihood actor takes action
    THREAT_CAPABILITY = "threat_capability"  # Actor's skill/resources
    CONTROL_STRENGTH = "control_strength"  # Resistance against threat

    # FAIR magnitude factors affected by controls
    PRIMARY_LOSS_MAGNITUDE = "primary_loss_magnitude"  # Direct financial impact
    SECONDARY_LOSS_MAGNITUDE = "secondary_loss_magnitude"  # Indirect consequences
    LOSS_EVENT_FREQUENCY = "loss_event_frequency"  # Combined frequency outcome


class ControlType(Enum):
    """Control implementation types"""

    PREVENTIVE = "preventive"
    DETECTIVE = "detective"
    CORRECTIVE = "corrective"
    ADMINISTRATIVE = "administrative"
    TECHNICAL = "technical"
    PHYSICAL = "physical"


class ComplexityLevel(Enum):
    """Control implementation complexity levels"""

    LOW = 1
    MEDIUM = 2
    HIGH = 3
    VERY_HIGH = 4


class DependencyType(Enum):
    """Types of control dependencies and relationships"""

    # Functional Dependencies
    PREREQUISITE = "prerequisite"  # Must exist before this control can function
    ENABLER = "enabler"  # Enhances effectiveness of this control
    COMPLEMENTARY = "complementary"  # Works together for combined effect

    # Technical Dependencies
    INFRASTRUCTURE = "infrastructure"  # Requires shared infrastructure
    DATA_FEED = "data_feed"  # Requires data from another control
    CONFIGURATION = "configuration"  # Shares configuration management

    # Operational Dependencies
    PROCESS = "process"  # Shares operational processes
    PERSONNEL = "personnel"  # Requires same skilled personnel
    MAINTENANCE = "maintenance"  # Shares maintenance schedules

    # Risk-based Relationships
    COMPENSATING = "compensating"  # Provides backup if other control fails
    REDUNDANT = "redundant"  # Provides similar protection (overlap)
    CASCADING = "cascading"  # Failure impacts other controls


class DependencyStrength(Enum):
    """Strength of dependency relationship"""

    WEAK = 0.25  # Minimal impact if dependency unavailable
    MODERATE = 0.5  # Noticeable impact on effectiveness
    STRONG = 0.75  # Significant effectiveness reduction
    CRITICAL = 1.0  # Control cannot function without dependency


@dataclass
class ControlDependency:
    """Represents a dependency relationship between controls"""

    source_control_id: str  # Control that has the dependency
    target_control_id: str  # Control that is depended upon
    dependency_type: DependencyType
    strength: DependencyStrength
    description: str = ""

    # Impact quantification
    effectiveness_impact: float = 0.0  # How much effectiveness is reduced (0.0-1.0)
    reliability_impact: float = 0.0  # How much reliability is reduced (0.0-1.0)

    # Temporal aspects
    delay_tolerance_hours: float = 24.0  # How long control can operate without dependency
    recovery_time_hours: float = 4.0  # Time to restore after dependency returns

    # Metadata
    created_date: datetime = field(default_factory=datetime.now)
    validated: bool = False  # Whether dependency has been operationally validated


class PerformanceType(Enum):
    """Types of performance measurements"""

    INTENDED = "intended"  # Design-time theoretical performance
    OPERATIONAL = "operational"  # Real-world measured performance
    BASELINE = "baseline"  # Initial deployment performance
    DEGRADED = "degraded"  # Performance during failure conditions


class PerformanceMetricType(Enum):
    """Categories of performance metrics"""

    EFFECTIVENESS = "effectiveness"  # How well control achieves its purpose
    RELIABILITY = "reliability"  # Consistency of control operation
    AVAILABILITY = "availability"  # Uptime and operational readiness
    RESPONSE_TIME = "response_time"  # Speed of control activation
    DETECTION_RATE = "detection_rate"  # True positive detection capability
    FALSE_POSITIVE_RATE = "false_positive_rate"  # False alarm generation
    COVERAGE = "coverage"  # Scope of protection
    COMPLIANCE = "compliance"  # Adherence to policies/standards


@dataclass
class PerformanceMetric:
    """Quantitative measure of control performance over time"""

    metric_type: PerformanceMetricType
    performance_type: PerformanceType
    value: float
    unit: str
    confidence_level: float = 0.95
    measurement_date: datetime = field(default_factory=datetime.now)
    source: str | None = None
    validation_method: str | None = None
    notes: str | None = None


@dataclass
class PerformanceGap:
    """Analysis of gap between intended and operational performance"""

    metric_type: PerformanceMetricType
    intended_value: float
    operational_value: float
    gap_percentage: float = 0.0
    gap_severity: str = ""  # Will be calculated in __post_init__
    root_cause: str | None = None
    remediation_plan: str | None = None
    target_date: datetime | None = None

    def __post_init__(self) -> None:
        # Calculate gap percentage if not provided
        if self.intended_value > 0:
            self.gap_percentage = (
                abs(self.intended_value - self.operational_value) / self.intended_value * 100
            )
        else:
            self.gap_percentage = 0.0

        # Determine severity based on gap percentage
        if self.gap_percentage <= 10:
            self.gap_severity = "low"
        elif self.gap_percentage <= 25:
            self.gap_severity = "medium"
        elif self.gap_percentage <= 50:
            self.gap_severity = "high"
        else:
            self.gap_severity = "critical"


@dataclass
class PerformanceBaseline:
    """Baseline performance measurements for comparison"""

    control_id: str
    baseline_date: datetime
    baseline_metrics: list[PerformanceMetric] = field(default_factory=list)
    baseline_conditions: str = ""
    validation_period_days: int = 30
    next_review_date: datetime | None = None

    def __post_init__(self) -> None:
        if not self.next_review_date:
            self.next_review_date = self.baseline_date + timedelta(days=self.validation_period_days)


@dataclass
class EffectivenessMetric:
    """Quantitative measure of control effectiveness (legacy - use PerformanceMetric)"""

    name: str
    value: float
    unit: str
    confidence_level: float = 0.95
    measurement_date: datetime = field(default_factory=datetime.now)
    source: str | None = None


@dataclass
class CostModel:
    """OPEX-only annual cost estimate for a control.

    Single field by design — the prior 5-field shape (initial_cost,
    annual_operating_cost, maintenance_cost, staff_time_hours,
    staff_hourly_rate) plus a derived ``total_annual_cost`` was
    audited and found to:
      - never amortise initial_cost (math wrong for high-CapEx controls)
      - have zero test coverage on the formula
      - feed a downstream ROI / NPV / payback pipeline that was declared
        but never written (every output field was perpetually 0.0)

    FAIR-CAM Standard does not prescribe a cost model; the field is
    Standard-orthogonal financial auxiliary data. This collapsed shape
    captures one operator-supplied annual cost estimate of all-in
    expenditure on the control. If/when CapEx amortisation, NPV, or
    payback need to be modelled, it lands as additive new fields here
    plus real implementations of the calculations — not aspirational
    declarations.
    """

    annual_cost: float = 0.0


@dataclass
class Control:
    """Core control model for FAIR CAM analysis with enhanced domain classification"""

    control_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    description: str = ""

    # FAIR-CAM Domain Classification (LEC/VMC/DSC)
    domain: ControlDomain = ControlDomain.LOSS_EVENT

    # Traditional control classifications
    control_type: ControlType = ControlType.TECHNICAL

    # Financial and operational data (moved before deprecated fields for clarity)
    cost_model: CostModel = field(default_factory=CostModel)

    # Per-assignment effectiveness (spec §4.3 + §4.4)
    assignments: list[FairCamControlFunctionAssignment] = field(default_factory=list)

    # DEPRECATED -- used by Layer 3 get_fair_impact_factor; PR mu removes
    fair_cam_mappings: list[FairCamMapping] = field(default_factory=list)

    # DEPRECATED -- Overview-era 9-value enum; v3 deleted analog in T14 (PR iota);
    # PR mu removes once Layer 3 refactor lands. Default to None to allow construction
    # without specifying a value.
    control_function: ControlFunction | None = None

    # Framework mappings
    nist_mappings: list[str] = field(default_factory=list)
    cis_mappings: list[str] = field(default_factory=list)
    iso27001_mappings: list[str] = field(default_factory=list)

    # Enhanced Dependencies and relationships
    depends_on: list[str] = field(default_factory=list)  # Simple control ID dependencies (legacy)
    enables: list[str] = field(default_factory=list)  # Simple control ID enablements (legacy)
    dependencies: list[ControlDependency] = field(
        default_factory=list
    )  # Enhanced dependency modeling

    implementation_complexity: ComplexityLevel = ComplexityLevel.MEDIUM

    # Time-based characteristics
    response_time_seconds: float = 300.0  # Time to detect/respond
    recovery_time_hours: float = 24.0  # Time to restore after failure

    # Effectiveness measurements (legacy)
    effectiveness_metrics: list[EffectivenessMetric] = field(default_factory=list)

    # Enhanced Performance Tracking
    performance_metrics: list[PerformanceMetric] = field(default_factory=list)
    performance_baseline: PerformanceBaseline | None = None
    performance_gaps: list[PerformanceGap] = field(default_factory=list)

    # Performance status tracking
    last_performance_review: datetime | None = None
    next_performance_review: datetime | None = None
    performance_status: str = "unknown"  # "excellent", "good", "degraded", "critical", "unknown"

    # Metadata
    created_date: datetime = field(default_factory=datetime.now)
    last_updated: datetime = field(default_factory=datetime.now)
    tags: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.assignments:
            raise ValueError(
                f"Control {self.control_id!r} requires at least one assignment "
                f"(per spec §4.3 + §4.4)"
            )

    # ------------------------------------------------------------------
    # Compat read-only properties (T8.5 PR κ)
    # These delegate to the representative assignment so legacy callers
    # (effectiveness.py, risk_engine, viz, excel) continue to work after
    # the flat-triple constructor fields were dropped in T7.
    # PR μ Layer-3 redesign will delete these and migrate all callers.
    # ------------------------------------------------------------------

    def _representative_assignment(self) -> "FairCamControlFunctionAssignment":
        """Return first non-time-unit assignment, or assignments[0] as fallback."""
        for a in self.assignments:
            if a.sub_function not in TIME_UNIT_EXCLUDED:
                return a
        return self.assignments[0]

    @property
    def control_strength(self) -> float:
        """Compat: capability_value of representative assignment (T8.5 PR κ; deleted PR μ)."""
        a = self._representative_assignment()
        cap = a.capability_value
        if cap is None:
            return 0.5  # half-life default when elapsed time is unknown
        return max(0.0, min(1.0, cap)) if cap <= 1.0 else cap

    @property
    def control_reliability(self) -> float:
        """Compat: reliability of representative assignment (T8.5 PR κ; deleted PR μ)."""
        return self._representative_assignment().reliability

    @property
    def control_coverage(self) -> float:
        """Compat: coverage of representative assignment (T8.5 PR κ; deleted PR μ)."""
        return self._representative_assignment().coverage

    def add_effectiveness_metric(self, metric: EffectivenessMetric) -> None:
        """Add a new effectiveness measurement"""
        self.effectiveness_metrics.append(metric)
        self.last_updated = datetime.now()

    def get_current_capability(self, sub_function: FairCamSubFunction) -> float:
        """Layer 1 input: capability for a specific sub-function, with degradation applied.

        Raises KeyError if no assignment for the given sub-function.
        """
        for a in self.assignments:
            if a.sub_function == sub_function:
                days_since_update = (datetime.now() - self.last_updated).days
                cap = a.capability_value
                if cap is None:
                    return 0.5  # half-life default when elapsed time is unknown
                degraded = cap * (1 - a.degradation_rate * days_since_update)
                return max(0.0, min(1.0, degraded)) if cap <= 1.0 else degraded
        raise KeyError(f"Control {self.control_id!r} has no assignment for {sub_function.value!r}")

    def calculate_risk_reduction_factor(self) -> float:
        """Per-control Layer-2 squash: OR-aggregation across all assignments
        of their Layer-1 OpEff.

        PR μ.1 (Arch-B3): uses compute_assignment_opeff_two_branch helper.
          * PROBABILITY / PERCENT_REDUCTION: (capability * coverage) * reliability
            (NULL capability → 0.5 * coverage * reliability fallback).
          * ELAPSED_TIME: exp(-t/τ) * c * r (NULL → τ·ln(2) fallback).
          * CURRENCY: EXCLUDED from the squash (no opeff semantic).

        Coarse per-control aggregation; legacy callers (Excel, viz). Risk math
        goes through `ControlAwareRiskCalculator` for proper Boolean topology.

        Spec §5.1, §3.2.4 + plan-gate Arch-B3.
        """
        from ..composition import compute_assignment_opeff_two_branch, or_compose

        opeffs: list[float] = []
        for a in self.assignments:
            v = compute_assignment_opeff_two_branch(a)
            if v is not None:  # exclude CURRENCY (returns None)
                opeffs.append(v)
        if not opeffs:
            return 0.0
        return or_compose(opeffs)

    def get_current_effectiveness(self) -> float:
        """Compat alias for calculate_risk_reduction_factor (T8.5 PR κ; deleted PR μ).

        Legacy callers (controls/effectiveness.py, models/control.py Bucket-B methods,
        ControlRegistry) used get_current_effectiveness().  Delegates to the new
        Layer-2 squash so behaviour is consistent.
        """
        return self.calculate_risk_reduction_factor()

    def get_framework_mappings(self) -> dict[str, list[str]]:
        """Get all framework mappings"""
        return {
            "NIST": self.nist_mappings,
            "CIS": self.cis_mappings,
            "ISO27001": self.iso27001_mappings,
        }

    def get_fair_cam_classification(self) -> dict[str, str | list[str]]:
        """Get FAIR-CAM domain classification"""
        cf = self.control_function
        return {
            "domain": self.domain.value,
            "domain_description": self._get_domain_description(),
            "function": cf.value if cf is not None else "",
            "function_description": self._get_function_description() if cf is not None else "",
            "fair_mappings": [mapping.value for mapping in self.fair_cam_mappings],
        }

    def _get_domain_description(self) -> str:
        """Get human-readable domain description"""
        descriptions = {
            ControlDomain.LOSS_EVENT: "Loss Event Controls (LEC) - Directly reduce threat event frequency or impact magnitude",
            ControlDomain.VARIANCE_MANAGEMENT: "Variance Management Controls (VMC) - Ensure reliable performance of other controls",
            ControlDomain.DECISION_SUPPORT: "Decision Support Controls (DSC) - Improve management decision-making capabilities",
        }
        return descriptions.get(self.domain, "Unknown domain")

    def _get_function_description(self) -> str:
        """Get human-readable function description"""
        descriptions = {
            # Loss Event Control Functions
            ControlFunction.THREAT_PREVENTION: "Blocks or deters threat actors from initiating attacks",
            ControlFunction.VULNERABILITY_REDUCTION: "Reduces attack surface and exploitable weaknesses",
            ControlFunction.IMPACT_MITIGATION: "Limits damage and consequences when loss events occur",
            # Variance Management Control Functions
            ControlFunction.PERFORMANCE_MONITORING: "Monitors effectiveness and performance of other controls",
            ControlFunction.CONFIGURATION_MANAGEMENT: "Maintains proper configuration of security controls",
            ControlFunction.MAINTENANCE_SCHEDULING: "Ensures controls remain operational and effective",
            # Decision Support Control Functions
            ControlFunction.RISK_VISIBILITY: "Provides risk awareness and visibility to management",
            ControlFunction.COMPLIANCE_REPORTING: "Supports regulatory compliance and reporting decisions",
            ControlFunction.STRATEGIC_PLANNING: "Enables risk-informed strategic planning and investment",
        }
        if self.control_function is None:
            return "Unknown function"
        return descriptions.get(self.control_function, "Unknown function")

    def add_fair_cam_mapping(self, mapping: FairCamMapping) -> None:
        """Add a FAIR-CAM ontological mapping"""
        if mapping not in self.fair_cam_mappings:
            self.fair_cam_mappings.append(mapping)
            self.last_updated = datetime.now()

    def get_fair_impact_factor(self) -> dict[str, float]:
        """Calculate how this control affects specific FAIR factors"""
        impact_factors = {}
        current_effectiveness = self.get_current_effectiveness()

        for mapping in self.fair_cam_mappings:
            if mapping == FairCamMapping.CONTACT_FREQUENCY:
                # Reduce frequency of threat actor contact attempts
                impact_factors["contact_frequency_reduction"] = current_effectiveness * 0.8
            elif mapping == FairCamMapping.PROBABILITY_OF_ACTION:
                # Reduce likelihood threat actor takes action
                impact_factors["action_probability_reduction"] = current_effectiveness * 0.7
            elif mapping == FairCamMapping.THREAT_CAPABILITY:
                # Effectively increases difficulty for threat actor
                impact_factors["threat_capability_mitigation"] = current_effectiveness * 0.6
            elif mapping == FairCamMapping.CONTROL_STRENGTH:
                # Direct resistance against threat
                impact_factors["control_strength_value"] = current_effectiveness
            elif mapping == FairCamMapping.PRIMARY_LOSS_MAGNITUDE:
                # Reduce direct financial impact
                impact_factors["primary_loss_reduction"] = current_effectiveness * 0.5
            elif mapping == FairCamMapping.SECONDARY_LOSS_MAGNITUDE:
                # Reduce indirect consequences
                impact_factors["secondary_loss_reduction"] = current_effectiveness * 0.4
            elif mapping == FairCamMapping.LOSS_EVENT_FREQUENCY:
                # Overall frequency reduction
                impact_factors["frequency_reduction"] = current_effectiveness * 0.9

        return impact_factors

    def add_dependency(self, dependency: ControlDependency) -> None:
        """Add a control dependency"""
        if dependency not in self.dependencies:
            self.dependencies.append(dependency)
            self.last_updated = datetime.now()

    def remove_dependency(
        self, target_control_id: str, dependency_type: DependencyType | None = None
    ) -> bool:
        """Remove a control dependency"""
        removed = False
        dependencies_to_remove = []

        for dep in self.dependencies:
            if dep.target_control_id == target_control_id and (
                dependency_type is None or dep.dependency_type == dependency_type
            ):
                dependencies_to_remove.append(dep)
                removed = True

        for dep in dependencies_to_remove:
            self.dependencies.remove(dep)

        if removed:
            self.last_updated = datetime.now()

        return removed

    def get_dependencies_by_type(self, dependency_type: DependencyType) -> list[ControlDependency]:
        """Get all dependencies of a specific type"""
        return [dep for dep in self.dependencies if dep.dependency_type == dependency_type]

    def get_critical_dependencies(self) -> list[ControlDependency]:
        """Get all critical dependencies that could cause control failure"""
        return [dep for dep in self.dependencies if dep.strength == DependencyStrength.CRITICAL]

    def calculate_dependency_risk_factor(self, registry: "ControlRegistry | None" = None) -> float:
        """Calculate how dependencies affect this control's reliability"""
        if not self.dependencies:
            return 1.0  # No dependencies = no risk

        risk_factors = []

        for dep in self.dependencies:
            # Base risk from dependency strength
            base_risk = 1.0 - dep.strength.value

            # If registry available, consider target control's actual reliability
            if registry:
                target_control = registry.get_control(dep.target_control_id)
                if target_control:
                    target_reliability = target_control.control_reliability
                    # Combine dependency strength with target reliability
                    dependency_risk = dep.strength.value * (1.0 - target_reliability)
                    risk_factors.append(dependency_risk)
                else:
                    # Target control doesn't exist - maximum risk
                    risk_factors.append(dep.strength.value)
            else:
                # Use strength as proxy for risk
                risk_factors.append(1.0 - base_risk)

        # Calculate combined risk (assuming dependencies are independent)
        combined_reliability = 1.0
        for risk in risk_factors:
            combined_reliability *= 1.0 - risk

        return combined_reliability

    def get_adjusted_effectiveness(self, registry: "ControlRegistry | None" = None) -> float:
        """Get effectiveness adjusted for dependency impacts"""
        base_effectiveness = self.get_current_effectiveness()
        dependency_factor = self.calculate_dependency_risk_factor(registry)
        return base_effectiveness * dependency_factor

    def get_dependency_summary(self) -> dict[str, Any]:
        """Get summary of all dependencies"""
        if not self.dependencies:
            return {"total_dependencies": 0, "critical_dependencies": 0, "dependency_types": {}}

        type_counts = {}
        for dep in self.dependencies:
            dep_type = dep.dependency_type.value
            if dep_type not in type_counts:
                type_counts[dep_type] = 0
            type_counts[dep_type] += 1

        return {
            "total_dependencies": len(self.dependencies),
            "critical_dependencies": len(self.get_critical_dependencies()),
            "dependency_types": type_counts,
            "average_strength": sum(dep.strength.value for dep in self.dependencies)
            / len(self.dependencies),
        }

    def add_performance_metric(self, metric: PerformanceMetric) -> None:
        """Add a new performance measurement"""
        if metric not in self.performance_metrics:
            self.performance_metrics.append(metric)
            self.last_updated = datetime.now()

            # Update performance status based on new metric
            self._update_performance_status()

    def set_performance_baseline(
        self, baseline_conditions: str = "", validation_period_days: int = 30
    ) -> None:
        """Establish performance baseline for future comparisons"""
        baseline_metrics = []

        # Create baseline metrics from current intended values
        baseline_metrics.extend(
            [
                PerformanceMetric(
                    metric_type=PerformanceMetricType.EFFECTIVENESS,
                    performance_type=PerformanceType.BASELINE,
                    value=self.control_strength,
                    unit="ratio",
                    source="baseline_establishment",
                ),
                PerformanceMetric(
                    metric_type=PerformanceMetricType.RELIABILITY,
                    performance_type=PerformanceType.BASELINE,
                    value=self.control_reliability,
                    unit="ratio",
                    source="baseline_establishment",
                ),
                PerformanceMetric(
                    metric_type=PerformanceMetricType.COVERAGE,
                    performance_type=PerformanceType.BASELINE,
                    value=self.control_coverage,
                    unit="ratio",
                    source="baseline_establishment",
                ),
                PerformanceMetric(
                    metric_type=PerformanceMetricType.RESPONSE_TIME,
                    performance_type=PerformanceType.BASELINE,
                    value=self.response_time_seconds,
                    unit="seconds",
                    source="baseline_establishment",
                ),
            ]
        )

        self.performance_baseline = PerformanceBaseline(
            control_id=self.control_id,
            baseline_date=datetime.now(),
            baseline_metrics=baseline_metrics,
            baseline_conditions=baseline_conditions,
            validation_period_days=validation_period_days,
        )

        self.last_performance_review = datetime.now()
        self.next_performance_review = datetime.now() + timedelta(days=validation_period_days)
        self.last_updated = datetime.now()

    def analyze_performance_gaps(self) -> list[PerformanceGap]:
        """Analyze gaps between intended and operational performance"""
        gaps = []

        # Get latest operational metrics for each type
        operational_metrics = {}
        intended_metrics = {}

        for metric in self.performance_metrics:
            if metric.performance_type == PerformanceType.OPERATIONAL:
                operational_metrics[metric.metric_type] = metric
            elif metric.performance_type == PerformanceType.INTENDED:
                intended_metrics[metric.metric_type] = metric

        # Compare intended vs operational for each metric type
        for metric_type in PerformanceMetricType:
            if metric_type in intended_metrics and metric_type in operational_metrics:
                intended_value = intended_metrics[metric_type].value
                operational_value = operational_metrics[metric_type].value

                gap = PerformanceGap(
                    metric_type=metric_type,
                    intended_value=intended_value,
                    operational_value=operational_value,
                    # gap_percentage and gap_severity will be calculated in __post_init__
                )
                gaps.append(gap)

        # Update stored gaps
        self.performance_gaps = gaps
        return gaps

    def get_performance_summary(self) -> dict[str, Any]:
        """Get comprehensive performance summary"""
        if not self.performance_metrics:
            return {"status": "no_data", "message": "No performance metrics available"}

        # Analyze current gaps
        current_gaps = self.analyze_performance_gaps()

        # Get latest metrics by type
        latest_metrics = {}
        for metric in sorted(
            self.performance_metrics, key=lambda x: x.measurement_date, reverse=True
        ):
            if metric.metric_type not in latest_metrics:
                latest_metrics[metric.metric_type] = metric

        # Calculate overall performance score
        performance_score = self._calculate_performance_score()

        return {
            "overall_score": performance_score,
            "performance_status": self.performance_status,
            "total_metrics": len(self.performance_metrics),
            "performance_gaps": len(current_gaps),
            "critical_gaps": len([g for g in current_gaps if g.gap_severity == "critical"]),
            "last_review": self.last_performance_review,
            "next_review": self.next_performance_review,
            "baseline_established": self.performance_baseline is not None,
            "latest_metrics": {mt.value: m.value for mt, m in latest_metrics.items()},
            "gap_details": [
                {
                    "metric": gap.metric_type.value,
                    "intended": gap.intended_value,
                    "operational": gap.operational_value,
                    "gap_percentage": gap.gap_percentage,
                    "severity": gap.gap_severity,
                }
                for gap in current_gaps
            ],
        }

    def get_intended_vs_operational_comparison(self) -> dict[str, dict[str, float | None]]:
        """Get side-by-side comparison of intended vs operational performance"""
        comparison: dict[str, dict[str, float | None]] = {}

        for metric_type in PerformanceMetricType:
            intended_value = None
            operational_value = None

            # Find latest metrics of each type
            for metric in sorted(
                self.performance_metrics, key=lambda x: x.measurement_date, reverse=True
            ):
                if metric.metric_type == metric_type:
                    if (
                        metric.performance_type == PerformanceType.INTENDED
                        and intended_value is None
                    ):
                        intended_value = metric.value
                    elif (
                        metric.performance_type == PerformanceType.OPERATIONAL
                        and operational_value is None
                    ):
                        operational_value = metric.value

            if intended_value is not None or operational_value is not None:
                comparison[metric_type.value] = {
                    "intended": intended_value,
                    "operational": operational_value,
                    "gap": abs(intended_value - operational_value)
                    if intended_value and operational_value
                    else None,
                }

        return comparison

    def _update_performance_status(self) -> None:
        """Update overall performance status based on current gaps"""
        gaps = self.analyze_performance_gaps()

        if not gaps:
            self.performance_status = "unknown"
            return

        # Count gaps by severity
        critical_gaps = len([g for g in gaps if g.gap_severity == "critical"])
        high_gaps = len([g for g in gaps if g.gap_severity == "high"])
        medium_gaps = len([g for g in gaps if g.gap_severity == "medium"])

        if critical_gaps > 0:
            self.performance_status = "critical"
        elif high_gaps > 0 or medium_gaps > 1:
            self.performance_status = "degraded"
        elif medium_gaps == 1:
            self.performance_status = "good"
        else:
            self.performance_status = "excellent"

    def _calculate_performance_score(self) -> float:
        """Calculate overall performance score (0.0 to 1.0)"""
        if not self.performance_gaps:
            return 1.0  # No gaps identified

        total_penalty = 0.0
        for gap in self.performance_gaps:
            # Penalty based on gap severity
            if gap.gap_severity == "critical":
                total_penalty += 0.4
            elif gap.gap_severity == "high":
                total_penalty += 0.2
            elif gap.gap_severity == "medium":
                total_penalty += 0.1
            elif gap.gap_severity == "low":
                total_penalty += 0.05

        return max(0.0, 1.0 - total_penalty)

    def get_performance_trend_analysis(self, days: int = 30) -> dict[str, Any]:
        """Analyze performance trends over specified period"""
        cutoff_date = datetime.now() - timedelta(days=days)
        recent_metrics = [m for m in self.performance_metrics if m.measurement_date >= cutoff_date]

        if not recent_metrics:
            return {
                "status": "insufficient_data",
                "message": f"No metrics found in last {days} days",
            }

        # Group by metric type and calculate trends
        trends: dict[str, dict[str, object]] = {}
        for metric_type in PerformanceMetricType:
            type_metrics = [m for m in recent_metrics if m.metric_type == metric_type]
            if len(type_metrics) >= 2:
                # Simple trend calculation (first vs last)
                type_metrics.sort(key=lambda x: x.measurement_date)
                first_value = type_metrics[0].value
                last_value = type_metrics[-1].value

                trend_direction = (
                    "improving"
                    if last_value > first_value
                    else "declining"
                    if last_value < first_value
                    else "stable"
                )
                trend_magnitude = (
                    abs(last_value - first_value) / first_value * 100 if first_value > 0 else 0
                )

                trends[metric_type.value] = {
                    "direction": trend_direction,
                    "magnitude": trend_magnitude,
                    "first_value": first_value,
                    "last_value": last_value,
                    "measurements": len(type_metrics),
                }

        return {
            "period_days": days,
            "total_measurements": len(recent_metrics),
            "metric_trends": trends,
            "overall_trend": self._determine_overall_trend(trends),
        }

    def _determine_overall_trend(self, trends: dict[str, dict[str, object]]) -> str:
        """Determine overall performance trend from individual metric trends"""
        if not trends:
            return "unknown"

        improving = len([t for t in trends.values() if t["direction"] == "improving"])
        declining = len([t for t in trends.values() if t["direction"] == "declining"])

        if improving > declining:
            return "improving"
        elif declining > improving:
            return "declining"
        else:
            return "stable"


class ControlRegistry:
    """Registry for managing and organizing controls"""

    def __init__(self) -> None:
        self._controls: dict[str, Control] = {}
        self._domain_index: dict[ControlDomain, list[str]] = {
            domain: [] for domain in ControlDomain
        }
        self._framework_index: dict[str, dict[str, list[str]]] = {
            "NIST": {},
            "CIS": {},
            "ISO27001": {},
        }

    def register_control(self, control: Control) -> None:
        """Register a new control in the registry"""
        self._controls[control.control_id] = control

        # Update domain index
        if control.control_id not in self._domain_index[control.domain]:
            self._domain_index[control.domain].append(control.control_id)

        # Update framework indices
        for framework, mappings in control.get_framework_mappings().items():
            for mapping in mappings:
                if mapping not in self._framework_index[framework]:
                    self._framework_index[framework][mapping] = []
                if control.control_id not in self._framework_index[framework][mapping]:
                    self._framework_index[framework][mapping].append(control.control_id)

    def get_control(self, control_id: str) -> Control | None:
        """Retrieve a control by ID"""
        return self._controls.get(control_id)

    def get_controls_by_domain(self, domain: ControlDomain) -> list[Control]:
        """Get all controls in a specific domain"""
        control_ids = self._domain_index[domain]
        return [self._controls[cid] for cid in control_ids if cid in self._controls]

    def get_controls_by_framework(self, framework: str, control_ref: str) -> list[Control]:
        """Get controls mapped to a specific framework reference"""
        if framework in self._framework_index and control_ref in self._framework_index[framework]:
            control_ids = self._framework_index[framework][control_ref]
            return [self._controls[cid] for cid in control_ids if cid in self._controls]
        return []

    def get_controls_by_function(self, function: ControlFunction) -> list[Control]:
        """Get all controls with a specific function"""
        return [
            control for control in self._controls.values() if control.control_function == function
        ]

    def get_controls_by_fair_mapping(self, mapping: FairCamMapping) -> list[Control]:
        """Get controls that affect a specific FAIR factor"""
        return [
            control for control in self._controls.values() if mapping in control.fair_cam_mappings
        ]

    def get_domain_distribution(self) -> dict[str, int]:
        """Get distribution of controls across FAIR-CAM domains"""
        distribution = {domain.value: 0 for domain in ControlDomain}
        for control in self._controls.values():
            distribution[control.domain.value] += 1
        return distribution

    def get_domain_effectiveness_summary(self) -> dict[str, dict[str, float]]:
        """Get effectiveness summary by domain"""
        summary = {}

        for domain in ControlDomain:
            domain_controls = self.get_controls_by_domain(domain)
            if domain_controls:
                effectiveness_values = [c.get_current_effectiveness() for c in domain_controls]
                summary[domain.value] = {
                    "count": len(domain_controls),
                    "avg_effectiveness": sum(effectiveness_values) / len(effectiveness_values),
                    "min_effectiveness": min(effectiveness_values),
                    "max_effectiveness": max(effectiveness_values),
                    "total_annual_cost": sum(c.cost_model.annual_cost for c in domain_controls),
                }
            else:
                summary[domain.value] = {
                    "count": 0,
                    "avg_effectiveness": 0.0,
                    "min_effectiveness": 0.0,
                    "max_effectiveness": 0.0,
                    "total_annual_cost": 0.0,
                }

        return summary

    def find_dependencies(self, control_id: str) -> list[Control]:
        """Find all controls that this control depends on"""
        control = self.get_control(control_id)
        if not control:
            return []

        dependencies = []
        for dep_id in control.depends_on:
            dep_control = self.get_control(dep_id)
            if dep_control:
                dependencies.append(dep_control)
        return dependencies

    def find_dependents(self, control_id: str) -> list[Control]:
        """Find all controls that depend on this control"""
        dependents = []
        for control in self._controls.values():
            if control_id in control.depends_on:
                dependents.append(control)
        return dependents

    def calculate_control_interdependency_factor(self, control_id: str) -> float:
        """Calculate quantified interdependency factor for a control"""
        control = self.get_control(control_id)
        if not control:
            return 0.0

        dependencies = self.find_dependencies(control_id)
        dependents = self.find_dependents(control_id)

        # Simple interdependency calculation based on dependency count and effectiveness
        dependency_factor = sum(dep.get_current_effectiveness() for dep in dependencies) / max(
            1, len(dependencies)
        )
        dependent_impact = len(dependents) * 0.1  # Each dependent adds 10% complexity

        return min(1.0, dependency_factor + dependent_impact)

    def get_all_controls(self) -> list[Control]:
        """Get all registered controls"""
        return list(self._controls.values())

    def search_controls(self, query: str) -> list[Control]:
        """Search controls by name, description, or tags"""
        query_lower = query.lower()
        results = []

        for control in self._controls.values():
            if (
                query_lower in control.name.lower()
                or query_lower in control.description.lower()
                or any(query_lower in tag.lower() for tag in control.tags)
            ):
                results.append(control)

        return results

    def add_dependency(
        self,
        source_control_id: str,
        target_control_id: str,
        dependency_type: DependencyType,
        strength: DependencyStrength,
        description: str = "",
        **kwargs: Any,
    ) -> bool:
        """Add a dependency relationship between two controls"""
        source_control = self.get_control(source_control_id)
        target_control = self.get_control(target_control_id)

        if not source_control or not target_control:
            return False

        if source_control_id == target_control_id:
            return False  # Prevent self-dependencies

        # Check for circular dependencies
        if self._would_create_circular_dependency(source_control_id, target_control_id):
            return False

        dependency = ControlDependency(
            source_control_id=source_control_id,
            target_control_id=target_control_id,
            dependency_type=dependency_type,
            strength=strength,
            description=description,
            **kwargs,
        )

        source_control.add_dependency(dependency)
        return True

    def remove_dependency(
        self,
        source_control_id: str,
        target_control_id: str,
        dependency_type: DependencyType | None = None,
    ) -> bool:
        """Remove a dependency relationship"""
        source_control = self.get_control(source_control_id)
        if source_control:
            return source_control.remove_dependency(target_control_id, dependency_type)
        return False

    def _would_create_circular_dependency(self, source_id: str, target_id: str) -> bool:
        """Check if adding dependency would create circular dependency"""
        visited = set()

        def has_path_to_source(current_id: str) -> bool:
            if current_id == source_id:
                return True
            if current_id in visited:
                return False

            visited.add(current_id)
            current_control = self.get_control(current_id)

            if current_control:
                for dep in current_control.dependencies:
                    if has_path_to_source(dep.target_control_id):
                        return True

            return False

        return has_path_to_source(target_id)

    def get_dependency_chain(self, control_id: str, max_depth: int = 10) -> list[list[str]]:
        """Get all dependency chains for a control"""
        chains: list[list[str]] = []
        visited: set[str] = set()

        def build_chains(current_id: str, current_chain: list[str], depth: int) -> None:
            if depth > max_depth or current_id in visited:
                return

            visited.add(current_id)
            current_control = self.get_control(current_id)

            if not current_control or not current_control.dependencies:
                if len(current_chain) > 1:  # Only add chains with dependencies
                    chains.append(current_chain.copy())
                return

            for dep in current_control.dependencies:
                new_chain = [*current_chain, dep.target_control_id]
                build_chains(dep.target_control_id, new_chain, depth + 1)

        build_chains(control_id, [control_id], 0)
        return chains

    def get_control_impact_analysis(self, control_id: str) -> dict[str, Any]:
        """Analyze impact of control failure on other controls"""
        impacted_controls: list[dict[str, Any]] = []
        direct_dependents = self.find_dependents(control_id)

        # Calculate cascading impacts
        all_impacts: set[str] = set()
        to_process = [(dep_id, 1) for dep_id in [c.control_id for c in direct_dependents]]

        while to_process:
            current_id, depth = to_process.pop(0)
            if current_id in all_impacts or depth > 5:  # Prevent infinite loops
                continue

            all_impacts.add(current_id)
            current_control = self.get_control(current_id)

            if current_control:
                impacted_controls.append(
                    {
                        "control_id": current_id,
                        "control_name": current_control.name,
                        "impact_depth": depth,
                        "dependency_summary": current_control.get_dependency_summary(),
                    }
                )

                # Add next level dependencies
                next_dependents = self.find_dependents(current_id)
                for next_dep in next_dependents:
                    to_process.append((next_dep.control_id, depth + 1))

        return {
            "target_control_id": control_id,
            "total_impacted_controls": len(impacted_controls),
            "direct_dependents": len(direct_dependents),
            "max_cascade_depth": max([c["impact_depth"] for c in impacted_controls])
            if impacted_controls
            else 0,
            "impacted_controls": impacted_controls,
        }

    def get_dependency_network_metrics(self) -> dict[str, Any]:
        """Calculate network-level dependency metrics"""
        total_controls = len(self._controls)
        controls_with_dependencies = sum(1 for c in self._controls.values() if c.dependencies)
        total_dependencies = sum(len(c.dependencies) for c in self._controls.values())

        # Calculate dependency types distribution
        type_distribution: dict[str, int] = {}
        strength_distribution: dict[float, int] = {}

        for control in self._controls.values():
            for dep in control.dependencies:
                dep_type = dep.dependency_type.value
                dep_strength = dep.strength.value

                type_distribution[dep_type] = type_distribution.get(dep_type, 0) + 1
                strength_distribution[dep_strength] = strength_distribution.get(dep_strength, 0) + 1

        # Find most connected controls (high fan-in/fan-out)
        control_connections: dict[str, dict[str, Any]] = {}
        for control in self._controls.values():
            dependents = len(self.find_dependents(control.control_id))
            dependencies = len(control.dependencies)
            control_connections[control.control_id] = {
                "name": control.name,
                "dependencies_out": dependencies,
                "dependents_in": dependents,
                "total_connections": dependencies + dependents,
            }

        most_connected = sorted(
            control_connections.items(), key=lambda x: x[1]["total_connections"], reverse=True
        )[:5]

        return {
            "total_controls": total_controls,
            "controls_with_dependencies": controls_with_dependencies,
            "dependency_ratio": controls_with_dependencies / max(1, total_controls),
            "total_dependencies": total_dependencies,
            "avg_dependencies_per_control": total_dependencies / max(1, total_controls),
            "dependency_type_distribution": type_distribution,
            "dependency_strength_distribution": strength_distribution,
            "most_connected_controls": most_connected,
        }
