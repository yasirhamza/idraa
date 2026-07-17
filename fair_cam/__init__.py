# FAIR-CAM Package
"""
FAIR Controls Analytics Model (FAIR-CAM)
==========================================

Quantitative cybersecurity-control effectiveness modeling on a native
numpy/scipy Monte-Carlo engine (epic #324 removed the pyfair dependency).

Key Components (post PR phi cleanup; post epic #324 native-engine cutover):
- Control: Individual cybersecurity control modeling
- NativeControlAwareRiskCalculator: native FAIR-CAM Monte-Carlo engine (the
  control-aware sampler v3 run_executor consumes)
- ControlEffectivenessCalculator: PR kappa multi-domain composition
- create_industry_calibrated_parameters: IRIS-2025-grounded scenario calibration
"""

__version__ = "0.1.0"
__author__ = "Idraa"

# Core model exports
# Control effectiveness
from .controls.effectiveness import ControlEffectivenessCalculator
from .models.control import (
    ComplexityLevel,
    Control,
    ControlDomain,
    ControlRegistry,
    ControlType,
    CostModel,
    EffectivenessMetric,
)
from .models.risk_enhanced import ControlAdjustment, ControlEnhancedRisk
from .parameters.industry_calibration import create_industry_calibrated_parameters

# Risk calculation engines
from .risk_engine.fair_core import (
    FAIREngine,
    FAIRParameters,
)
from .risk_engine.native_control_aware import NativeControlAwareRiskCalculator

__all__ = [  # noqa: RUF022 — grouped by category (models / engines / effectiveness), not alphabetical
    # Core models
    "Control",
    "ControlDomain",
    "ControlType",
    "ComplexityLevel",
    "CostModel",
    "EffectivenessMetric",
    "ControlRegistry",
    "ControlEnhancedRisk",
    "ControlAdjustment",
    # Risk engines
    "NativeControlAwareRiskCalculator",
    "FAIREngine",
    "FAIRParameters",
    "create_industry_calibrated_parameters",
    # Effectiveness
    "ControlEffectivenessCalculator",
]
