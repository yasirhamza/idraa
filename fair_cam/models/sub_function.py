"""Standard §3-§5 sub-function enum (26 values) + unit-type metadata.

This is fair_cam's own copy mirroring v3's `idraa.models.enums.FairCamSubFunction`
(added in commit 5776ff1, PR ι). String values are byte-for-byte identical so
serialized data is interchangeable between v3 and fair_cam.

The audit doc at `docs/reference/fair-cam-standard-alignment.md` §3 is the
canonical sub-function inventory and the source of truth for any future addition.

`UnitType`, `SUB_FUNCTION_UNITS`, and `TIME_UNIT_EXCLUDED` mirror v3's
`idraa.models.enums` equivalents (paranoid-review fix A2/A3 — canonical
placement at the data source, not the engine layer). The v3-side parity test
`tests/integration/test_fair_cam_v3_unit_type_parity.py` fails loudly on drift.
"""

from enum import StrEnum


class FairCamSubFunction(StrEnum):
    # LEC Prevention OR-trio (Standard §3.1)
    LEC_PREV_AVOIDANCE = "lec_prev_avoidance"
    LEC_PREV_DETERRENCE = "lec_prev_deterrence"
    LEC_PREV_RESISTANCE = "lec_prev_resistance"
    # LEC Detection AND-trio (Standard §3.2)
    LEC_DET_VISIBILITY = "lec_det_visibility"
    LEC_DET_MONITORING = "lec_det_monitoring"
    LEC_DET_RECOGNITION = "lec_det_recognition"
    # LEC Response weak-AND-trio (Standard §3.3)
    LEC_RESP_EVENT_TERMINATION = "lec_resp_event_termination"
    LEC_RESP_RESILIENCE = "lec_resp_resilience"
    LEC_RESP_LOSS_REDUCTION = "lec_resp_loss_reduction"
    # VMC Variance Prevention OR-pair (Standard §4.1)
    VMC_PREV_REDUCE_CHANGE_FREQ = "vmc_prev_reduce_change_freq"
    VMC_PREV_REDUCE_VARIANCE_PROB = "vmc_prev_reduce_variance_prob"
    # VMC Identification AND-pair (Standard §4.2)
    VMC_ID_THREAT_INTELLIGENCE = "vmc_id_threat_intelligence"
    VMC_ID_CONTROL_MONITORING = "vmc_id_control_monitoring"
    # VMC Correction AND-pair (Standard §4.3)
    VMC_CORR_TREATMENT_SELECTION = "vmc_corr_treatment_selection"
    VMC_CORR_IMPLEMENTATION = "vmc_corr_implementation"
    # DSC Misaligned-Decision Prevention AND-group (Standard §5.1)
    DSC_PREV_DEFINED_EXPECTATIONS = "dsc_prev_defined_expectations"
    DSC_PREV_COMMUNICATION = "dsc_prev_communication"
    DSC_PREV_SA_DATA_ASSET = "dsc_prev_sa_data_asset"
    DSC_PREV_SA_DATA_THREAT = "dsc_prev_sa_data_threat"
    DSC_PREV_SA_DATA_CONTROLS = "dsc_prev_sa_data_controls"
    DSC_PREV_SA_ANALYSIS = "dsc_prev_sa_analysis"
    DSC_PREV_SA_REPORTING = "dsc_prev_sa_reporting"
    DSC_PREV_ENSURE_CAPABILITY = "dsc_prev_ensure_capability"
    DSC_PREV_INCENTIVES = "dsc_prev_incentives"
    # DSC Identification (Standard §5.2)
    DSC_ID_MISALIGNED = "dsc_id_misaligned"
    # DSC Correction (Standard §5.3, virtual sub-function)
    DSC_CORR_MISALIGNED = "dsc_corr_misaligned"


class UnitType(StrEnum):
    """Unit types for FairCamSubFunction capability_value fields.

    Used by the M1 unit-type validator in ControlFunctionAssignmentDTO
    and by the OQ7 bridge skip logic in _v3_to_fair_cam_control.

    Standard: audit §2.6 unit-type table.
    """

    PROBABILITY = "probability"  # [0, 1] bounded
    PERCENT_REDUCTION = "percent_reduction"  # [0, 1] bounded
    ELAPSED_TIME = "elapsed_time"  # non-negative, no upper bound
    CURRENCY = "currency"  # non-negative, no upper bound


