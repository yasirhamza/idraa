"""Unit tests for idraa.models.enums helpers (#68)."""

from __future__ import annotations

import pytest

from idraa.models.enums import (
    ControlDomain,
    FairCamSubFunction,
    subfunction_to_domain,
)


@pytest.mark.parametrize(
    "subfn, expected_domain",
    [
        (FairCamSubFunction.LEC_PREV_AVOIDANCE, ControlDomain.LOSS_EVENT),
        (FairCamSubFunction.LEC_RESP_RESILIENCE, ControlDomain.LOSS_EVENT),
        (FairCamSubFunction.LEC_RESP_LOSS_REDUCTION, ControlDomain.LOSS_EVENT),
        (FairCamSubFunction.VMC_PREV_REDUCE_CHANGE_FREQ, ControlDomain.VARIANCE_MANAGEMENT),
        (FairCamSubFunction.VMC_ID_THREAT_INTELLIGENCE, ControlDomain.VARIANCE_MANAGEMENT),
        (FairCamSubFunction.VMC_CORR_IMPLEMENTATION, ControlDomain.VARIANCE_MANAGEMENT),
        (FairCamSubFunction.DSC_PREV_DEFINED_EXPECTATIONS, ControlDomain.DECISION_SUPPORT),
        (FairCamSubFunction.DSC_PREV_SA_DATA_THREAT, ControlDomain.DECISION_SUPPORT),
        (FairCamSubFunction.DSC_CORR_MISALIGNED, ControlDomain.DECISION_SUPPORT),
    ],
)
def test_subfunction_to_domain(subfn: FairCamSubFunction, expected_domain: ControlDomain) -> None:
    """Pure decoder of the LEC/VMC/DSC slug prefix → ControlDomain."""
    assert subfunction_to_domain(subfn) == expected_domain
