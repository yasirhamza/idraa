"""DTO rejects NaN/Inf for all unit types (Sec-B3)."""

import pytest
from pydantic import ValidationError

from idraa.models.enums import FairCamSubFunction
from idraa.schemas.control import ControlFunctionAssignmentDTO


class TestFiniteCheck:
    """math.isfinite enforcement on capability_value."""

    @pytest.mark.parametrize(
        "sub_function",
        [
            FairCamSubFunction.LEC_PREV_RESISTANCE,  # PROBABILITY
            FairCamSubFunction.VMC_ID_CONTROL_MONITORING,  # ELAPSED_TIME
            FairCamSubFunction.LEC_RESP_LOSS_REDUCTION,  # CURRENCY
            FairCamSubFunction.VMC_PREV_REDUCE_CHANGE_FREQ,  # PERCENT_REDUCTION
        ],
    )
    def test_nan_rejected_for_all_units(self, sub_function: FairCamSubFunction) -> None:
        with pytest.raises(ValidationError, match=r"finite|NaN|Inf"):
            ControlFunctionAssignmentDTO(
                sub_function=sub_function,
                capability_value=float("nan"),
                coverage=0.8,
                reliability=0.8,
            )

    @pytest.mark.parametrize(
        "sub_function",
        [
            FairCamSubFunction.VMC_ID_CONTROL_MONITORING,  # ELAPSED_TIME
            FairCamSubFunction.LEC_RESP_LOSS_REDUCTION,  # CURRENCY
        ],
    )
    def test_inf_rejected_for_unbounded_units(self, sub_function: FairCamSubFunction) -> None:
        with pytest.raises(ValidationError, match=r"finite|Inf"):
            ControlFunctionAssignmentDTO(
                sub_function=sub_function,
                capability_value=float("inf"),
                coverage=0.8,
                reliability=0.8,
            )

    def test_large_finite_accepted_for_elapsed_time(self) -> None:
        # 1e8 days = ~270k years; large but finite. DTO accepts; DB CHECK constraint
        # at <= 1e10 rejects only when persisted (covered in migration test).
        # Issue #131: switched dropped VMC_ID_CONTROL_MONITORING → kept
        # LEC_DET_MONITORING (still ELAPSED_TIME post-recalibration).
        dto = ControlFunctionAssignmentDTO(
            sub_function=FairCamSubFunction.LEC_DET_MONITORING,
            capability_value=1e8,
            coverage=0.8,
            reliability=0.8,
        )
        assert dto.capability_value == 1e8

    def test_null_still_accepted(self) -> None:
        # NULL is the sentinel for "use industry-median fallback".
        # Issue #131: switched dropped VMC_ID_CONTROL_MONITORING → kept
        # LEC_DET_MONITORING (still ELAPSED_TIME).
        dto = ControlFunctionAssignmentDTO(
            sub_function=FairCamSubFunction.LEC_DET_MONITORING,
            capability_value=None,
            coverage=0.8,
            reliability=0.8,
        )
        assert dto.capability_value is None