# Static mapping: 26 entries, one per FairCamSubFunction slug.
# Source: audit §2.6 unit-type table (docs/reference/fair-cam-standard-alignment.md).
# Used by ControlFunctionAssignmentDTO.validate_capability_value_unit (M1)
# and _v3_to_fair_cam_control bridge (OQ7 skip logic).
SUB_FUNCTION_UNITS: dict[FairCamSubFunction, UnitType] = {
    # LEC — 9 sub-functions
    FairCamSubFunction.LEC_PREV_AVOIDANCE: UnitType.PROBABILITY,
    FairCamSubFunction.LEC_PREV_DETERRENCE: UnitType.PROBABILITY,
    FairCamSubFunction.LEC_PREV_RESISTANCE: UnitType.PROBABILITY,
    FairCamSubFunction.LEC_DET_VISIBILITY: UnitType.PROBABILITY,
    FairCamSubFunction.LEC_DET_MONITORING: UnitType.ELAPSED_TIME,
    FairCamSubFunction.LEC_DET_RECOGNITION: UnitType.PROBABILITY,
    FairCamSubFunction.LEC_RESP_EVENT_TERMINATION: UnitType.ELAPSED_TIME,
    # Issue #131: reclassified ELAPSED_TIME → PROBABILITY (evidentially-deferred).
    FairCamSubFunction.LEC_RESP_RESILIENCE: UnitType.PROBABILITY,
    FairCamSubFunction.LEC_RESP_LOSS_REDUCTION: UnitType.CURRENCY,
    # VMC — 6 sub-functions
    FairCamSubFunction.VMC_PREV_REDUCE_CHANGE_FREQ: UnitType.PERCENT_REDUCTION,
    FairCamSubFunction.VMC_PREV_REDUCE_VARIANCE_PROB: UnitType.PERCENT_REDUCTION,
    # Issue #131: reclassified ELAPSED_TIME → PROBABILITY (evidentially-deferred).
    FairCamSubFunction.VMC_ID_THREAT_INTELLIGENCE: UnitType.PROBABILITY,
    FairCamSubFunction.VMC_ID_CONTROL_MONITORING: UnitType.PROBABILITY,
    FairCamSubFunction.VMC_CORR_TREATMENT_SELECTION: UnitType.PROBABILITY,
    FairCamSubFunction.VMC_CORR_IMPLEMENTATION: UnitType.ELAPSED_TIME,
    # DSC — 11 sub-functions
    FairCamSubFunction.DSC_PREV_DEFINED_EXPECTATIONS: UnitType.PROBABILITY,
    FairCamSubFunction.DSC_PREV_COMMUNICATION: UnitType.PROBABILITY,
    FairCamSubFunction.DSC_PREV_SA_DATA_ASSET: UnitType.PROBABILITY,
    FairCamSubFunction.DSC_PREV_SA_DATA_THREAT: UnitType.PROBABILITY,
    FairCamSubFunction.DSC_PREV_SA_DATA_CONTROLS: UnitType.PROBABILITY,
    FairCamSubFunction.DSC_PREV_SA_ANALYSIS: UnitType.PROBABILITY,
    FairCamSubFunction.DSC_PREV_SA_REPORTING: UnitType.PROBABILITY,
    FairCamSubFunction.DSC_PREV_ENSURE_CAPABILITY: UnitType.PROBABILITY,
    FairCamSubFunction.DSC_PREV_INCENTIVES: UnitType.PROBABILITY,
    # Issue #131: reclassified ELAPSED_TIME → PROBABILITY (evidentially-deferred /
    # standard-virtual). DSC_CORR_MISALIGNED is FAIR-CAM §5.3 VIRTUAL.
    FairCamSubFunction.DSC_ID_MISALIGNED: UnitType.PROBABILITY,
    FairCamSubFunction.DSC_CORR_MISALIGNED: UnitType.PROBABILITY,
}

# Sub-functions whose capability_value is NOT in "per-year" time units.
# The composition formula's time-unit conversion step is skipped for these.
# Derived from SUB_FUNCTION_UNITS: all ELAPSED_TIME + CURRENCY entries (4 total
# post issue #131 τ recalibration):
#   3× ELAPSED_TIME: LEC_DET_MONITORING, LEC_RESP_EVENT_TERMINATION,
#                    VMC_CORR_IMPLEMENTATION
#   1× CURRENCY:     LEC_RESP_LOSS_REDUCTION
# (Six previously-ELAPSED_TIME sub-functions were reclassified to PROBABILITY
#  in issue #131 because they lacked primary-cited calibration sources; see
#  docs/plans/2026-05-15-issue-131-tau-calibration-design.md §3.)
TIME_UNIT_EXCLUDED: frozenset[FairCamSubFunction] = frozenset(
    sf for sf, ut in SUB_FUNCTION_UNITS.items() if ut in (UnitType.ELAPSED_TIME, UnitType.CURRENCY)
)
