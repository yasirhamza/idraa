"""Unit tests for the FAIR-CAM CSV path → FairCamSubFunction lookup (#68)."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from idraa.models.enums import FairCamSubFunction
from idraa.services.controls_importer_lookup import (
    PATH_TO_SUB_FUNCTION,
    VIRTUAL_REJECT,
    normalize_path,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CSV_PATH = REPO_ROOT / "docs" / "reference" / "fair-cam-controls-library.csv"


def _csv_distinct_paths() -> set[str]:
    out: set[str] = set()
    with CSV_PATH.open(encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))
    for row in rows[2:]:
        if len(row) < 4 or not (row[1] or "").strip():
            continue
        for line in (row[3] or "").split("\n"):
            stripped = line.strip()
            if stripped:
                out.add(stripped)
    return out


@pytest.mark.parametrize(
    "canonical_path, expected_subfn",
    [
        ("LEC - Prevention - Avoidance", FairCamSubFunction.LEC_PREV_AVOIDANCE),
        ("LEC - Prevention - Deterrence", FairCamSubFunction.LEC_PREV_DETERRENCE),
        ("LEC - Prevention - Resistance", FairCamSubFunction.LEC_PREV_RESISTANCE),
        ("LEC - Detection - Visibility", FairCamSubFunction.LEC_DET_VISIBILITY),
        ("LEC - Detection - Monitoring", FairCamSubFunction.LEC_DET_MONITORING),
        ("LEC - Detection - Recognition", FairCamSubFunction.LEC_DET_RECOGNITION),
        ("LEC - Response - Event Termination", FairCamSubFunction.LEC_RESP_EVENT_TERMINATION),
        ("LEC - Response - Resilience", FairCamSubFunction.LEC_RESP_RESILIENCE),
        ("LEC - Response - Loss Reduction", FairCamSubFunction.LEC_RESP_LOSS_REDUCTION),
        (
            "VMC - Prevention - Reduce Change Frequency",
            FairCamSubFunction.VMC_PREV_REDUCE_CHANGE_FREQ,
        ),
        (
            "VMC - Prevention - Reduce Variance Probability",
            FairCamSubFunction.VMC_PREV_REDUCE_VARIANCE_PROB,
        ),
        (
            "VMC - Identification - Threat Capability Monitoring",
            FairCamSubFunction.VMC_ID_THREAT_INTELLIGENCE,
        ),
        (
            "VMC - Identification - Control Monitoring",
            FairCamSubFunction.VMC_ID_CONTROL_MONITORING,
        ),
        ("VMC - Correction - Implementation", FairCamSubFunction.VMC_CORR_IMPLEMENTATION),
        (
            "DSC - Prevent Misaligned Decisions - Define Expectations and Objectives",
            FairCamSubFunction.DSC_PREV_DEFINED_EXPECTATIONS,
        ),
        (
            "DSC - Prevent Misaligned Decisions - Communicate Expectations and Objectives",
            FairCamSubFunction.DSC_PREV_COMMUNICATION,
        ),
        (
            "DSC - Prevent Misaligned Decisions - Provide Situational Awareness - Provide Data - Provide Asset Data",
            FairCamSubFunction.DSC_PREV_SA_DATA_ASSET,
        ),
        (
            "DSC - Prevent Misaligned Decisions - Provide Situational Awareness - Provide Data - Provide Threat Data",
            FairCamSubFunction.DSC_PREV_SA_DATA_THREAT,
        ),
        (
            "DSC - Prevent Misaligned Decisions - Provide Situational Awareness - Provide Data - Provide Control Data",
            FairCamSubFunction.DSC_PREV_SA_DATA_CONTROLS,
        ),
        (
            "DSC - Prevent Misaligned Decisions - Provide Situational Awareness - Analysis",
            FairCamSubFunction.DSC_PREV_SA_ANALYSIS,
        ),
        (
            "DSC - Prevent Misaligned Decisions - Provide Situational Awareness - Reporting",
            FairCamSubFunction.DSC_PREV_SA_REPORTING,
        ),
        (
            "DSC - Prevent Misaligned Decisions - Incentives",
            FairCamSubFunction.DSC_PREV_INCENTIVES,
        ),
    ],
)
def test_canonical_path_resolves(canonical_path: str, expected_subfn: FairCamSubFunction) -> None:
    assert PATH_TO_SUB_FUNCTION[normalize_path(canonical_path)] == expected_subfn


def test_virtual_path_returns_sentinel() -> None:
    """DSC_CORR_MISALIGNED is virtual — sentinel return; callers skip the
    assignment + log warning."""
    assert (
        PATH_TO_SUB_FUNCTION[normalize_path("DSC - Correct Misaligned Decisions")] is VIRTUAL_REJECT
    )


def test_every_csv_distinct_path_has_coverage() -> None:
    """Every distinct path in the cleaned canonical CSV resolves."""
    missing: list[str] = []
    for path in _csv_distinct_paths():
        if normalize_path(path) not in PATH_TO_SUB_FUNCTION:
            missing.append(path)
    assert not missing, (
        "PATH_TO_SUB_FUNCTION missing coverage for CSV paths:\n  "
        + "\n  ".join(sorted(missing))
        + "\n\nAdd a canonical entry to controls_importer_lookup.py."
    )


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("  LEC - Prevention - Avoidance  ", "lec - prevention - avoidance"),
        ("LEC  -  Prevention  -  Avoidance", "lec - prevention - avoidance"),
        ("\tLEC\t-\tPrevention\t-\tAvoidance", "lec - prevention - avoidance"),
        ("", ""),
    ],
)
def test_normalize_path(raw: str, expected: str) -> None:
    assert normalize_path(raw) == expected
