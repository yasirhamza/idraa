from __future__ import annotations

import pytest

from idraa.services.fair_cam_validation import (
    FAIRCAMValidationError,
    validate_fair_distributions,
)

_TEF = {"distribution": "PERT", "low": 0.1, "mode": 0.5, "high": 2}
_PL = {"distribution": "PERT", "low": 100000, "mode": 1000000, "high": 15000000}


def _validate(vuln: dict) -> None:
    validate_fair_distributions(
        threat_event_frequency=_TEF, vulnerability=vuln, primary_loss=_PL, secondary_loss=None
    )


def test_valid_vuln_passes() -> None:
    _validate({"distribution": "PERT", "low": 0.1, "mode": 0.3, "high": 0.6})  # no raise


def test_vuln_above_one_rejected() -> None:
    with pytest.raises(FAIRCAMValidationError, match="vuln"):
        _validate({"distribution": "PERT", "low": 0.1, "mode": 0.5, "high": 1.5})


def test_vuln_below_zero_rejected() -> None:
    with pytest.raises(FAIRCAMValidationError):
        _validate({"distribution": "PERT", "low": -0.1, "mode": 0.5, "high": 0.9})


def test_vuln_out_of_order_rejected() -> None:
    with pytest.raises(FAIRCAMValidationError):
        _validate({"distribution": "PERT", "low": 0.6, "mode": 0.3, "high": 0.9})


def test_vuln_non_numeric_rejected() -> None:
    with pytest.raises(FAIRCAMValidationError):
        _validate({"distribution": "PERT", "low": "x", "mode": 0.3, "high": 0.6})


_VALID_VULN = {"distribution": "PERT", "low": 0.1, "mode": 0.3, "high": 0.6}
_INF = float("inf")
_NAN = float("nan")


def _validate_dists(
    *,
    tef: dict | None = None,
    pl: dict | None = None,
    sl: dict | None = None,
    vuln: dict | None = None,
) -> None:
    validate_fair_distributions(
        threat_event_frequency=tef if tef is not None else dict(_TEF),
        vulnerability=vuln if vuln is not None else dict(_VALID_VULN),
        primary_loss=pl if pl is not None else dict(_PL),
        secondary_loss=sl,
    )


def test_finite_scenario_passes() -> None:
    _validate_dists()  # no raise


# Meth-B1: non-finite (inf) in unbounded-above distributions must be rejected.
def test_tef_inf_high_rejected() -> None:
    with pytest.raises(FAIRCAMValidationError, match="finite"):
        _validate_dists(tef={"distribution": "PERT", "low": 0.1, "mode": 0.5, "high": _INF})


def test_primary_loss_inf_high_rejected() -> None:
    with pytest.raises(FAIRCAMValidationError, match="finite"):
        _validate_dists(pl={"distribution": "PERT", "low": 100000, "mode": 1000000, "high": _INF})


def test_secondary_loss_inf_high_rejected() -> None:
    with pytest.raises(FAIRCAMValidationError, match="finite"):
        _validate_dists(sl={"distribution": "PERT", "low": 1000, "mode": 5000, "high": _INF})


# NaN must be rejected explicitly (not relying on incidental fair_cam rejection).
def test_tef_nan_rejected() -> None:
    with pytest.raises(FAIRCAMValidationError, match="finite"):
        _validate_dists(tef={"distribution": "PERT", "low": 0.1, "mode": 0.5, "high": _NAN})


def test_primary_loss_nan_rejected() -> None:
    with pytest.raises(FAIRCAMValidationError, match="finite"):
        _validate_dists(pl={"distribution": "PERT", "low": 100000, "mode": 1000000, "high": _NAN})


def test_secondary_loss_nan_rejected() -> None:
    with pytest.raises(FAIRCAMValidationError, match="finite"):
        _validate_dists(sl={"distribution": "PERT", "low": 1000, "mode": 5000, "high": _NAN})


# Regression: vuln inf/nan still rejected (the [0,1] bound also excludes these).
def test_vuln_inf_rejected() -> None:
    with pytest.raises(FAIRCAMValidationError):
        _validate_dists(vuln={"distribution": "PERT", "low": 0.1, "mode": 0.3, "high": _INF})


def test_vuln_nan_rejected() -> None:
    with pytest.raises(FAIRCAMValidationError):
        _validate_dists(vuln={"distribution": "PERT", "low": 0.1, "mode": 0.3, "high": _NAN})
