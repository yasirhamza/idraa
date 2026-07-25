"""D12: "lognormal is strictly a loss distribution" — chokepoint + import gates.

TEF and vulnerability are PERT-only in v3 storage; lognormal/lognormal_mixture
are loss-node shapes. The rule is enforced at validate_fair_distributions
(covers every write path) AND at both import allow-tables (structural gate).
"""

from __future__ import annotations

import pytest

from idraa.services.fair_cam_validation import FAIRCAMValidationError, validate_fair_distributions
from idraa.services.scenario_import import _structural_dist_problem

_PERT_TEF = {"distribution": "PERT", "low": 0.2, "mode": 0.5, "high": 1.5}
_PERT_VULN = {"distribution": "PERT", "low": 0.1, "mode": 0.2, "high": 0.4}
_LOGN = {"distribution": "lognormal", "mean": 0.66, "sigma": 0.58}
_LOGN_LOSS = {"distribution": "lognormal", "mean": 12.0, "sigma": 1.7}


def test_chokepoint_rejects_lognormal_tef() -> None:
    with pytest.raises(FAIRCAMValidationError, match="strictly a loss distribution"):
        validate_fair_distributions(
            threat_event_frequency=_LOGN,
            vulnerability=_PERT_VULN,
            primary_loss=_LOGN_LOSS,
            secondary_loss=None,
        )


def test_chokepoint_rejects_lognormal_vulnerability() -> None:
    with pytest.raises(FAIRCAMValidationError, match="strictly a loss distribution"):
        validate_fair_distributions(
            threat_event_frequency=_PERT_TEF,
            vulnerability=_LOGN,
            primary_loss=_LOGN_LOSS,
            secondary_loss=None,
        )


def test_chokepoint_accepts_lognormal_loss_and_keyless_vuln() -> None:
    """Lognormal LOSS stays legal, and a keyless vuln dict (the real prod
    shape — no 'distribution' key, engine defaults PERT) passes."""
    validate_fair_distributions(
        threat_event_frequency=_PERT_TEF,
        vulnerability={"low": 0.1, "mode": 0.2, "high": 0.4},
        primary_loss=_LOGN_LOSS,
        secondary_loss=_LOGN_LOSS,
    )


def test_import_gate_rejects_lognormal_tef() -> None:
    problem = _structural_dist_problem("threat_event_frequency", _LOGN, allow_lognormal=False)
    assert problem is not None and "not allowed" in problem


def test_import_gate_still_accepts_lognormal_loss() -> None:
    assert _structural_dist_problem("primary_loss", _LOGN_LOSS, allow_lognormal=True) is None
