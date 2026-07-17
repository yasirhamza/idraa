# tests/schemas/test_control_library_seed.py
import pytest
from pydantic import ValidationError

from idraa.schemas.control_library import ControlLibraryEntrySeed


def _valid():
    return {
        "slug": "mfa",
        "name": "Multi-Factor Authentication",
        "description": "MFA adds an authentication factor beyond a password.",
        "control_type": "technical",
        "reference_annual_cost": "30000",
        "nist_csf_subcategories": ["PR.AC-7"],
        "cis_safeguards": ["6.3"],
        "iso_27001_controls": [],
        "compliance_mappings": {},
        "applicable_industries": [],
        "applicable_org_sizes": [],
        "tags": ["identity"],
        "source_citations": ["FAIR Institute NIST CSF 1.1 → FAIR-CAM mapping"],
        "status": "published",
        "assignments": [
            {
                "sub_function": "lec_prev_resistance",
                "capability_default": 0.7,
                "coverage_default": 0.8,
                "reliability_default": 0.8,
            },
        ],
    }


def test_valid_entry_validates():
    seed = ControlLibraryEntrySeed.model_validate(_valid())
    assert seed.assignments[0].sub_function.value == "lec_prev_resistance"


def test_probability_capability_out_of_range_rejected():
    bad = _valid()
    bad["assignments"][0]["capability_default"] = 1.5  # PROBABILITY unit must be [0,1]
    with pytest.raises(ValidationError):
        ControlLibraryEntrySeed.model_validate(bad)


def test_virtual_subfunction_rejected():
    bad = _valid()
    bad["assignments"][0]["sub_function"] = (
        "dsc_corr_misaligned"  # virtual — no control may claim it
    )
    with pytest.raises(ValidationError):
        ControlLibraryEntrySeed.model_validate(bad)


def test_status_pattern_enforced():
    bad = _valid()
    bad["status"] = "live"
    with pytest.raises(ValidationError):
        ControlLibraryEntrySeed.model_validate(bad)
