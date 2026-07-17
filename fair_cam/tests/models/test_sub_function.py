"""Parity test: fair_cam's FairCamSubFunction must mirror v3's enum
verbatim. v3 is the canonical source (added in commit 5776ff1, PR ι);
fair_cam's copy exists so fair_cam doesn't depend on v3."""

from fair_cam.models.sub_function import FairCamSubFunction


def test_enum_has_26_values():
    """Standard §3-§5 defines 26 sub-functions across LEC/VMC/DSC."""
    assert len(list(FairCamSubFunction)) == 26


def test_lec_prevention_or_trio_present():
    assert FairCamSubFunction.LEC_PREV_AVOIDANCE.value == "lec_prev_avoidance"
    assert FairCamSubFunction.LEC_PREV_DETERRENCE.value == "lec_prev_deterrence"
    assert FairCamSubFunction.LEC_PREV_RESISTANCE.value == "lec_prev_resistance"


def test_lec_detection_and_trio_present():
    assert FairCamSubFunction.LEC_DET_VISIBILITY.value == "lec_det_visibility"
    assert FairCamSubFunction.LEC_DET_MONITORING.value == "lec_det_monitoring"
    assert FairCamSubFunction.LEC_DET_RECOGNITION.value == "lec_det_recognition"


def test_lec_response_subfunctions_present():
    """LEC Response weak-AND-trio sub-functions present (verified separately
    from cross-system parity, which lives in v3-side test per architectural
    layering — fair_cam doesn't import from idraa)."""
    assert FairCamSubFunction.LEC_RESP_EVENT_TERMINATION.value == "lec_resp_event_termination"
    assert FairCamSubFunction.LEC_RESP_RESILIENCE.value == "lec_resp_resilience"
    assert FairCamSubFunction.LEC_RESP_LOSS_REDUCTION.value == "lec_resp_loss_reduction"


def test_vmc_subfunctions_present():
    assert FairCamSubFunction.VMC_PREV_REDUCE_CHANGE_FREQ.value == "vmc_prev_reduce_change_freq"
    assert FairCamSubFunction.VMC_PREV_REDUCE_VARIANCE_PROB.value == "vmc_prev_reduce_variance_prob"
    assert FairCamSubFunction.VMC_ID_THREAT_INTELLIGENCE.value == "vmc_id_threat_intelligence"
    assert FairCamSubFunction.VMC_ID_CONTROL_MONITORING.value == "vmc_id_control_monitoring"
    assert FairCamSubFunction.VMC_CORR_TREATMENT_SELECTION.value == "vmc_corr_treatment_selection"
    assert FairCamSubFunction.VMC_CORR_IMPLEMENTATION.value == "vmc_corr_implementation"


def test_dsc_subfunctions_present():
    assert FairCamSubFunction.DSC_PREV_DEFINED_EXPECTATIONS.value == "dsc_prev_defined_expectations"
    assert FairCamSubFunction.DSC_PREV_COMMUNICATION.value == "dsc_prev_communication"
    assert FairCamSubFunction.DSC_PREV_SA_DATA_ASSET.value == "dsc_prev_sa_data_asset"
    assert FairCamSubFunction.DSC_PREV_SA_DATA_THREAT.value == "dsc_prev_sa_data_threat"
    assert FairCamSubFunction.DSC_PREV_SA_DATA_CONTROLS.value == "dsc_prev_sa_data_controls"
    assert FairCamSubFunction.DSC_PREV_SA_ANALYSIS.value == "dsc_prev_sa_analysis"
    assert FairCamSubFunction.DSC_PREV_SA_REPORTING.value == "dsc_prev_sa_reporting"
    assert FairCamSubFunction.DSC_PREV_ENSURE_CAPABILITY.value == "dsc_prev_ensure_capability"
    assert FairCamSubFunction.DSC_PREV_INCENTIVES.value == "dsc_prev_incentives"
    assert FairCamSubFunction.DSC_ID_MISALIGNED.value == "dsc_id_misaligned"
    assert FairCamSubFunction.DSC_CORR_MISALIGNED.value == "dsc_corr_misaligned"
