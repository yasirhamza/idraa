from idraa.models.enums import FairCamSubFunction
from idraa.services.crosswalk_reconciliation import (
    SPREADSHEET_LABEL_TO_SUBFUNCTION,
    normalize_label,
    resolve_label,
)


def test_every_mapped_label_resolves_to_a_real_enum_member():
    for sf in SPREADSHEET_LABEL_TO_SUBFUNCTION.values():
        assert isinstance(sf, FairCamSubFunction)


def test_event_termination_typo_and_threat_intel_variants_handled():
    assert resolve_label("Event Termintion") == FairCamSubFunction.LEC_RESP_EVENT_TERMINATION
    # NIST and CIS label the VMC threat-intel function differently — BOTH resolve:
    assert resolve_label("Threat Capability Intel") == FairCamSubFunction.VMC_ID_THREAT_INTELLIGENCE
    assert resolve_label("Threat Intel") == FairCamSubFunction.VMC_ID_THREAT_INTELLIGENCE


def test_normalize_is_whitespace_and_case_insensitive():
    # handles the embedded newline in "Define\nExp's & Obj's"
    assert normalize_label("Define\nExp's & Obj's") == normalize_label("define exp's & obj's")


def test_no_distinct_subfunction_is_unmapped():
    mapped = set(SPREADSHEET_LABEL_TO_SUBFUNCTION.values())
    distinct = {sf for sf in FairCamSubFunction if sf != FairCamSubFunction.DSC_CORR_MISALIGNED}
    assert distinct - mapped == set(), f"unmapped sub-functions: {distinct - mapped}"


def test_virtual_function_label_is_not_mapped():
    # "Correct Misaligned Decisions" is a virtual placeholder column with no X-data;
    # it must NEVER resolve to a real control function.
    import pytest

    with pytest.raises(KeyError):
        resolve_label("Correct Misaligned Decisions")
